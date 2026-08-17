from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import connect, init_db
from .facts import normalize_client_key


def get_client_context(db_path: Path, client: str, include_history: bool = False) -> dict[str, Any]:
    if not isinstance(client, str) or not client.strip():
        raise ValueError("client must be a non-empty string")
    conn = connect(db_path)
    init_db(conn)
    client_key = normalize_client_key(client)
    row = conn.execute("SELECT * FROM clients WHERE client_key = ?", (client_key,)).fetchone()
    if not row:
        conn.close()
        raise KeyError(f"No client context found for {client}")
    current_rows = conn.execute(
        """
        SELECT
          fv.*,
          COALESCE(fdv.display_name, fd.display_name) AS display_name,
          COALESCE(fdv.value_kind, fd.value_kind) AS value_kind,
          fdv.id AS fact_definition_version_id,
          fdv.config_hash AS fact_definition_config_hash,
          fdv.active AS fact_definition_active,
          fdv.effective_at AS fact_definition_effective_at
        FROM fact_versions fv
        JOIN fact_definitions fd ON fd.fact_type = fv.fact_type
        LEFT JOIN fact_definition_versions fdv ON fdv.id = fv.fact_definition_version_id
        WHERE fv.client_id = ? AND fv.is_current = 1
        ORDER BY fv.fact_type
        """,
        (row["id"],),
    ).fetchall()
    context_version = 0
    facts = {}
    for fact in current_rows:
        context_version = max(context_version, int(fact["context_version"]))
        facts[fact["fact_type"]] = _format_fact(fact)
    result = {
        "client": {"key": row["client_key"], "displayName": row["display_name"]},
        "contextVersion": context_version,
        "facts": facts,
    }
    if include_history:
        history_rows = conn.execute(
            """
            SELECT
              fv.*,
              COALESCE(fdv.display_name, fd.display_name) AS display_name,
              COALESCE(fdv.value_kind, fd.value_kind) AS value_kind,
              fdv.id AS fact_definition_version_id,
              fdv.config_hash AS fact_definition_config_hash,
              fdv.active AS fact_definition_active,
              fdv.effective_at AS fact_definition_effective_at
            FROM fact_versions fv
            JOIN fact_definitions fd ON fd.fact_type = fv.fact_type
            LEFT JOIN fact_definition_versions fdv ON fdv.id = fv.fact_definition_version_id
            WHERE fv.client_id = ?
            ORDER BY fv.fact_type, fv.observed_at, fv.id
            """,
            (row["id"],),
        ).fetchall()
        history = {}
        for fact in history_rows:
            history.setdefault(fact["fact_type"], []).append(_format_fact(fact))
        result["history"] = history
    conn.close()
    return result


def _format_fact(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "displayName": row["display_name"],
        "valueKind": row["value_kind"],
        "value": json.loads(row["normalized_value_json"]),
        "displayValue": row["display_value"],
        "isCurrent": bool(row["is_current"]),
        "contextVersion": row["context_version"],
        "updateKind": row["update_kind"],
        "observedAt": row["observed_at"],
        "extractedAt": row["extracted_at"],
        "provenance": {
            "meetingId": row["source_meeting_id"],
            "excerpt": row["source_excerpt"],
            "startTime": row["source_start_time"],
            "endTime": row["source_end_time"],
            "confidence": row["confidence"],
            "reason": row["extraction_reason"],
            "extractor": {
                "name": row["extractor_name"],
                "version": row["extractor_version"],
            },
            "factDefinition": {
                "versionId": row["fact_definition_version_id"],
                "configHash": row["fact_definition_config_hash"],
                "activeWhenDefined": bool(row["fact_definition_active"]) if row["fact_definition_active"] is not None else None,
                "effectiveAt": row["fact_definition_effective_at"],
            },
        },
    }
