"""Explainable graph and graph+BM25 evidence retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import deque
from itertools import combinations
from typing import Any, Literal

from json_repair import loads as repair_json

from .models import (
    EvidenceHit,
    EvidencePack,
    EvidenceRelation,
    QueryGraphEdge,
    QueryPlan,
    RetrievalRound,
)
from .store import GraphStore

QUERY_SYSTEM_PROMPT = """Create a high-recall graph retrieval plan for a pharmaceutical
investment-research question. Return JSON only with query_type, entity_names, record_types (Claim,
Observation, Event), domains, keywords, relation_types, subquestions, required_evidence_types,
time_scope and max_hops (0-4). Allowed query types are simple_fact, multi_hop, comparison,
global_trend, temporal_status and quantitative_analysis. Allowed domains are
clinical, financial, market, regulatory, commercial, mechanism and other. Allowed relation_types
are ENTAILS, EQUIVALENT_TO, SUPPORTS, SUPPLEMENTS, QUALIFIES, ELABORATES,
TEMPORAL_PRECEDES, COMPARES, PREREQUISITE, RELATED, INCOMPARABLE_WITH, CONTRADICTS,
SUPERSEDES and SAME_ORIGIN_AS. Include aliases, abbreviations, Chinese/English names and likely table-header
terms in entity_names or keywords. Prefer recall over precision; downstream ranking removes
noise. Subquestions must be objects with id, question, required_evidence_types, depends_on and
keywords. Use an empty relation_types list when no specific evidence relation is required. Do not
answer the question and do not invent identifiers."""

QUERY_RELATION_SYSTEM_PROMPT = """Build only query-relevant semantic edges between the supplied
atomic pharmaceutical investment-research records. Return JSON only. Allowed relations are
ENTAILS, EQUIVALENT_TO, SUPPORTS, SUPPLEMENTS, QUALIFIES, ELABORATES, TEMPORAL_PRECEDES,
COMPARES, PREREQUISITE, RELATED, INCOMPARABLE_WITH, CONTRADICTS, SUPERSEDES,
SAME_ORIGIN_AS. Compare proposition, qualifiers, reporting period, origin study, source, page and evidence quote, not text
similarity alone. EQUIVALENT_TO requires the same object, metric, period, conditions and unit.
SUPPORTS is evidentiary direction, not chronology. SUPERSEDES requires an explicit update,
correction, withdrawal or stable business identifier; a later date alone is insufficient.
INCOMPARABLE_WITH is only for records the query actually invites users to compare, never for
unrelated records. SAME_ORIGIN_AS requires the same evidence span or an explicit matching
origin_study_id; a shared company or topic is insufficient. Omit uncertain pairs. Output
{"relations": [{"left_id": ..., "right_id": ...,
"relation": ..., "confidence": 0.0, "reason": ..., "comparability": {}}]}."""

SEMANTIC_RELATIONS = {
    "ENTAILS",
    "EQUIVALENT_TO",
    "SUPPORTS",
    "SUPPLEMENTS",
    "QUALIFIES",
    "ELABORATES",
    "TEMPORAL_PRECEDES",
    "COMPARES",
    "PREREQUISITE",
    "RELATED",
    "INCOMPARABLE_WITH",
    "CONTRADICTS",
    "SUPERSEDES",
    "SAME_ORIGIN_AS",
}


def _tokens(value: str) -> set[str]:
    value = value.translate(str.maketrans({"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV"}))
    latin = re.findall(r"[a-z0-9][a-z0-9._-]*", value.lower())
    chinese: list[str] = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(sequence) <= 4:
            chinese.append(sequence)
        for width in (2, 3, 4):
            chinese.extend(
                sequence[index : index + width]
                for index in range(len(sequence) - width + 1)
            )
    return set(latin + chinese)


def _reporting_periods(value: str) -> set[str]:
    """Normalize explicit quarter/half/year markers used to rank period-specific evidence."""
    compact = re.sub(r"[\s年期第季度半年度报告]", "", value.upper())
    periods = set(re.findall(r"20\d{2}(?:Q[1-4]|H[12]|FY)", compact))
    periods.update(f"{year}H1" for year in re.findall(r"(20\d{2})\s*年?\s*上半年", value))
    periods.update(f"{year}H2" for year in re.findall(r"(20\d{2})\s*年?\s*下半年", value))
    quarter_numbers = {"一": "1", "二": "2", "三": "3", "四": "4"}
    periods.update(
        f"{year}Q{quarter_numbers.get(quarter, quarter)}"
        for year, quarter in re.findall(
            r"(20\d{2})\s*年?\s*第?\s*([一二三四1-4])\s*季度", value
        )
    )
    return periods


class GraphRetriever:
    """Retrieve graph evidence and always return citable passage text."""

    def __init__(
        self,
        store: GraphStore,
        llm: Any | None = None,
        model_name: str | None = None,
        *,
        planner_timeout_seconds: float = 30.0,
        claim_candidate_pool: int = 120,
        query_relation_record_limit: int = 24,
        relation_confidence_threshold: float = 0.8,
    ):
        self.store = store
        self.llm = llm
        self.model_name = model_name
        self.planner_timeout_seconds = max(1.0, float(planner_timeout_seconds))
        self.claim_candidate_pool = max(20, int(claim_candidate_pool))
        self.query_relation_record_limit = max(2, int(query_relation_record_limit))
        self.relation_confidence_threshold = min(
            1.0, max(0.0, float(relation_confidence_threshold))
        )
        self._graph_projection = None

    async def plan_query(self, query: str, max_hops: int = 2) -> tuple[QueryPlan, list[str]]:
        """Produce a bounded query plan, falling back to deterministic lexical terms."""
        warnings: list[str] = []
        if self.llm is not None:
            try:
                response = await asyncio.wait_for(
                    self.llm.generate(
                        [
                            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
                            {"role": "user", "content": query},
                        ],
                        include_stop_string=False,
                        temperature=0,
                        max_tokens=4096,
                        response_format={"type": "json_object"},
                        extra_body={"thinking": {"type": "disabled"}},
                    ),
                    timeout=self.planner_timeout_seconds,
                )
                text = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", str(response or "").strip(), flags=re.I
                )
                plan = QueryPlan.model_validate(repair_json(text))
                plan.max_hops = min(max_hops, plan.max_hops)
                return plan, warnings
            except Exception as exc:
                warnings.append(f"LLM query planning failed; lexical plan used: {exc}")
        keywords = sorted(_tokens(query), key=lambda item: (-len(item), item))[:20]
        return QueryPlan(keywords=keywords, max_hops=max_hops), warnings

    async def search(
        self,
        query: str,
        *,
        backend: Literal["graph", "hybrid", "chunk"] = "hybrid",
        top_k: int = 8,
        max_hops: int = 2,
        source_id: str | None = None,
        document_hash: str | None = None,
        allowed_source_ids: set[str] | None = None,
        index_dir=None,
    ) -> EvidencePack:
        """Return ranked citable evidence from graph, chunk, or hybrid retrieval."""
        query = str(query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        top_k = max(1, min(int(top_k), 30))
        if allowed_source_ids is not None:
            allowed_source_ids = {
                str(item).upper() for item in allowed_source_ids if str(item).strip()
            }
        if (
            source_id
            and allowed_source_ids is not None
            and source_id.upper() not in allowed_source_ids
        ):
            raise ValueError(f"source_id is outside the approved evidence scope: {source_id}")
        plan, warnings = await self.plan_query(query, max_hops=max_hops)
        claim_hits = (
            self._claim_search(
                query,
                plan,
                top_k=self.claim_candidate_pool,
                source_id=source_id,
                document_hash=document_hash,
                allowed_source_ids=allowed_source_ids,
            )
            if backend in {"graph", "hybrid"}
            else []
        )
        graph_hits: list[EvidenceHit] = []
        if backend in {"graph", "hybrid"}:
            graph_hits = self._graph_search(
                query,
                plan,
                top_k=top_k * 3,
                source_id=source_id,
                document_hash=document_hash,
                allowed_source_ids=allowed_source_ids,
            )
            if not graph_hits and not claim_hits:
                warnings.append("Claim and graph retrieval returned no evidence; BM25 fallback was used.")
        chunk_hits: list[EvidenceHit] = []
        if backend in {"chunk", "hybrid"} or not (graph_hits or claim_hits):
            chunk_hits = self._chunk_search(
                query,
                top_k=top_k * 2,
                source_id=source_id,
                document_hash=document_hash,
                allowed_source_ids=allowed_source_ids,
                index_dir=index_dir,
            )
        query_edges = await self._build_query_edges(query, plan, claim_hits, warnings)
        merged: dict[tuple[str | None, str], EvidenceHit] = {}
        for hit in [*claim_hits, *graph_hits, *chunk_hits]:
            key = (hit.source_id, hit.chunk_id)
            if key not in merged:
                merged[key] = hit
                continue
            current = merged[key]
            current.score = max(current.score, hit.score) + 0.15
            current.retrieval_channels = sorted(
                set(current.retrieval_channels + hit.retrieval_channels)
            )
            current.retrieval_signals.update(hit.retrieval_signals)
            current.record_ids = sorted(set(current.record_ids + hit.record_ids))
            summaries = {
                item["id"]: item for item in [*current.record_summaries, *hit.record_summaries]
            }
            current.record_summaries = list(summaries.values())
            current.graph_paths.extend(path for path in hit.graph_paths if path not in current.graph_paths)
            current.relation_reasons.extend(
                reason for reason in hit.relation_reasons if reason not in current.relation_reasons
            )
        self._attach_query_edges(merged, query_edges)
        ranked = sorted(
            merged.values(),
            key=lambda item: (-item.score, item.source_id or "", item.chunk_id),
        )
        selected: list[EvidenceHit] = []
        source_counts: dict[str, int] = {}
        source_total = len({hit.source_id or hit.document_id for hit in ranked})
        per_source_limit = (
            top_k
            if source_total <= 1
            else max(3, math.ceil(top_k / source_total) + 1)
        )
        for hit in ranked:
            source_key = hit.source_id or hit.document_id
            if source_counts.get(source_key, 0) >= per_source_limit:
                continue
            selected.append(hit)
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            if len(selected) >= top_k:
                break
        candidate_record_ids = sorted(
            {record_id for hit in claim_hits for record_id in hit.record_ids}
        )
        active_record_ids = sorted(
            {record_id for hit in selected for record_id in hit.record_ids}
        )
        subquestion_gaps = self._subquestion_gaps(plan, selected)
        trace_payload = json.dumps(
            {
                "query": query,
                "plan": plan.model_dump(mode="json"),
                "candidates": candidate_record_ids,
                "active": active_record_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return EvidencePack(
            query=query,
            backend=backend,
            hits=selected,
            query_plan=plan,
            query_edges=query_edges,
            candidate_record_ids=candidate_record_ids,
            active_record_ids=active_record_ids,
            retrieval_rounds=[
                RetrievalRound(
                    round_index=0,
                    query=query,
                    candidate_record_ids=candidate_record_ids,
                    new_record_ids=candidate_record_ids,
                    channels=sorted(
                        {channel for hit in [*claim_hits, *graph_hits, *chunk_hits] for channel in hit.retrieval_channels}
                    ),
                    gaps=subquestion_gaps,
                )
            ],
            open_gaps=subquestion_gaps,
            verification_status="not_run",
            trace_id="trace:" + hashlib.sha256(trace_payload.encode("utf-8")).hexdigest()[:24],
            warnings=warnings,
        )

    @staticmethod
    def _subquestion_gaps(plan: QueryPlan, selected: list[EvidenceHit]) -> list[dict[str, Any]]:
        """Flag planned subquestions with no lexical support in active evidence."""
        active_text = " ".join(
            [
                *(hit.quote + " " + hit.passage_text for hit in selected),
                *(
                    str(summary.get("text") or "")
                    for hit in selected
                    for summary in hit.record_summaries
                ),
            ]
        )
        active_tokens = _tokens(active_text)
        gaps: list[dict[str, Any]] = []
        for index, subquestion in enumerate(plan.subquestions):
            question = str(subquestion.get("question") or "").strip()
            keywords = [str(item) for item in subquestion.get("keywords", [])]
            required = _tokens(" ".join([question, *keywords]))
            if required and not (required & active_tokens):
                gaps.append(
                    {
                        "subquestion_id": str(subquestion.get("id") or f"sq-{index + 1}"),
                        "kind": "missing_active_evidence",
                        "question": question,
                        "required_evidence_types": list(
                            subquestion.get("required_evidence_types") or []
                        ),
                    }
                )
        return gaps

    def _claim_search(
        self,
        query: str,
        plan: QueryPlan,
        *,
        top_k: int,
        source_id: str | None,
        document_hash: str | None,
        allowed_source_ids: set[str] | None,
    ) -> list[EvidenceHit]:
        """Retrieve atomic records directly with a deliberately wide candidate pool."""
        query_terms = sorted(_tokens(query), key=lambda item: (-len(item), item))
        subquestion_terms = [
            str(value)
            for subquestion in plan.subquestions
            for value in [
                subquestion.get("question", ""),
                *(subquestion.get("keywords") or []),
            ]
            if str(value).strip()
        ]
        terms = list(
            dict.fromkeys([*plan.entity_names, *plan.keywords, *subquestion_terms, *query_terms])
        )[:150]
        rows = self.store.search_record_candidates(
            terms,
            record_types={item.value for item in plan.record_types} or None,
            domains=set(plan.domains) or None,
            source_id=source_id,
            document_hash=document_hash,
            allowed_source_ids=allowed_source_ids,
            limit=top_k,
        )
        hits: list[EvidenceHit] = []
        for row in rows:
            metadata = json.loads(row.get("metadata_json") or "{}")
            summary = {
                "id": row["id"],
                "record_type": row["record_type"],
                "text": row["text"],
                "domain": row["domain"],
                "epistemic_status": row["epistemic_status"],
                "polarity": row["polarity"],
                "assertion_kind": row["assertion_kind"],
                "proposition": json.loads(row["proposition_json"] or "{}"),
                "qualifiers": json.loads(row["qualifiers_json"] or "{}"),
                "canonical_proposition_id": row["canonical_proposition_id"],
                "origin_study_id": row["origin_study_id"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "recorded_at": row["recorded_at"],
                "materiality": row["materiality"],
                "attributes": json.loads(row["attributes_json"]),
                "review_status": row["review_status"],
                "entity_names": row["entity_names"],
                "evidence_id": row["evidence_id"],
                "source_id": row["source_id"],
                "reporting_period": row["reporting_period"],
                "quote": row["quote"],
            }
            hits.append(
                EvidenceHit(
                    evidence_id=row["evidence_id"],
                    passage_id=row["passage_id"],
                    document_id=row["document_id"],
                    source_id=row["source_id"],
                    title=row["title"],
                    publisher=row["publisher"],
                    source_url=row["source_url"],
                    source_type=metadata.get("source_type"),
                    published_at=row["published_at"],
                    retrieved_at=row["retrieved_at"],
                    reporting_period=row["reporting_period"],
                    reliability_tier=metadata.get("reliability_tier"),
                    limitations=list(metadata.get("limitations") or []),
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    chunk_id=row["chunk_id"],
                    quote=row["quote"],
                    passage_text=row["passage_text"],
                    record_ids=[row["id"]],
                    record_summaries=[summary],
                    graph_paths=[[row["id"]]],
                    score=2.0 + float(row["retrieval_score"]),
                    retrieval_channels=["claim"],
                    retrieval_signals=row["retrieval_signals"],
                )
            )
        return hits

    async def _build_query_edges(
        self,
        query: str,
        plan: QueryPlan,
        claim_hits: list[EvidenceHit],
        warnings: list[str],
    ) -> list[QueryGraphEdge]:
        """Classify semantic edges only inside the records recalled for this query."""
        records: dict[str, dict[str, Any]] = {}
        record_scores: dict[str, float] = {}
        for hit in sorted(claim_hits, key=lambda item: (-item.score, item.evidence_id)):
            for summary in hit.record_summaries:
                records.setdefault(summary["id"], summary)
                record_scores[summary["id"]] = max(
                    record_scores.get(summary["id"], 0.0), hit.score
                )
        ranked_ids = sorted(records, key=lambda item: (-record_scores[item], item))[
            : self.query_relation_record_limit
        ]
        if len(ranked_ids) < 2:
            return []

        deterministic: list[QueryGraphEdge] = []
        pair_payloads: list[dict[str, Any]] = []
        comparison_intent = bool(
            plan.relation_types
            or any(marker in query for marker in ("比较", "对比", "冲突", "差异", "变化", "更新"))
        )
        for left_id, right_id in combinations(ranked_ids, 2):
            left = records[left_id]
            right = records[right_id]
            shared_entities = sorted(
                set(left.get("entity_names") or []) & set(right.get("entity_names") or [])
            )
            left_attrs = left.get("attributes") or {}
            right_attrs = right.get("attributes") or {}
            shared_dimensions = [
                key
                for key in ("object", "subject", "metric", "event_type", "predicate")
                if left_attrs.get(key) is not None
                and left_attrs.get(key) == right_attrs.get(key)
            ]
            same_evidence = (
                left.get("source_id") == right.get("source_id")
                and left.get("evidence_id") == right.get("evidence_id")
            )
            same_origin_study = bool(
                left.get("origin_study_id")
                and left.get("origin_study_id") == right.get("origin_study_id")
            )
            same_origin = same_evidence or same_origin_study
            if same_origin:
                cache_key = self._edge_cache_key(left, right, "SAME_ORIGIN_AS")
                deterministic.append(
                    QueryGraphEdge(
                        source_id=left_id,
                        target_id=right_id,
                        relation="SAME_ORIGIN_AS",
                        confidence=1.0,
                        reason=(
                            "Records are grounded in the same evidence span."
                            if same_evidence
                            else "Records report the same explicitly identified origin study."
                        ),
                        comparability={
                            "same_evidence_id": same_evidence,
                            "same_origin_study": same_origin_study,
                        },
                        query_relevance=min(
                            1.0, min(record_scores[left_id], record_scores[right_id]) / 10
                        ),
                        context_compatible=True,
                        edge_role="exploration",
                        edge_evidence_ids=sorted(
                            {left.get("evidence_id"), right.get("evidence_id")} - {None}
                        ),
                        cache_key=cache_key,
                        model_name=None,
                    )
                )
            if not (shared_entities or shared_dimensions or same_origin):
                continue
            pair_payloads.append(
                {
                    "left_id": left_id,
                    "right_id": right_id,
                    "left": left,
                    "right": right,
                    "shared_entities": shared_entities,
                    "shared_dimensions": shared_dimensions,
                    "comparison_intent": comparison_intent,
                }
            )
            if len(pair_payloads) >= 100:
                break
        if not pair_payloads or self.llm is None:
            return deterministic
        try:
            response = await asyncio.wait_for(
                self.llm.generate(
                    [
                        {"role": "system", "content": QUERY_RELATION_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"query": query, "pairs": pair_payloads}, ensure_ascii=False
                            ),
                        },
                    ],
                    include_stop_string=False,
                    temperature=0,
                    max_tokens=8192,
                    response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "disabled"}},
                ),
                timeout=self.planner_timeout_seconds,
            )
            parsed = repair_json(
                re.sub(r"^```(?:json)?\s*|\s*```$", "", str(response or "").strip(), flags=re.I)
            )
        except Exception as exc:
            warnings.append(f"Query-time relation construction failed; claims were retained: {exc}")
            return deterministic
        allowed_pairs = {
            (item["left_id"], item["right_id"]): item for item in pair_payloads
        }
        edges = list(deterministic)
        seen = {(edge.source_id, edge.target_id, edge.relation.value) for edge in edges}
        for payload in parsed.get("relations", []) if isinstance(parsed, dict) else []:
            try:
                relation = EvidenceRelation.model_validate(payload)
            except Exception:
                continue
            pair = (relation.left_id, relation.right_id)
            reverse = (relation.right_id, relation.left_id)
            pair_context = allowed_pairs.get(pair) or allowed_pairs.get(reverse)
            if pair_context is None or relation.confidence < self.relation_confidence_threshold:
                continue
            if plan.relation_types and relation.relation not in set(plan.relation_types):
                continue
            if not self._query_relation_allowed(relation, pair_context, comparison_intent):
                continue
            key = (relation.left_id, relation.right_id, relation.relation.value)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                QueryGraphEdge(
                    source_id=relation.left_id,
                    target_id=relation.right_id,
                    relation=relation.relation,
                    confidence=relation.confidence,
                    reason=relation.reason,
                    comparability=relation.comparability,
                    query_relevance=min(
                        1.0,
                        min(
                            record_scores.get(relation.left_id, 0.0),
                            record_scores.get(relation.right_id, 0.0),
                        )
                        / 10,
                    ),
                    context_compatible=True,
                    verified=False,
                    edge_role=self._edge_role(relation.relation.value),
                    edge_evidence_ids=sorted(
                        {
                            pair_context["left"].get("evidence_id"),
                            pair_context["right"].get("evidence_id"),
                        }
                        - {None}
                    ),
                    cache_key=self._edge_cache_key(
                        pair_context["left"], pair_context["right"], relation.relation.value
                    ),
                    model_name=self.model_name,
                )
            )
        return edges

    def _edge_cache_key(
        self, left: dict[str, Any], right: dict[str, Any], relation: str
    ) -> str:
        """Version a stable relation independently from its query-specific utility."""
        payload = json.dumps(
            {
                "left": left.get("canonical_proposition_id") or left.get("id"),
                "right": right.get("canonical_proposition_id") or right.get("id"),
                "relation": relation,
                "model": self.model_name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return "edge-cache:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _edge_role(relation: str) -> str:
        if relation in {"ENTAILS", "SUPPORTS", "CONTRADICTS"}:
            return "proof"
        if relation in {"COMPARES", "INCOMPARABLE_WITH", "EQUIVALENT_TO"}:
            return "comparison"
        if relation in {"TEMPORAL_PRECEDES", "SUPERSEDES"}:
            return "temporal"
        return "exploration"

    @staticmethod
    def _query_relation_allowed(
        relation: EvidenceRelation,
        pair: dict[str, Any],
        comparison_intent: bool,
    ) -> bool:
        """Apply conservative deterministic gates after the model classification."""
        left = pair["left"]
        right = pair["right"]
        left_attrs = left.get("attributes") or {}
        right_attrs = right.get("attributes") or {}
        shared_entities = bool(pair.get("shared_entities"))
        shared_dimensions = set(pair.get("shared_dimensions") or [])
        if relation.relation.value == "SAME_ORIGIN_AS":
            return (
                (
                    left.get("source_id") == right.get("source_id")
                    and left.get("evidence_id") == right.get("evidence_id")
                )
                or bool(
                    left.get("origin_study_id")
                    and left.get("origin_study_id") == right.get("origin_study_id")
                )
            )
        if relation.relation.value == "EQUIVALENT_TO":
            required = ["object", "metric"]
            if not all(
                left_attrs.get(key) is not None
                and left_attrs.get(key) == right_attrs.get(key)
                for key in required
            ):
                return False
            for key in ("conditions", "unit", "timepoint", "reporting_period"):
                if left_attrs.get(key) is not None and right_attrs.get(key) is not None:
                    if left_attrs.get(key) != right_attrs.get(key):
                        return False
            return left_attrs.get("value") == right_attrs.get("value")
        if relation.relation.value == "SUPERSEDES":
            text = f"{left.get('text', '')} {right.get('text', '')}"
            update_signal = any(
                marker in text
                for marker in ("更新", "更正", "撤回", "替代", "不再", "终止", "变更为", "修订")
            )
            stable_key = bool(shared_entities or {"object", "subject"} & shared_dimensions)
            return update_signal and stable_key
        if relation.relation.value == "SUPPORTS":
            source_type = left.get("record_type")
            target_type = right.get("record_type")
            return shared_entities and not (source_type == target_type == "Event")
        if relation.relation.value == "INCOMPARABLE_WITH":
            return comparison_intent and bool(shared_entities or shared_dimensions)
        if relation.relation.value == "CONTRADICTS":
            return comparison_intent and bool({"metric", "event_type", "predicate"} & shared_dimensions)
        if relation.relation.value == "SUPPLEMENTS":
            return bool(shared_entities or {"object", "subject"} & shared_dimensions)
        if relation.relation.value == "ENTAILS":
            # Entailment is intentionally stricter than support: known qualifiers
            # may not conflict, and the pair must share a semantic key.
            left_qualifiers = left.get("qualifiers") or {}
            right_qualifiers = right.get("qualifiers") or {}
            conflict = any(
                left_qualifiers.get(key) is not None
                and right_qualifiers.get(key) is not None
                and left_qualifiers.get(key) != right_qualifiers.get(key)
                for key in set(left_qualifiers) & set(right_qualifiers)
            )
            return not conflict and bool(shared_entities and shared_dimensions)
        if relation.relation.value in {"QUALIFIES", "ELABORATES", "RELATED"}:
            return bool(shared_entities or shared_dimensions)
        if relation.relation.value == "COMPARES":
            return comparison_intent and bool(shared_entities or shared_dimensions)
        if relation.relation.value == "PREREQUISITE":
            return bool(shared_entities and shared_dimensions)
        if relation.relation.value == "TEMPORAL_PRECEDES":
            left_time = left.get("valid_from") or (left_attrs.get("event_time"))
            right_time = right.get("valid_from") or (right_attrs.get("event_time"))
            return bool(shared_entities and left_time and right_time and left_time < right_time)
        return False

    @staticmethod
    def _attach_query_edges(
        merged: dict[tuple[str | None, str], EvidenceHit],
        edges: list[QueryGraphEdge],
    ) -> None:
        """Attach query-scoped paths and rationale to the citable hits they connect."""
        by_record: dict[str, EvidenceHit] = {}
        for hit in merged.values():
            for record_id in hit.record_ids:
                by_record.setdefault(record_id, hit)
        for edge in edges:
            path = [edge.source_id, edge.target_id]
            for record_id in (edge.source_id, edge.target_id):
                hit = by_record.get(record_id)
                if hit is None:
                    continue
                if path not in hit.graph_paths:
                    hit.graph_paths.append(path)
                if edge.reason not in hit.relation_reasons:
                    hit.relation_reasons.append(edge.reason)
                hit.score += min(edge.confidence, 1.0) * 0.75

    def _graph_search(
        self,
        query: str,
        plan: QueryPlan,
        top_k: int,
        source_id: str | None = None,
        document_hash: str | None = None,
        allowed_source_ids: set[str] | None = None,
    ) -> list[EvidenceHit]:
        terms = [*plan.entity_names, *plan.keywords]
        if not terms:
            terms = sorted(_tokens(query))
        # Full filings contain many repeated issuer and metric mentions. Twenty
        # lexical seeds was too narrow: summary pages could be crowded out by
        # detailed tables before period-aware evidence scoring ran.
        seed_ids = self.store.search_seed_ids(terms, limit=max(60, top_k * 4))
        if not seed_ids:
            return []
        if self._graph_projection is None:
            # A configured retriever is created after ingest/build and reused for the
            # whole collection run. Rebuilding ~20k graph objects for every query was
            # the dominant retrieval cost on full PDFs.
            self._graph_projection = self.store.to_networkx()
        graph = self._graph_projection
        allowed: dict[str, tuple[int, list[str]]] = {}
        queue: deque[tuple[str, int, list[str]]] = deque()
        for seed in seed_ids:
            if seed in graph:
                queue.append((seed, 0, [seed]))
                allowed[seed] = (0, [seed])
        while queue:
            node, distance, path = queue.popleft()
            if distance >= plan.max_hops:
                continue
            neighbours = set(graph.successors(node)) | set(graph.predecessors(node))
            for neighbour in neighbours:
                edge_relations = {
                    data.get("relation")
                    for data in graph.get_edge_data(node, neighbour, default={}).values()
                } | {
                    data.get("relation")
                    for data in graph.get_edge_data(neighbour, node, default={}).values()
                }
                # Persisted semantic edges may come from a previous graph version.
                # Query-time construction is authoritative; seed expansion uses only
                # structural and entity-role edges.
                if edge_relations and not (edge_relations - SEMANTIC_RELATIONS):
                    continue
                if neighbour in allowed and allowed[neighbour][0] <= distance + 1:
                    continue
                next_path = [*path, neighbour]
                allowed[neighbour] = (distance + 1, next_path)
                queue.append((neighbour, distance + 1, next_path))
        raw_hits = self.store.evidence_hits_for_nodes(set(allowed))
        query_tokens = _tokens(query)
        requested_periods = _reporting_periods(query)
        count_intent = any(marker in query for marker in ("数量", "多少", "几项", "多少项"))
        hits: list[EvidenceHit] = []
        for raw in raw_hits:
            if source_id and str(raw.get("source_id") or "").upper() != source_id.upper():
                continue
            if (
                allowed_source_ids is not None
                and str(raw.get("source_id") or "").upper() not in allowed_source_ids
            ):
                continue
            if document_hash and raw.get("document_hash") != document_hash:
                continue
            records = raw.pop("records")
            metadata = json.loads(raw.get("metadata_json") or "{}")
            record_ids = sorted({record["id"] for record in records})
            paths = [allowed[item][1] for item in record_ids if item in allowed]
            distance = min((allowed[item][0] for item in record_ids if item in allowed), default=2)
            lexical = len(query_tokens & _tokens(raw["quote"] + " " + raw["passage_text"]))
            approved = sum(record["review_status"] == "approved" for record in records)
            score = 2.0 + lexical + approved * 0.5 - distance * 0.25
            if count_intent:
                evidence_text = raw["quote"] + " " + raw["passage_text"]
                score += 4.0 if "共有" in evidence_text else 0.0
                score += min(3, len(set(re.findall(r"\d+\s*项", evidence_text))))
            hit_periods = _reporting_periods(str(raw.get("reporting_period") or ""))
            if requested_periods:
                # An explicit reporting period is a hard semantic constraint for
                # issuer filings, not just another keyword. A Q1 passage can share
                # nearly every financial/clinical term with an H1 question and used
                # to outrank the requested filing on lexical density alone.
                score += 6.0 if requested_periods & hit_periods else -12.0
            if raw.get("source_id") and str(raw["source_id"]).casefold() in query.casefold():
                score += 5.0
            summaries = [
                {
                    "id": record["id"],
                    "record_type": record["record_type"],
                    "text": record["text"],
                    "domain": record["domain"],
                    "epistemic_status": record["epistemic_status"],
                    "polarity": record["polarity"],
                    "assertion_kind": record.get("assertion_kind") or "unknown",
                    "proposition": json.loads(record.get("proposition_json") or "{}"),
                    "qualifiers": json.loads(record.get("qualifiers_json") or "{}"),
                    "canonical_proposition_id": record.get("canonical_proposition_id") or "",
                    "origin_study_id": record.get("origin_study_id"),
                    "valid_from": record.get("valid_from"),
                    "valid_to": record.get("valid_to"),
                    "recorded_at": record.get("recorded_at") or "",
                    "materiality": json.loads(record.get("materiality_json") or "{}"),
                    "attributes": json.loads(record["attributes_json"]),
                    "review_status": record["review_status"],
                }
                for record in records
            ]
            relation_reasons: list[str] = []
            for path in paths:
                for left, right in zip(path, path[1:]):
                    for edge_data in (
                        *graph.get_edge_data(left, right, default={}).values(),
                        *graph.get_edge_data(right, left, default={}).values(),
                    ):
                        if edge_data.get("relation") in SEMANTIC_RELATIONS:
                            continue
                        attributes = json.loads(edge_data.get("attributes_json") or "{}")
                        reason = attributes.get("reason")
                        if reason and reason not in relation_reasons:
                            relation_reasons.append(reason)
            score += min(3, len(relation_reasons)) * 1.5
            hits.append(
                EvidenceHit(
                    evidence_id=raw["id"],
                    passage_id=raw["passage_id"],
                    document_id=raw["document_id"],
                    source_id=raw["source_id"],
                    title=raw["title"],
                    publisher=raw["publisher"],
                    source_url=raw["source_url"],
                    source_type=metadata.get("source_type"),
                    published_at=raw.get("published_at"),
                    retrieved_at=raw.get("retrieved_at"),
                    reporting_period=raw.get("reporting_period"),
                    reliability_tier=metadata.get("reliability_tier"),
                    limitations=list(metadata.get("limitations") or []),
                    page_start=raw["page_start"],
                    page_end=raw["page_end"],
                    chunk_id=raw["chunk_id"],
                    quote=raw["quote"],
                    passage_text=raw["passage_text"],
                    record_ids=record_ids,
                    record_summaries=summaries,
                    graph_paths=paths,
                    relation_reasons=relation_reasons,
                    score=score,
                    retrieval_channels=["graph"],
                )
            )
        # Multiple exact quotes from the same passage must not consume the whole
        # pre-merge top-k window; merge them before truncation so other pages and
        # relation-bearing passages remain reachable.
        merged_hits: dict[tuple[str | None, str], EvidenceHit] = {}
        for hit in sorted(hits, key=lambda item: (-item.score, item.evidence_id)):
            key = (hit.source_id, hit.chunk_id)
            current = merged_hits.get(key)
            if current is None:
                merged_hits[key] = hit
                continue
            current.score = max(current.score, hit.score)
            current.record_ids = sorted(set(current.record_ids + hit.record_ids))
            summaries = {
                item["id"]: item
                for item in [*current.record_summaries, *hit.record_summaries]
            }
            current.record_summaries = list(summaries.values())
            current.graph_paths.extend(
                path for path in hit.graph_paths if path not in current.graph_paths
            )
            current.relation_reasons.extend(
                reason
                for reason in hit.relation_reasons
                if reason not in current.relation_reasons
            )
        for hit in merged_hits.values():
            hit.graph_paths = hit.graph_paths[:20]
            hit.relation_reasons = hit.relation_reasons[:12]
        return sorted(
            merged_hits.values(), key=lambda item: (-item.score, item.evidence_id)
        )[:top_k]

    def _chunk_search(
        self,
        query: str,
        *,
        top_k: int,
        source_id: str | None,
        document_hash: str | None,
        allowed_source_ids: set[str] | None,
        index_dir: Any,
    ) -> list[EvidenceHit]:
        from src.tools.document.document_index import search_document_indexes

        if allowed_source_ids is None or source_id or document_hash:
            raw_hits = search_document_indexes(
                query,
                source_id=source_id,
                document_hash=document_hash,
                top_k=top_k,
                index_dir=index_dir,
            )
            if allowed_source_ids is not None:
                raw_hits = [
                    hit
                    for hit in raw_hits
                    if str(hit.get("source_id") or "").upper() in allowed_source_ids
                ]
        else:
            raw_hits = []
            for allowed_source_id in sorted(allowed_source_ids):
                raw_hits.extend(
                    search_document_indexes(
                        query,
                        source_id=allowed_source_id,
                        top_k=top_k,
                        index_dir=index_dir,
                    )
                )
            raw_hits.sort(key=lambda item: (-float(item.get("score") or 0), item["chunk_id"]))
            raw_hits = raw_hits[:top_k]
        results: list[EvidenceHit] = []
        for raw in raw_hits:
            chunk_id = str(raw["chunk_id"])
            evidence_id = "bm25:" + hashlib.sha256(
                f"{raw.get('document_hash')}:{chunk_id}".encode()
            ).hexdigest()[:24]
            results.append(
                EvidenceHit(
                    evidence_id=evidence_id,
                    passage_id=f"passage:{chunk_id}",
                    document_id=f"doc:{raw.get('document_hash')}",
                    source_id=raw.get("source_id"),
                    title=str(raw.get("title") or raw.get("source_id") or "indexed document"),
                    publisher=raw.get("publisher"),
                    source_url=raw.get("source_url"),
                    page_start=raw.get("page_start"),
                    page_end=raw.get("page_end"),
                    chunk_id=chunk_id,
                    quote=str(raw.get("text") or ""),
                    passage_text=str(raw.get("text") or ""),
                    score=1.0 + float(raw.get("score") or 0.0) / 10.0,
                    retrieval_channels=["bm25"],
                )
            )
        return results
