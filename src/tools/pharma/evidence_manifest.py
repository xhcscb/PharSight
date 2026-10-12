"""Load a controlled pharmaceutical evidence pack into FinSight ToolResults.

The manifest is deliberately local and deterministic. Online connectors can be
added later behind the same ``Tool``/``ToolResult`` contract without changing
the agent orchestration.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from src.tools.base import Tool, ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
SUPPORTED_TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}


def _inside_project(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"Evidence path must stay inside Pharsight: {resolved}") from exc
    return resolved


def _resolve_manifest_path(manifest_path: str | Path) -> Path:
    path = Path(manifest_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = _inside_project(path)
    if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise ValueError("Evidence manifest must be JSON or YAML")
    if not path.is_file():
        raise ValueError(f"Evidence manifest not found: {path}")
    return path


def _parse_timestamp(value: Any, field_name: str, fallback_tz=None) -> datetime:
    if value is None or str(value).strip() == "":
        raise ValueError(f"Evidence field '{field_name}' is required")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Evidence field '{field_name}' must be ISO-8601: {value}") from exc
    if parsed.tzinfo is None and fallback_tz is not None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def _load_document(path: Path, max_chars: int) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8")[:max_chars]
    if suffix == ".pdf":
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - dependency is optional at runtime
            raise RuntimeError("Reading PDF evidence requires pdfplumber") from exc
        pages = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                pages.append(f"[Page {page_number}]\n{page.extract_text() or ''}")
                if sum(len(item) for item in pages) >= max_chars:
                    break
        return "\n\n".join(pages)[:max_chars]
    raise ValueError(
        f"Unsupported evidence document type '{suffix}'. "
        "Use PDF, Markdown, text, JSON, YAML or CSV."
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _index_pdf_document(
    path: Path,
    *,
    source_id: str,
    source_url: str,
    title: str,
    retrieved_at: str,
) -> tuple[str, dict[str, Any]]:
    """Extract every PDF page and persist a page-aware text index."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is optional at runtime
        raise RuntimeError("Reading PDF evidence requires pdfplumber") from exc

    from src.tools.document import build_document_index

    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    document_hash = _sha256_file(path)
    index = build_document_index(
        pages=pages,
        source_url=source_url,
        title=title,
        parser="pdfplumber",
        retrieved_at=retrieved_at,
        document_hash=document_hash,
        source_id=source_id,
    )
    return document_hash, index


def _load_manifest_data(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_text)
    else:
        payload = yaml.safe_load(raw_text)

    if isinstance(payload, list):
        return {}, payload
    if not isinstance(payload, dict):
        raise ValueError("Evidence manifest must contain an object or a source list")
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("Evidence manifest 'sources' must be a list")
    return payload, sources


def _normalize_source_ids(source_ids: Iterable[str] | None) -> set[str] | None:
    if source_ids is None:
        return None
    normalized = {str(source_id).strip().upper() for source_id in source_ids}
    return normalized or None


def load_evidence_manifest(
    manifest_path: str | Path,
    *,
    as_of: str | None = None,
    source_ids: Iterable[str] | None = None,
    strict_cutoff: bool = True,
    max_chars_per_source: int = 120_000,
) -> list[ToolResult]:
    """Load and validate an evidence manifest.

    Sources later than ``as_of`` are excluded when ``strict_cutoff`` is true.
    Every accepted record keeps its source metadata inside ``ToolResult.data``.
    """
    manifest = _resolve_manifest_path(manifest_path)
    metadata, sources = _load_manifest_data(manifest)
    effective_as_of = as_of or metadata.get("as_of")
    cutoff = _parse_timestamp(effective_as_of, "as_of") if effective_as_of else None
    selected_ids = _normalize_source_ids(source_ids)

    seen_ids: set[str] = set()
    results: list[ToolResult] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Every evidence source must be an object")

        source_id = str(source.get("source_id", "")).strip().upper()
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise ValueError(
                f"Invalid source_id '{source_id}'. Use a stable ID such as S001."
            )
        if source_id in seen_ids:
            raise ValueError(f"Duplicate source_id in evidence manifest: {source_id}")
        seen_ids.add(source_id)
        if selected_ids is not None and source_id not in selected_ids:
            continue

        required = ("source_type", "publisher", "title", "published_at")
        missing = [key for key in required if not source.get(key)]
        if missing:
            raise ValueError(f"{source_id} is missing fields: {', '.join(missing)}")

        published_at = _parse_timestamp(
            source["published_at"],
            "published_at",
            fallback_tz=cutoff.tzinfo if cutoff else None,
        )
        updated_at = None
        if source.get("updated_at"):
            updated_at = _parse_timestamp(
                source["updated_at"],
                "updated_at",
                fallback_tz=cutoff.tzinfo if cutoff else published_at.tzinfo,
            )
        cutoff_eligible = bool(source.get("cutoff_eligible", True))
        if cutoff and (
            published_at > cutoff or (updated_at is not None and updated_at > cutoff)
        ):
            cutoff_eligible = False
        if strict_cutoff and not cutoff_eligible:
            continue

        local_path = source.get("local_path")
        inline_content = source.get("content")
        resolved_local_path: Path | None = None
        document_index: dict[str, Any] | None = None
        retrieved_at = source.get("retrieved_at") or datetime.now().astimezone().isoformat()
        document_hash: str | None = None
        if local_path:
            candidate = Path(str(local_path))
            if not candidate.is_absolute():
                candidate = manifest.parent / candidate
            resolved_local_path = _inside_project(candidate)
            if not resolved_local_path.is_file():
                raise ValueError(f"Evidence file not found for {source_id}: {resolved_local_path}")
            if resolved_local_path.suffix.lower() == ".pdf":
                source_url = str(
                    source.get("url")
                    or resolved_local_path.relative_to(PROJECT_ROOT).as_posix()
                )
                document_hash, full_index = _index_pdf_document(
                    resolved_local_path,
                    source_id=source_id,
                    source_url=source_url,
                    title=str(source["title"]),
                    retrieved_at=retrieved_at,
                )
                document_index = {
                    key: value for key, value in full_index.items() if key != "chunks"
                }
                content = (
                    "完整 PDF 已使用 pdfplumber 按页解析并建立持久文本索引："
                    f"共 {full_index['page_count']} 页、{full_index['chunk_count']} 个文本块。"
                    f"文档哈希：{document_hash}。事实检索必须读取分页索引，不得把 PDF "
                    "或整本文本直接注入模型上下文。"
                )
            else:
                content = _load_document(resolved_local_path, max_chars_per_source)
        elif inline_content is not None:
            content = str(inline_content)[:max_chars_per_source]
        else:
            raise ValueError(
                f"{source_id} must provide 'local_path' or reviewed inline 'content'"
            )
        if not content.strip():
            raise ValueError(f"Evidence content is empty for {source_id}")

        if not any((source.get("url"), resolved_local_path, source.get("external_id"))):
            raise ValueError(
                f"{source_id} must provide 'url', 'local_path' or 'external_id'"
            )

        source_location = source.get("url") or source.get("external_id")
        if not source_location and resolved_local_path:
            source_location = resolved_local_path.relative_to(PROJECT_ROOT).as_posix()

        record = {
            "source_id": source_id,
            "source_type": str(source["source_type"]),
            "publisher": str(source["publisher"]),
            "title": str(source["title"]),
            "published_at": published_at.isoformat(),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "retrieved_at": retrieved_at,
            "reporting_period": source.get("reporting_period"),
            "url": source.get("url"),
            "external_id": source.get("external_id"),
            "local_path": (
                resolved_local_path.relative_to(PROJECT_ROOT).as_posix()
                if resolved_local_path
                else None
            ),
            "locator": source.get("locator"),
            "language": source.get("language", "zh-CN"),
            "reliability_tier": source.get("reliability_tier", "primary"),
            "evidence_label": source.get("evidence_label", "fact"),
            "cutoff_eligible": cutoff_eligible,
            "entity_keys": source.get("entity_keys", {}),
            "limitations": source.get("limitations", []),
            "notes": source.get("notes"),
            "document_hash": document_hash
            or hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }
        if document_index is not None:
            record["document_index"] = document_index
        description = (
            f"{record['source_type']} | {record['publisher']} | "
            f"published {record['published_at']} | locator {record['locator'] or 'N/A'}"
        )
        results.append(
            ToolResult(
                name=f"{source_id} {record['title']}",
                description=description,
                data=record,
                source=str(source_location or record["title"]),
            )
        )

    if selected_ids is not None:
        missing_ids = selected_ids - seen_ids
        if missing_ids:
            raise ValueError(
                "Requested source IDs not found: " + ", ".join(sorted(missing_ids))
            )
    if not results:
        raise ValueError("Evidence manifest contains no eligible sources for this run")
    return results


class PharmaEvidenceManifestTool(Tool):
    """Read an approved local evidence manifest with cutoff enforcement."""

    def __init__(self):
        super().__init__(
            name="pharma_evidence_manifest",
            description=(
                "读取经审核的医药投研资料清单，保留来源编号、发布日期、定位、"
                "证据标签及正文，并按研究截止时间过滤。"
            ),
            parameters=[
                {
                    "name": "manifest_path",
                    "type": "str",
                    "description": "Pharsight 仓库内的 YAML/JSON 资料清单路径",
                    "required": True,
                },
                {
                    "name": "as_of",
                    "type": "str",
                    "description": "带时区的 ISO-8601 研究截止时间",
                    "required": False,
                },
                {
                    "name": "source_ids",
                    "type": "list[str] | None",
                    "description": "可选来源编号白名单",
                    "required": False,
                },
            ],
        )

    async def api_function(
        self,
        manifest_path: str,
        as_of: str | None = None,
        source_ids: list[str] | None = None,
        strict_cutoff: bool = True,
    ) -> list[ToolResult]:
        return load_evidence_manifest(
            manifest_path,
            as_of=as_of,
            source_ids=source_ids,
            strict_cutoff=strict_cutoff,
        )
