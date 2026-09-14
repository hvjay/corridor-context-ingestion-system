from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .attribution import POLICY_VERSION, assess_segments, employer_directory, resolve_target
from .llm_attribution import analyze_attribution, model_name, VERSION as LLM_POLICY_VERSION
from .reviews import CANDIDATE_VERSION, record_reviews, backfill_reviews
from . import transcript_api
from .db import apply_fact, connect, existing_meeting_hash, finish_ingestion_run, get_active_fact_definitions, init_db, record_meeting, record_meeting_attempt, start_ingestion_run, transaction
from .extractors import SUPPORTED_EXTRACTORS, extract_meeting_facts
from .env import load_local_env
from .timeline import POLICY_VERSION as TIMELINE_VERSION, reconcile, instant


def stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _log_verbose(verbose: bool, message: str) -> None:
    if verbose:
        print(message, file=sys.stderr, flush=True)


def ingest(
    base_url: str,
    db_path: Path,
    updated_after: str | None = None,
    fact_config_path: Path | None = None,
    extractor: str = "llm",
    verbose: bool = False,
    attribution: str = "llm",
) -> dict:
    load_local_env()
    if attribution not in {"llm", "rules"}:
        raise ValueError("Attribution must be llm or rules")
    if attribution == "rules" and extractor == "llm":
        raise ValueError("Use --extractor deterministic with --attribution rules")
    conn = connect(db_path)
    init_db(conn, fact_config_path)
    if extractor not in SUPPORTED_EXTRACTORS:
        raise ValueError(f"Unsupported extractor {extractor!r}; expected one of {sorted(SUPPORTED_EXTRACTORS)}")
    summaries = {"seen": 0, "processed": 0, "skipped": 0, "failed": 0, "facts": 0, "review_segments": 0, "extractor": extractor}
    run_id = start_ingestion_run(conn, base_url, updated_after, extractor, fact_config_path)
    summaries["run_id"] = run_id
    _log_verbose(verbose, f"started ingestion run {run_id} using {extractor}")

    try:
        meetings = transcript_api.list_meetings(base_url, updated_after)
        meetings.sort(key=lambda item: (item["updated_at"], item["created_at"], item["id"]))
        _log_verbose(verbose, f"found {len(meetings)} candidate meetings")
        definitions = get_active_fact_definitions(conn)
        with transaction(conn):
            backfill_reviews(conn, definitions)

        for item in meetings:
            summaries["seen"] += 1
            meeting_id = item["id"]
            _log_verbose(verbose, f"processing {meeting_id}...")
            content_hash = None
            try:
                meeting = transcript_api.get_meeting(base_url, meeting_id)
                previous = conn.execute("SELECT updated_at FROM meetings WHERE id=? AND processing_status='processed'", (meeting_id,)).fetchone()
                if previous and instant(meeting['updated_at']) < instant(previous['updated_at']):
                    summaries['skipped'] += 1
                    with transaction(conn):
                        record_meeting_attempt(conn, run_id, meeting_id, None, 'skipped')
                    _log_verbose(verbose, f'skipped {meeting_id}: older source revision')
                    continue
                content_hash = stable_hash({"meeting": meeting, "timeline_policy": TIMELINE_VERSION, "attribution_policy": POLICY_VERSION,
                                            "employers": employer_directory(), "extractor": extractor, "candidate_version": CANDIDATE_VERSION, "attribution": attribution,
                                            "llm_policy": LLM_POLICY_VERSION, "model": model_name(),
                                            "definitions": [asdict(d) for d in definitions]})
                if existing_meeting_hash(conn, meeting_id) == content_hash:
                    summaries["skipped"] += 1
                    with transaction(conn):
                        record_meeting_attempt(conn, run_id, meeting_id, content_hash, "skipped")
                    _log_verbose(verbose, f"skipped {meeting_id}: unchanged")
                    continue

                target = resolve_target(meeting)
                decisions = analyze_attribution(meeting, definitions) if attribution == "llm" else assess_segments(meeting, target)
                facts = extract_meeting_facts(meeting, definitions, extractor, decisions)
                with transaction(conn):
                    record_meeting(conn, meeting, content_hash, "processed", decisions=decisions)
                    record_reviews(conn, run_id, meeting, target, decisions, definitions)
                    for fact in facts:
                        apply_fact(conn, meeting_id, fact)
                    client = conn.execute('SELECT id FROM clients WHERE client_key=?', (target['key'],)).fetchone()
                    if client:
                        reconcile(conn, client['id'])
                        if not facts:
                            conn.execute('INSERT INTO client_context_versions(client_id,source_meeting_id) VALUES (?,?)', (client['id'],meeting_id))
                    record_meeting_attempt(conn, run_id, meeting_id, content_hash, "processed", len(facts))
                summaries["review_segments"] += sum(d["status"] == "review" for d in decisions)
                summaries["processed"] += 1
                summaries["facts"] += len(facts)
                _log_verbose(verbose, f"processed {meeting_id}: {len(facts)} facts")
            except Exception as exc:  # Keep ingestion fault-tolerant per meeting.
                summaries["failed"] += 1
                with transaction(conn):
                    if "meeting" in locals() and isinstance(meeting, dict) and meeting.get("id") == meeting_id and content_hash and existing_meeting_hash(conn, meeting_id) is None:
                        record_meeting(conn, meeting, content_hash, "failed", str(exc))
                    record_meeting_attempt(conn, run_id, meeting_id, content_hash, "failed", 0, str(exc))
                _log_verbose(verbose, f"failed {meeting_id}: {exc}")
        finish_ingestion_run(conn, run_id, summaries)
        _log_verbose(
            verbose,
            "finished ingestion run "
            f"{run_id}: processed={summaries['processed']} skipped={summaries['skipped']} "
            f"failed={summaries['failed']} facts={summaries['facts']}",
        )
    except Exception as exc:
        finish_ingestion_run(conn, run_id, summaries, str(exc))
        raise
    finally:
        conn.close()
    return summaries


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description="Ingest Corridor transcript meetings into client context storage.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", type=Path, default=Path("data/client_context.sqlite3"))
    parser.add_argument("--updated-after")
    parser.add_argument("--facts-config", type=Path, help="Path to fact definition JSON config.")
    parser.add_argument("--extractor", choices=sorted(SUPPORTED_EXTRACTORS), default="llm")
    parser.add_argument("--attribution", choices=["llm", "rules"], default="llm", help="LLM reasoning by default; rules is an explicit offline baseline.")
    parser.add_argument("--verbose", action="store_true", help="Print per-meeting progress to stderr.")
    args = parser.parse_args()
    print(
        json.dumps(
            ingest(args.base_url, args.db, args.updated_after, args.facts_config, args.extractor, args.verbose, args.attribution),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
