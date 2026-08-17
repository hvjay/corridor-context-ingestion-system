from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .facts import load_fact_definitions, normalize_client_key
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
    _migrate_existing_schema(conn)
    seed_fact_definitions(conn, fact_config_path)


def _migrate_existing_schema(conn: sqlite3.Connection) -> None:
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


def upsert_client(conn: sqlite3.Connection, display_name: str) -> int:
    client_key = normalize_client_key(display_name)
    conn.execute(
        """
        INSERT INTO clients (client_key, display_name)
        VALUES (?, ?)
        ON CONFLICT(client_key) DO UPDATE SET display_name = excluded.display_name, updated_at = CURRENT_TIMESTAMP
        """,
        (client_key, display_name),
    )
    return int(conn.execute("SELECT id FROM clients WHERE client_key = ?", (client_key,)).fetchone()["id"])


def record_meeting(conn: sqlite3.Connection, meeting: dict, content_hash: str, status: str, error: Optional[str] = None) -> None:
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


def existing_meeting_hash(conn: sqlite3.Connection, meeting_id: str) -> Optional[str]:
    row = conn.execute("SELECT content_hash FROM meetings WHERE id = ? AND processing_status = 'processed'", (meeting_id,)).fetchone()
    return row["content_hash"] if row else None


def apply_fact(conn: sqlite3.Connection, meeting_id: str, fact: ExtractedFact) -> int:
    client_id = upsert_client(conn, fact.client_name)
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

    if old:
        conn.execute("UPDATE fact_versions SET is_current = 0 WHERE id = ?", (old["id"],))

    new_id = conn.execute(
        """
        INSERT INTO fact_versions (
          client_id, fact_type, fact_definition_version_id, normalized_value_json, display_value, source_meeting_id,
          source_excerpt, source_start_time, source_end_time, confidence, extraction_reason,
          extractor_name, extractor_version, is_current, supersedes_fact_version_id,
          update_kind, context_version, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
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
            old["id"] if old else None,
            update_kind,
            context_version,
            fact.observed_at,
        ),
    ).lastrowid

    if old:
        conn.execute("UPDATE fact_versions SET superseded_by_fact_version_id = ? WHERE id = ?", (new_id, old["id"]))
    return int(new_id)
