"""Explainable graph and graph+BM25 evidence retrieval."""

from __future__ import annotations

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
Observation, Event), domains, keywords, relation_types and max_hops (0-2). Do not answer the
question and do not invent identifiers."""


def _tokens(value: str) -> set[str]:
    latin = re.findall(r"[a-z0-9][a-z0-9._-]*", value.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    return set(latin + chinese)


class GraphRetriever:
    """Retrieve graph evidence and always return citable passage text."""

    def __init__(self, store: GraphStore, llm: Any | None = None, model_name: str | None = None):
        self.store = store
        self.llm = llm
        self.model_name = model_name

    async def plan_query(self, query: str, max_hops: int = 2) -> tuple[QueryPlan, list[str]]:
        """Produce a bounded query plan, falling back to deterministic lexical terms."""
        warnings: list[str] = []
        if self.llm is not None:
            try:
                response = await self.llm.generate(
                    [
                        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
                        {"role": "user", "content": query},
                    ],
                    include_stop_string=False,
                    temperature=0,
                    max_tokens=1200,
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
        index_dir=None,
    ) -> EvidencePack:
        """Return ranked citable evidence from graph, chunk, or hybrid retrieval."""
        query = str(query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        top_k = max(1, min(int(top_k), 30))
        plan, warnings = await self.plan_query(query, max_hops=max_hops)
        graph_hits: list[EvidenceHit] = []
        if backend in {"graph", "hybrid"}:
            graph_hits = self._graph_search(
                query,
                plan,
                top_k=top_k * 3,
                source_id=source_id,
                document_hash=document_hash,
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
            current.graph_paths.extend(path for path in hit.graph_paths if path not in current.graph_paths)
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
    ) -> list[EvidenceHit]:
        terms = [*plan.entity_names, *plan.keywords]
        if not terms:
            terms = sorted(_tokens(query))
        seed_ids = self.store.search_seed_ids(terms, limit=max(20, top_k))
        if not seed_ids:
            return []
        graph = self.store.to_networkx()
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
                    allowed_relations = structural | set(plan.relation_types)
                    edge_relations = {
                        data.get("relation")
                        for data in graph.get_edge_data(node, neighbour, default={}).values()
                    } | {
                        data.get("relation")
                        for data in graph.get_edge_data(neighbour, node, default={}).values()
                    }
                    if edge_relations and not edge_relations & allowed_relations:
                        continue
                if neighbour in allowed and allowed[neighbour][0] <= distance + 1:
                    continue
                next_path = [*path, neighbour]
                allowed[neighbour] = (distance + 1, next_path)
                queue.append((neighbour, distance + 1, next_path))
        raw_hits = self.store.evidence_hits_for_nodes(set(allowed))
        query_tokens = _tokens(query)
        hits: list[EvidenceHit] = []
        for raw in raw_hits:
            if source_id and str(raw.get("source_id") or "").upper() != source_id.upper():
                continue
            if document_hash and raw.get("document_hash") != document_hash:
                continue
            records = raw.pop("records")
            record_ids = sorted({record["id"] for record in records})
            paths = [allowed[item][1] for item in record_ids if item in allowed]
            distance = min((allowed[item][0] for item in record_ids if item in allowed), default=2)
            lexical = len(query_tokens & _tokens(raw["quote"] + " " + raw["passage_text"]))
            approved = sum(record["review_status"] == "approved" for record in records)
            score = 2.0 + lexical + approved * 0.5 - distance * 0.25
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
            hits.append(
                EvidenceHit(
                    evidence_id=raw["id"],
                    passage_id=raw["passage_id"],
                    document_id=raw["document_id"],
                    source_id=raw["source_id"],
                    title=raw["title"],
                    publisher=raw["publisher"],
                    source_url=raw["source_url"],
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
        return sorted(hits, key=lambda item: (-item.score, item.evidence_id))[:top_k]

    def _chunk_search(
        self,
        query: str,
        *,
        top_k: int,
        source_id: str | None,
        document_hash: str | None,
        index_dir: Any,
    ) -> list[EvidenceHit]:
        from src.tools.document.document_index import search_document_indexes

        raw_hits = search_document_indexes(
            query,
            source_id=source_id,
            document_hash=document_hash,
            top_k=top_k,
            index_dir=index_dir,
        )
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
