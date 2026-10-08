"""Persistent page-aware chunk index for parsed research documents.

The first implementation intentionally uses deterministic lexical retrieval.
It has no model or vector-database dependency, works offline, and keeps the
evidence contract stable for a later vector/graph backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from ..base import Tool, ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT_INDEX_DIR = PROJECT_ROOT / "outputs" / ".document_cache" / "indexes"
INDEX_SCHEMA_VERSION = 1
DEFAULT_CHUNK_CHARS = 1800
DEFAULT_CHUNK_OVERLAP = 240

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.+-]*|\d+(?:\.\d+)?%?|[\u3400-\u9fff]+")
_TITLE_RE = re.compile(r"^#{1,6}\s+(.+)$")
_QUERY_EXPANSIONS = {
    "研发": ("研究开发", "研发投入", "研发费用", "开发支出", "临床"),
    "管线": ("在研产品", "临床阶段", "适应症", "获批", "申报"),
    "产品": ("商业化", "销售", "收入", "获批", "适应症"),
    "财务": ("营业收入", "净利润", "现金流", "毛利率", "研发投入"),
    "估值": ("市盈率", "现金流", "预测", "收入", "利润"),
    "风险": ("风险因素", "不确定性", "竞争", "减值", "监管"),
    "医保": ("国家医保目录", "集采", "支付", "准入", "价格"),
    "治理": ("董事会", "股东", "内部控制", "关联交易", "审计"),
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(value.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", match):
            if len(match) <= 4:
                tokens.append(match)
            for width in (2, 3, 4):
                tokens.extend(match[index : index + width] for index in range(len(match) - width + 1))
        else:
            tokens.append(match)
    return tokens


def _expanded_query(query: str) -> str:
    additions: list[str] = []
    for marker, synonyms in _QUERY_EXPANSIONS.items():
        if marker in query:
            additions.extend(synonyms)
    return " ".join([query, *additions])


def _split_page(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + target_chars)
        end = hard_end
        if hard_end < len(text):
            floor = start + max(target_chars // 2, 1)
            candidates = [
                text.rfind(separator, floor, hard_end)
                for separator in ("\n\n", "\n", "。", "；", ". ")
            ]
            boundary = max(candidates)
            if boundary > floor:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _page_title(page_text: str) -> str | None:
    for line in str(page_text).splitlines()[:30]:
        match = _TITLE_RE.match(line.strip())
        if match:
            return match.group(1).strip()[:160]
    return None


def build_document_index(
    *,
    pages: Iterable[str],
    source_url: str,
    title: str,
    parser: str,
    retrieved_at: str,
    document_hash: str | None = None,
    source_id: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
    index_dir: Path | None = None,
) -> dict[str, Any]:
    """Build or reuse a complete page-aware index and return its manifest."""
    page_list = [str(page or "").strip() for page in pages]
    if not page_list or not any(page_list):
        raise ValueError("Cannot index a document without parsed pages")
    if chunk_chars < 400:
        raise ValueError("chunk_chars must be at least 400")
    if overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than chunk_chars")

    full_text = "\n\n".join(page_list)
    stable_hash = document_hash or _sha256_text(full_text)
    directory = index_dir or DOCUMENT_INDEX_DIR
    path = directory / f"{stable_hash}.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                existing.get("schema_version") == INDEX_SCHEMA_VERSION
                and existing.get("document_hash") == stable_hash
                and existing.get("chunk_chars") == chunk_chars
                and existing.get("overlap_chars") == overlap_chars
            ):
                return existing
        except (OSError, json.JSONDecodeError):
            pass

    chunks: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(page_list, start=1):
        section_title = _page_title(page_text)
        for chunk_number, chunk_text in enumerate(
            _split_page(page_text, chunk_chars, overlap_chars), start=1
        ):
            chunks.append(
                {
                    "chunk_id": f"{stable_hash[:12]}:p{page_number:04d}:c{chunk_number:03d}",
                    "page_start": page_number,
                    "page_end": page_number,
                    "section_title": section_title,
                    "content_hash": _sha256_text(chunk_text),
                    "text": chunk_text,
                }
            )

    parsed_host = urlparse(source_url).netloc.lower()
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "document_hash": stable_hash,
        "source_id": source_id,
        "source_url": source_url,
        "publisher": parsed_host or "本地授权文档",
        "title": title or source_url,
        "parser": parser,
        "retrieved_at": retrieved_at,
        "page_count": len(page_list),
        "chunk_count": len(chunks),
        "chunk_chars": chunk_chars,
        "overlap_chars": overlap_chars,
        "created_at": datetime.now().astimezone().isoformat(),
        "chunks": chunks,
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return index


def _load_indexes(
    *,
    document_hash: str | None = None,
    source_id: str | None = None,
    index_dir: Path | None = None,
) -> list[dict[str, Any]]:
    directory = index_dir or DOCUMENT_INDEX_DIR
    if not directory.exists():
        return []
    candidates = (
        [directory / f"{document_hash}.json"]
        if document_hash
        else sorted(directory.glob("*.json"))
    )
    indexes: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if index.get("schema_version") != INDEX_SCHEMA_VERSION:
            continue
        if source_id and str(index.get("source_id", "")).upper() != source_id.upper():
            continue
        indexes.append(index)
    return indexes


def search_document_indexes(
    query: str,
    *,
    document_hash: str | None = None,
    source_id: str | None = None,
    top_k: int = 8,
    index_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Rank chunks with deterministic BM25 and exact-phrase bonuses."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    top_k = max(1, min(int(top_k), 30))
    indexes = _load_indexes(document_hash=document_hash, source_id=source_id, index_dir=index_dir)
    corpus: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = []
    document_frequency: Counter[str] = Counter()
    for index in indexes:
        for chunk in index.get("chunks") or []:
            tokens = _tokenize(str(chunk.get("text", "")))
            if not tokens:
                continue
            corpus.append((index, chunk, tokens))
            document_frequency.update(set(tokens))
    if not corpus:
        return []

    query_tokens = _tokenize(_expanded_query(query))
    query_counts = Counter(query_tokens)
    average_length = sum(len(item[2]) for item in corpus) / len(corpus)
    scored: list[dict[str, Any]] = []
    corpus_size = len(corpus)
    for index, chunk, tokens in corpus:
        frequencies = Counter(tokens)
        score = 0.0
        for token, query_weight in query_counts.items():
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(
                1 + (corpus_size - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / average_length)
            score += query_weight * inverse_frequency * (frequency * 2.5 / denominator)
        compact_query = re.sub(r"\s+", "", query.lower())
        compact_text = re.sub(r"\s+", "", str(chunk.get("text", "")).lower())
        if len(compact_query) >= 2 and compact_query in compact_text:
            score += 8.0
        if score <= 0:
            continue
        scored.append(
            {
                "score": round(score, 6),
                "source_id": index.get("source_id"),
                "document_hash": index.get("document_hash"),
                "source_url": index.get("source_url"),
                "publisher": index.get("publisher"),
                "title": index.get("title"),
                "parser": index.get("parser"),
                "retrieved_at": index.get("retrieved_at"),
                "page_count": index.get("page_count"),
                **chunk,
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["page_start"], item["chunk_id"]))

    selected: list[dict[str, Any]] = []
    per_page: defaultdict[tuple[str, int], int] = defaultdict(int)
    for hit in scored:
        page_key = (str(hit.get("document_hash")), int(hit.get("page_start") or 0))
        if per_page[page_key] >= 2:
            continue
        selected.append(hit)
        per_page[page_key] += 1
        if len(selected) >= top_k:
            break
    return selected


class PDFEvidenceSearchTool(Tool):
    """Retrieve page-located chunks from complete parsed PDF indexes."""

    def __init__(self):
        super().__init__(
            name="PDF evidence search",
            description=(
                "Search complete PDFs previously parsed by PaddleOCR. Returns task-relevant "
                "chunks with exact page numbers, chunk IDs, source IDs, and document hashes."
            ),
            parameters=[
                {
                    "name": "query",
                    "type": "str",
                    "description": "Specific research question or evidence need.",
                    "required": True,
                },
                {
                    "name": "source_id",
                    "type": "str",
                    "description": "Optional exact WEB-* source ID to restrict the search.",
                    "required": False,
                },
                {
                    "name": "document_hash",
                    "type": "str",
                    "description": "Optional exact document hash to restrict the search.",
                    "required": False,
                },
                {
                    "name": "top_k",
                    "type": "int",
                    "description": "Number of chunks to return, from 1 to 30 (default 8).",
                    "required": False,
                },
            ],
        )
        self.type = "tool_pdf_evidence_search"

    async def api_function(
        self,
        query: str,
        source_id: str = None,
        document_hash: str = None,
        top_k: int = 8,
    ) -> list[ToolResult]:
        hits = search_document_indexes(
            query,
            source_id=(source_id or "").strip() or None,
            document_hash=(document_hash or "").strip() or None,
            top_k=top_k,
        )
        if not hits:
            return [
                ToolResult(
                    name=self.name,
                    description="No indexed PDF chunks matched the query.",
                    data={"query": query, "hits": [], "content": ""},
                    source="local parsed-document index",
                )
            ]

        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for hit in hits:
            grouped[str(hit.get("source_id") or hit["document_hash"])].append(hit)

        results: list[ToolResult] = []
        for group_hits in grouped.values():
            first = group_hits[0]
            locators = [
                f"第{hit['page_start']}页/{hit['chunk_id']}" for hit in group_hits
            ]
            content = "\n\n".join(
                f"[第{hit['page_start']}页｜{hit['chunk_id']}]\n{hit['text']}"
                for hit in group_hits
            )
            record = {
                "source_id": first.get("source_id"),
                "source_type": "web_pdf",
                "publisher": first.get("publisher"),
                "title": first.get("title"),
                "published_at": None,
                "retrieved_at": first.get("retrieved_at"),
                "url": first.get("source_url"),
                "final_url": first.get("source_url"),
                "locator": "；".join(locators),
                "reliability_tier": "secondary_web",
                "evidence_label": "sourced_fact",
                "cutoff_eligible": True,
                "limitations": ["检索结果来自 OCR 解析文本，应回看对应 PDF 页核验关键表格和药品名称。"],
                "document_hash": first.get("document_hash"),
                "parser": first.get("parser"),
                "page_count": first.get("page_count"),
                "query": query,
                "retrieval_hits": group_hits,
                "content": content,
            }
            results.append(
                ToolResult(
                    name=f"{first.get('source_id') or first['document_hash']} {first.get('title')}",
                    description=f"PDF task retrieval | query={query} | {len(group_hits)} chunks",
                    data=record,
                    source=first.get("source_url") or "local parsed-document index",
                )
            )
        return results

