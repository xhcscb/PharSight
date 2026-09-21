"""Task-level evidence selection for pharmaceutical analysis and reports.

A controlled evidence pack can contain dozens of sources covering many drugs,
indications and jurisdictions. Injecting every source into every analysis and
report prompt inflates context and cost while lowering relevance. This module
selects the sources that match the current task and falls back to the complete
set whenever selection is disabled or fails, so evidence is never silently
dropped.

Configuration (``src/config/default_config.yaml``):

- ``evidence_selection``: ``auto`` (default), ``off`` or ``llm``.
- ``evidence_selection_threshold``: ``auto`` selects only when the catalog is
  larger than this value.
- ``evidence_selection_max_k``: maximum number of records passed to one task.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_SELECTION_MODES = ("auto", "off", "llm")
DEFAULT_SELECTION_MODE = "auto"
DEFAULT_SELECTION_THRESHOLD = 10
DEFAULT_SELECTION_MAX_K = 12


def _positive_int(config: Any, key: str, default: int) -> int:
    try:
        value = int(config.config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def get_selection_mode(config: Any) -> str:
    """Return the configured selection mode, defaulting on unknown values."""
    mode = str(config.config.get("evidence_selection", DEFAULT_SELECTION_MODE)).strip().lower()
    return mode if mode in VALID_SELECTION_MODES else DEFAULT_SELECTION_MODE


def should_select_evidence(config: Any, item_count: int) -> bool:
    """Return whether a catalog of ``item_count`` records should be narrowed."""
    if item_count <= 0:
        return False
    mode = get_selection_mode(config)
    if mode == "off":
        return False
    if mode == "llm":
        return True
    threshold = _positive_int(config, "evidence_selection_threshold", DEFAULT_SELECTION_THRESHOLD)
    return item_count > threshold


def _ordered_subset(items: list, selected, max_k: int) -> list:
    selected_ids = {id(item) for item in selected}
    return [item for item in items if id(item) in selected_ids][:max_k]


async def select_evidence_by_task(
    memory,
    task_text: str,
    data_list,
    *,
    model_name: str,
    config: Any,
) -> list:
    """Return an ordered, task-relevant subset of collected evidence.

    The full list is returned when selection is disabled, when the catalog is
    small enough, or when the selection call fails.
    """
    items = list(data_list)
    if not items or not task_text or not str(task_text).strip():
        return items
    if not should_select_evidence(config, len(items)):
        return items

    max_k = _positive_int(config, "evidence_selection_max_k", DEFAULT_SELECTION_MAX_K)
    try:
        selected, _ = await memory.select_data_by_llm(
            str(task_text), max_k=max_k, model_name=model_name
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Evidence selection failed; passing all %d sources: %s", len(items), exc)
        return items

    ordered = _ordered_subset(items, selected or [], max_k)
    return ordered or items


async def select_analyses_by_task(
    memory,
    task_text: str,
    analysis_list,
    *,
    model_name: str,
    config: Any,
) -> list:
    """Return an ordered, task-relevant subset of existing analysis results."""
    items = list(analysis_list)
    if not items or not task_text or not str(task_text).strip():
        return items
    if not should_select_evidence(config, len(items)):
        return items

    max_k = _positive_int(config, "evidence_selection_max_k", DEFAULT_SELECTION_MAX_K)
    try:
        selected = await memory.select_analysis_result_by_llm(
            str(task_text), max_k=max_k, model_name=model_name
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Analysis selection failed; passing all %d analyses: %s", len(items), exc)
        return items

    ordered = _ordered_subset(items, selected or [], max_k)
    return ordered or items
