from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.graph_rag import GraphBuilder, GraphRetriever, GraphStore
from src.graph_rag.models import ReviewStatus
from src.tools import get_tool_by_name
from src.tools.document.document_index import build_document_index
from src.tools.graph import GraphEvidenceSearchTool


@pytest.mark.asyncio
async def test_preloaded_graph_logging_supports_project_logger(monkeypatch, tmp_path):
    """The project Logger accepts a single rendered message, unlike stdlib logging."""
    from run_report import _build_preloaded_graph
    from src.graph_rag import BuildReport

    report = BuildReport(
        document_id="doc:test",
        document_hash="hash",
        status="built",
        model_name="deepseek-flash",
    )

    class BuilderStub:
        def __init__(self, *args, **kwargs):
            pass

        async def build_evidence_record(self, payload, *, as_of=None):
            return report

    class LoggerStub:
        def __init__(self):
            self.messages = []

        def info(self, message):
            self.messages.append(message)

    monkeypatch.setattr("src.graph_rag.GraphBuilder", BuilderStub)
    config = SimpleNamespace(
        config={
            "retrieval_backend": "graph",
            "graph_build_on_ingest": True,
            "graph_store_path": str(tmp_path / "graph.sqlite3"),
            "as_of": "2026-10-10T23:59:59+08:00",
        },
        llm_dict={"deepseek-flash": object()},
    )
    memory = SimpleNamespace(
        get_collect_data=lambda: [
            SimpleNamespace(source_id="S001", data={"content": "evidence"})
        ]
    )
    logger = LoggerStub()

    result = await _build_preloaded_graph(config, memory, "deepseek-flash", logger)

    assert result == [report]
    assert logger.messages and "doc:test" in logger.messages[0]


class MockLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected LLM call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


def extraction_response(record_type, text, quote, attributes, entity_name="测试药物"):
    return {
        "records": [
            {
                "record_type": record_type,
                "text": text,
                "domain": "clinical" if record_type != "Event" else "regulatory",
                "epistemic_status": "reported",
                "polarity": "neutral",
                "attributes": attributes,
                "evidence_quote": quote,
                "entities": [
                    {
                        "name": entity_name,
                        "entity_type": "Drug",
                        "role": "about",
                        "aliases": [],
                        "external_ids": {"internal": "DRUG-001"},
                        "confidence": 0.98,
                    }
                ],
            }
        ]
    }


def document_index():
    first = "测试药物在III期试验中，48周体重下降22.5%，结果为直接报告值。"
    second = "2026年5月，测试药物的上市申请获监管机构受理。"
    return {
        "document_hash": "a" * 64,
        "source_id": "WEB-TEST-001",
        "source_url": "https://example.invalid/report.pdf",
        "publisher": "测试发布者",
        "title": "测试药物临床与监管进展",
        "retrieved_at": "2026-10-10T10:00:00+08:00",
        "chunks": [
            {
                "chunk_id": "aaaaaaaaaaaa:p0001:c001",
                "page_start": 1,
                "page_end": 1,
                "section_title": "临床结果",
                "content_hash": "b" * 64,
                "text": first,
            },
            {
                "chunk_id": "aaaaaaaaaaaa:p0002:c001",
                "page_start": 2,
                "page_end": 2,
                "section_title": "监管进展",
                "content_hash": "c" * 64,
                "text": second,
            },
        ],
    }


@pytest.mark.asyncio
async def test_build_persists_three_layers_relations_and_is_idempotent(tmp_path):
    observation_quote = "48周体重下降22.5%，结果为直接报告值"
    event_quote = "2026年5月，测试药物的上市申请获监管机构受理"
    llm = MockLLM(
        extraction_response(
            "Observation",
            "测试药物48周体重下降22.5%",
            observation_quote,
            {
                "object": "测试药物",
                "conditions": {"timepoint": "48周"},
                "metric": "体重变化百分比",
                "value": -22.5,
                "unit": "%",
                "value_nature": "reported",
            },
        ),
        extraction_response(
            "Event",
            "测试药物上市申请于2026年5月获受理",
            event_quote,
            {
                "subject": "测试药物",
                "event_type": "application_accepted",
                "event_time": "2026-05",
                "time_precision": "month",
                "new_status": "accepted",
            },
        ),
        {
            "relations": [
                {
                    "left_id": "placeholder",
                    "right_id": "placeholder",
                    "relation": "SUPPLEMENTS",
                    "confidence": 0.95,
                    "reason": "同一药物的临床结果与监管进展互补",
                    "comparability": {"shared_object": True},
                }
            ]
        },
    )
    store = GraphStore(tmp_path / "graph.sqlite3")
    builder = GraphBuilder(store, llm, "deepseek-flash")

    # The relation response needs the deterministic record IDs. Build extraction first,
    # inspect candidates, then substitute IDs before relation validation.
    original_generate = builder._generate_json
    call_count = 0

    async def generate_with_relation_ids(prompt, payload):
        nonlocal call_count
        call_count += 1
        if "pairs" in payload:
            pair = payload["pairs"][0]
            return {
                "relations": [
                    {
                        "left_id": pair["left_id"],
                        "right_id": pair["right_id"],
                        "relation": "SUPPLEMENTS",
                        "confidence": 0.95,
                        "reason": "同一药物的临床结果与监管进展互补",
                        "comparability": {"shared_object": True},
                    }
                ]
            }
        return await original_generate(prompt, payload)

    builder._generate_json = generate_with_relation_ids
    report = await builder.build_document(
        document_index(), as_of="2026-10-10T23:59:59+08:00"
    )

    assert report.status == "built"
    assert report.record_count == 2
    assert report.relation_count == 1
    snapshot = store.snapshot()
    assert snapshot["documents"] == 1
    assert snapshot["passages"] == 2
    assert snapshot["evidence_spans"] == 2
    assert snapshot["records"] == 2
    assert snapshot["entities"] == 1
    graph = store.to_networkx()
    assert not any(data.get("node_type") == "Source" for _, data in graph.nodes(data=True))
    assert any(data.get("relation") == "SUPPLEMENTS" for *_, data in graph.edges(data=True))

    calls_before = call_count
    reused = await builder.build_document(document_index())
    assert reused.status == "reused"
    assert call_count == calls_before


@pytest.mark.asyncio
async def test_missing_evidence_quote_is_rejected_without_polluting_records(tmp_path):
    llm = MockLLM(
        extraction_response(
            "Claim",
            "原文没有这句话",
            "完全不存在的引文",
            {"predicate": "unsupported"},
        )
    )
    index = document_index()
    index["chunks"] = index["chunks"][:1]
    store = GraphStore(tmp_path / "graph.sqlite3")
    report = await GraphBuilder(store, llm, "deepseek-flash").build_document(index)

    assert report.status == "built"
    assert report.record_count == 0
    assert report.evidence_count == 0
    assert "evidence quote was not found" in report.errors[0]
    assert store.snapshot()["records"] == 0


@pytest.mark.asyncio
async def test_all_extraction_failures_fail_build_and_remain_retryable(tmp_path):
    index = document_index()
    index["chunks"] = index["chunks"][:1]
    store = GraphStore(tmp_path / "graph.sqlite3")
    report = await GraphBuilder(
        store,
        MockLLM("", "", ""),
        "deepseek-flash",
    ).build_document(index)

    assert report.status == "failed"
    assert "No structured records" in report.errors[-1]
    assert store.snapshot()["documents"] == 0
    assert not store.is_current(index["document_hash"], "deepseek-flash")


@pytest.mark.asyncio
async def test_hybrid_bm25_is_limited_to_approved_source_scope(tmp_path):
    index_dir = tmp_path / "indexes"
    for source_id, content in (
        ("APPROVED-001", "恒瑞医药营业收入316.3亿元"),
        ("OUTSIDE-001", "恒瑞医药营业收入9999亿元"),
    ):
        build_document_index(
            pages=[content],
            source_url=f"https://example.invalid/{source_id}.pdf",
            title=source_id,
            parser="test",
            retrieved_at="2026-10-10T10:00:00+08:00",
            source_id=source_id,
            index_dir=index_dir,
        )
    retriever = GraphRetriever(GraphStore(tmp_path / "graph.sqlite3"))

    pack = await retriever.search(
        "恒瑞医药营业收入",
        backend="hybrid",
        allowed_source_ids={"APPROVED-001"},
        index_dir=index_dir,
    )

    assert {hit.source_id for hit in pack.hits} == {"APPROVED-001"}
    empty_pack = await retriever.search(
        "恒瑞医药营业收入",
        backend="hybrid",
        allowed_source_ids=set(),
        index_dir=index_dir,
    )
    assert empty_pack.hits == []
    with pytest.raises(ValueError, match="outside the approved evidence scope"):
        await retriever.search(
            "恒瑞医药营业收入",
            backend="hybrid",
            source_id="OUTSIDE-001",
            allowed_source_ids={"APPROVED-001"},
            index_dir=index_dir,
        )


@pytest.mark.asyncio
async def test_graph_retrieval_returns_original_passage_and_candidate_warning(tmp_path):
    quote = "48周体重下降22.5%，结果为直接报告值"
    llm = MockLLM(
        extraction_response(
            "Observation",
            "测试药物48周体重下降22.5%",
            quote,
            {
                "conditions": {"timepoint": "48周"},
                "metric": "体重变化百分比",
                "value": -22.5,
                "unit": "%",
                "value_nature": "reported",
            },
        ),
        {
            "entity_names": ["测试药物"],
            "record_types": ["Observation"],
            "domains": ["clinical"],
            "keywords": ["48周", "体重"],
            "relation_types": [],
            "max_hops": 2,
        },
    )
    index = document_index()
    index["chunks"] = index["chunks"][:1]
    store = GraphStore(tmp_path / "graph.sqlite3")
    builder = GraphBuilder(store, llm, "deepseek-flash")
    report = await builder.build_document(index)
    assert report.status == "built"

    pack = await GraphRetriever(store, llm, "deepseek-flash").search(
        "测试药物48周体重下降多少？", backend="graph", top_k=3
    )
    assert len(pack.hits) == 1
    assert quote in pack.hits[0].passage_text
    assert pack.hits[0].source_id == "WEB-TEST-001"
    assert pack.hits[0].record_summaries[0]["review_status"] == "candidate"
    assert len(pack.hits[0].record_summaries) == len(
        {item["id"] for item in pack.hits[0].record_summaries}
    )
    assert pack.hits[0].graph_paths


def test_review_status_and_projection_rebuild(tmp_path):
    store = GraphStore(tmp_path / "graph.sqlite3")
    with pytest.raises(KeyError):
        store.set_review_status("record:missing", ReviewStatus.APPROVED)
    assert store.to_networkx().number_of_nodes() == 0


def test_store_transaction_rolls_back_invalid_bundle(tmp_path):
    store = GraphStore(tmp_path / "graph.sqlite3")
    before = store.snapshot()
    with store.connect() as connection:
        with pytest.raises(Exception):
            connection.execute("INSERT INTO passages(id, document_id) VALUES ('bad', 'missing')")
    assert store.snapshot() == before


def test_graph_search_tool_is_registered():
    assert get_tool_by_name("Graph evidence search") is GraphEvidenceSearchTool


@pytest.mark.asyncio
async def test_query_planning_failure_uses_lexical_fallback(tmp_path):
    store = GraphStore(tmp_path / "graph.sqlite3")
    retriever = GraphRetriever(store, MockLLM(RuntimeError("planner unavailable")), "deepseek-flash")
    plan, warnings = await retriever.plan_query("测试药物监管进展")
    assert plan.keywords
    assert "lexical plan used" in warnings[0]


@pytest.mark.asyncio
async def test_query_planning_timeout_uses_lexical_fallback(tmp_path):
    class SlowLLM:
        async def generate(self, messages, **kwargs):
            await asyncio.sleep(1)
            return {}

    retriever = GraphRetriever(GraphStore(tmp_path / "graph.sqlite3"), SlowLLM())
    retriever.planner_timeout_seconds = 0.01

    plan, warnings = await retriever.plan_query("恒瑞医药营业收入")

    assert "恒瑞医药" in plan.keywords
    assert "lexical plan used" in warnings[0]


@pytest.mark.asyncio
async def test_ambiguous_entity_resolution_uses_model_and_stays_conservative(tmp_path):
    quote = "候选药物A进入临床阶段"
    response = extraction_response(
        "Claim",
        quote,
        quote,
        {"predicate": "entered_clinical_stage"},
        entity_name="候选药物A",
    )
    response["records"][0]["entities"][0]["external_ids"] = {}
    llm = MockLLM(
        response,
        {
            "entities": [
                {
                    "input_index": 0,
                    "canonical_name": "候选药物A",
                    "matched_entity_id": None,
                    "resolution_status": "needs_review",
                    "reason": "无外部标识符",
                }
            ]
        },
    )
    index = document_index()
    index["chunks"] = [
        {
            "chunk_id": "aaaaaaaaaaaa:p0001:c001",
            "page_start": 1,
            "page_end": 1,
            "section_title": "管线",
            "content_hash": "d" * 64,
            "text": quote,
        }
    ]
    store = GraphStore(tmp_path / "graph.sqlite3")
    report = await GraphBuilder(store, llm, "deepseek-flash").build_document(index)
    assert report.status == "built"
    entity = next(data for _, data in store.to_networkx().nodes(data=True) if data["node_type"] == "Entity")
    assert entity["resolution_status"] == "needs_review"


@pytest.mark.asyncio
async def test_graph_tool_returns_toolresult_compatible_evidence(tmp_path):
    quote = "48周体重下降22.5%，结果为直接报告值"
    build_llm = MockLLM(
        extraction_response(
            "Observation",
            "测试药物48周体重下降22.5%",
            quote,
            {"metric": "体重变化百分比", "value": -22.5, "value_nature": "reported"},
        )
    )
    index = document_index()
    index["chunks"] = index["chunks"][:1]
    store = GraphStore(tmp_path / "graph.sqlite3")
    await GraphBuilder(store, build_llm, "deepseek-flash").build_document(index)
    query_llm = MockLLM(
        {
            "entity_names": ["测试药物"],
            "record_types": ["Observation"],
            "domains": ["clinical"],
            "keywords": ["48周"],
            "relation_types": [],
            "max_hops": 2,
        }
    )
    tool = GraphEvidenceSearchTool()
    tool._retriever = GraphRetriever(store, query_llm, "deepseek-flash")
    tool._backend = "graph"
    results = await tool.api_function(query="测试药物48周结果", top_k=3)
    assert results[0].source_id == "WEB-TEST-001"
    assert quote in results[0].data["content"]
    assert results[0].data["retrieval_hits"][0]["graph_paths"]


def test_collector_merge_uses_graph_passage_text_instead_of_none():
    from src.agents.data_collector.data_collector import DataCollector

    existing = SimpleNamespace(
        source_id="S001",
        data={"source_id": "S001", "content": "original manifest content"},
    )
    collector = object.__new__(DataCollector)
    collector.memory = SimpleNamespace(
        get_collect_data=lambda: [existing],
        add_data=lambda item: None,
    )
    collector.code_executor = SimpleNamespace(set_variable=lambda *args: None)

    collector._merge_pdf_retrieval_response(
        [
            {
                "source_id": "S001",
                "content": "tool rendering",
                "query": "收入",
                "retrieval_hits": [
                    {
                        "chunk_id": "chunk-1",
                        "page_start": 3,
                        "passage_text": "营业收入316.3亿元",
                    }
                ],
            }
        ]
    )

    assert "营业收入316.3亿元" in existing.data["content"]
    assert "None" not in existing.data["content"]


def test_collector_refreshes_graph_scope_from_current_memory():
    from src.agents.data_collector.data_collector import DataCollector

    class GraphToolStub:
        name = "Graph evidence search"

        def set_allowed_source_ids(self, source_ids):
            self.source_ids = source_ids

    collector = object.__new__(DataCollector)
    collector.memory = SimpleNamespace(
        get_collect_data=lambda: [SimpleNamespace(source_id="NEW-PDF-001")]
    )
    graph_tool = GraphToolStub()
    collector.tools = [graph_tool]

    collector._refresh_graph_source_scope()

    assert graph_tool.source_ids == {"NEW-PDF-001"}
