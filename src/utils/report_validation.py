"""Deterministic validation helpers for pharmaceutical research reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

SOURCE_REFERENCE_PATTERN = re.compile(r"\[[Ss]ource[：:]\s*([^\]]+)\]")
SOURCE_ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_-]{1,31}\b")


class ReportValidationError(ValueError):
    """Raised when a report fails a publication-blocking validation rule."""


@dataclass(frozen=True)
class CitationValidationResult:
    """Summary of deterministic source-ID validation."""

    cited_source_ids: set[str] = field(default_factory=set)
    unknown_source_ids: set[str] = field(default_factory=set)
    malformed_references: list[str] = field(default_factory=list)


def extract_source_ids(reference: str) -> list[str]:
    """Extract normalized source IDs from one ``[Source:...]`` payload."""
    return [match.group(0).upper() for match in SOURCE_ID_PATTERN.finditer(reference)]


def validate_source_citations(
    markdown: str,
    known_source_ids: Iterable[str],
    *,
    require_any: bool = False,
) -> CitationValidationResult:
    """Validate that every structured citation points to an available source."""
    known = {str(item).strip().upper() for item in known_source_ids if item}
    cited: set[str] = set()
    malformed: list[str] = []

    for payload in SOURCE_REFERENCE_PATTERN.findall(markdown or ""):
        ids = extract_source_ids(payload)
        if not ids:
            malformed.append(payload.strip())
        cited.update(ids)

    unknown = cited - known
    if require_any and not cited:
        raise ReportValidationError("Report contains no structured [Source:Sxxx] citations")
    if malformed:
        raise ReportValidationError(
            "Malformed source references: " + ", ".join(sorted(set(malformed)))
        )
    if unknown:
        raise ReportValidationError(
            "Unknown source IDs: " + ", ".join(sorted(unknown))
        )
    return CitationValidationResult(
        cited_source_ids=cited,
        unknown_source_ids=unknown,
        malformed_references=malformed,
    )


PHARMA_DISCLOSURE = (
    "本报告仅用于公开信息基础上的投资研究辅助，不构成证券买卖建议，"
    "也不构成诊断、治疗或其他医疗建议。临床、监管和商业化进展存在不确定性，"
    "读者应核验原始来源并独立判断。"
)
