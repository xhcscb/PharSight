"""Tests for fail-closed pharmaceutical report citations."""

import pytest

from src.utils.report_validation import (
    ReportValidationError,
    extract_source_ids,
    normalize_localized_source_citations,
    validate_source_citations,
)


def test_extract_multiple_source_ids():
    assert extract_source_ids("S001, S002;S003") == ["S001", "S002", "S003"]


def test_validate_known_source_ids():
    result = validate_source_citations(
        "关键事实 [Source:S001]，另一事实 [Source:S002,S001]。",
        {"S001", "S002"},
        require_any=True,
    )
    assert result.cited_source_ids == {"S001", "S002"}


def test_unknown_source_id_blocks_report():
    with pytest.raises(ReportValidationError, match="Unknown source IDs: S999"):
        validate_source_citations("错误引用 [Source:S999]", {"S001"})


def test_free_text_source_reference_is_rejected():
    with pytest.raises(ReportValidationError, match="Malformed source references"):
        validate_source_citations("模糊引用 [Source:公司公告]", {"S001"})


def test_malformed_reference_error_includes_context():
    with pytest.raises(ReportValidationError, match="公司公告 @"):
        validate_source_citations("前文 模糊引用 [Source:公司公告] 后文", {"S001"})


def test_inline_code_examples_are_not_citations():
    result = validate_source_citations(
        "关键事实 [Source:S001]。方法说明中的格式示例 `[Source:Sxxx]` 不算引用。",
        {"S001"},
        require_any=True,
    )
    assert result.cited_source_ids == {"S001"}
    assert result.malformed_references == []


def test_fenced_code_block_references_are_ignored():
    result = validate_source_citations(
        "```\n[Source:S999] 只是代码示例\n```\n关键事实 [Source:S001]。",
        {"S001"},
        require_any=True,
    )
    assert result.cited_source_ids == {"S001"}
    assert result.unknown_source_ids == set()


def test_report_without_citations_is_rejected_when_required():
    with pytest.raises(ReportValidationError, match="contains no structured"):
        validate_source_citations("没有引用的结论", {"S001"}, require_any=True)


def test_exact_chinese_source_alias_is_normalized_but_unknown_is_not_repaired():
    normalized = normalize_localized_source_citations(
        "事实 [来源: WEB-ABC123]；未知 [来源：WEB-NOTKNOWN]。",
        {"WEB-ABC123"},
    )

    assert "[Source:WEB-ABC123]" in normalized
    assert "[来源：WEB-NOTKNOWN]" in normalized


def test_exact_known_inline_code_source_mentions_are_normalized():
    source_id = "WEB-56697731BB-B999420E"
    normalized = normalize_localized_source_citations(
        (
            f"事实来源：`{source_id}`。核心证据源: {source_id}。"
            "未知来源：`WEB-UNKNOWN-12345678`。普通代码：`get_data(1)`。"
        ),
        {source_id},
    )

    assert normalized.count(f"[Source:{source_id}]") == 2
    assert "`WEB-UNKNOWN-12345678`" in normalized
    assert "`get_data(1)`" in normalized


def test_labeled_bracketed_or_unclosed_source_aliases_do_not_leave_delimiters():
    source_id = "WEB-56697731BB-B999420E"
    normalized = normalize_localized_source_citations(
        (
            f"表格来源：[`{source_id}`]（第11页）。"
            f"另一项来源：`{source_id}\n下一段。"
        ),
        {source_id},
    )

    assert normalized.count(f"[Source:{source_id}]") == 2
    assert f"[Source:{source_id}]]" not in normalized
    assert f"`{source_id}" not in normalized


def test_exact_known_source_is_normalized_inside_custom_locator_notation():
    source_id = "WEB-56697731BB-B999420E"
    normalized = normalize_localized_source_citations(
        f"事实【{source_id}｜第32页】；说明引自{source_id}。",
        {source_id},
    )

    assert normalized.count(f"[Source:{source_id}]") == 2
