from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .attribution import employer_directory
from .db import connect, init_db
from .facts import normalize_client_key


def get_client_context(db_path: Path, client: str, include_history: bool = False, as_of: str | None = None) -> dict[str, Any]:
    from .timeline import select_facts, instant
    import hashlib
    if not isinstance(client, str) or not client.strip():
        raise ValueError("client must be a non-empty string")
    if as_of is not None:
        instant(as_of)
    conn = connect(db_path)
    try:
        init_db(conn)
        conn.execute('BEGIN')
        client_key = normalize_client_key(client)
        for employer in employer_directory():
            if client.strip().casefold() in {n.casefold() for n in [employer['key'], employer['name'], *employer.get('aliases', [])]}:
                client_key = employer['key']
                break
        client_row = conn.execute('SELECT * FROM clients WHERE client_key = ?', (client_key,)).fetchone()
        if not client_row:
            raise KeyError(f'No client context found for {client}')
        winners, conflicts = select_facts(conn, client_row['id'], as_of)
        rows = conn.execute("""SELECT fv.*,
            COALESCE(fdv.display_name, fd.display_name) AS display_name,
            COALESCE(fdv.value_kind, fd.value_kind) AS value_kind,
            fdv.id AS fact_definition_version_id, fdv.config_hash AS fact_definition_config_hash,
            fdv.active AS fact_definition_active, fdv.effective_at AS fact_definition_effective_at
            FROM fact_versions fv JOIN fact_definitions fd ON fd.fact_type = fv.fact_type
            LEFT JOIN fact_definition_versions fdv ON fdv.id = fv.fact_definition_version_id
            WHERE fv.client_id = ? ORDER BY fv.fact_type, fv.observed_at, fv.id""", (client_row['id'],)).fetchall()
        current_ids = {row['id'] for row in winners.values()}
        formatted = {row['id']: _format_fact({**dict(row), 'is_current': row['id'] in current_ids}) for row in rows}
        version = conn.execute('SELECT COALESCE(MAX(id),0) FROM client_context_versions WHERE client_id=?', (client_row['id'],)).fetchone()[0]
        selection = json.dumps({'current': sorted(current_ids), 'conflicts': {key: sorted(r['id'] for r in group) for key, group in sorted(conflicts.items())}}, sort_keys=True)
        result = {'client': {'key':client_key,'displayName':client_row['display_name']},
                  'contextVersion':version, 'selectionVersion':hashlib.sha256(selection.encode()).hexdigest(),
                  'facts':{key:formatted[row['id']] for key,row in winners.items()},
                  'conflicts':{key:[formatted[row['id']] for row in group] for key,group in conflicts.items()}}
        if as_of is not None:
            result['asOf'] = as_of
        if include_history:
            history = {}
            for row in rows:
                history.setdefault(row['fact_type'], []).append(formatted[row['id']])
            result['history'] = history
        return result
    finally:
        conn.close()


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
        "statementAt": row["observed_at"] if row["statement_known"] else None,
        "effectiveFrom": row['effective_from'],
        "effectiveTo": row['effective_to'],
        "effectiveDateBasis": 'unknown' if not row['statement_known'] else 'explicit' if row['effective_from'] else 'statement_time',
        "learnedAt": row['extracted_at'],
        "sourceUpdatedAt": row['source_updated_at'],
        "temporalEvidence": row['temporal_evidence'],
        "extractedAt": row["extracted_at"],
        "provenance": {
            "meetingId": row["source_meeting_id"],
            "sourceType": "human_correction" if row["review_resolution_id"] else "transcript",
            "reviewResolutionId": row["review_resolution_id"],
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
