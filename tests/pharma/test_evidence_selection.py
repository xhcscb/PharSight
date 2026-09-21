"""Tests for task-level evidence selection in analysis and report prompts."""

from types import SimpleNamespace

import pytest

from src.agents.report_generator.report_generator import ReportGenerator
from src.tools.base import ToolResult
from src.utils.evidence_selection import (
    get_selection_mode,
    select_analyses_by_task,
    select_evidence_by_task,
    should_select_evidence,
)


class _MemoryStub:
    def __init__(
        self,
        evidence=(),
        analyses=(),
        select_evidence_returns=None,
        select_analysis_returns=None,
        fail_selection=False,
    ):
        self._evidence = list(evidence)
        self._analyses = list(analyses)
        self._select_evidence_returns = select_evidence_returns
        self._select_analysis_returns = select_analysis_returns
        self._fail_selection = fail_selection
        self.data_calls = 0
        self.analysis_calls = 0
        self.last_max_k = None

    def get_collect_data(self, exclude_type=None):
        return list(self._evidence)

    def get_analysis_result(self):
        return list(self._analyses)

    async def select_data_by_llm(self, query, max_k=-1, model_name="model"):
        self.data_calls += 1
        self.last_max_k = max_k
        if self._fail_selection:
            raise RuntimeError("selection unavailable")
        source = self._evidence if self._select_evidence_returns is None else self._select_evidence_returns
        return list(source), ""

    async def select_analysis_result_by_llm(self, query, max_k=-1, model_name="model"):
        self.analysis_calls += 1
        if self._fail_selection:
            raise RuntimeError("selection unavailable")
        source = self._analyses if self._select_analysis_returns is None else self._select_analysis_returns
        return list(source)


def _evidence(source_id: str) -> ToolResult:
    return ToolResult(
        name=f"{source_id} 来源",
        description="受控证据",
        data={
            "source_id": source_id,
            "publisher": "示例发布者",
            "title": f"{source_id} 标题",
            "published_at": "2026-09-01T08:00:00+08:00",
            "content": f"{source_id} 正文",
        },
        source="https://example.invalid/source",
    )


def _analysis(title: str) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        brief_str=lambda: f"Report Title: {title}\nReport Content: {title} 正文\n\n",
    )


def _config(**overrides):
    values = {
        "evidence_selection": "llm",
        "evidence_selection_threshold": 2,
        "evidence_selection_max_k": 10,
    }
    values.update(overrides)
    return SimpleNamespace(config=values)


def test_selection_mode_defaults_to_auto_on_unknown_value():
    assert get_selection_mode(_config(evidence_selection="bogus")) == "auto"
    assert should_select_evidence(_config(evidence_selection="off"), 100) is False
    assert should_select_evidence(_config(evidence_selection="auto", evidence_selection_threshold=2), 2) is False
    assert should_select_evidence(_config(evidence_selection="auto", evidence_selection_threshold=2), 3) is True


@pytest.mark.asyncio
async def test_off_mode_returns_all_without_calling_memory():
    memory = _MemoryStub(evidence=[_evidence("S001")])
    selected = await select_evidence_by_task(
        memory, "任务", memory._evidence, model_name="model", config=_config(evidence_selection="off")
    )
    assert selected == memory._evidence
    assert memory.data_calls == 0


@pytest.mark.asyncio
async def test_above_threshold_selection_keeps_catalog_order_and_limit():
    items = [_evidence("S001"), _evidence("S002"), _evidence("S003")]
    memory = _MemoryStub(evidence=items, select_evidence_returns=[items[1]])
    selected = await select_evidence_by_task(
        memory, "只看 S002", items, model_name="model", config=_config(evidence_selection_max_k=1)
    )
    assert [item.source_id for item in selected] == ["S002"]
    assert memory.last_max_k == 1


@pytest.mark.asyncio
async def test_selection_failure_falls_back_to_all_sources():
    items = [_evidence("S001"), _evidence("S002")]
    memory = _MemoryStub(evidence=items, fail_selection=True)
    selected = await select_evidence_by_task(
        memory, "任务", items, model_name="model", config=_config()
    )
    assert selected == items


@pytest.mark.asyncio
async def test_analysis_selection_returns_subset():
    analyses = [_analysis("分析甲"), _analysis("分析乙")]
    memory = _MemoryStub(analyses=analyses, select_analysis_returns=[analyses[0]])
    selected = await select_analyses_by_task(
        memory, "章节", analyses, model_name="model", config=_config()
    )
    assert [item.title for item in selected] == ["分析甲"]


@pytest.mark.asyncio
async def test_analyzer_prompt_keeps_original_ids_and_reports_gaps():
    pytest.importorskip("json_repair")
    from src.agents.data_analyzer.data_analyzer import DataAnalyzer

    items = [_evidence("S001"), _evidence("S002")]
    memory = _MemoryStub(evidence=items, select_evidence_returns=[items[1]])
    analyzer = DataAnalyzer.__new__(DataAnalyzer)
    analyzer.memory = memory
    analyzer.config = _config()
    analyzer.use_llm_name = "model"

    formatted = await analyzer._format_collect_data("分析 S002", items)

    assert "Data (id:1):" in formatted
    assert "Data (id:0):" not in formatted
    assert "从 2 条证据中选取 1 条" in formatted


@pytest.mark.asyncio
async def test_generator_section_prompt_filters_data_and_analyses():
    evidence = [_evidence("S001"), _evidence("S002")]
    analyses = [_analysis("分析甲"), _analysis("分析乙")]
    memory = _MemoryStub(
        evidence=evidence,
        analyses=analyses,
        select_evidence_returns=[evidence[0]],
        select_analysis_returns=[analyses[0]],
    )
    generator = ReportGenerator.__new__(ReportGenerator)
    generator.config = _config()
    generator.memory = memory
    generator.enable_chart = False
    generator.target_language_name = "Chinese (中文)"
    generator.use_llm_name = "model"
    generator.prompt_loader = SimpleNamespace(get_prompt=lambda key: "DATA_API")
    generator.SECTION_WRITING_WO_CHART_PROMPT = (
        "theme={report_theme} section={section_description} api={data_api} "
        "data={data_info} it={max_iterations} lang={target_language}"
    )

    messages = await generator._prepare_init_prompt(
        {"task": "示例公司", "section_outline": "## 商业化产品", "max_iterations": 3}
    )

    content = messages[0]["content"]
    assert "Data ID 0" in content and "Data ID 1" not in content
    assert "Analysis Report ID 0" in content and "Analysis Report ID 1" not in content
    assert "按章节相关性筛选后" in content
