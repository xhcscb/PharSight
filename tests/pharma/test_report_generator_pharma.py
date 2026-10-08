"""Focused tests for the pharmaceutical report post-processing path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, mock_open, patch

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
    report.abstract = "摘要事实 [来源: S001]。"

    rendered = await generator._add_reference(report)

    assert "关键事实 [1]" in rendered.content
    assert "摘要事实 [1]" in rendered.content
    assert "1. S001. 示例交易所：《示例公告》" in rendered.content


@pytest.mark.asyncio
async def test_reference_registry_prefers_canonical_parent_document_metadata():
    source_id = "WEB-56697731BB-B999420E"
    canonical = ToolResult(
        name=f"{source_id} 恒瑞医药2025年年度报告",
        description="完整 PDF 索引",
        data={
            "source_id": source_id,
            "source_type": "web_pdf",
            "publisher": "恒瑞医药",
            "title": "恒瑞医药2025年年度报告",
            "url": "https://example.invalid/annual-report.pdf",
            "document_hash": "a" * 64,
            "parser": "paddleocr:test",
            "page_count": 189,
            "retrieved_at": "2026-09-22T12:00:00+08:00",
            "content": "索引摘要",
        },
        source="https://example.invalid/annual-report.pdf",
    )
    task_alias = ToolResult(
        name=f"{source_id} PDF检索任务",
        description="任务命中",
        data={
            "source_id": source_id,
            "title": "恒瑞医药2025年报_PDF检索_任务证据",
            "content": "命中内容",
        },
        source="PDF evidence search",
    )
    generator = _generator(
        {"strict_citations": True, "require_citations": True},
        [canonical, task_alias],
    )
    report = Report("# 示例报告\n\n## 结论")
    report.sections[0].set_content(f"关键事实 [Source:{source_id}]。")

    rendered = await generator._add_reference(report)

    assert "恒瑞医药：《恒瑞医药2025年年度报告》" in rendered.content
    assert "PDF检索_任务证据" not in rendered.content


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
async def test_strict_report_ignores_inline_code_examples_and_numbers_real_citations():
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
    report = Report("# 示例报告\n\n## 方法与限制")
    report.sections[0].set_content("关键事实 [Source:S001]。格式示例：`[Source:Sxxx]`。")

    rendered = await generator._add_reference(report)

    assert "关键事实 [1]。" in rendered.content
    assert "`[Source:Sxxx]`" in rendered.content


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


@pytest.mark.asyncio
async def test_direct_outline_template_skips_agentic_expansion():
    generator = _generator(
        {
            "use_outline_template_directly": True,
            "outline_template_path": "controlled-outline.md",
        }
    )
    generator.logger = SimpleNamespace(info=lambda *args, **kwargs: None)

    with (
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data="# 报告\n\n## 第一章\n\n## 第二章")),
    ):
        report = await generator.generate_outline({"task": "测试"})

    assert [section.title for section in report.sections] == ["第一章", "第二章"]


def test_pharma_section_completion_guard_accepts_and_removes_marker():
    content = "## 章节\n\n" + "完整事实与限制。" * 30 + "\n<!-- SECTION_COMPLETE -->"

    cleaned = ReportGenerator._validated_pharma_section(content, stage="测试")

    assert "SECTION_COMPLETE" not in cleaned
    assert cleaned.endswith("。")


@pytest.mark.parametrize(
    "content",
    [
        "没有完成标记。" * 30,
        "<execute>print('still working')</execute>"
        + "说明。" * 80
        + "<!-- SECTION_COMPLETE -->",
        "内容，" * 80 + "<!-- SECTION_COMPLETE -->",
    ],
)
def test_pharma_section_completion_guard_rejects_incomplete_output(content):
    with pytest.raises(ValueError):
        ReportGenerator._validated_pharma_section(content, stage="测试")


@pytest.mark.asyncio
async def test_incomplete_polish_falls_back_to_validated_draft():
    generator = _generator({"target_type": "pharma_company"})
    generator.memory = SimpleNamespace(get_analysis_result=lambda: [])
    generator.FINAL_POLISH_PROMPT = "{draft_report}\n{reference_image}\n{target_language}"
    generator.llm = SimpleNamespace(
        generate=AsyncMock(return_value="未带完成标记的润色文本。" * 30)
    )
    generator.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    draft = "## 完整草稿\n\n" + "事实、限制与结论完整。" * 30

    result = await generator._final_polish({}, draft)

    assert result == draft
    assert generator.llm.generate.await_count == 2


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# 模型自拟标题\n\n正文。", "## 模板标题\n\n正文。"),
        ("没有标题的正文。", "## 模板标题\n\n没有标题的正文。"),
    ],
)
def test_pharma_component_heading_is_normalized_to_template_h2(content, expected):
    assert ReportGenerator._normalize_markdown_heading(content, "模板标题") == expected
