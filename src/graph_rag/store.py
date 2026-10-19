"""SQLite authority store and NetworkX projection for GraphRAG."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import networkx as nx

from .models import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    GraphBundle,
    RelationCandidate,
    ReviewStatus,
)

DEFAULT_GRAPH_DIR = Path(__file__).resolve().parents[2] / "outputs" / ".graph_cache"
DEFAULT_GRAPH_DB = DEFAULT_GRAPH_DIR / "pharsight_graph.sqlite3"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _normalize_search_text(value: Any) -> str:
    """Normalize common clinical-stage glyph variants before lexical seeding."""
    return str(value or "").translate(
        str.maketrans({"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV"})
    ).strip().lower()


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


class GraphStore:
    """Own the versioned graph records and rebuildable graph projection."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_GRAPH_DB).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS graph_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    document_hash TEXT NOT NULL UNIQUE,
                    source_id TEXT,
                    title TEXT NOT NULL,
                    publisher TEXT,
                    source_url TEXT,
                    published_at TEXT,
                    retrieved_at TEXT,
                    reporting_period TEXT,
                    as_of TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    section_title TEXT,
                    content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_spans (
                    id TEXT PRIMARY KEY,
                    passage_id TEXT NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
                    quote TEXT NOT NULL,
                    start_char INTEGER NOT NULL,
                    end_char INTEGER NOT NULL,
                    field_paths_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    record_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    epistemic_status TEXT NOT NULL,
                    polarity TEXT NOT NULL,
                    assertion_kind TEXT NOT NULL DEFAULT 'unknown',
                    proposition_json TEXT NOT NULL DEFAULT '{}',
                    qualifiers_json TEXT NOT NULL DEFAULT '{}',
                    canonical_proposition_id TEXT NOT NULL DEFAULT '',
                    origin_study_id TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    recorded_at TEXT NOT NULL DEFAULT '',
                    materiality_json TEXT NOT NULL DEFAULT '{}',
                    attributes_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    model_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    external_ids_json TEXT NOT NULL,
                    resolution_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS record_entities (
                    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    PRIMARY KEY (record_id, entity_id, role)
                );
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    supporting_record_ids_json TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    model_name TEXT,
                    review_status TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    as_of TEXT,
                    attributes_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relation_assessments (
                    edge_id TEXT PRIMARY KEY REFERENCES edges(id) ON DELETE CASCADE,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    comparability_json TEXT NOT NULL,
                    assessed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relation_pair_reviews (
                    pair_key TEXT PRIMARY KEY,
                    left_id TEXT NOT NULL,
                    right_id TEXT NOT NULL,
                    relation TEXT,
                    confidence REAL,
                    reason TEXT,
                    comparability_json TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    assessed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS build_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT,
                    document_hash TEXT,
                    schema_version TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_passages_document ON passages(document_id);
                CREATE INDEX IF NOT EXISTS idx_records_type_domain ON records(record_type, domain);
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
                """
            )
            record_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(records)").fetchall()
            }
            record_migrations = {
                "materiality_json": "TEXT NOT NULL DEFAULT '{}'",
                "assertion_kind": "TEXT NOT NULL DEFAULT 'unknown'",
                "proposition_json": "TEXT NOT NULL DEFAULT '{}'",
                "qualifiers_json": "TEXT NOT NULL DEFAULT '{}'",
                "canonical_proposition_id": "TEXT NOT NULL DEFAULT ''",
                "origin_study_id": "TEXT",
                "valid_from": "TEXT",
                "valid_to": "TEXT",
                "recorded_at": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in record_migrations.items():
                if column not in record_columns:
                    connection.execute(f"ALTER TABLE records ADD COLUMN {column} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_proposition ON records(canonical_proposition_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_origin_study ON records(origin_study_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_recorded_at ON records(recorded_at)"
            )
            self._backfill_record_semantics(connection)
            connection.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            connection.commit()

    @staticmethod
    def _backfill_record_semantics(connection: sqlite3.Connection) -> None:
        """Deterministically make legacy v1.2 records queryable under the v1.3 contract."""
        qualifier_keys = {
            "trial_id",
            "phase",
            "population",
            "line_of_therapy",
            "arm",
            "design",
            "comparator",
            "dose",
            "endpoint",
            "estimand",
            "analysis_set",
            "timepoint",
            "reporting_period",
            "geography",
            "unit",
            "currency",
            "conditions",
        }
        proposition_keys = {
            "subject",
            "predicate",
            "object",
            "metric",
            "value",
            "event_type",
            "new_status",
        }
        rows = connection.execute(
            """SELECT * FROM records
               WHERE canonical_proposition_id='' OR recorded_at=''"""
        ).fetchall()
        for row in rows:
            attributes = _loads(row["attributes_json"], {})
            proposition = _loads(row["proposition_json"], {}) or {
                key: attributes[key] for key in proposition_keys if key in attributes
            }
            qualifiers = _loads(row["qualifiers_json"], {}) or {
                key: value
                for key, value in attributes.items()
                if key in qualifier_keys and value is not None
            }
            assertion_kind = row["assertion_kind"] or "unknown"
            if assertion_kind == "unknown":
                if row["record_type"] == "Observation" and row["domain"] == "clinical":
                    assertion_kind = "study_reported_observation"
                elif row["record_type"] == "Observation" and row["domain"] == "financial":
                    assertion_kind = "financial_reported_observation"
                elif row["record_type"] == "Event" and row["domain"] == "regulatory":
                    assertion_kind = "regulatory_status"
                elif row["epistemic_status"] == "forecast":
                    assertion_kind = "forecast"
                else:
                    assertion_kind = "source_assertion"
            canonical_id = row["canonical_proposition_id"] or _stable_id(
                "proposition",
                row["record_type"],
                proposition,
                qualifiers,
                row["polarity"],
                row["epistemic_status"],
            )
            origin_study_id = row["origin_study_id"] or attributes.get("trial_id")
            recorded_at = row["recorded_at"]
            if not recorded_at:
                evidence_ids = _loads(row["evidence_ids_json"], [])
                if evidence_ids:
                    timestamp = connection.execute(
                        """SELECT d.retrieved_at
                           FROM evidence_spans es
                           JOIN passages p ON p.id=es.passage_id
                           JOIN documents d ON d.id=p.document_id
                           WHERE es.id=?""",
                        (evidence_ids[0],),
                    ).fetchone()
                    recorded_at = timestamp[0] if timestamp else ""
            connection.execute(
                """UPDATE records SET assertion_kind=?, proposition_json=?, qualifiers_json=?,
                          canonical_proposition_id=?, origin_study_id=?, recorded_at=?
                   WHERE id=?""",
                (
                    assertion_kind,
                    _json(proposition),
                    _json(qualifiers),
                    canonical_id,
                    origin_study_id,
                    recorded_at or datetime.now().astimezone().isoformat(),
                    row["id"],
                ),
            )

    def is_current(self, document_hash: str, model_name: str) -> bool:
        """Return whether the document has a successful current-version build."""
        with self.connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM build_runs
                   WHERE document_hash=? AND schema_version=? AND extractor_version=?
                     AND model_name=? AND status='built'
                   ORDER BY id DESC LIMIT 1""",
                (document_hash, SCHEMA_VERSION, EXTRACTOR_VERSION, model_name),
            ).fetchone()
        return row is not None

    def start_build(self, document_id: str, document_hash: str, model_name: str) -> int:
        """Create an auditable running build record and return its integer ID."""
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO build_runs(
                       document_id, document_hash, schema_version, extractor_version,
                       model_name, status, started_at
                   ) VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (
                    document_id,
                    document_hash,
                    SCHEMA_VERSION,
                    EXTRACTOR_VERSION,
                    model_name,
                    datetime.now().astimezone().isoformat(),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def finish_build(self, run_id: int, status: str, error: str | None = None) -> None:
        """Finalize a build record with status and optional diagnostic text."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE build_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, datetime.now().astimezone().isoformat(), error, run_id),
            )
            connection.commit()

    def replace_document_bundle(self, bundle: GraphBundle) -> None:
        """Replace one document and its owned nodes in one transaction."""
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old = connection.execute(
                    "SELECT id FROM documents WHERE document_hash=?",
                    (bundle.document.document_hash,),
                ).fetchone()
                if old:
                    passage_ids = [
                        row[0]
                        for row in connection.execute(
                            "SELECT id FROM passages WHERE document_id=?", (old["id"],)
                        ).fetchall()
                    ]
                    evidence_ids: list[str] = []
                    if passage_ids:
                        placeholders = ",".join("?" for _ in passage_ids)
                        evidence_ids = [
                            row[0]
                            for row in connection.execute(
                                f"SELECT id FROM evidence_spans WHERE passage_id IN ({placeholders})",
                                passage_ids,
                            ).fetchall()
                        ]
                    owned_records = []
                    for row in connection.execute("SELECT id, evidence_ids_json FROM records"):
                        if set(_loads(row["evidence_ids_json"], [])) & set(evidence_ids):
                            owned_records.append(row["id"])
                    owned_nodes = [old["id"], *passage_ids, *evidence_ids, *owned_records]
                    if owned_nodes:
                        placeholders = ",".join("?" for _ in owned_nodes)
                        edge_ids = [
                            row[0]
                            for row in connection.execute(
                                f"SELECT id FROM edges WHERE source_id IN ({placeholders}) "
                                f"OR target_id IN ({placeholders})",
                                [*owned_nodes, *owned_nodes],
                            ).fetchall()
                        ]
                        if edge_ids:
                            edge_placeholders = ",".join("?" for _ in edge_ids)
                            connection.execute(
                                f"DELETE FROM relation_assessments WHERE edge_id IN ({edge_placeholders})",
                                edge_ids,
                            )
                            connection.execute(
                                f"DELETE FROM edges WHERE id IN ({edge_placeholders})", edge_ids
                            )
                    if owned_records:
                        placeholders = ",".join("?" for _ in owned_records)
                        connection.execute(
                            f"DELETE FROM relation_pair_reviews WHERE left_id IN ({placeholders}) "
                            f"OR right_id IN ({placeholders})",
                            [*owned_records, *owned_records],
                        )
                    if owned_records:
                        placeholders = ",".join("?" for _ in owned_records)
                        connection.execute(f"DELETE FROM records WHERE id IN ({placeholders})", owned_records)
                    connection.execute("DELETE FROM documents WHERE id=?", (old["id"],))

                document = bundle.document
                connection.execute(
                    """INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        document.id,
                        document.document_hash,
                        document.source_id,
                        document.title,
                        document.publisher,
                        document.source_url,
                        document.published_at,
                        document.retrieved_at,
                        document.reporting_period,
                        document.as_of,
                        _json(document.metadata),
                    ),
                )
                connection.executemany(
                    "INSERT INTO passages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item.id,
                            item.document_id,
                            item.chunk_id,
                            item.text,
                            item.page_start,
                            item.page_end,
                            item.section_title,
                            item.content_hash,
                        )
                        for item in bundle.passages
                    ],
                )
                connection.executemany(
                    "INSERT INTO evidence_spans VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item.id,
                            item.passage_id,
                            item.quote,
                            item.start_char,
                            item.end_char,
                            _json(item.field_paths),
                        )
                        for item in bundle.evidence
                    ],
                )
                for entity in bundle.entities:
                    connection.execute(
                        """INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET
                             aliases_json=excluded.aliases_json,
                             external_ids_json=excluded.external_ids_json""",
                        (
                            entity.id,
                            entity.entity_type.value,
                            entity.canonical_name,
                            _json(entity.aliases),
                            _json(entity.external_ids),
                            entity.resolution_status,
                        ),
                    )
                connection.executemany(
                    """INSERT INTO records(
                           id, record_type, text, domain, epistemic_status, polarity,
                           assertion_kind, proposition_json, qualifiers_json,
                           canonical_proposition_id, origin_study_id, valid_from, valid_to,
                           recorded_at,
                           materiality_json, attributes_json, evidence_ids_json, review_status,
                           extractor_version, model_name
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            item.id,
                            item.record_type.value,
                            item.text,
                            item.domain,
                            item.epistemic_status,
                            item.polarity,
                            item.assertion_kind,
                            _json(item.proposition),
                            _json(item.qualifiers),
                            item.canonical_proposition_id,
                            item.origin_study_id,
                            item.valid_from,
                            item.valid_to,
                            item.recorded_at,
                            _json(item.materiality.model_dump(mode="json")),
                            _json(item.attributes),
                            _json(item.evidence_ids),
                            item.review_status.value,
                            item.extractor_version,
                            item.model_name,
                        )
                        for item in bundle.records
                    ],
                )
                connection.executemany(
                    """INSERT OR REPLACE INTO edges VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            item.id,
                            item.source_id,
                            item.target_id,
                            item.relation,
                            item.origin,
                            _json(item.evidence_ids),
                            _json(item.supporting_record_ids),
                            item.extractor_version,
                            item.model_name,
                            item.review_status.value,
                            item.valid_from,
                            item.valid_to,
                            item.as_of,
                            _json(item.attributes),
                        )
                        for item in bundle.edges
                    ],
                )
                for edge in bundle.edges:
                    if edge.relation.startswith("HAS_") or edge.relation in {
                        "ABOUT",
                        "IN_TRIAL",
                        "FOR_INDICATION",
                        "AFFECTS",
                    }:
                        connection.execute(
                            "INSERT OR IGNORE INTO record_entities VALUES (?, ?, ?)",
                            (edge.source_id, edge.target_id, edge.relation),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def add_relation_edge(
        self,
        edge_id: str,
        left_id: str,
        right_id: str,
        relation: str,
        confidence: float,
        reason: str,
        comparability: dict[str, Any],
        model_name: str,
    ) -> None:
        """Atomically persist an accepted evidence relation and its assessment."""
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT OR REPLACE INTO edges VALUES
                       (?, ?, ?, ?, 'extracted', '[]', ?, ?, ?, 'candidate',
                        NULL, NULL, NULL, ?)""",
                    (
                        edge_id,
                        left_id,
                        right_id,
                        relation,
                        _json([left_id, right_id]),
                        EXTRACTOR_VERSION,
                        model_name,
                        _json({"reason": reason, "confidence": confidence}),
                    ),
                )
                connection.execute(
                    """INSERT OR REPLACE INTO relation_assessments VALUES (?, ?, ?, ?, ?)""",
                    (
                        edge_id,
                        confidence,
                        reason,
                        _json(comparability),
                        datetime.now().astimezone().isoformat(),
                    ),
                )
                pair_key = "|".join(sorted((left_id, right_id)))
                connection.execute(
                    """INSERT OR REPLACE INTO relation_pair_reviews VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pair_key,
                        left_id,
                        right_id,
                        relation,
                        confidence,
                        reason,
                        _json(comparability),
                        EXTRACTOR_VERSION,
                        model_name,
                        datetime.now().astimezone().isoformat(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def set_review_status(self, record_id: str, status: ReviewStatus | str) -> None:
        """Set a record's controlled review status or fail for an unknown record."""
        parsed = ReviewStatus(status)
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE records SET review_status=? WHERE id=?", (parsed.value, record_id)
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown graph record: {record_id}")
            connection.commit()

    def record_no_relation(self, left_id: str, right_id: str, model_name: str) -> None:
        """Audit an LLM-reviewed pair that produced none of the seven graph relations."""
        pair_key = "|".join(sorted((left_id, right_id)))
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO relation_pair_reviews VALUES
                   (?, ?, ?, NULL, NULL, ?, '{}', ?, ?, ?)""",
                (
                    pair_key,
                    left_id,
                    right_id,
                    "No supported relation returned",
                    EXTRACTOR_VERSION,
                    model_name,
                    datetime.now().astimezone().isoformat(),
                ),
            )
            connection.commit()

    def candidate_record_pairs(self, model_name: str, limit: int = 100) -> list[RelationCandidate]:
        """Return high-signal blocked pairs instead of generic company-only pairs."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT a.record_id AS left_id, b.record_id AS right_id,
                       ra.text AS left_text, rb.text AS right_text,
                       e.canonical_name AS shared_entity, e.entity_type AS shared_entity_type,
                       ra.domain AS left_domain, rb.domain AS right_domain,
                       json_extract(ra.attributes_json, '$.metric') AS left_metric,
                       json_extract(rb.attributes_json, '$.metric') AS right_metric,
                       json_extract(ra.attributes_json, '$.object') AS left_object,
                       json_extract(rb.attributes_json, '$.object') AS right_object,
                       json_extract(ra.attributes_json, '$.event_type') AS left_event_type,
                       json_extract(rb.attributes_json, '$.event_type') AS right_event_type
                FROM record_entities a
                JOIN record_entities b ON a.entity_id=b.entity_id AND a.record_id < b.record_id
                JOIN records ra ON ra.id=a.record_id
                JOIN records rb ON rb.id=b.record_id
                JOIN entities e ON e.id=a.entity_id
                WHERE ra.review_status != 'rejected' AND rb.review_status != 'rejected'
                  AND (
                    e.entity_type IN (
                      'Drug', 'Program', 'Brand', 'Target', 'Indication', 'Trial',
                      'Policy', 'MarketSegment', 'TechnologyPlatform', 'Transaction'
                    )
                    OR (
                      ra.domain = rb.domain AND (
                        (json_extract(ra.attributes_json, '$.metric') IS NOT NULL AND
                         json_extract(ra.attributes_json, '$.metric') =
                         json_extract(rb.attributes_json, '$.metric'))
                        OR
                        (json_extract(ra.attributes_json, '$.object') IS NOT NULL AND
                         json_extract(ra.attributes_json, '$.object') =
                         json_extract(rb.attributes_json, '$.object'))
                        OR
                        (json_extract(ra.attributes_json, '$.event_type') IS NOT NULL AND
                         json_extract(ra.attributes_json, '$.event_type') =
                         json_extract(rb.attributes_json, '$.event_type'))
                      )
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM relation_pair_reviews x
                    WHERE x.pair_key = CASE
                        WHEN a.record_id < b.record_id THEN a.record_id || '|' || b.record_id
                        ELSE b.record_id || '|' || a.record_id END
                      AND x.extractor_version=? AND x.model_name=?
                  )
                ORDER BY
                  CASE WHEN e.entity_type IN (
                    'Drug', 'Program', 'Brand', 'Target', 'Indication', 'Trial',
                    'Policy', 'MarketSegment', 'TechnologyPlatform', 'Transaction'
                  ) THEN 0 ELSE 1 END,
                  CASE WHEN ra.domain = rb.domain THEN 0 ELSE 1 END,
                  a.record_id, b.record_id
                LIMIT ?
                """,
                (EXTRACTOR_VERSION, model_name, max(1, int(limit)) * 10),
            ).fetchall()
        grouped: dict[tuple[str, str], RelationCandidate] = {}
        for row in rows:
            key = (row["left_id"], row["right_id"])
            if key not in grouped:
                grouped[key] = RelationCandidate(
                    left_id=row["left_id"],
                    right_id=row["right_id"],
                    left_text=row["left_text"],
                    right_text=row["right_text"],
                    shared_entities=[],
                )
            grouped[key].shared_entities.append(row["shared_entity"])
        return list(grouped.values())[: max(1, int(limit))]

    def find_entity_candidates(self, entity_type: str, names: list[str], limit: int = 12) -> list[dict[str, Any]]:
        """Find conservative same-type entity candidates by exact or contained names."""
        normalized = {name.strip().casefold() for name in names if name and name.strip()}
        if not normalized:
            return []
        candidates: list[dict[str, Any]] = []
        with self.connect() as connection:
            for row in connection.execute(
                "SELECT * FROM entities WHERE entity_type=? ORDER BY canonical_name LIMIT 500",
                (entity_type,),
            ):
                aliases = _loads(row["aliases_json"], [])
                searchable = {str(row["canonical_name"]).casefold(), *(str(item).casefold() for item in aliases)}
                if any(
                    left == right or left in right or right in left
                    for left in normalized
                    for right in searchable
                ):
                    item = dict(row)
                    item["aliases"] = aliases
                    item["external_ids"] = _loads(row["external_ids_json"], {})
                    candidates.append(item)
                    if len(candidates) >= limit:
                        break
        return candidates

    def search_record_candidates(
        self,
        terms: list[str],
        *,
        record_types: set[str] | None = None,
        domains: set[str] | None = None,
        source_id: str | None = None,
        document_hash: str | None = None,
        allowed_source_ids: set[str] | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        """Return a high-recall pool of atomic records without requiring prebuilt relations."""
        normalized_terms = list(
            dict.fromkeys(
                _normalize_search_text(term)
                for term in terms
                if term and _normalize_search_text(term)
            )
        )
        with self.connect() as connection:
            entity_names: dict[str, list[str]] = {}
            for row in connection.execute(
                """SELECT e.source_id AS record_id, n.canonical_name
                   FROM edges e JOIN entities n ON n.id=e.target_id
                   JOIN records r ON r.id=e.source_id
                   WHERE e.origin='extracted'"""
            ):
                entity_names.setdefault(row["record_id"], []).append(row["canonical_name"])
            rows = connection.execute(
                """SELECT r.*, es.id AS evidence_id, es.quote, p.id AS passage_id,
                          p.chunk_id, p.text AS passage_text, p.page_start, p.page_end,
                          p.section_title, d.id AS document_id, d.document_hash, d.source_id,
                          d.title, d.publisher, d.source_url, d.published_at, d.retrieved_at,
                          d.reporting_period, d.metadata_json
                   FROM records r
                   JOIN json_each(r.evidence_ids_json) je ON CAST(je.key AS INTEGER)=0
                   JOIN evidence_spans es ON es.id=je.value
                   JOIN passages p ON p.id=es.passage_id
                   JOIN documents d ON d.id=p.document_id
                   WHERE r.review_status != 'rejected'"""
            ).fetchall()
        candidates: list[dict[str, Any]] = []
        allowed = {item.upper() for item in allowed_source_ids} if allowed_source_ids is not None else None
        for row in rows:
            item = dict(row)
            current_source = str(item.get("source_id") or "")
            if source_id and current_source.upper() != source_id.upper():
                continue
            if allowed is not None and current_source.upper() not in allowed:
                continue
            if document_hash and item.get("document_hash") != document_hash:
                continue
            names = entity_names.get(item["id"], [])
            fields = {
                "claim": _normalize_search_text(item.get("text")),
                "proposition": _normalize_search_text(item.get("proposition_json")),
                "qualifier": _normalize_search_text(
                    " ".join(
                        (str(item.get("qualifiers_json") or ""), str(item.get("attributes_json") or ""))
                    )
                ),
                "source_span": _normalize_search_text(item.get("quote")),
                "section": _normalize_search_text(item.get("section_title")),
                "entity": _normalize_search_text(" ".join(names)),
                "metadata": _normalize_search_text(
                    " ".join(
                        (
                            str(item.get("reporting_period") or ""),
                            str(item.get("origin_study_id") or ""),
                            str(item.get("assertion_kind") or ""),
                        )
                    )
                ),
            }
            signals = {
                channel: sum(1.0 + min(len(term), 12) / 12 for term in normalized_terms if term in text)
                for channel, text in fields.items()
            }
            if normalized_terms and not any(signals.values()):
                continue
            signals["entity"] += 4.0 * sum(
                term == _normalize_search_text(name)
                for term in normalized_terms
                for name in names
            )
            signals["type_domain"] = 0.0
            if record_types and item["record_type"] in record_types:
                signals["type_domain"] += 0.05
            if domains and item["domain"] in domains:
                signals["type_domain"] += 0.04
            materiality = _loads(item.get("materiality_json"), {})
            signals["materiality"] = float(materiality.get("score") or 0.5) * 0.05
            item["entity_names"] = sorted(set(names))
            item["materiality"] = materiality
            item["retrieval_signals"] = signals
            candidates.append(item)
        # Reciprocal-rank fusion keeps heterogeneous channels comparable without
        # pretending their raw lexical scores share one calibrated scale.
        channel_weights = {
            "claim": 1.0,
            "proposition": 1.1,
            "qualifier": 1.1,
            "source_span": 0.9,
            "section": 0.5,
            "entity": 1.25,
            "metadata": 0.9,
        }
        for item in candidates:
            item["retrieval_score"] = (
                item["retrieval_signals"]["type_domain"]
                + item["retrieval_signals"]["materiality"]
            )
        for channel, weight in channel_weights.items():
            ranked = sorted(
                (item for item in candidates if item["retrieval_signals"][channel] > 0),
                key=lambda item: (-item["retrieval_signals"][channel], item["id"]),
            )
            for rank, item in enumerate(ranked, start=1):
                item["retrieval_score"] += weight / (60 + rank)
        return sorted(candidates, key=lambda item: (-item["retrieval_score"], item["id"]))[
            : max(1, int(limit))
        ]

    def to_networkx(self) -> nx.MultiDiGraph:
        """Rebuild the complete traversable property-graph projection from SQLite."""
        graph = nx.MultiDiGraph(schema_version=SCHEMA_VERSION)
        with self.connect() as connection:
            for table, node_type, label_column in (
                ("documents", "Document", "title"),
                ("passages", "Passage", "text"),
                ("evidence_spans", "Evidence", "quote"),
                ("records", "Record", "text"),
                ("entities", "Entity", "canonical_name"),
            ):
                for row in connection.execute(f"SELECT * FROM {table}"):
                    data = dict(row)
                    data["node_type"] = node_type
                    data["label"] = str(data.get(label_column, ""))[:200]
                    graph.add_node(data["id"], **data)
            for row in connection.execute("SELECT * FROM edges"):
                data = dict(row)
                graph.add_edge(
                    data["source_id"],
                    data["target_id"],
                    key=data["id"],
                    **data,
                )
        return graph

    def search_seed_ids(self, terms: list[str], limit: int = 40) -> list[str]:
        """Return deterministically ranked entity, record and passage seed IDs."""
        normalized = [_normalize_search_text(term) for term in terms if term and term.strip()]
        if not normalized:
            return []
        scored: dict[str, int] = {}
        with self.connect() as connection:
            for table, columns in (
                ("entities", ("canonical_name", "aliases_json", "external_ids_json")),
                ("records", ("text", "attributes_json")),
                ("passages", ("text", "section_title")),
            ):
                for row in connection.execute(f"SELECT * FROM {table}"):
                    haystack = _normalize_search_text(
                        " ".join(str(row[column] or "") for column in columns)
                    )
                    score = sum(3 if term == haystack else 1 for term in normalized if term in haystack)
                    if score:
                        scored[row["id"]] = max(scored.get(row["id"], 0), score)
        return [node_id for node_id, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:limit]]

    def evidence_hits_for_nodes(self, node_ids: set[str]) -> list[dict[str, Any]]:
        """Resolve graph nodes to citable evidence and passages."""
        if not node_ids:
            return []
        with self.connect() as connection:
            record_rows = {
                row["id"]: dict(row)
                for row in connection.execute("SELECT * FROM records")
                if row["id"] in node_ids
            }
            evidence_to_records: dict[str, list[dict[str, Any]]] = {}
            for record in record_rows.values():
                for evidence_id in _loads(record["evidence_ids_json"], []):
                    evidence_to_records.setdefault(evidence_id, []).append(record)
            for row in connection.execute("SELECT * FROM edges"):
                if row["source_id"] in node_ids or row["target_id"] in node_ids:
                    for record_id in _loads(row["supporting_record_ids_json"], []):
                        record = connection.execute(
                            "SELECT * FROM records WHERE id=?", (record_id,)
                        ).fetchone()
                        if record:
                            for evidence_id in _loads(record["evidence_ids_json"], []):
                                evidence_to_records.setdefault(evidence_id, []).append(dict(record))
            hits: list[dict[str, Any]] = []
            for evidence_id, records in evidence_to_records.items():
                records = list({record["id"]: record for record in records}.values())
                row = connection.execute(
                    """SELECT e.*, p.document_id, p.chunk_id, p.text AS passage_text,
                              p.page_start, p.page_end, d.source_id, d.title, d.publisher,
                              d.source_url, d.document_hash, d.published_at, d.retrieved_at,
                              d.reporting_period, d.metadata_json
                       FROM evidence_spans e
                       JOIN passages p ON p.id=e.passage_id
                       JOIN documents d ON d.id=p.document_id
                       WHERE e.id=?""",
                    (evidence_id,),
                ).fetchone()
                if not row:
                    continue
                item = dict(row)
                item["records"] = records
                hits.append(item)
        return hits

    def snapshot(self) -> dict[str, int | str]:
        """Return schema identity and table counts for diagnostics and tests."""
        with self.connect() as connection:
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("documents", "passages", "evidence_spans", "records", "entities", "edges")
            }
        return {"schema_version": SCHEMA_VERSION, **counts}
