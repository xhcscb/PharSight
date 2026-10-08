"""Deterministic validation helpers for pharmaceutical research reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

SOURCE_REFERENCE_PATTERN = re.compile(r"\[[Ss]ource[：:]\s*([^\]]+)\]")
LOCALIZED_SOURCE_REFERENCE_PATTERN = re.compile(r"\[来源[：:]\s*([^\]]+)\]")
SOURCE_ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_-]{1,31}\b")
FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")


class ReportValidationError(ValueError):
    """Raised when a report fails a publication-blocking validation rule."""


@dataclass(frozen=True)
class CitationValidationResult:
    """Summary of deterministic source-ID validation."""

    cited_source_ids: set[str] = field(default_factory=set)
    unknown_source_ids: set[str] = field(default_factory=set)
    malformed_references: list[str] = field(default_factory=list)


def strip_code_spans(markdown: str) -> str:
    """Remove fenced and inline code before citation extraction.

    Inline code is where prompts and reports put format examples such as
    ``[Source:Sxxx]``; those are explanations, not citations, and must not
    block publication.
    """
    text = FENCED_CODE_PATTERN.sub(" ", markdown or "")
    return INLINE_CODE_PATTERN.sub(" ", text)


def extract_source_ids(reference: str) -> list[str]:
    """Extract normalized source IDs from one ``[Source:...]`` payload."""
    return [match.group(0).upper() for match in SOURCE_ID_PATTERN.finditer(reference)]


def normalize_localized_source_citations(
    markdown: str,
    known_source_ids: Iterable[str],
) -> str:
    """Normalize exact known-source aliases without repairing unknown IDs.

    Long Chinese reports commonly render an allowed source ID as inline code,
    such as ``来源：`WEB-...``` or simply `` `WEB-...` ``. Replacement is
    restricted to the run's exact allow-list, so this is deterministic format
    normalization rather than fuzzy citation repair.
    """
    known = {str(item).strip().upper() for item in known_source_ids if item}

    def replace(match: re.Match) -> str:
        payload = match.group(1)
        ids = extract_source_ids(payload)
        remainder = SOURCE_ID_PATTERN.sub("", payload)
        remainder = re.sub(r"[`\s,，;；]+", "", remainder)
        if not ids or remainder or any(source_id not in known for source_id in ids):
            return match.group(0)
        return f"[Source:{','.join(ids)}]"

    normalized = LOCALIZED_SOURCE_REFERENCE_PATTERN.sub(replace, markdown or "")
    for source_id in sorted(known, key=len, reverse=True):
        escaped = re.escape(source_id)
        normalized = re.sub(
            rf"(?<!\[)(?P<label>证据来源|核心证据源|来源|Source)\s*[：:]\s*"
            rf"\[\s*`?\s*{escaped}\s*`?\s*\]",
            rf"\g<label>：[Source:{source_id}]",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            rf"(?<!\[)(?P<label>证据来源|核心证据源|来源|Source)\s*[：:]\s*"
            rf"`?\s*{escaped}\b`?",
            rf"\g<label>：[Source:{source_id}]",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            rf"`\s*{escaped}\s*`",
            f"[Source:{source_id}]",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            rf"(?<!Source:)(?<![A-Z0-9_-]){escaped}(?![A-Z0-9_-])",
            f"[Source:{source_id}]",
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def _context_snippet(text: str, match: re.Match, radius: int = 30) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end].replace("\n", " ").strip()


def validate_source_citations(
    markdown: str,
    known_source_ids: Iterable[str],
    *,
    require_any: bool = False,
) -> CitationValidationResult:
    """Validate that every structured citation points to an available source."""
    known = {str(item).strip().upper() for item in known_source_ids if item}
    content = strip_code_spans(markdown)
    cited: set[str] = set()
    malformed: list[str] = []
    malformed_matches: list[re.Match] = []

    for match in SOURCE_REFERENCE_PATTERN.finditer(content):
        ids = extract_source_ids(match.group(1))
        if not ids:
            malformed.append(match.group(1).strip())
            malformed_matches.append(match)
        cited.update(ids)

    unknown = cited - known
    if require_any and not cited:
        raise ReportValidationError("Report contains no structured [Source:Sxxx] citations")
    if malformed:
        details = "; ".join(
            f'{match.group(1).strip()} @ "{_context_snippet(content, match)}"'
            for match in malformed_matches[:3]
        )
        raise ReportValidationError("Malformed source references: " + details)
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
