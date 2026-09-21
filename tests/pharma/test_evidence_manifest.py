"""Tests for the deterministic pharmaceutical evidence manifest."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from run_report import _preload_evidence
from src.tools import get_tool_by_name, get_tool_categories
from src.tools.pharma import PharmaEvidenceManifestTool, load_evidence_manifest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pharma" / "evidence_manifest.yaml"


def test_manifest_preserves_metadata_and_filters_post_cutoff():
    results = load_evidence_manifest(FIXTURE, strict_cutoff=True)

    assert [item.source_id for item in results] == ["S001"]
    record = results[0].data
    assert record["entity_keys"]["drug"] == "测试药物"
    assert record["cutoff_eligible"] is True
    assert len(record["document_hash"]) == 64
    assert "测试用证据" in record["content"]
    assert "S001" in results[0].brief_str()
    assert "https://example.invalid/s001" in results[0].reference_text()


def test_manifest_can_include_post_cutoff_record_when_explicitly_allowed():
    results = load_evidence_manifest(FIXTURE, strict_cutoff=False)
    assert [item.source_id for item in results] == ["S001", "S002", "S003"]
    assert results[1].data["cutoff_eligible"] is False
    assert results[2].data["cutoff_eligible"] is False


def test_manifest_source_id_filter_fails_for_unknown_id():
    with pytest.raises(ValueError, match="Requested source IDs not found"):
        load_evidence_manifest(FIXTURE, source_ids=["S999"])


@pytest.mark.asyncio
async def test_manifest_tool_uses_tool_result_contract():
    tool = PharmaEvidenceManifestTool()
    results = await tool.api_function(str(FIXTURE), strict_cutoff=True)
    assert tool.name == "pharma_evidence_manifest"
    assert results[0].source_id == "S001"


def test_manifest_tool_is_registered_in_pharma_category():
    assert get_tool_by_name("pharma_evidence_manifest") is PharmaEvidenceManifestTool
    assert "pharma_evidence_manifest" in get_tool_categories()["pharma"]


def test_resume_rejects_changed_evidence_snapshot():
    existing = load_evidence_manifest(FIXTURE, strict_cutoff=True)[0]
    existing.data["document_hash"] = "changed"

    class MemoryStub:
        def get_collect_data(self):
            return [existing]

        def add_data(self, item):  # pragma: no cover - fail-closed path must not add
            raise AssertionError(f"Unexpected evidence addition: {item.source_id}")

    config = SimpleNamespace(
        config={
            "source_manifest": str(FIXTURE),
            "as_of": "2026-09-21T18:00:00+08:00",
            "strict_cutoff": True,
        }
    )
    with pytest.raises(RuntimeError, match="changed since the checkpoint"):
        _preload_evidence(config, MemoryStub())
