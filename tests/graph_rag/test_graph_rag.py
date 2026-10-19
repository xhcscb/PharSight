from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.graph_rag import GraphBuilder, GraphRetriever, GraphStore
from src.graph_rag.builder import _locate_evidence_quote
from src.graph_rag.models import EvidenceRelation, ReviewStatus
from src.graph_rag.retriever import _reporting_periods, _tokens
from src.tools import get_tool_by_name
from src.tools.document.document_index import build_document_index
from src.tools.graph import GraphEvidenceSearchTool


def test_reporting_period_normalization():
    assert _reporting_periods("恒瑞医药2026Q1第一季度报告") == {"2026Q1"}
    assert _reporting_periods("2026年上半年经营情况") == {"2026H1"}
    assert _reporting_periods("2026年第三季度") == {"2026Q3"}


def test_clinical_stage_glyphs_share_search_tokens():
    assert "iii" in _tokens("III期临床")
    assert "iii" in _tokens("Ⅲ期临床")


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


def test_evidence_quote_locator_preserves_original_pdf_whitespace():
    text = "营业收入 8,140,565,320.77\n上年同期 7,205,611,122.72"
    located = _locate_evidence_quote(
        text, "营业收入8,140,565,320.77 上年同期7,205,611,122.72"
    )

    assert located == (0, text)


@pytest.mark.asyncio
async def test_shared_quote_is_deduplicated_and_invalid_peer_record_is_isolated(tmp_path):
    quote = "营业收入为100亿元"
    valid_one = extraction_response(
        "Observation",
        quote,
        quote,
        {"metric": "营业收入", "value": 100, "value_nature": "reported"},
    )["records"][0]
    valid_two = extraction_response(
        "Claim",
        "营业收入同比增长",
        quote,
        {"predicate": "同比增长"},
    )["records"][0]
    invalid = dict(valid_two)
    invalid["entities"] = [dict(valid_two["entities"][0], entity_type="Other")]
    index = document_index()
    index["chunks"] = [dict(index["chunks"][0], text=quote)]
    report = await GraphBuilder(
        GraphStore(tmp_path / "graph.sqlite3"),
        MockLLM({"records": [valid_one, valid_two, invalid]}, {"relations": []}),
        "deepseek-flash",
        build_concurrency=1,
    ).build_document(index)

    assert report.status == "built"
    assert report.evidence_count == 1
    assert report.record_count == 2
    assert any("rejected invalid record" in error for error in report.errors)


@pytest.mark.asyncio
async def test_build_persists_atomic_records_then_builds_query_scoped_relations(tmp_path):
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
    )
    store = GraphStore(tmp_path / "graph.sqlite3")
    builder = GraphBuilder(store, llm, "deepseek-flash")

    report = await builder.build_document(
        document_index(), as_of="2026-10-10T23:59:59+08:00"
    )

    assert report.status == "built"
    assert report.record_count == 2
    assert report.relation_count == 0
    snapshot = store.snapshot()
    assert snapshot["documents"] == 1
    assert snapshot["passages"] == 2
    assert snapshot["evidence_spans"] == 2
    assert snapshot["records"] == 2
    assert snapshot["entities"] == 1
    graph = store.to_networkx()
    assert not any(data.get("node_type") == "Source" for _, data in graph.nodes(data=True))
    assert not any(data.get("relation") == "SUPPLEMENTS" for *_, data in graph.edges(data=True))

    with store.connect() as connection:
        record_ids = [row[0] for row in connection.execute("SELECT id FROM records ORDER BY id")]

    planner = MockLLM(
        {
            "entity_names": ["测试药物"],
            "record_types": ["Observation", "Event"],
            "domains": ["clinical", "regulatory"],
            "keywords": ["临床结果", "监管进展"],
            "relation_types": ["SUPPLEMENTS"],
            "max_hops": 2,
        },
        {
            "relations": [
                {
                    "left_id": record_ids[0],
                    "right_id": record_ids[1],
                    "relation": "SUPPLEMENTS",
                    "confidence": 0.95,
                    "reason": "同一药物的临床结果与监管进展互补",
                    "comparability": {"shared_object": True},
                }
            ]
        },
    )
    pack = await GraphRetriever(store, planner, "deepseek-flash").search(
        "测试药物的临床结果与监管进展如何互补？", backend="graph", top_k=5
    )
    assert pack.query_edges
    assert all(edge.origin == "query_time" for edge in pack.query_edges)
    assert any(
        edge.relation.value == "SUPPLEMENTS" for edge in pack.query_edges
    ), [edge.model_dump(mode="json") for edge in pack.query_edges]
    assert all(edge.cache_key.startswith("edge-cache:") for edge in pack.query_edges)
    assert all(edge.context_compatible for edge in pack.query_edges)
    assert all(edge.edge_evidence_ids for edge in pack.query_edges)
    assert any(hit.relation_reasons for hit in pack.hits)
    assert any(len(path) == 2 for hit in pack.hits for path in hit.graph_paths)

    calls_before = len(llm.calls)
    reused = await builder.build_document(document_index())
    assert reused.status == "reused"
    assert len(llm.calls) == calls_before


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
async def test_rejected_quote_does_not_shift_later_entity_resolution(tmp_path):
    valid_quote = "48周体重下降22.5%，结果为直接报告值"
    invalid = extraction_response(
        "Claim",
        "原文不存在的记录",
        "完全不存在的引文",
        {"predicate": "unsupported"},
        entity_name="错误实体",
    )["records"][0]
    valid = extraction_response(
        "Observation",
        "正确实体48周体重下降22.5%",
        valid_quote,
        {
            "object": "正确实体",
            "metric": "体重变化百分比",
            "value": -22.5,
            "unit": "%",
            "value_nature": "reported",
        },
        entity_name="正确实体",
    )["records"][0]
    index = document_index()
    index["chunks"] = index["chunks"][:1]
    store = GraphStore(tmp_path / "graph.sqlite3")

    report = await GraphBuilder(
        store, MockLLM({"records": [invalid, valid]}), "deepseek-flash"
    ).build_document(index)

    assert report.record_count == 1
    with store.connect() as connection:
        names = [row[0] for row in connection.execute("SELECT canonical_name FROM entities")]
    assert names == ["正确实体"]


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


def test_store_migrates_v12_record_identity_and_bitemporal_columns(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE records (
                id TEXT PRIMARY KEY, record_type TEXT NOT NULL, text TEXT NOT NULL,
                domain TEXT NOT NULL, epistemic_status TEXT NOT NULL, polarity TEXT NOT NULL,
                materiality_json TEXT NOT NULL DEFAULT '{}', attributes_json TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL, review_status TEXT NOT NULL,
                extractor_version TEXT NOT NULL, model_name TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "record:legacy",
                "Observation",
                "试验NCT0001报告ORR为48%",
                "clinical",
                "reported",
                "neutral",
                "{}",
                json.dumps({"metric": "ORR", "value": 0.48, "trial_id": "NCT0001"}),
                "[]",
                "candidate",
                "graphrag-v1.2",
                "deepseek-flash",
            ),
        )

    store = GraphStore(path)

    with store.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(records)")}
    assert {
        "assertion_kind",
        "proposition_json",
        "qualifiers_json",
        "canonical_proposition_id",
        "origin_study_id",
        "valid_from",
        "valid_to",
        "recorded_at",
    } <= columns
    with store.connect() as connection:
        row = connection.execute(
            """SELECT canonical_proposition_id, origin_study_id, assertion_kind,
                      proposition_json, qualifiers_json, recorded_at
               FROM records WHERE id='record:legacy'"""
        ).fetchone()
    assert row["canonical_proposition_id"].startswith("proposition:")
    assert row["origin_study_id"] == "NCT0001"
    assert row["assertion_kind"] == "study_reported_observation"
    assert json.loads(row["proposition_json"])["metric"] == "ORR"
    assert json.loads(row["qualifiers_json"])["trial_id"] == "NCT0001"
    assert row["recorded_at"]


@pytest.mark.asyncio
async def test_materiality_dimensions_are_scored_and_persisted(tmp_path):
    quote = "测试药物获得关键适应症上市批准"
    response = extraction_response(
        "Event",
        quote,
        quote,
        {"event_type": "approval", "event_time": "2026-10-01"},
    )
    response["records"][0]["materiality"] = {
        "decision_impact": 1.0,
        "evidence_strength": 0.8,
        "novelty": 0.9,
        "time_sensitivity": 1.0,
        "scope": "product",
        "reason": "关键适应症首次获批",
        "score": 0.0,
    }
    index = document_index()
    index["chunks"] = [dict(index["chunks"][0], text=quote)]
    store = GraphStore(tmp_path / "graph.sqlite3")

    report = await GraphBuilder(store, MockLLM(response), "deepseek-flash").build_document(index)

    assert report.status == "built"
    with store.connect() as connection:
        materiality = json.loads(connection.execute("SELECT materiality_json FROM records").fetchone()[0])
    assert materiality["score"] == pytest.approx(0.93)
    assert materiality["scope"] == "product"


@pytest.mark.asyncio
async def test_source_assertions_share_canonical_proposition_but_keep_independent_provenance(
    tmp_path,
):
    quote = "试验NCT0001报告ORR为48%"
    response = extraction_response(
        "Observation",
        quote,
        quote,
        {"metric": "ORR", "value": 0.48, "value_nature": "reported", "trial_id": "NCT0001"},
    )
    response["records"][0].update(
        {
            "assertion_kind": "study_reported_observation",
            "proposition": {"metric": "ORR", "value": 0.48},
            "qualifiers": {"trial_id": "NCT0001", "population": "ITT"},
            "origin_study_id": "NCT0001",
            "valid_from": "2026-01-01",
        }
    )
    store = GraphStore(tmp_path / "graph.sqlite3")
    first = document_index()
    first["chunks"] = [dict(first["chunks"][0], text=quote)]
    second = document_index()
    second["document_hash"] = "d" * 64
    second["source_id"] = "WEB-TEST-002"
    second["chunks"] = [
        dict(first["chunks"][0], chunk_id="dddddddddddd:p0001:c001", text=quote)
    ]

    await GraphBuilder(store, MockLLM(response), "deepseek-flash").build_document(first)
    await GraphBuilder(store, MockLLM(response), "deepseek-flash").build_document(second)

    with store.connect() as connection:
        rows = connection.execute(
            """SELECT id, canonical_proposition_id, origin_study_id, assertion_kind,
                      valid_from, recorded_at, proposition_json, qualifiers_json
               FROM records ORDER BY id"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["canonical_proposition_id"] == rows[1]["canonical_proposition_id"]
    assert {row["origin_study_id"] for row in rows} == {"NCT0001"}
    assert all(row["assertion_kind"] == "study_reported_observation" for row in rows)
    assert all(row["valid_from"] == "2026-01-01" for row in rows)
    assert all(row["recorded_at"] for row in rows)
    assert json.loads(rows[0]["proposition_json"])["metric"] == "ORR"
    assert json.loads(rows[0]["qualifiers_json"])["population"] == "ITT"

    pack = await GraphRetriever(store).search("试验NCT0001 ORR", backend="graph", top_k=2)
    origin_edges = [edge for edge in pack.query_edges if edge.relation.value == "SAME_ORIGIN_AS"]
    assert origin_edges
    assert origin_edges[0].comparability["same_origin_study"] is True


@pytest.mark.asyncio
async def test_evidence_pack_separates_candidate_pool_active_evidence_and_trace(tmp_path):
    quote = "测试药物在III期试验中报告主要终点结果"
    response = extraction_response("Claim", quote, quote, {"predicate": "报告主要终点"})
    index = document_index()
    index["chunks"] = [dict(index["chunks"][0], text=quote)]
    store = GraphStore(tmp_path / "graph.sqlite3")
    await GraphBuilder(store, MockLLM(response), "deepseek-flash").build_document(index)

    pack = await GraphRetriever(store).search("测试药物主要终点", backend="graph", top_k=1)

    assert pack.candidate_record_ids
    assert pack.active_record_ids
    assert set(pack.active_record_ids) <= set(pack.candidate_record_ids)
    assert pack.trace_id.startswith("trace:")
    assert pack.retrieval_rounds[0].candidate_record_ids == pack.candidate_record_ids
    assert pack.verification_status == "not_run"
    assert pack.hits[0].retrieval_signals["claim"] > 0


def test_query_relation_gate_rejects_same_quote_with_different_metric():
    relation = EvidenceRelation(
        left_id="record:left",
        right_id="record:right",
        relation="EQUIVALENT_TO",
        confidence=1.0,
        reason="same quote",
    )
    pair = {
        "left": {
            "attributes": {"object": "直销", "metric": "营业收入", "value": 100},
        },
        "right": {
            "attributes": {"object": "直销", "metric": "毛利率", "value": 90},
        },
        "shared_entities": ["测试公司"],
        "shared_dimensions": ["object"],
    }

    assert not GraphRetriever._query_relation_allowed(relation, pair, True)


@pytest.mark.asyncio
async def test_single_source_high_recall_pool_is_not_capped_at_three(tmp_path):
    index = document_index()
    index["chunks"] = []
    responses = []
    for number in range(5):
        text = f"测试药物第{number + 1}项临床结果"
        index["chunks"].append(
            {
                "chunk_id": f"aaaaaaaaaaaa:p{number + 1:04d}:c001",
                "page_start": number + 1,
                "page_end": number + 1,
                "section_title": "临床结果",
                "content_hash": str(number) * 64,
                "text": text,
            }
        )
        responses.append(
            extraction_response(
                "Claim",
                text,
                text,
                {"predicate": "临床结果"},
            )
        )
    store = GraphStore(tmp_path / "graph.sqlite3")
    await GraphBuilder(store, MockLLM(*responses), "deepseek-flash").build_document(index)

    pack = await GraphRetriever(store).search(
        "测试药物临床结果",
        backend="graph",
        top_k=5,
    )

    assert len(pack.hits) == 5
    assert all("claim" in hit.retrieval_channels for hit in pack.hits)


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
async def test_entity_without_candidates_stays_temporary_without_extra_model_call(tmp_path):
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
    assert entity["resolution_status"] == "temporary"


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
    assert results[0].data["query_edges"] == []


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
