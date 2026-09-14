from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .attribution import resolve_target, validate_fact
from .facts import load_fact_definitions
from .types import ExtractedFact, FactDefinition

DEFAULT_DB_PATH = Path("data/client_context.sqlite3")


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def init_db(conn: sqlite3.Connection, fact_config_path: Optional[Path] = None) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_key TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS meetings (
          id TEXT PRIMARY KEY,
          owner_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          transcript_json TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          processing_status TEXT NOT NULL,
          processing_error TEXT,
          processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ingestion_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          finished_at TEXT,
          status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
          base_url TEXT NOT NULL,
          updated_after TEXT,
          extractor TEXT NOT NULL,
          fact_config_path TEXT,
          meetings_seen INTEGER NOT NULL DEFAULT 0,
          meetings_processed INTEGER NOT NULL DEFAULT 0,
          meetings_skipped INTEGER NOT NULL DEFAULT 0,
          meetings_failed INTEGER NOT NULL DEFAULT 0,
          facts_extracted INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS meeting_processing_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
          meeting_id TEXT NOT NULL,
          content_hash TEXT,
          status TEXT NOT NULL CHECK (status IN ('processed', 'skipped', 'failed')),
          facts_extracted INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attribution_reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
          meeting_id TEXT NOT NULL REFERENCES meetings(id),
          target_client_key TEXT NOT NULL,
          segment_index INTEGER NOT NULL,
          source_excerpt TEXT NOT NULL,
          reason TEXT NOT NULL,
          policy_version TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_fact_candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          review_id INTEGER NOT NULL REFERENCES attribution_reviews(id),
          ordinal INTEGER NOT NULL,
          fact_type TEXT NOT NULL,
          label TEXT NOT NULL,
          value_json TEXT NOT NULL,
          display_value TEXT NOT NULL,
          source_excerpt TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          UNIQUE(review_id, ordinal)
        );

        CREATE TABLE IF NOT EXISTS review_suggestions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          review_id INTEGER NOT NULL REFERENCES attribution_reviews(id),
          suggestion_json TEXT NOT NULL,
          baseline_json TEXT NOT NULL,
          model TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS review_suggestions_review ON review_suggestions(review_id, id);
        CREATE TABLE IF NOT EXISTS fact_retractions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_id INTEGER NOT NULL REFERENCES clients(id),
          fact_type TEXT NOT NULL,
          retracted_at TEXT NOT NULL,
          resolution_id TEXT REFERENCES review_resolutions(id)
        );
        CREATE TABLE IF NOT EXISTS correction_proposals (
          id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL UNIQUE,
          review_id INTEGER NOT NULL REFERENCES attribution_reviews(id),
          target_client_key TEXT NOT NULL,
          instruction TEXT NOT NULL,
          reviewer TEXT NOT NULL,
          plan_json TEXT NOT NULL,
          baseline_json TEXT NOT NULL,
          model TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS review_resolutions (
          id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL UNIQUE,
          review_id INTEGER NOT NULL UNIQUE REFERENCES attribution_reviews(id),
          proposal_id TEXT REFERENCES correction_proposals(id),
          target_client_key TEXT NOT NULL,
          action TEXT NOT NULL CHECK(action IN ('updated', 'ignored')),
          instruction TEXT NOT NULL,
          reviewer TEXT NOT NULL,
          summary TEXT NOT NULL,
          before_json TEXT NOT NULL,
          after_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS fact_definitions (
          fact_type TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          description TEXT NOT NULL,
          value_kind TEXT NOT NULL,
          value_schema_json TEXT NOT NULL,
          extractor_name TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          current_version_id INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS fact_definition_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fact_type TEXT NOT NULL REFERENCES fact_definitions(fact_type),
          display_name TEXT NOT NULL,
          description TEXT NOT NULL,
          value_kind TEXT NOT NULL,
          value_schema_json TEXT NOT NULL,
          extractor_name TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          active INTEGER NOT NULL,
          config_hash TEXT NOT NULL,
          supersedes_version_id INTEGER REFERENCES fact_definition_versions(id),
          effective_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_context_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_id INTEGER NOT NULL REFERENCES clients(id),
          source_meeting_id TEXT NOT NULL REFERENCES meetings(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS fact_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_id INTEGER NOT NULL REFERENCES clients(id),
          fact_type TEXT NOT NULL REFERENCES fact_definitions(fact_type),
          fact_definition_version_id INTEGER REFERENCES fact_definition_versions(id),
          normalized_value_json TEXT NOT NULL,
          display_value TEXT NOT NULL,
          source_meeting_id TEXT NOT NULL REFERENCES meetings(id),
          source_excerpt TEXT NOT NULL,
          source_start_time TEXT,
          source_end_time TEXT,
          confidence REAL NOT NULL,
          extraction_reason TEXT NOT NULL,
          extractor_name TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          is_current INTEGER NOT NULL DEFAULT 1,
          supersedes_fact_version_id INTEGER REFERENCES fact_versions(id),
          superseded_by_fact_version_id INTEGER REFERENCES fact_versions(id),
          update_kind TEXT NOT NULL CHECK (update_kind IN ('initial', 'update', 'correction', 'reaffirmation')),
          context_version INTEGER NOT NULL REFERENCES client_context_versions(id),
          observed_at TEXT NOT NULL,
          extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_current_fact_per_client_type
          ON fact_versions(client_id, fact_type)
          WHERE is_current = 1;

        CREATE INDEX IF NOT EXISTS fact_versions_history
          ON fact_versions(client_id, fact_type, observed_at, id);

        CREATE INDEX IF NOT EXISTS fact_definition_versions_lookup
          ON fact_definition_versions(fact_type, id);

        CREATE INDEX IF NOT EXISTS meeting_processing_attempts_run
          ON meeting_processing_attempts(ingestion_run_id, meeting_id);

        CREATE INDEX IF NOT EXISTS meeting_processing_attempts_meeting
          ON meeting_processing_attempts(meeting_id, attempted_at);
        """
    )
    with transaction(conn):
        _migrate_existing_schema(conn)
    seed_fact_definitions(conn, fact_config_path)


def _migrate_existing_schema(conn: sqlite3.Connection) -> None:
    if "payload_json" not in _table_columns(conn, "meetings"):
        conn.execute("ALTER TABLE meetings ADD COLUMN payload_json TEXT")
    if "attribution_json" not in _table_columns(conn, "meetings"):
        conn.execute("ALTER TABLE meetings ADD COLUMN attribution_json TEXT")
    if "subject_client_key" not in _table_columns(conn, "review_fact_candidates"):
        conn.execute("ALTER TABLE review_fact_candidates ADD COLUMN subject_client_key TEXT")
    review_columns = _table_columns(conn, "attribution_reviews")
    for column in ("review_key", "snapshot_json", "target_name", "candidate_version"):
        if column not in review_columns:
            conn.execute(f"ALTER TABLE attribution_reviews ADD COLUMN {column} TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS attribution_review_key ON attribution_reviews(review_key) WHERE review_key IS NOT NULL")
    if "review_resolution_id" not in _table_columns(conn, "fact_versions"):
        conn.execute("ALTER TABLE fact_versions ADD COLUMN review_resolution_id TEXT REFERENCES review_resolutions(id)")
    migrate_revisions = 'source_revision' not in _table_columns(conn, 'fact_versions')
    for column in ('effective_from', 'effective_to', 'temporal_evidence', 'source_updated_at', 'source_revision'):
        if column not in _table_columns(conn, 'fact_versions'):
            conn.execute(f'ALTER TABLE fact_versions ADD COLUMN {column} TEXT')
    if migrate_revisions:
        conn.execute("UPDATE fact_versions SET source_revision=(SELECT content_hash FROM meetings WHERE meetings.id=fact_versions.source_meeting_id)")
        conn.execute("""UPDATE fact_versions SET source_revision='legacy-superseded:' || id
            WHERE superseded_by_fact_version_id IN
            (SELECT newer.id FROM fact_versions newer WHERE newer.source_meeting_id=fact_versions.source_meeting_id
             AND newer.source_start_time=fact_versions.source_start_time)""")
    if 'statement_known' not in _table_columns(conn, 'fact_versions'):
        conn.execute('ALTER TABLE fact_versions ADD COLUMN statement_known INTEGER NOT NULL DEFAULT 1')
        # Legacy extraction could fall back to upload time. Do not pretend that is a meeting date.
        for row in conn.execute('SELECT f.id,f.source_start_time,m.payload_json FROM fact_versions f JOIN meetings m ON m.id=f.source_meeting_id').fetchall():
            payload = json.loads(row['payload_json'] or '{}')
            statement = row['source_start_time'] or payload.get('meeting_date') or payload.get('occurred_at')
            if statement:
                conn.execute('UPDATE fact_versions SET observed_at=? WHERE id=?', (statement,row['id']))
            else:
                conn.execute('UPDATE fact_versions SET statement_known=0 WHERE id=?', (row['id'],))
        # Preserve removals made before timeline support was installed.
        for row in conn.execute('SELECT * FROM review_resolutions').fetchall():
            before = json.loads(row['before_json']).get('facts', {})
            after = json.loads(row['after_json']).get('facts', {})
            client = conn.execute('SELECT id FROM clients WHERE client_key=?', (row['target_client_key'],)).fetchone()
            if client:
                for kind in before.keys() - after.keys():
                    conn.execute('INSERT INTO fact_retractions(client_id,fact_type,retracted_at,resolution_id) VALUES (?,?,?,?)',
                                 (client['id'],kind,row['created_at'],row['id']))
    fact_definition_columns = _table_columns(conn, "fact_definitions")
    if "current_version_id" not in fact_definition_columns:
        conn.execute("ALTER TABLE fact_definitions ADD COLUMN current_version_id INTEGER")

    fact_version_columns = _table_columns(conn, "fact_versions")
    if "fact_definition_version_id" not in fact_version_columns:
        conn.execute("ALTER TABLE fact_versions ADD COLUMN fact_definition_version_id INTEGER")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def seed_fact_definitions(conn: sqlite3.Connection, fact_config_path: Optional[Path] = None) -> None:
    definitions = load_fact_definitions(fact_config_path) if fact_config_path else load_fact_definitions()
    for definition in definitions:
        value_schema_json = json.dumps(definition.value_schema, sort_keys=True)
        config_hash = _definition_hash(definition, value_schema_json)
        current = conn.execute(
            """
            SELECT fd.*, fdv.config_hash AS current_config_hash
            FROM fact_definitions fd
            LEFT JOIN fact_definition_versions fdv ON fdv.id = fd.current_version_id
            WHERE fd.fact_type = ?
            """,
            (definition.fact_type,),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO fact_definitions (
              fact_type, display_name, description, value_kind, value_schema_json,
              extractor_name, extractor_version, active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(fact_type) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              value_kind = excluded.value_kind,
              value_schema_json = excluded.value_schema_json,
              extractor_name = excluded.extractor_name,
              extractor_version = excluded.extractor_version,
              active = excluded.active,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                definition.fact_type,
                definition.display_name,
                definition.description,
                definition.value_kind,
                value_schema_json,
                definition.extractor_name,
                definition.extractor_version,
                1 if definition.active else 0,
            ),
        )

        if not current or current["current_config_hash"] != config_hash:
            version_id = conn.execute(
                """
                INSERT INTO fact_definition_versions (
                  fact_type, display_name, description, value_kind, value_schema_json,
                  extractor_name, extractor_version, active, config_hash, supersedes_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition.fact_type,
                    definition.display_name,
                    definition.description,
                    definition.value_kind,
                    value_schema_json,
                    definition.extractor_name,
                    definition.extractor_version,
                    1 if definition.active else 0,
                    config_hash,
                    current["current_version_id"] if current else None,
                ),
            ).lastrowid
            conn.execute(
                "UPDATE fact_definitions SET current_version_id = ? WHERE fact_type = ?",
                (version_id, definition.fact_type),
            )
    conn.commit()


def _definition_hash(definition: FactDefinition, value_schema_json: str) -> str:
    payload = {
        "fact_type": definition.fact_type,
        "display_name": definition.display_name,
        "description": definition.description,
        "value_kind": definition.value_kind,
        "value_schema": json.loads(value_schema_json),
        "extractor_name": definition.extractor_name,
        "extractor_version": definition.extractor_version,
        "active": bool(definition.active),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def start_ingestion_run(
    conn: sqlite3.Connection,
    base_url: str,
    updated_after: Optional[str],
    extractor: str,
    fact_config_path: Optional[Path],
) -> int:
    run_id = int(
        conn.execute(
            """
            INSERT INTO ingestion_runs (base_url, updated_after, extractor, fact_config_path, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (base_url, updated_after, extractor, str(fact_config_path) if fact_config_path else None),
        ).lastrowid
    )
    conn.commit()
    return run_id


def finish_ingestion_run(conn: sqlite3.Connection, run_id: int, summary: dict, error: Optional[str] = None) -> None:
    status = "failed" if error else "partial" if summary.get("failed", 0) else "succeeded"
    conn.execute(
        """
        UPDATE ingestion_runs
        SET finished_at = CURRENT_TIMESTAMP,
            status = ?,
            meetings_seen = ?,
            meetings_processed = ?,
            meetings_skipped = ?,
            meetings_failed = ?,
            facts_extracted = ?,
            error = ?
        WHERE id = ?
        """,
        (
            status,
            summary.get("seen", 0),
            summary.get("processed", 0),
            summary.get("skipped", 0),
            summary.get("failed", 0),
            summary.get("facts", 0),
            error,
            run_id,
        ),
    )
    conn.commit()


def record_meeting_attempt(
    conn: sqlite3.Connection,
    ingestion_run_id: int,
    meeting_id: str,
    content_hash: Optional[str],
    status: str,
    facts_extracted: int = 0,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO meeting_processing_attempts (
          ingestion_run_id, meeting_id, content_hash, status, facts_extracted, error
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ingestion_run_id, meeting_id, content_hash, status, facts_extracted, error),
    )


def get_active_fact_definitions(conn: sqlite3.Connection):
    rows = conn.execute("SELECT * FROM fact_definitions WHERE active = 1 ORDER BY fact_type").fetchall()
    return [
        FactDefinition(
            fact_type=row["fact_type"],
            display_name=row["display_name"],
            description=row["description"],
            value_kind=row["value_kind"],
            value_schema=json.loads(row["value_schema_json"]),
            extractor_name=row["extractor_name"],
            extractor_version=row["extractor_version"],
            active=bool(row["active"]),
        )
        for row in rows
    ]


def upsert_client(conn: sqlite3.Connection, display_name: str, client_key: str) -> int:
    conn.execute(
        """
        INSERT INTO clients (client_key, display_name)
        VALUES (?, ?)
        ON CONFLICT(client_key) DO UPDATE SET display_name = excluded.display_name, updated_at = CURRENT_TIMESTAMP
        """,
        (client_key, display_name),
    )
    return int(conn.execute("SELECT id FROM clients WHERE client_key = ?", (client_key,)).fetchone()["id"])


def record_meeting(conn: sqlite3.Connection, meeting: dict, content_hash: str, status: str, error: Optional[str] = None, decisions=None) -> None:
    conn.execute(
        """
        INSERT INTO meetings (id, owner_json, created_at, updated_at, transcript_json, content_hash, processing_status, processing_error, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
          owner_json = excluded.owner_json,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at,
          transcript_json = excluded.transcript_json,
          content_hash = excluded.content_hash,
          processing_status = excluded.processing_status,
          processing_error = excluded.processing_error,
          processed_at = CURRENT_TIMESTAMP
        """,
        (
            meeting["id"],
            json.dumps(meeting.get("owner", {}), sort_keys=True),
            meeting["created_at"],
            meeting["updated_at"],
            json.dumps(meeting.get("transcript", []), sort_keys=True),
            content_hash,
            status,
            error,
        ),
    )

    conn.execute("UPDATE meetings SET payload_json = ?, attribution_json = ? WHERE id = ?",
                 (json.dumps(meeting, sort_keys=True), json.dumps(decisions) if decisions is not None else None, meeting["id"]))


def existing_meeting_hash(conn: sqlite3.Connection, meeting_id: str) -> Optional[str]:
    row = conn.execute("SELECT content_hash FROM meetings WHERE id = ? AND processing_status = 'processed'", (meeting_id,)).fetchone()
    return row["content_hash"] if row else None


def apply_fact(conn: sqlite3.Connection, meeting_id: str, fact: ExtractedFact) -> int:
    row = conn.execute("SELECT payload_json, attribution_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not row or not row["payload_json"]:
        raise ValueError("Meeting attribution metadata missing; reingest the meeting")
    meeting = json.loads(row["payload_json"])
    decisions = json.loads(row["attribution_json"]) if row["attribution_json"] else None
    validate_fact(meeting, fact, decisions)
    target = resolve_target(meeting)
    return _write_validated_fact(conn, meeting_id, fact, target)


def _write_validated_fact(conn, meeting_id, fact, target):
    """Internal write after either attribution validation or confirmed human correction."""
    from .timeline import instant, validate_period, reconcile
    instant(fact.observed_at)
    validate_period(fact.effective_from, fact.effective_to, fact.temporal_evidence, fact.source.excerpt)
    client_id = upsert_client(conn, target["name"], target["key"])
    reconcile(conn, client_id)
    context_version = conn.execute(
        "INSERT INTO client_context_versions (client_id, source_meeting_id) VALUES (?, ?)",
        (client_id, meeting_id),
    ).lastrowid

    definition = conn.execute(
        "SELECT current_version_id FROM fact_definitions WHERE fact_type = ?",
        (fact.fact_type,),
    ).fetchone()
    if not definition or definition["current_version_id"] is None:
        raise ValueError(f"No active fact definition version found for {fact.fact_type}")

    old = conn.execute(
        """
        SELECT * FROM fact_versions
        WHERE client_id = ? AND fact_type = ? AND is_current = 1
        """,
        (client_id, fact.fact_type),
    ).fetchone()

    value_json = json.dumps(fact.normalized_value, sort_keys=True)
    if old and old["normalized_value_json"] == value_json:
        update_kind = "reaffirmation"
    elif not old:
        update_kind = "initial"
    else:
        update_kind = fact.update_kind

    new_id = conn.execute(
        """
        INSERT INTO fact_versions (
          client_id, fact_type, fact_definition_version_id, normalized_value_json, display_value, source_meeting_id,
          source_excerpt, source_start_time, source_end_time, confidence, extraction_reason,
          extractor_name, extractor_version, is_current, supersedes_fact_version_id,
          update_kind, context_version, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            client_id,
            fact.fact_type,
            definition["current_version_id"],
            value_json,
            fact.display_value,
            meeting_id,
            fact.source.excerpt,
            fact.source.start_time,
            fact.source.end_time,
            fact.confidence,
            fact.reason,
            fact.extractor_name,
            fact.extractor_version,
            None,
            update_kind,
            context_version,
            fact.observed_at,
        ),
    ).lastrowid

    source = conn.execute('SELECT updated_at,content_hash FROM meetings WHERE id = ?', (meeting_id,)).fetchone()
    conn.execute('UPDATE fact_versions SET effective_from=?, effective_to=?, temporal_evidence=?, source_updated_at=?, source_revision=? WHERE id=?',
                 (fact.effective_from, fact.effective_to, fact.temporal_evidence, source['updated_at'], source['content_hash'], new_id))
    winners = reconcile(conn, client_id)
    winner = winners.get(fact.fact_type)
    if old and winner and winner['id'] == new_id and old['id'] != new_id:
        conn.execute('UPDATE fact_versions SET supersedes_fact_version_id=? WHERE id=?', (old['id'], new_id))
        conn.execute('UPDATE fact_versions SET superseded_by_fact_version_id=? WHERE id=?', (new_id, old['id']))
    return int(new_id)
