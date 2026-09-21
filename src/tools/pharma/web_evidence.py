"""Normalize browsed web pages into citable pharmaceutical evidence."""

from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import urlparse

from src.tools.base import ToolResult


def _parse_timestamp(value: str | None, fallback_tz=None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).strip().replace("Z", "+00:00")
    for pattern in (None, "%Y-%m-%d", "%Y/%m/%d"):
        try:
            if pattern is None:
                parsed = datetime.fromisoformat(normalized)
            else:
                parsed = datetime.strptime(normalized, pattern)
            if parsed.tzinfo is None and fallback_tz is not None:
                parsed = parsed.replace(tzinfo=fallback_tz)
            return parsed
        except ValueError:
            continue
    return None


def build_web_evidence(
    *,
    url: str,
    title: str,
    content: str,
    as_of: str,
    published_at: str | None = None,
    retrieved_at: str | None = None,
) -> ToolResult | None:
    """Build one stable evidence snapshot from a successfully browsed page.

    A live page is eligible only when it was observed no later than the report
    cutoff. If the provider exposes a parseable publication date, that date is
    checked as an additional cutoff constraint. Unparseable publication labels
    are preserved as raw metadata rather than guessed.
    """
    if not url or not content.strip():
        raise ValueError("Web evidence requires a URL and non-empty browsed content")

    cutoff = _parse_timestamp(as_of)
    if cutoff is None or cutoff.tzinfo is None:
        raise ValueError("Web evidence requires an ISO-8601 as_of timestamp with timezone")
    retrieved = _parse_timestamp(retrieved_at) if retrieved_at else datetime.now().astimezone()
    if retrieved is None:
        raise ValueError("retrieved_at must be an ISO-8601 timestamp")
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=cutoff.tzinfo)
    published = _parse_timestamp(published_at, fallback_tz=cutoff.tzinfo)

    cutoff_eligible = retrieved <= cutoff and (
        published is None or published <= cutoff
    )
    if not cutoff_eligible:
        return None

    normalized_url = url.strip()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    url_hash = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    source_id = f"WEB-{url_hash[:10].upper()}-{content_hash[:8].upper()}"
    publisher = urlparse(normalized_url).netloc.lower() or "未知网页发布者"
    limitations = [
        "网页内容为运行时抓取快照，应优先与监管、交易所或发行人一手材料交叉核验。"
    ]
    if published is None:
        limitations.append("网页发布日期未能结构化确认；仅确认该内容在获取时间已可访问。")

    record = {
        "source_id": source_id,
        "source_type": "web_page",
        "publisher": publisher,
        "title": title.strip() or normalized_url,
        "published_at": published.isoformat() if published else None,
        "published_at_raw": published_at,
        "updated_at": None,
        "retrieved_at": retrieved.isoformat(),
        "reporting_period": None,
        "url": normalized_url,
        "external_id": None,
        "local_path": None,
        "locator": "网页正文抓取快照",
        "language": None,
        "reliability_tier": "secondary_web",
        "evidence_label": "sourced_fact",
        "cutoff_eligible": True,
        "entity_keys": {},
        "limitations": limitations,
        "notes": "由 Serper 发现并在 Deep Search 中打开页面后自动规范化。",
        "document_hash": content_hash,
        "content": content,
    }
    return ToolResult(
        name=f"{source_id} {record['title']}",
        description=(
            f"web_page | {publisher} | retrieved {record['retrieved_at']} | "
            "locator 网页正文抓取快照"
        ),
        data=record,
        source=normalized_url,
    )
