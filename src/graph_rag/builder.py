"""Incremental LLM-assisted construction of the three-layer evidence graph."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from json_repair import loads as repair_json

from .models import (
    BuildReport,
    DocumentNode,
    EvidenceSpan,
    ExtractionResponse,
    GraphBundle,
    GraphEdge,
    GraphEntity,
    GraphRecord,
    PassageNode,
    RelationResponse,
)
from .store import GraphStore

EXTRACTION_SYSTEM_PROMPT = """You extract auditable pharmaceutical investment-research records.
Return JSON only. Never add facts that are not present in the passage. Preserve negation,
uncertainty, units, dose, population, timepoint, estimand, reporting period and time precision.
Use one of Claim, Observation, Event. Observation attributes must retain object, conditions,
metric, value and value_nature when present. Event attributes must retain subject, event_type,
event_time, disclosure_time, jurisdiction, indication, prior_status and new_status when present.
Claim attributes may retain predicate, qualifiers, forecast target and conditions.
Allowed entity types: Company, Security, Drug, Program, Brand, Target, Modality, Indication,
Trial, Regulator, Policy, Payer, MarketSegment, TechnologyPlatform, Transaction.
Every record needs an exact, contiguous evidence_quote copied from the passage. Unknown values
must remain absent or null. Output: {"records": [{"record_type": ..., "text": ...,
"domain": ..., "epistemic_status": ..., "polarity": ..., "attributes": {},
"evidence_quote": ..., "entities": [{"name": ..., "entity_type": ..., "role": ...,
"aliases": [], "external_ids": {}, "confidence": 0.0}]}]}"""

RELATION_SYSTEM_PROMPT = """Classify only meaningful relationships between the supplied
pharmaceutical investment-research record pairs. Return JSON only. Allowed relations are
EQUIVALENT_TO, SUPPORTS, SUPPLEMENTS, INCOMPARABLE_WITH, CONTRADICTS, SUPERSEDES,
SAME_ORIGIN_AS. Comparability must precede contradiction: differing trial, population, dose,
endpoint, timepoint, estimand, reporting period, scope, currency or unit normally means
INCOMPARABLE_WITH unless a deterministic alignment is explicit. A later status update is
SUPERSEDES, not CONTRADICTS. Omit unrelated or uncertain pairs. Do not invent evidence.
Output: {"relations": [{"left_id": ..., "right_id": ..., "relation": ...,
"confidence": 0.0, "reason": ..., "comparability": {}}]}"""

ENTITY_RESOLUTION_SYSTEM_PROMPT = """Resolve extracted pharmaceutical entities conservatively.
Return JSON only. Match a candidate only when the passage and identifiers show the same real
entity; a similar name is insufficient. Preserve Company versus Security and Drug versus Program
versus Brand. If uncertain, use matched_entity_id=null, resolution_status=needs_review, and keep
the extracted name. Output: {"entities": [{"input_index": 0, "canonical_name": ...,
"matched_entity_id": null, "resolution_status": "resolved|temporary|needs_review",
"reason": ...}]}"""


def stable_id(prefix: str, *parts: Any) -> str:
    """Create a reproducible, namespaced identifier from canonical JSON parts."""
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _parse_json_payload(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    return repair_json(text)


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s\-_（）()]+", "", value).casefold()


class GraphBuilder:
    """Build document graph bundles with the configured primary language model."""

    def __init__(
        self,
        store: GraphStore,
        llm: Any,
        model_name: str,
        *,
        relation_candidate_limit: int = 100,
        relation_confidence_threshold: float = 0.7,
    ):
        self.store = store
        self.llm = llm
        self.model_name = model_name
        self.relation_candidate_limit = max(1, int(relation_candidate_limit))
        self.relation_confidence_threshold = min(1.0, max(0.0, float(relation_confidence_threshold)))

    async def _generate_json(self, system_prompt: str, payload: dict[str, Any]) -> Any:
        response = await self.llm.generate(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            include_stop_string=False,
            temperature=0,
            max_tokens=8192,
        )
        return _parse_json_payload(response)

    async def build_document(
        self,
        document_index: dict[str, Any],
        *,
        as_of: str | None = None,
        force: bool = False,
    ) -> BuildReport:
        """Extract one indexed document and atomically replace its graph bundle."""
        document_hash = str(document_index.get("document_hash") or "").strip()
        if not document_hash:
            raise ValueError("document_index requires document_hash")
        document_id = stable_id("doc", document_hash)
        if not force and self.store.is_current(document_hash, self.model_name):
            return BuildReport(
                document_id=document_id,
                document_hash=document_hash,
                status="reused",
                model_name=self.model_name,
            )

        run_id = self.store.start_build(document_id, document_hash, self.model_name)
        errors: list[str] = []
        try:
            bundle = await self._extract_bundle(document_index, as_of=as_of, errors=errors)
            self.store.replace_document_bundle(bundle)
            relation_count = await self.precompute_relations()
            self.store.finish_build(run_id, "built", "\n".join(errors) or None)
            return BuildReport(
                document_id=document_id,
                document_hash=document_hash,
                status="built",
                passage_count=len(bundle.passages),
                evidence_count=len(bundle.evidence),
                record_count=len(bundle.records),
                entity_count=len(bundle.entities),
                edge_count=len(bundle.edges),
                relation_count=relation_count,
                errors=errors,
                model_name=self.model_name,
            )
        except Exception as exc:
            self.store.finish_build(run_id, "failed", str(exc))
            return BuildReport(
                document_id=document_id,
                document_hash=document_hash,
                status="failed",
                errors=[*errors, str(exc)],
                model_name=self.model_name,
            )

    async def build_evidence_record(self, payload: dict[str, Any], *, as_of: str | None = None) -> BuildReport:
        """Normalize a non-indexed manifest/web record into one page-aware passage."""
        if payload.get("cutoff_eligible") is False:
            source_id = str(payload.get("source_id") or "ineligible")
            return BuildReport(
                document_id=stable_id("doc", source_id),
                document_hash=str(payload.get("document_hash") or stable_id("hash", source_id)),
                status="reused",
                errors=["Evidence was excluded by the report cutoff."],
                model_name=self.model_name,
            )
        content = str(payload.get("content") or "").strip()
        if not content:
            raise ValueError("Evidence record requires content")
        document_hash = str(payload.get("document_hash") or hashlib.sha256(content.encode()).hexdigest())
        return await self.build_document(
            {
                "document_hash": document_hash,
                "source_id": payload.get("source_id"),
                "source_url": payload.get("url") or payload.get("final_url"),
                "publisher": payload.get("publisher"),
                "title": payload.get("title") or payload.get("source_id") or "evidence",
                "published_at": payload.get("published_at"),
                "retrieved_at": payload.get("retrieved_at"),
                "reporting_period": payload.get("reporting_period"),
                "chunks": [
                    {
                        "chunk_id": f"{document_hash[:12]}:p0001:c001",
                        "page_start": 1,
                        "page_end": 1,
                        "section_title": payload.get("locator"),
                        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                        "text": content,
                    }
                ],
                "metadata": {
                    "source_type": payload.get("source_type"),
                    "reliability_tier": payload.get("reliability_tier"),
                    "limitations": payload.get("limitations") or [],
                },
            },
            as_of=as_of,
        )

    async def _extract_bundle(
        self,
        document_index: dict[str, Any],
        *,
        as_of: str | None,
        errors: list[str],
    ) -> GraphBundle:
        document_hash = str(document_index["document_hash"])
        document_id = stable_id("doc", document_hash)
        document = DocumentNode(
            id=document_id,
            document_hash=document_hash,
            source_id=document_index.get("source_id"),
            title=str(document_index.get("title") or document_index.get("source_url") or document_hash),
            publisher=document_index.get("publisher"),
            source_url=document_index.get("source_url"),
            published_at=document_index.get("published_at"),
            retrieved_at=document_index.get("retrieved_at"),
            reporting_period=document_index.get("reporting_period"),
            as_of=as_of,
            metadata=dict(document_index.get("metadata") or {}),
        )
        passages: list[PassageNode] = []
        evidence: list[EvidenceSpan] = []
        records: list[GraphRecord] = []
        entity_by_id: dict[str, GraphEntity] = {}
        edges: list[GraphEdge] = []

        for chunk in document_index.get("chunks") or []:
            text = str(chunk.get("text") or "")
            if not text.strip():
                continue
            chunk_id = str(chunk.get("chunk_id") or stable_id("chunk", document_hash, text))
            passage_id = stable_id("passage", document_hash, chunk_id)
            passages.append(
                PassageNode(
                    id=passage_id,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=text,
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end"),
                    section_title=chunk.get("section_title"),
                    content_hash=str(chunk.get("content_hash") or hashlib.sha256(text.encode()).hexdigest()),
                )
            )
            edges.append(
                GraphEdge(
                    id=stable_id("edge", document_id, passage_id, "CONTAINS"),
                    source_id=document_id,
                    target_id=passage_id,
                    relation="CONTAINS",
                    origin="structural",
                    review_status="approved",
                )
            )
            try:
                raw = await self._generate_json(
                    EXTRACTION_SYSTEM_PROMPT,
                    {
                        "document": {
                            "title": document.title,
                            "publisher": document.publisher,
                            "published_at": document.published_at,
                            "reporting_period": document.reporting_period,
                            "as_of": as_of,
                        },
                        "passage": {"chunk_id": chunk_id, "text": text},
                    },
                )
                extraction = ExtractionResponse.model_validate(raw)
            except Exception as exc:
                errors.append(f"{chunk_id}: extraction failed: {exc}")
                continue

            for item in extraction.records:
                quote = item.evidence_quote
                start = text.find(quote)
                if start < 0:
                    errors.append(f"{chunk_id}: rejected record because evidence quote was not found")
                    continue
                evidence_id = stable_id("evidence", passage_id, start, quote)
                record_id = stable_id(
                    "record",
                    item.record_type.value,
                    item.text,
                    item.attributes,
                    evidence_id,
                )
                evidence.append(
                    EvidenceSpan(
                        id=evidence_id,
                        passage_id=passage_id,
                        quote=quote,
                        start_char=start,
                        end_char=start + len(quote),
                        field_paths=["text", "attributes"],
                    )
                )
                records.append(
                    GraphRecord(
                        id=record_id,
                        record_type=item.record_type,
                        text=item.text,
                        domain=item.domain,
                        epistemic_status=item.epistemic_status,
                        polarity=item.polarity,
                        attributes=item.attributes,
                        evidence_ids=[evidence_id],
                        model_name=self.model_name,
                    )
                )
                edges.extend(
                    [
                        GraphEdge(
                            id=stable_id("edge", passage_id, evidence_id, "YIELDS"),
                            source_id=passage_id,
                            target_id=evidence_id,
                            relation="YIELDS",
                            origin="structural",
                            evidence_ids=[evidence_id],
                            review_status="approved",
                        ),
                        GraphEdge(
                            id=stable_id("edge", evidence_id, record_id, "SUPPORTS_RECORD"),
                            source_id=evidence_id,
                            target_id=record_id,
                            relation="SUPPORTS_RECORD",
                            origin="structural",
                            evidence_ids=[evidence_id],
                            supporting_record_ids=[record_id],
                            review_status="approved",
                        ),
                    ]
                )
                resolved_entities = await self._resolve_entities(item.entities, text, errors)
                for extracted_entity, resolution in zip(item.entities, resolved_entities):
                    identity = extracted_entity.external_ids or {
                        "name": _normalized_name(resolution["canonical_name"])
                    }
                    entity_id = resolution.get("matched_entity_id") or stable_id(
                        "entity", extracted_entity.entity_type.value, identity
                    )
                    entity_by_id.setdefault(
                        entity_id,
                        GraphEntity(
                            id=entity_id,
                            entity_type=extracted_entity.entity_type,
                            canonical_name=resolution["canonical_name"],
                            aliases=sorted(set(extracted_entity.aliases)),
                            external_ids=extracted_entity.external_ids,
                            resolution_status=resolution["resolution_status"],
                        ),
                    )
                    relation = re.sub(r"[^A-Z0-9]+", "_", extracted_entity.role.upper()).strip("_")
                    relation = relation or "ABOUT"
                    edges.append(
                        GraphEdge(
                            id=stable_id("edge", record_id, entity_id, relation),
                            source_id=record_id,
                            target_id=entity_id,
                            relation=relation,
                            origin="extracted",
                            evidence_ids=[evidence_id],
                            supporting_record_ids=[record_id],
                            model_name=self.model_name,
                            attributes={"confidence": extracted_entity.confidence},
                        )
                    )

        return GraphBundle(
            document=document,
            passages=passages,
            evidence=evidence,
            records=records,
            entities=list(entity_by_id.values()),
            edges=edges,
        )

    async def _resolve_entities(self, entities, passage_text: str, errors: list[str]) -> list[dict[str, Any]]:
        """Use exact identifiers first and the configured LLM for ambiguous names."""
        resolved: list[dict[str, Any] | None] = [None] * len(entities)
        unresolved_payload = []
        for index, entity in enumerate(entities):
            if entity.external_ids:
                resolved[index] = {
                    "canonical_name": entity.name.strip(),
                    "matched_entity_id": None,
                    "resolution_status": "resolved",
                }
                continue
            candidates = self.store.find_entity_candidates(
                entity.entity_type.value,
                [entity.name, *entity.aliases],
            )
            unresolved_payload.append(
                {
                    "input_index": index,
                    "name": entity.name,
                    "aliases": entity.aliases,
                    "entity_type": entity.entity_type.value,
                    "role": entity.role,
                    "candidates": candidates,
                }
            )
        if unresolved_payload:
            try:
                response = await self._generate_json(
                    ENTITY_RESOLUTION_SYSTEM_PROMPT,
                    {"passage": passage_text, "entities": unresolved_payload},
                )
                by_index = {
                    int(item["input_index"]): item for item in response.get("entities", [])
                }
            except Exception as exc:
                errors.append(f"entity resolution failed; temporary IDs used: {exc}")
                by_index = {}
            for payload in unresolved_payload:
                index = payload["input_index"]
                decision = by_index.get(index, {})
                candidate_ids = {item["id"] for item in payload["candidates"]}
                matched = decision.get("matched_entity_id")
                if matched not in candidate_ids:
                    matched = None
                status = decision.get("resolution_status")
                if status not in {"resolved", "temporary", "needs_review"}:
                    status = "temporary" if not payload["candidates"] else "needs_review"
                resolved[index] = {
                    "canonical_name": str(decision.get("canonical_name") or payload["name"]).strip(),
                    "matched_entity_id": matched,
                    "resolution_status": status,
                }
        return [item for item in resolved if item is not None]

    async def precompute_relations(self) -> int:
        """Classify blocked record pairs and persist accepted global relations."""
        candidates = self.store.candidate_record_pairs(
            self.model_name, self.relation_candidate_limit
        )
        if not candidates:
            return 0
        raw = await self._generate_json(
            RELATION_SYSTEM_PROMPT,
            {"pairs": [candidate.model_dump(mode="json") for candidate in candidates]},
        )
        response = RelationResponse.model_validate(raw)
        allowed_pairs = {(item.left_id, item.right_id) for item in candidates}
        count = 0
        reviewed_pairs: set[tuple[str, str]] = set()
        for relation in response.relations:
            pair = (relation.left_id, relation.right_id)
            reverse = (relation.right_id, relation.left_id)
            if pair not in allowed_pairs and reverse not in allowed_pairs:
                continue
            if relation.confidence < self.relation_confidence_threshold:
                continue
            reviewed_pairs.add(pair if pair in allowed_pairs else reverse)
            edge_id = stable_id(
                "relation", relation.left_id, relation.right_id, relation.relation.value
            )
            self.store.add_relation_edge(
                edge_id,
                relation.left_id,
                relation.right_id,
                relation.relation.value,
                relation.confidence,
                relation.reason,
                relation.comparability,
                self.model_name,
            )
            count += 1
        for candidate in candidates:
            pair = (candidate.left_id, candidate.right_id)
            if pair not in reviewed_pairs:
                self.store.record_no_relation(
                    candidate.left_id, candidate.right_id, self.model_name
                )
        return count


def load_document_index_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
