"""Lazy agent exports.

Importing one lightweight submodule must not require every optional agent
dependency. Public names retain the original ``from src.agents import ...`` API.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AnalysisResult",
    "DataAnalyzer",
    "DataCollector",
    "DeepSearchAgent",
    "DeepSearchResult",
    "ReportGenerator",
]


def __getattr__(name: str) -> Any:
    if name in {"AnalysisResult", "DataAnalyzer"}:
        from src.agents.data_analyzer.data_analyzer import AnalysisResult, DataAnalyzer

        globals().update(
            {"AnalysisResult": AnalysisResult, "DataAnalyzer": DataAnalyzer}
        )
        return globals()[name]
    if name == "DataCollector":
        from src.agents.data_collector.data_collector import DataCollector

        globals()[name] = DataCollector
        return DataCollector
    if name == "ReportGenerator":
        from src.agents.report_generator.report_generator import ReportGenerator

        globals()[name] = ReportGenerator
        return ReportGenerator
    if name in {"DeepSearchAgent", "DeepSearchResult"}:
        try:
            from src.agents.search_agent.search_agent import (
                DeepSearchAgent,
                DeepSearchResult,
            )
        except ModuleNotFoundError:
            DeepSearchAgent = None
            DeepSearchResult = None
        globals().update(
            {
                "DeepSearchAgent": DeepSearchAgent,
                "DeepSearchResult": DeepSearchResult,
            }
        )
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
