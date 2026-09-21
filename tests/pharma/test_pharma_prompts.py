"""Regression tests for pharmaceutical mode prompt routing."""

import string

import pytest

from src.utils.prompt_loader import get_prompt_loader

REQUIRED_KEYS = {
    "memory": {"generate_collect_task", "generate_task", "select_data", "select_analysis"},
    "data_collector": {"data_collect"},
    "data_analyzer": {
        "data_analysis",
        "data_analysis_wo_chart",
        "data_api",
        "report_draft",
        "report_draft_wo_chart",
        "draw_chart",
        "vlm_critique",
    },
    "report_generator": {
        "section_writing",
        "section_writing_wo_chart",
        "final_polish",
        "data_api_outline",
        "data_api",
        "outline_draft",
        "outline_critique",
        "outline_refinement",
        "abstract",
        "title_generation",
        "table_beautify",
    },
}


@pytest.mark.parametrize("target_type", ["pharma_company", "pharma_industry"])
@pytest.mark.parametrize("module_name", sorted(REQUIRED_KEYS))
def test_pharma_mode_has_complete_prompt_set(target_type, module_name):
    loader = get_prompt_loader(module_name, report_type=target_type)
    prompts = loader.get_all_prompts()
    assert REQUIRED_KEYS[module_name] <= set(prompts)
    assert all(isinstance(prompts[key], str) and prompts[key].strip() for key in REQUIRED_KEYS[module_name])


@pytest.mark.parametrize("target_type", ["pharma_company", "pharma_industry"])
def test_prompt_format_fields_are_supported_by_callers(target_type):
    allowed_fields = {
        "query",
        "existing_tasks",
        "max_num",
        "data_description",
        "section_description",
        "analysis_description",
        "api_descriptions",
        "current_time",
        "research_target",
        "approved_evidence_summary",
        "target_language",
        "task",
        "data_info",
        "user_query",
        "analysis_info",
        "chart_name",
        "content",
        "data",
        "report_theme",
        "data_api",
        "max_iterations",
        "draft_report",
        "reference_image",
        "report_requirements",
        "analysis_result_info",
        "draft_outline",
        "critique",
        "report_content",
        "table_name",
        "table_data",
    }
    formatter = string.Formatter()
    for module_name in REQUIRED_KEYS:
        prompts = get_prompt_loader(module_name, report_type=target_type).get_all_prompts()
        for key in REQUIRED_KEYS[module_name]:
            fields = {field for _, field, _, _ in formatter.parse(prompts[key]) if field}
            assert fields <= allowed_fields, f"{module_name}.{key} has unsupported fields: {fields - allowed_fields}"
