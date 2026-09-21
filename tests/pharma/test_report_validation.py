"""Tests for fail-closed pharmaceutical report citations."""

import pytest

from src.utils.report_validation import (
    ReportValidationError,
    extract_source_ids,
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


def test_report_without_citations_is_rejected_when_required():
    with pytest.raises(ReportValidationError, match="contains no structured"):
        validate_source_citations("没有引用的结论", {"S001"}, require_any=True)
