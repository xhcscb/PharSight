"""PaddleOCR document reader for PDFs and layout-heavy financial reports.

Supports two hosted APIs (no local PaddlePaddle inference):

1. Official PaddleOCR document-parsing API (AI Studio access token)
   POST https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
2. Baidu Intelligent Cloud 文档解析 (PaddleOCR-VL)
   POST https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task

Credentials are read from environment variables only. Never store API keys in source.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from ..base import Tool, ToolResult
from ..pharma.web_evidence import stable_web_source_id
from .document_index import build_document_index

# PaddleOCR credentials are configured only through the project-root .env file.
# See .env.example for the supported AI Studio and Baidu Cloud variables.
# =============================================================================

OFFICIAL_DEFAULT_BASE_URL = "https://paddleocr.aistudio-app.com"
OFFICIAL_JOBS_PATH = "/api/v2/ocr/jobs"
OFFICIAL_DEFAULT_MODEL = "PaddleOCR-VL"

BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_SUBMIT_URL = "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task"
BAIDU_QUERY_URL = "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task/query"

_baidu_token_cache: Dict[str, str] = {}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT_CACHE_DIR = PROJECT_ROOT / "outputs" / ".document_cache" / "paddleocr"


def _first_nonempty(*values: Optional[str]) -> str:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return ""


def resolve_credentials() -> Dict[str, str]:
    """Resolve PaddleOCR credentials exclusively from environment variables."""
    return {
        "access_token": _first_nonempty(os.getenv("PADDLEOCR_ACCESS_TOKEN")),
        "api_key": _first_nonempty(os.getenv("PADDLEOCR_API_KEY")),
        "secret_key": _first_nonempty(os.getenv("PADDLEOCR_SECRET_KEY")),
        "base_url": _first_nonempty(
            os.getenv("PADDLEOCR_BASE_URL"),
            OFFICIAL_DEFAULT_BASE_URL,
        ),
    }


def credentials_configured(creds: Optional[Dict[str, str]] = None) -> bool:
    creds = creds or resolve_credentials()
    if creds.get("access_token"):
        return True
    return bool(creds.get("api_key") and creds.get("secret_key"))


def missing_key_message() -> str:
    return (
        "PaddleOCR API key is not set. Fill PADDLEOCR_ACCESS_TOKEN "
        "(AI Studio) or PADDLEOCR_API_KEY + PADDLEOCR_SECRET_KEY "
        "(Baidu OCR) in the project-root .env file."
    )


def _guess_file_name(file_path: Optional[str], file_url: Optional[str]) -> str:
    if file_path:
        return os.path.basename(file_path)
    if file_url:
        parsed = urlparse(file_url)
        name = os.path.basename(parsed.path) or "document.pdf"
        return name.split("?")[0] or "document.pdf"
    return "document.pdf"


def _normalize_source(file_path: Optional[str], file_url: Optional[str]) -> str:
    if file_path:
        return os.path.abspath(file_path)
    return file_url or ""


def _cache_key(
    source: str,
    *,
    backend: str,
    model: str,
    analysis_chart: bool,
    merge_tables: bool,
) -> str:
    """Build a credential-free cache key for one complete OCR parse."""
    material = json.dumps(
        {
            "source": source,
            "backend": backend,
            "model": model,
            "analysis_chart": bool(analysis_chart),
            "merge_tables": bool(merge_tables),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cache_path(cache_key: str, suffix: str = ".json") -> Path:
    return DOCUMENT_CACHE_DIR / f"{cache_key}{suffix}"


def _load_cached_payload(cache_key: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("markdown") else None


def _save_cached_payload(cache_key: str, payload: Dict[str, Any]) -> None:
    DOCUMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_key)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _limit_payload(payload: Dict[str, Any], max_pages: Optional[int]) -> Dict[str, Any]:
    """Return a copy restricted for the caller without shrinking the cache."""
    limited = copy.deepcopy(payload)
    if not max_pages:
        return limited
    page_limit = max(1, int(max_pages))
    pages = list(limited.get("pages") or [])[:page_limit]
    if pages:
        limited["pages"] = pages
        limited["markdown"] = "\n\n".join(pages)
        limited["page_count"] = len(pages)
    return limited


def _page_markdown_from_layout(item: Dict[str, Any]) -> str:
    markdown = item.get("markdown") or {}
    if isinstance(markdown, dict) and markdown.get("text"):
        return str(markdown["text"])
    if isinstance(markdown, str):
        return markdown
    return ""


def _extract_official_pages(jsonl_data: List[Any]) -> Tuple[str, List[str], int]:
    pages: List[str] = []
    for line_obj in jsonl_data:
        if not isinstance(line_obj, dict):
            continue
        result = line_obj.get("result") or line_obj
        layout_results = result.get("layoutParsingResults") or []
        for item in layout_results:
            text = _page_markdown_from_layout(item)
            if text.strip():
                pages.append(text.strip())
        ocr_results = result.get("ocrResults") or []
        if not layout_results and ocr_results:
            chunks = []
            for ocr_item in ocr_results:
                pruned = ocr_item.get("prunedResult") or {}
                rec_texts = pruned.get("rec_texts") or pruned.get("recTexts") or []
                if rec_texts:
                    chunks.append("\n".join(str(t) for t in rec_texts))
            if chunks:
                pages.append("\n".join(chunks))
    markdown = "\n\n".join(pages).strip()
    return markdown, pages, len(pages)


def _extract_baidu_pages(parse_json: Dict[str, Any]) -> Tuple[str, List[str], List[str], int]:
    pages_payload = parse_json.get("pages") or []
    page_texts: List[str] = []
    tables: List[str] = []
    for page in pages_payload:
        text = (page.get("text") or "").strip()
        if text:
            page_texts.append(text)
        for table in page.get("tables") or []:
            md = table.get("markdown")
            if md:
                tables.append(md)
    markdown = "\n\n".join(page_texts).strip()
    return markdown, page_texts, tables, len(pages_payload)


class PaddleOCRDocumentReader(Tool):
    """Read PDFs (and similar documents) via hosted PaddleOCR-VL APIs."""

    def __init__(self):
        super().__init__(
            name="PaddleOCR PDF reader",
            description=(
                "Parse PDF / image / Office documents with PaddleOCR-VL. "
                "Use this for annual reports, prospectuses, research notes, "
                "scanned filings, and other layout-heavy financial PDFs. "
                "Provide either a local file_path or a public file_url."
            ),
            parameters=[
                {
                    "name": "file_path",
                    "type": "str",
                    "description": "Local PDF/image/Office file path. Mutually exclusive with file_url.",
                    "required": False,
                },
                {
                    "name": "file_url",
                    "type": "str",
                    "description": "Public URL of the document. Mutually exclusive with file_path.",
                    "required": False,
                },
                {
                    "name": "model",
                    "type": "str",
                    "description": "Official API model name, e.g. PaddleOCR-VL or PP-StructureV3.",
                    "required": False,
                },
                {
                    "name": "analysis_chart",
                    "type": "bool",
                    "description": "Parse statistical charts in the document (default True).",
                    "required": False,
                },
                {
                    "name": "merge_tables",
                    "type": "bool",
                    "description": "Merge cross-page tables (default True).",
                    "required": False,
                },
                {
                    "name": "max_pages",
                    "type": "int",
                    "description": (
                        "Optional preview limit only. The complete parsed document is always "
                        "cached and indexed; omit this for complete-document workflows."
                    ),
                    "required": False,
                },
                {
                    "name": "document_title",
                    "type": "str",
                    "description": "Verified disclosure title used for document identity and provenance.",
                    "required": False,
                },
            ],
        )
        self.type = "tool_paddleocr_pdf"

    def _error_result(self, message: str, source: str = "") -> List[ToolResult]:
        return [
            ToolResult(
                name=self.name,
                description=message,
                data=None,
                source=source or "PaddleOCR document parser",
            )
        ]

    async def api_function(
        self,
        file_path: str = None,
        file_url: str = None,
        model: str = None,
        analysis_chart: bool = True,
        merge_tables: bool = True,
        max_pages: int = None,
        document_title: str = None,
    ) -> List[ToolResult]:
        file_path = (file_path or "").strip() or None
        file_url = (file_url or "").strip() or None
        if bool(file_path) == bool(file_url):
            return self._error_result(
                "Provide exactly one of file_path or file_url."
            )
        if file_path and not os.path.exists(file_path):
            return self._error_result(f"Local file not found: {file_path}")
        if file_path:
            resolved_path = Path(file_path).resolve()
            try:
                resolved_path.relative_to(PROJECT_ROOT)
            except ValueError:
                return self._error_result("Local documents must stay inside the Pharsight project directory.")
            file_path = str(resolved_path)

        creds = resolve_credentials()
        if not credentials_configured(creds):
            return self._error_result(missing_key_message())

        source = _normalize_source(file_path, file_url)
        model_name = model or os.getenv("PADDLEOCR_MODEL") or OFFICIAL_DEFAULT_MODEL
        backend = "official" if creds["access_token"] else "baidu"
        cache_key = _cache_key(
            source,
            backend=backend,
            model=model_name if backend == "official" else "PaddleOCR-VL",
            analysis_chart=analysis_chart,
            merge_tables=merge_tables,
        )
        try:
            payload = _load_cached_payload(cache_key)
            if payload is None:
                if creds["access_token"]:
                    payload = await self._parse_official(
                        creds=creds,
                        file_path=file_path,
                        file_url=file_url,
                        model=model_name,
                        analysis_chart=analysis_chart,
                        merge_tables=merge_tables,
                        max_pages=None,
                        cache_key=cache_key,
                    )
                else:
                    payload = await self._parse_baidu(
                        creds=creds,
                        file_path=file_path,
                        file_url=file_url,
                        analysis_chart=analysis_chart,
                        merge_tables=merge_tables,
                        max_pages=None,
                    )
                _save_cached_payload(cache_key, payload)
                _cache_path(cache_key, ".pending.json").unlink(missing_ok=True)
            retrieved_at = payload.get("retrieved_at") or datetime.now().astimezone().isoformat()
            parser = f"paddleocr:{payload.get('backend', backend)}:{payload.get('model', model_name)}"
            full_markdown = str(payload.get("markdown") or "")
            document_hash = hashlib.sha256(full_markdown.encode("utf-8")).hexdigest()
            citation_source_id = (
                stable_web_source_id(file_url, document_hash)
                if file_url
                else f"DOC-{document_hash[:16].upper()}"
            )
            index = build_document_index(
                pages=payload.get("pages") or [full_markdown],
                source_url=file_url or file_path or "",
                title=(document_title or "").strip() or payload.get("file_name") or "document",
                parser=parser,
                retrieved_at=retrieved_at,
                document_hash=document_hash,
                source_id=citation_source_id,
            )
            payload = _limit_payload(payload, max_pages)
            payload["retrieved_at"] = retrieved_at
            payload["source_url"] = file_url
            payload["document_title"] = (document_title or "").strip() or None
            payload["parser"] = parser
            payload["document_hash"] = document_hash
            payload["source_id"] = citation_source_id
            payload["index"] = {
                "document_hash": index["document_hash"],
                "source_id": index["source_id"],
                "page_count": index["page_count"],
                "chunk_count": index["chunk_count"],
                "chunk_chars": index["chunk_chars"],
                "overlap_chars": index["overlap_chars"],
            }
        except Exception as exc:
            return self._error_result(f"PaddleOCR parse failed: {exc}", source=source)

        return [
            ToolResult(
                name=f"{self.name}: {payload.get('file_name', 'document')}",
                description=(
                    f"Parsed document ({payload.get('page_count', 0)} pages) "
                    "via PaddleOCR-VL."
                ),
                data=payload,
                source=f"PaddleOCR-VL. {source}".strip(),
            )
        ]

    async def _parse_official(
        self,
        creds: Dict[str, str],
        file_path: Optional[str],
        file_url: Optional[str],
        model: str,
        analysis_chart: bool,
        merge_tables: bool,
        max_pages: Optional[int],
        cache_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        base_url = creds["base_url"].rstrip("/")
        jobs_url = f"{base_url}{OFFICIAL_JOBS_PATH}"
        headers = {
            "Authorization": f"Bearer {creds['access_token']}",
        }
        optional_payload = {
            "useChartRecognition": bool(analysis_chart),
            "mergeTables": bool(merge_tables),
            "prettifyMarkdown": True,
        }
        timeout = httpx.Timeout(300.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            job_id = self._load_pending_job(cache_key) if cache_key else None
            if not job_id:
                if file_url:
                    response = await client.post(
                        jobs_url,
                        headers={**headers, "Content-Type": "application/json"},
                        json={
                            "fileUrl": file_url,
                            "model": model,
                            "optionalPayload": optional_payload,
                        },
                    )
                else:
                    with open(file_path, "rb") as handle:
                        file_bytes = handle.read()
                    response = await client.post(
                        jobs_url,
                        headers=headers,
                        data={
                            "model": model,
                            "optionalPayload": json.dumps(optional_payload),
                        },
                        files={"file": (_guess_file_name(file_path, None), file_bytes)},
                    )
                response.raise_for_status()
                job_id = self._unwrap_official_job_id(response.json())
                if cache_key:
                    self._save_pending_job(cache_key, job_id)
            status_data = await self._poll_official_job(client, jobs_url, headers, job_id)
            result_url = ((status_data.get("resultUrl") or {}).get("jsonUrl") or "")
            if not result_url:
                raise RuntimeError("Official PaddleOCR job finished without jsonUrl.")
            jsonl_resp = await client.get(result_url)
            jsonl_resp.raise_for_status()
            jsonl_data = [
                json.loads(line)
                for line in jsonl_resp.text.strip().splitlines()
                if line.strip()
            ]

        markdown, pages, page_count = _extract_official_pages(jsonl_data)
        if max_pages:
            pages = pages[: max(1, int(max_pages))]
            markdown = "\n\n".join(pages)
            page_count = len(pages)
        return {
            "backend": "official",
            "model": model,
            "file_name": _guess_file_name(file_path, file_url),
            "job_id": job_id,
            "page_count": page_count,
            "markdown": markdown,
            "pages": pages,
            "tables": [],
        }

    @staticmethod
    def _load_pending_job(cache_key: str) -> Optional[str]:
        path = _cache_path(cache_key, ".pending.json")
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return str(payload.get("job_id") or "").strip() or None

    @staticmethod
    def _save_pending_job(cache_key: str, job_id: str) -> None:
        DOCUMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(cache_key, ".pending.json")
        path.write_text(json.dumps({"job_id": job_id}), encoding="utf-8")

    def _unwrap_official_job_id(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise RuntimeError("Official PaddleOCR response is not a JSON object.")
        code = payload.get("code", 0)
        if code not in (0, None):
            raise RuntimeError(
                payload.get("msg")
                or payload.get("errorMsg")
                or payload.get("message")
                or f"Official API error code={code}"
            )
        data = payload.get("data") or payload
        job_id = data.get("jobId") or data.get("job_id")
        if not job_id:
            raise RuntimeError("Official PaddleOCR submit response missing jobId.")
        return job_id

    async def _poll_official_job(
        self,
        client: httpx.AsyncClient,
        jobs_url: str,
        headers: Dict[str, str],
        job_id: str,
        max_wait: float = 600.0,
    ) -> Dict[str, Any]:
        interval = 3.0
        elapsed = 0.0
        while elapsed < max_wait:
            response = await client.get(f"{jobs_url}/{job_id}", headers=headers)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or payload
            state = data.get("state")
            if state == "done":
                return data
            if state == "failed":
                raise RuntimeError(data.get("errorMsg") or "Official PaddleOCR job failed.")
            await asyncio.sleep(interval)
            elapsed += interval
            interval = min(interval * 1.5, 15.0)
        raise TimeoutError(f"Official PaddleOCR job timed out: {job_id}")

    async def _parse_baidu(
        self,
        creds: Dict[str, str],
        file_path: Optional[str],
        file_url: Optional[str],
        analysis_chart: bool,
        merge_tables: bool,
        max_pages: Optional[int],
    ) -> Dict[str, Any]:
        token = await self._get_baidu_access_token(creds["api_key"], creds["secret_key"])
        file_name = _guess_file_name(file_path, file_url)
        form: Dict[str, Any] = {
            "file_name": file_name,
            "analysis_chart": str(bool(analysis_chart)).lower(),
            "merge_tables": str(bool(merge_tables)).lower(),
        }
        if file_path:
            with open(file_path, "rb") as handle:
                form["file_data"] = base64.b64encode(handle.read()).decode("utf-8")
        else:
            form["file_url"] = file_url

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        timeout = httpx.Timeout(180.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            submit_resp = await client.post(
                f"{BAIDU_SUBMIT_URL}?access_token={token}",
                headers=headers,
                data=form,
            )
            submit_resp.raise_for_status()
            submit_json = submit_resp.json()
            if submit_json.get("error_code"):
                raise RuntimeError(
                    submit_json.get("error_msg")
                    or f"Baidu PaddleOCR submit error {submit_json.get('error_code')}"
                )
            task_id = (submit_json.get("result") or {}).get("task_id")
            if not task_id:
                raise RuntimeError("Baidu PaddleOCR submit response missing task_id.")

            query_json = await self._poll_baidu_task(client, token, task_id, headers)
            markdown = ""
            markdown_url = query_json.get("markdown_url") or ""
            parse_result_url = query_json.get("parse_result_url") or ""
            pages: List[str] = []
            tables: List[str] = []
            page_count = 0

            if markdown_url:
                md_resp = await client.get(markdown_url)
                if md_resp.status_code == 200:
                    markdown = md_resp.text.strip()

            if parse_result_url:
                parse_resp = await client.get(parse_result_url)
                parse_resp.raise_for_status()
                parse_json = parse_resp.json()
                parsed_md, pages, tables, page_count = _extract_baidu_pages(parse_json)
                if not markdown:
                    markdown = parsed_md

        if max_pages and pages:
            pages = pages[: max(1, int(max_pages))]
            markdown = "\n\n".join(pages)
            page_count = len(pages)
        elif markdown and max_pages:
            split_pages = [p for p in markdown.split("\n\n") if p.strip()]
            pages = split_pages[: max(1, int(max_pages))]
            markdown = "\n\n".join(pages)
            page_count = len(pages)

        return {
            "backend": "baidu",
            "model": "PaddleOCR-VL",
            "file_name": file_name,
            "job_id": query_json.get("task_id"),
            "page_count": page_count or (markdown.count("\n\n") + 1 if markdown else 0),
            "markdown": markdown,
            "pages": pages,
            "tables": tables,
            "markdown_url": markdown_url,
            "parse_result_url": parse_result_url,
        }

    async def _get_baidu_access_token(self, api_key: str, secret_key: str) -> str:
        cache_key = f"{api_key}:{secret_key}"
        if cache_key in _baidu_token_cache:
            return _baidu_token_cache[cache_key]
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                BAIDU_TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": api_key,
                    "client_secret": secret_key,
                },
            )
            response.raise_for_status()
            payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(
                payload.get("error_description")
                or "Failed to obtain Baidu access_token. Check API Key / Secret Key."
            )
        _baidu_token_cache[cache_key] = token
        return token

    async def _poll_baidu_task(
        self,
        client: httpx.AsyncClient,
        token: str,
        task_id: str,
        headers: Dict[str, str],
        max_wait: float = 300.0,
    ) -> Dict[str, Any]:
        interval = 5.0
        elapsed = 0.0
        while elapsed < max_wait:
            response = await client.post(
                f"{BAIDU_QUERY_URL}?access_token={token}",
                headers=headers,
                data={"task_id": task_id},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error_code"):
                raise RuntimeError(
                    payload.get("error_msg")
                    or f"Baidu PaddleOCR query error {payload.get('error_code')}"
                )
            result = payload.get("result") or {}
            status = result.get("status")
            if status == "success":
                return result
            if status == "failed":
                raise RuntimeError(result.get("task_error") or "Baidu PaddleOCR task failed.")
            await asyncio.sleep(interval)
            elapsed += interval
        raise TimeoutError(f"Baidu PaddleOCR task timed out: {task_id}")


async def read_pdf_via_paddleocr(
    file_path: str = None,
    file_url: str = None,
    **kwargs,
) -> Optional[str]:
    """Convenience helper used by other tools (e.g. web PDF fallback)."""
    if not credentials_configured():
        return None
    reader = PaddleOCRDocumentReader()
    results = await reader.api_function(file_path=file_path, file_url=file_url, **kwargs)
    if not results or results[0].data is None:
        return None
    data = results[0].data
    if isinstance(data, dict):
        return data.get("markdown") or None
    return str(data) if data else None
