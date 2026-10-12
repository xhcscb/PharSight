"""Explainable graph and graph+BM25 evidence retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import deque
from typing import Any, Literal

from json_repair import loads as repair_json

from .models import EvidenceHit, EvidencePack, QueryPlan
from .store import GraphStore

QUERY_SYSTEM_PROMPT = """Create a conservative graph retrieval plan for a pharmaceutical
investment-research question. Return JSON only with entity_names, record_types (Claim,
Observation, Event), domains, keywords, relation_types and max_hops (0-2). Allowed domains are
clinical, financial, market, regulatory, commercial, mechanism and other. Allowed relation_types
are EQUIVALENT_TO, SUPPORTS, SUPPLEMENTS, INCOMPARABLE_WITH, CONTRADICTS, SUPERSEDES and
SAME_ORIGIN_AS. Use an empty relation_types list when no evidence relation is required. Do not
answer the question and do not invent identifiers."""


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
    ):
        self.store = store
        self.llm = llm
        self.model_name = model_name
        self.planner_timeout_seconds = max(1.0, float(planner_timeout_seconds))
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
            if not graph_hits:
                warnings.append("Graph retrieval returned no evidence; BM25 fallback was used.")
        chunk_hits: list[EvidenceHit] = []
        if backend in {"chunk", "hybrid"} or not graph_hits:
            chunk_hits = self._chunk_search(
                query,
                top_k=top_k * 2,
                source_id=source_id,
                document_hash=document_hash,
                allowed_source_ids=allowed_source_ids,
                index_dir=index_dir,
            )
        merged: dict[tuple[str | None, str], EvidenceHit] = {}
        for hit in [*graph_hits, *chunk_hits]:
            key = (hit.source_id, hit.chunk_id)
            if key not in merged:
                merged[key] = hit
                continue
            current = merged[key]
            current.score = max(current.score, hit.score) + 0.15
            current.retrieval_channels = sorted(
                set(current.retrieval_channels + hit.retrieval_channels)
            )
            current.record_ids = sorted(set(current.record_ids + hit.record_ids))
            summaries = {
                item["id"]: item for item in [*current.record_summaries, *hit.record_summaries]
            }
            current.record_summaries = list(summaries.values())
            current.graph_paths.extend(path for path in hit.graph_paths if path not in current.graph_paths)
            current.relation_reasons.extend(
                reason for reason in hit.relation_reasons if reason not in current.relation_reasons
            )
        ranked = sorted(
            merged.values(),
            key=lambda item: (-item.score, item.source_id or "", item.chunk_id),
        )
        selected: list[EvidenceHit] = []
        source_counts: dict[str, int] = {}
        for hit in ranked:
            source_key = hit.source_id or hit.document_id
            if source_counts.get(source_key, 0) >= 3:
                continue
            selected.append(hit)
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            if len(selected) >= top_k:
                break
        return EvidencePack(
            query=query,
            backend=backend,
            hits=selected,
            query_plan=plan,
            warnings=warnings,
        )

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
                if plan.relation_types:
                    structural = {"CONTAINS", "YIELDS", "SUPPORTS_RECORD"}
                    semantic_relations = {
                        "EQUIVALENT_TO",
                        "SUPPORTS",
                        "SUPPLEMENTS",
                        "INCOMPARABLE_WITH",
                        "CONTRADICTS",
                        "SUPERSEDES",
                        "SAME_ORIGIN_AS",
                    }
                    allowed_relations = structural | set(plan.relation_types)
                    edge_relations = {
                        data.get("relation")
                        for data in graph.get_edge_data(node, neighbour, default={}).values()
                    } | {
                        data.get("relation")
                        for data in graph.get_edge_data(neighbour, node, default={}).values()
                    }
                    # Entity-role edges are necessary to reach records from an entity
                    # seed. Only filter out unrequested members of the seven global
                    # semantic relation types; keep structural and role edges traversable.
                    role_relations = edge_relations - semantic_relations
                    if edge_relations and not edge_relations & (allowed_relations | role_relations):
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
                        attributes = json.loads(edge_data.get("attributes_json") or "{}")
                        reason = attributes.get("reason")
                        if reason and reason not in relation_reasons:
                            relation_reasons.append(reason)
            # A matching record is often selected as a distance-zero seed. In that
            # case the shortest seed path contains no edge even though the record has
            # an explicitly requested semantic relation. Surface those incident edges
            # so comparison/contradiction queries retain their relationship rationale.
            if plan.relation_types:
                requested_relations = set(plan.relation_types)
                for record_id in record_ids:
                    for neighbour in set(graph.successors(record_id)) | set(
                        graph.predecessors(record_id)
                    ):
                        for left, right in ((record_id, neighbour), (neighbour, record_id)):
                            for edge_data in graph.get_edge_data(left, right, default={}).values():
                                if edge_data.get("relation") not in requested_relations:
                                    continue
                                relation_path = [left, right]
                                if relation_path not in paths:
                                    paths.append(relation_path)
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
