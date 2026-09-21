"""Tests for fail-closed Serper web-evidence normalization."""

import pytest

from src.tools.pharma import build_web_evidence
from src.tools.web.search_engine_requests import SerperSearch


def test_browsed_page_becomes_citable_evidence_before_cutoff():
    evidence = build_web_evidence(
        url="https://example.invalid/filing",
        title="示例披露",
        content="经过打开页面取得的正文。",
        as_of="2026-09-21T23:59:59+08:00",
        published_at="2026-09-20",
        retrieved_at="2026-09-21T20:00:00+08:00",
    )

    assert evidence is not None
    assert evidence.source_id.startswith("WEB-")
    assert evidence.data["published_at"] == "2026-09-20T00:00:00+08:00"
    assert evidence.data["cutoff_eligible"] is True
    assert evidence.data["url"] == "https://example.invalid/filing"


def test_web_source_id_changes_when_snapshot_content_changes():
    common = {
        "url": "https://example.invalid/page",
        "title": "示例网页",
        "as_of": "2026-09-21T23:59:59+08:00",
        "retrieved_at": "2026-09-21T20:00:00+08:00",
    }
    first = build_web_evidence(content="版本一", **common)
    second = build_web_evidence(content="版本二", **common)

    assert first is not None and second is not None
    assert first.source_id != second.source_id


def test_live_page_after_cutoff_is_not_citable():
    evidence = build_web_evidence(
        url="https://example.invalid/page",
        title="示例网页",
        content="正文",
        as_of="2026-09-20T23:59:59+08:00",
        retrieved_at="2026-09-21T20:00:00+08:00",
    )
    assert evidence is None


@pytest.mark.asyncio
async def test_serper_fails_before_network_when_key_is_missing(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    search = SerperSearch()
    with pytest.raises(RuntimeError, match="SERPER_API_KEY is required"):
        await search.api_function("测试查询")
