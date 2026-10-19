"""Tests for pharmaceutical configuration safety and validation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from run_report import _build_research_query, _resolve_model_name
from src.config.config import Config


def _config_for_validation(**overrides):
    config = Config.__new__(Config)
    config.config = {
        "target_name": "测试医药公司",
        "stock_code": "000001.SZ",
        "target_type": "pharma_company",
        "as_of": "2026-09-21T18:00:00+08:00",
        "jurisdictions": ["CN"],
        "research_questions": ["测试问题"],
        "custom_collect_tasks": ["测试采集"],
        "custom_analysis_tasks": ["测试分析"],
        "output_formats": ["md"],
    }
    config.config.update(overrides)
    return config


def test_pharma_config_requires_timezone():
    config = _config_for_validation(as_of="2026-09-21T18:00:00")
    with pytest.raises(ValueError, match="explicit timezone"):
        config._validate_research_config()


def test_pharma_config_accepts_minimum_contract():
    _config_for_validation()._validate_research_config()


def test_pharma_config_rejects_fuzzy_citation_mode():
    config = _config_for_validation(strict_citations=False)
    with pytest.raises(ValueError, match="strict_citations.*must be true"):
        config._validate_research_config()


def test_pharma_config_rejects_invalid_evidence_selection():
    config = _config_for_validation(evidence_selection="always")
    with pytest.raises(ValueError, match="evidence_selection"):
        config._validate_research_config()


def test_pharma_config_rejects_invalid_selection_threshold():
    config = _config_for_validation(evidence_selection_threshold=0)
    with pytest.raises(ValueError, match="evidence_selection_threshold"):
        config._validate_research_config()


def test_pharma_config_validates_graph_retrieval_controls():
    config = _config_for_validation(retrieval_backend="hybrid", graph_max_hops=2)
    config._validate_research_config()
    assert config.config["retrieval_backend"] == "hybrid"

    invalid_backend = _config_for_validation(retrieval_backend="neo4j")
    with pytest.raises(ValueError, match="retrieval_backend"):
        invalid_backend._validate_research_config()

    max_hops = _config_for_validation(graph_max_hops=4)
    max_hops._validate_research_config()
    assert max_hops.config["graph_max_hops"] == 4

    invalid_hops = _config_for_validation(graph_max_hops=5)
    with pytest.raises(ValueError, match="graph_max_hops"):
        invalid_hops._validate_research_config()


def _query_config(**overrides):
    values = {
        "target_name": "示例医药公司",
        "stock_code": "000001.SZ",
        "target_type": "pharma_company",
        "as_of": "2026-09-21T18:00:00+08:00",
        "jurisdictions": ["CN"],
        "research_questions": ["核心证据是否支持预期？"],
    }
    values.update(overrides)
    return SimpleNamespace(config=values)


def test_research_query_defaults_to_full_pipeline_coverage():
    query = _build_research_query(_query_config())
    assert "全部已上市产品和在研管线" in query
    assert "全部适应症" in query
    assert "focus assets" not in query


def test_research_query_respects_explicit_focus_scope():
    query = _build_research_query(
        _query_config(focus_assets=["药物甲"], focus_indications=["适应症乙"])
    )
    assert "focus assets: 药物甲" in query
    assert "focus indications: 适应症乙" in query
    assert "全部已上市产品和在研管线" not in query


def test_live_search_requires_supported_provider_and_key(monkeypatch):
    config = _config_for_validation(
        live_web_search=True,
        search_provider="unsupported",
    )
    with pytest.raises(ValueError, match="Unsupported search_provider"):
        config._validate_live_search_config()

    config.config["search_provider"] = "serper"
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SERPER_API_KEY is required"):
        config._validate_live_search_config()


def test_config_snapshot_redacts_nested_credentials():
    redacted = Config.redacted_copy(
        {
            "llm_config_list": [
                {"model_name": "model", "api_key": "secret", "base_url": "https://example.invalid"}
            ],
            "nested": {"access_token": "token", "normal": "visible"},
        }
    )
    assert redacted["llm_config_list"][0]["api_key"] == "***REDACTED***"
    assert redacted["nested"]["access_token"] == "***REDACTED***"
    assert redacted["nested"]["normal"] == "visible"


def test_environment_variables_are_expanded_after_merge(monkeypatch):
    monkeypatch.setenv("PHARSIGHT_TEST_MODEL", "test-model")
    resolved = Config._replace_env_vars(
        {"llm_config_list": [{"model_name": "${PHARSIGHT_TEST_MODEL}"}]}
    )
    assert resolved["llm_config_list"][0]["model_name"] == "test-model"


def test_minimal_pharma_example_does_not_require_optional_model_env(monkeypatch):
    monkeypatch.setenv("DS_MODEL_NAME", "test-model")
    monkeypatch.setenv("DS_API_KEY", "test-key")
    monkeypatch.setenv("DS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    for name in (
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
        "VLM_MODEL_NAME",
        "VLM_API_KEY",
        "VLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Config, "_set_dirs", lambda self: None)
    monkeypatch.setattr(Config, "_set_llms", lambda self: setattr(self, "llm_dict", {}))
    monkeypatch.setattr(Config, "_set_rate_limiter", lambda self: None)

    project_root = Path(__file__).resolve().parents[2]
    config = Config(project_root / "docs/example_configs/my_config_company.yaml")

    assert [item["model_name"] for item in config.config["llm_config_list"]] == [
        "test-model"
    ]


def test_disabled_optional_model_ignores_unrelated_environment(monkeypatch):
    monkeypatch.setenv("VLM_MODEL_NAME", "unconfigured-vlm")
    config = SimpleNamespace(config={}, llm_dict={})

    assert _resolve_model_name(config, "vlm_model_name", "VLM_MODEL_NAME", required=False) is None


def test_config_creates_isolated_llm_clients():
    class FakeLLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config = Config.__new__(Config)
    config.config = {
        "llm_config_list": [
            {
                "model_name": "test-model",
                "api_key": "test-key",
                "base_url": "https://example.invalid/v1",
                "generation_params": {"temperature": 0.2},
            }
        ]
    }

    first = config.create_llm("test-model", llm_class=FakeLLM)
    second = config.create_llm("test-model", llm_class=FakeLLM)

    assert first is not second
    assert first.kwargs == second.kwargs
