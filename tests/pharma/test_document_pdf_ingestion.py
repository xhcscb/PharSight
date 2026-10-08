from __future__ import annotations

import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.data_collector.data_collector import DataCollector
from src.tools import get_tool_by_name, get_tool_categories
from src.tools.base import ToolResult
from src.tools.document import document_index, paddleocr_reader
from src.tools.document.document_index import (
    PDFEvidenceSearchTool,
    build_document_index,
    search_document_indexes,
)
from src.tools.document.paddleocr_reader import (
    PaddleOCRDocumentReader,
    credentials_configured,
    resolve_credentials,
)
from src.tools.document.pdf_ingestion import (
    _validate_public_http_url,
    classify_pdf_document,
    is_probable_pdf_url,
)
from src.tools.pharma.web_evidence import build_web_evidence


def test_credentials_are_environment_only(monkeypatch):
    for key in (
        "PADDLEOCR_ACCESS_TOKEN",
        "PADDLEOCR_API_KEY",
        "PADDLEOCR_SECRET_KEY",
        "PADDLEOCR_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert credentials_configured(resolve_credentials()) is False
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "test-token")
    assert resolve_credentials()["access_token"] == "test-token"


@pytest.mark.asyncio
async def test_reader_fails_closed_without_credentials(monkeypatch):
    for key in ("PADDLEOCR_ACCESS_TOKEN", "PADDLEOCR_API_KEY", "PADDLEOCR_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)

    result = await PaddleOCRDocumentReader().api_function(file_url="https://example.com/report.pdf")

    assert result[0].data is None
    assert ".env" in result[0].description


def test_search_result_pdf_detection_supports_query_and_metadata():
    assert is_probable_pdf_url("https://example.com/download?file=trial.pdf")
    assert is_probable_pdf_url("https://example.com/download/123", {"title": "招股书（PDF）"})
    assert not is_probable_pdf_url("https://example.com/article", {"title": "普通网页"})


def test_pdf_document_identity_distinguishes_annual_report_and_presentation():
    annual = classify_pdf_document("江苏恒瑞医药股份有限公司 2024年年度报告")
    presentation = classify_pdf_document("江苏恒瑞医药股份有限公司 2024年全年业绩推介材料")

    assert annual["document_type"] == "annual_report"
    assert annual["reporting_period"] == "2024"
    assert presentation["document_type"] == "earnings_presentation"


def test_private_pdf_hosts_are_rejected_before_download():
    with pytest.raises(ValueError, match="Private"):
        _validate_public_http_url("http://127.0.0.1/report.pdf")
    with pytest.raises(ValueError, match="Private"):
        _validate_public_http_url("http://localhost/report.pdf")


def test_document_reader_is_registered_in_document_category():
    assert get_tool_by_name("PaddleOCR PDF reader") is PaddleOCRDocumentReader
    assert "PaddleOCR PDF reader" in get_tool_categories()["document"]
    assert get_tool_by_name("PDF evidence search") is PDFEvidenceSearchTool
    assert "PDF evidence search" in get_tool_categories()["document"]


def _isolated_index_dir():
    return (
        document_index.PROJECT_ROOT
        / "outputs"
        / ".document_cache"
        / "pytest"
        / uuid4().hex
    )


def test_complete_document_index_preserves_pages_and_retrieves_task_chunks():
    index_dir = _isolated_index_dir()
    pages = [
        "# 公司概览\n公司主营创新药研发与商业化。",
        "# 研发投入\n本期研发费用为 50 亿元，开发支出为 3 亿元。",
        "# 管线\n药物 A 针对适应症 B 已进入 III 期临床试验。",
    ]
    try:
        index = build_document_index(
            pages=pages,
            source_url="https://example.com/report.pdf",
            title="示例公司年度报告",
            parser="paddleocr:official:PaddleOCR-VL",
            retrieved_at="2026-09-22T12:00:00+08:00",
            source_id="WEB-EXAMPLE-12345678",
            chunk_chars=400,
            overlap_chars=40,
            index_dir=index_dir,
        )

        assert index["page_count"] == 3
        assert index["chunk_count"] == 3
        hits = search_document_indexes("研发投入和开发支出", top_k=2, index_dir=index_dir)
        assert hits[0]["page_start"] == 2
        assert "50 亿元" in hits[0]["text"]
        assert hits[0]["chunk_id"].endswith(":p0002:c001")
    finally:
        shutil.rmtree(index_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_pdf_evidence_search_returns_page_located_evidence(monkeypatch):
    index_dir = _isolated_index_dir()
    monkeypatch.setattr(document_index, "DOCUMENT_INDEX_DIR", index_dir)
    try:
        build_document_index(
            pages=["概览", "风险因素包括临床失败和医保降价。"],
            source_url="https://example.com/report.pdf",
            title="示例年报",
            parser="test",
            retrieved_at="2026-09-22T12:00:00+08:00",
            source_id="WEB-EXAMPLE-12345678",
            index_dir=index_dir,
        )

        results = await PDFEvidenceSearchTool().api_function(query="风险因素", top_k=3)

        assert results[0].source_id == "WEB-EXAMPLE-12345678"
        assert results[0].data["retrieval_hits"][0]["page_start"] == 2
        assert "第2页" in results[0].data["content"]
    finally:
        shutil.rmtree(index_dir, ignore_errors=True)


def test_pdf_evidence_preserves_file_provenance():
    evidence = build_web_evidence(
        url="https://example.com/report.pdf",
        title="临床试验报告",
        content="[Page 1]\n主要终点达到。",
        as_of="2026-09-21T23:59:59+08:00",
        retrieved_at="2026-09-21T12:00:00+08:00",
        source_type="web_pdf",
        parser="paddleocr:official:PaddleOCR-VL",
        document_hash="a" * 64,
        page_count=12,
        final_url="https://cdn.example.com/report.pdf",
        byte_size=2048,
    )

    assert evidence is not None
    assert evidence.data["source_type"] == "web_pdf"
    assert evidence.data["document_hash"] == "a" * 64
    assert evidence.data["page_count"] == 12
    assert evidence.data["parser"].startswith("paddleocr:")
    assert evidence.data["final_url"] == "https://cdn.example.com/report.pdf"
    assert evidence.data["byte_size"] == 2048
    assert "PDF" in evidence.data["locator"]


@pytest.mark.asyncio
async def test_paddle_reader_reuses_complete_parse_across_page_limits(
    monkeypatch,
):
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "test-token")
    cache_dir = (
        paddleocr_reader.PROJECT_ROOT
        / "outputs"
        / ".document_cache"
        / "pytest"
        / uuid4().hex
    )
    monkeypatch.setattr(paddleocr_reader, "DOCUMENT_CACHE_DIR", cache_dir)
    monkeypatch.setattr(document_index, "DOCUMENT_INDEX_DIR", cache_dir / "indexes")
    calls = []

    async def fake_parse(self, **kwargs):
        calls.append(kwargs)
        return {
            "backend": "official",
            "model": "PaddleOCR-VL",
            "file_name": "report.pdf",
            "job_id": "job-1",
            "page_count": 3,
            "markdown": "p1\n\np2\n\np3",
            "pages": ["p1", "p2", "p3"],
            "tables": [],
        }

    monkeypatch.setattr(PaddleOCRDocumentReader, "_parse_official", fake_parse)
    url = "https://example.com/report.pdf"

    first = await PaddleOCRDocumentReader().api_function(file_url=url, max_pages=1)
    second = await PaddleOCRDocumentReader().api_function(file_url=url, max_pages=2)

    assert len(calls) == 1
    assert calls[0]["max_pages"] is None
    assert first[0].data["pages"] == ["p1"]
    assert second[0].data["pages"] == ["p1", "p2"]
    assert second[0].data["index"]["page_count"] == 3
    assert second[0].data["index"]["chunk_count"] == 3


def test_collector_normalizes_remote_paddle_result_as_web_pdf(monkeypatch):
    payload = {
        "file_name": "report.pdf",
        "markdown": "[Page 1]\n研发投入为示例内容。",
        "retrieved_at": "2026-09-22T12:00:00+08:00",
        "parser": "paddleocr:official:PaddleOCR-VL",
        "page_count": 1,
        "source_url": "https://example.com/report.pdf",
    }
    raw = [payload]
    monkeypatch.setattr(BaseAgent, "_agent_tool_function", lambda *args, **kwargs: raw)

    class MemoryStub:
        def __init__(self):
            self.items = []

        def get_collect_data(self):
            return self.items

        def add_data(self, item):
            self.items.append(item)

    collector = object.__new__(DataCollector)
    collector.config = SimpleNamespace(
        config={"as_of": "2026-09-22T23:59:59+08:00"}
    )
    collector.memory = MemoryStub()
    collector.code_executor = SimpleNamespace(set_variable=lambda *args: None)

    result = collector._agent_tool_function(
        tool_name="PaddleOCR PDF reader",
        file_url="https://example.com/report.pdf",
    )

    assert result[0]["source_id"].startswith("WEB-")
    assert result[0]["source_type"] == "web_pdf"
    assert collector.memory.items[0].source_id == result[0]["source_id"]


def test_collector_merges_task_retrieval_hits_by_chunk_id():
    source_id = "WEB-EXAMPLE-12345678"

    class MemoryStub:
        def __init__(self):
            self.items = [
                ToolResult(
                    name=f"{source_id} 示例年报",
                    description="完整文档索引",
                    data={
                        "source_id": source_id,
                        "title": "示例年报",
                        "content": "索引已建立",
                    },
                    source="https://example.com/report.pdf",
                )
            ]

        def get_collect_data(self):
            return self.items

        def add_data(self, item):
            self.items.append(item)

    variables = {}
    collector = object.__new__(DataCollector)
    collector.memory = MemoryStub()
    collector.code_executor = SimpleNamespace(
        set_variable=lambda key, value: variables.__setitem__(key, value)
    )
    hit = {
        "chunk_id": "doc:p0002:c001",
        "page_start": 2,
        "page_end": 2,
        "text": "研发投入为 50 亿元。",
    }
    response = [
        {
            "source_id": source_id,
            "title": "任务证据包临时标题",
            "url": "https://example.com/report.pdf",
            "query": "研发投入",
            "content": "任务证据",
            "retrieval_hits": [hit],
        }
    ]

    collector._merge_pdf_retrieval_response(response)
    collector._merge_pdf_retrieval_response(response)

    evidence = collector.memory.items[0].data
    assert evidence["title"] == "示例年报"
    assert evidence["retrieval_queries"] == ["研发投入"]
    assert evidence["retrieval_hits"] == [hit]
    assert "第2页｜doc:p0002:c001" in evidence["content"]
    assert variables["approved_evidence"][0]["retrieval_hits"] == [hit]
