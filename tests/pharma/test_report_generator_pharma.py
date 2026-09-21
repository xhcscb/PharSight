"""Focused tests for the pharmaceutical report post-processing path."""

from types import SimpleNamespace

import pytest

from src.agents.report_generator.report_class import Report
from src.agents.report_generator.report_generator import ReportGenerator
from src.tools.base import ToolResult


class _MemoryStub:
    def __init__(self, evidence):
        self._evidence = evidence

    def get_collect_data(self):
        return self._evidence


def _generator(config, evidence=()):
    generator = ReportGenerator.__new__(ReportGenerator)
    generator.config = SimpleNamespace(config=config)
    generator.memory = _MemoryStub(list(evidence))
    generator.target_language_name = "Chinese (中文)"
    return generator


@pytest.mark.asyncio
async def test_strict_report_references_are_numbered_from_metadata():
    evidence = ToolResult(
        name="S001 交易所公告",
        description="受控证据",
        data={
            "source_id": "S001",
            "publisher": "示例交易所",
            "title": "示例公告",
            "published_at": "2026-09-01T08:00:00+08:00",
            "url": "https://example.invalid/source",
            "content": "示例内容",
        },
        source="https://example.invalid/source",
    )
    generator = _generator(
        {"strict_citations": True, "require_citations": True},
        [evidence],
    )
    report = Report("# 示例报告\n\n## 结论")
    report.sections[0].set_content("关键事实 [Source:S001]。")

    rendered = await generator._add_reference(report)

    assert "关键事实 [1]" in rendered.content
    assert "1. S001. 示例交易所：《示例公告》" in rendered.content


@pytest.mark.asyncio
async def test_pharma_cover_records_scope_without_financial_tables():
    generator = _generator(
        {
            "target_name": "示例医药公司",
            "stock_code": "000001.SZ",
            "as_of": "2026-09-21T18:00:00+08:00",
            "jurisdictions": ["CN"],
            "focus_assets": ["药物甲"],
            "focus_indications": ["适应症乙"],
        }
    )
    report = Report("# 示例报告\n\n## 投资摘要")

    rendered = await generator._add_cover_page(
        {"target_type": "pharma_company"}, report
    )

    assert "研究范围与截止时间" in rendered.content
    assert "000001.SZ" in rendered.content
    assert "药物甲" in rendered.content


@pytest.mark.asyncio
async def test_pharma_cover_defaults_to_full_pipeline_coverage():
    generator = _generator(
        {
            "target_name": "示例医药公司",
            "stock_code": "000001.SZ",
            "as_of": "2026-09-21T18:00:00+08:00",
            "jurisdictions": ["CN"],
        }
    )
    report = Report("# 示例报告\n\n## 投资摘要")

    rendered = await generator._add_cover_page(
        {"target_type": "pharma_company"}, report
    )

    assert "全部已上市产品与在研管线" in rendered.content
