"""Versioned runtime schema for the three-layer Pharsight evidence graph."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "pharsight-graph-v1.2"
EXTRACTOR_VERSION = "graphrag-v1.3"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordType(str, Enum):
    CLAIM = "Claim"
    OBSERVATION = "Observation"
    EVENT = "Event"


class ReviewStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class EntityType(str, Enum):
    COMPANY = "Company"
    SECURITY = "Security"
    DRUG = "Drug"
    PROGRAM = "Program"
    BRAND = "Brand"
    TARGET = "Target"
    MODALITY = "Modality"
    INDICATION = "Indication"
    TRIAL = "Trial"
    REGULATOR = "Regulator"
    POLICY = "Policy"
    PAYER = "Payer"
    MARKET_SEGMENT = "MarketSegment"
    TECHNOLOGY_PLATFORM = "TechnologyPlatform"
    TRANSACTION = "Transaction"


class RelationType(str, Enum):
    ENTAILS = "ENTAILS"
    EQUIVALENT_TO = "EQUIVALENT_TO"
    SUPPORTS = "SUPPORTS"
    SUPPLEMENTS = "SUPPLEMENTS"
    QUALIFIES = "QUALIFIES"
    ELABORATES = "ELABORATES"
    TEMPORAL_PRECEDES = "TEMPORAL_PRECEDES"
    COMPARES = "COMPARES"
    PREREQUISITE = "PREREQUISITE"
    RELATED = "RELATED"
    INCOMPARABLE_WITH = "INCOMPARABLE_WITH"
    CONTRADICTS = "CONTRADICTS"
    SUPERSEDES = "SUPERSEDES"
    SAME_ORIGIN_AS = "SAME_ORIGIN_AS"


class MaterialityProfile(StrictModel):
    """Query-independent importance signals attached to one atomic record."""

    decision_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    time_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    scope: Literal["company", "product", "trial", "market", "policy", "accounting", "other"] = "other"
    reason: str = ""
    score: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def calculate_score(self):
        # Keep the aggregate deterministic; the model supplies only auditable dimensions.
        self.score = round(
            0.35 * self.decision_impact
            + 0.25 * self.evidence_strength
            + 0.20 * self.novelty
            + 0.20 * self.time_sensitivity,
            4,
        )
        return self


class DocumentNode(StrictModel):
    id: str
    document_hash: str
    source_id: str | None = None
    title: str
    publisher: str | None = None
    source_url: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    reporting_period: str | None = None
    as_of: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PassageNode(StrictModel):
    id: str
    document_id: str
    chunk_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section_title: str | None = None
    content_hash: str


class EvidenceSpan(StrictModel):
    id: str
    passage_id: str
    quote: str
    start_char: int
    end_char: int
    field_paths: list[str] = Field(default_factory=list)

    @field_validator("quote")
    @classmethod
    def quote_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence quote must not be blank")
        return value


class ExtractedEntity(StrictModel):
    name: str
    entity_type: EntityType
    role: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedRecord(StrictModel):
    record_type: RecordType
    text: str
    domain: Literal["clinical", "financial", "market", "regulatory", "commercial", "mechanism", "other"]
    epistemic_status: Literal[
        "asserted", "reported", "estimated", "forecast", "opinion", "hypothesis", "unknown"
    ] = "unknown"
    polarity: Literal["positive", "negative", "uncertain", "neutral"] = "neutral"
    assertion_kind: Literal[
        "source_assertion",
        "study_reported_observation",
        "regulatory_status",
        "financial_reported_observation",
        "management_guidance",
        "forecast",
        "opinion",
        "hypothesis",
        "planned_action",
        "unknown",
    ] = "unknown"
    proposition: dict[str, Any] = Field(default_factory=dict)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    origin_study_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    materiality: MaterialityProfile = Field(default_factory=MaterialityProfile)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_quote: str
    entities: list[ExtractedEntity] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_record_profile(self):
        if self.record_type == RecordType.OBSERVATION:
            missing = [
                key
                for key in ("metric", "value", "value_nature")
                if key not in self.attributes
            ]
            if missing:
                raise ValueError("Observation is missing required attributes: " + ", ".join(missing))
        if self.record_type == RecordType.EVENT:
            if not self.attributes.get("event_type"):
                raise ValueError("Event requires event_type")
            if not (self.attributes.get("event_time") or self.attributes.get("disclosure_time")):
                raise ValueError("Event requires event_time or disclosure_time")
        return self


class ExtractionResponse(StrictModel):
    records: list[ExtractedRecord] = Field(default_factory=list)


class GraphRecord(StrictModel):
    id: str
    record_type: RecordType
    text: str
    domain: str
    epistemic_status: str
    polarity: str
    assertion_kind: str = "unknown"
    proposition: dict[str, Any] = Field(default_factory=dict)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    canonical_proposition_id: str
    origin_study_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str
    materiality: MaterialityProfile = Field(default_factory=MaterialityProfile)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str]
    review_status: ReviewStatus = ReviewStatus.CANDIDATE
    extractor_version: str = EXTRACTOR_VERSION
    model_name: str


class GraphEntity(StrictModel):
    id: str
    entity_type: EntityType
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    resolution_status: Literal["resolved", "temporary", "needs_review"] = "temporary"


class GraphEdge(StrictModel):
    id: str
    source_id: str
    target_id: str
    relation: str
    origin: Literal["structural", "extracted", "derived", "reviewed"]
    evidence_ids: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    extractor_version: str = EXTRACTOR_VERSION
    model_name: str | None = None
    review_status: ReviewStatus = ReviewStatus.CANDIDATE
    valid_from: str | None = None
    valid_to: str | None = None
    as_of: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphBundle(StrictModel):
    document: DocumentNode
    passages: list[PassageNode]
    evidence: list[EvidenceSpan]
    records: list[GraphRecord]
    entities: list[GraphEntity]
    edges: list[GraphEdge]


class RelationCandidate(StrictModel):
    left_id: str
    right_id: str
    left_text: str
    right_text: str
    shared_entities: list[str]


class EvidenceRelation(StrictModel):
    left_id: str
    right_id: str
    relation: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    comparability: dict[str, Any] = Field(default_factory=dict)


class QueryGraphEdge(StrictModel):
    """Ephemeral, query-scoped semantic edge between retrieved records."""

    source_id: str
    target_id: str
    relation: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    comparability: dict[str, Any] = Field(default_factory=dict)
    query_relevance: float = Field(default=0.0, ge=0.0)
    context_compatible: bool = False
    verified: bool = False
    edge_role: Literal["proof", "exploration", "comparison", "temporal"] = "exploration"
    edge_evidence_ids: list[str] = Field(default_factory=list)
    cache_key: str
    origin: Literal["query_time"] = "query_time"
    review_status: ReviewStatus = ReviewStatus.CANDIDATE
    model_name: str | None = None


class RelationResponse(StrictModel):
    relations: list[EvidenceRelation] = Field(default_factory=list)


class QueryPlan(StrictModel):
    query_type: Literal[
        "simple_fact",
        "multi_hop",
        "comparison",
        "global_trend",
        "temporal_status",
        "quantitative_analysis",
    ] = "simple_fact"
    entity_names: list[str] = Field(default_factory=list)
    record_types: list[RecordType] = Field(default_factory=list)
    domains: list[
        Literal["clinical", "financial", "market", "regulatory", "commercial", "mechanism", "other"]
    ] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    relation_types: list[RelationType] = Field(default_factory=list)
    subquestions: list[dict[str, Any]] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    max_hops: int = Field(default=2, ge=0, le=4)


class EvidenceHit(StrictModel):
    evidence_id: str
    passage_id: str
    document_id: str
    source_id: str | None = None
    title: str
    publisher: str | None = None
    source_url: str | None = None
    source_type: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    reporting_period: str | None = None
    reliability_tier: str | None = None
    limitations: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    chunk_id: str
    quote: str
    passage_text: str
    record_ids: list[str] = Field(default_factory=list)
    record_summaries: list[dict[str, Any]] = Field(default_factory=list)
    graph_paths: list[list[str]] = Field(default_factory=list)
    relation_reasons: list[str] = Field(default_factory=list)
    score: float = 0.0
    retrieval_channels: list[str] = Field(default_factory=list)
    retrieval_signals: dict[str, float] = Field(default_factory=dict)


class RetrievalRound(StrictModel):
    """Auditable summary of one candidate-retrieval round."""

    round_index: int = Field(ge=0)
    query: str
    candidate_record_ids: list[str] = Field(default_factory=list)
    new_record_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)


class EvidencePack(StrictModel):
    query: str
    backend: Literal["graph", "hybrid", "chunk"]
    hits: list[EvidenceHit] = Field(default_factory=list)
    query_plan: QueryPlan
    query_edges: list[QueryGraphEdge] = Field(default_factory=list)
    candidate_record_ids: list[str] = Field(default_factory=list)
    active_record_ids: list[str] = Field(default_factory=list)
    retrieval_rounds: list[RetrievalRound] = Field(default_factory=list)
    open_gaps: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: Literal["not_run", "partial", "sufficient", "insufficient"] = "not_run"
    trace_id: str
    warnings: list[str] = Field(default_factory=list)


class BuildReport(StrictModel):
    document_id: str
    document_hash: str
    status: Literal["built", "reused", "failed"]
    passage_count: int = 0
    evidence_count: int = 0
    record_count: int = 0
    entity_count: int = 0
    edge_count: int = 0
    relation_count: int = 0
    errors: list[str] = Field(default_factory=list)
    model_name: str
    schema_version: str = SCHEMA_VERSION
