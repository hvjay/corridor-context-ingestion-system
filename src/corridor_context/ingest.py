from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import transcript_api
from .db import apply_fact, connect, existing_meeting_hash, finish_ingestion_run, get_active_fact_definitions, init_db, record_meeting, record_meeting_attempt, start_ingestion_run, transaction
from .extractors import SUPPORTED_EXTRACTORS, extract_meeting_facts
from .env import load_local_env


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
    extractor: str = "deterministic",
    verbose: bool = False,
) -> dict:
    conn = connect(db_path)
    init_db(conn, fact_config_path)
    if extractor not in SUPPORTED_EXTRACTORS:
        raise ValueError(f"Unsupported extractor {extractor!r}; expected one of {sorted(SUPPORTED_EXTRACTORS)}")
    summaries = {"seen": 0, "processed": 0, "skipped": 0, "failed": 0, "facts": 0, "extractor": extractor}
    run_id = start_ingestion_run(conn, base_url, updated_after, extractor, fact_config_path)
    summaries["run_id"] = run_id
    _log_verbose(verbose, f"started ingestion run {run_id} using {extractor}")

    try:
        meetings = transcript_api.list_meetings(base_url, updated_after)
        meetings.sort(key=lambda item: (item["updated_at"], item["created_at"], item["id"]))
        _log_verbose(verbose, f"found {len(meetings)} candidate meetings")
        definitions = get_active_fact_definitions(conn)

        for item in meetings:
            summaries["seen"] += 1
            meeting_id = item["id"]
            _log_verbose(verbose, f"processing {meeting_id}...")
            content_hash = None
            try:
                meeting = transcript_api.get_meeting(base_url, meeting_id)
                content_hash = stable_hash(meeting)
                if existing_meeting_hash(conn, meeting_id) == content_hash:
                    summaries["skipped"] += 1
                    with transaction(conn):
                        record_meeting_attempt(conn, run_id, meeting_id, content_hash, "skipped")
                    _log_verbose(verbose, f"skipped {meeting_id}: unchanged")
                    continue

                facts = extract_meeting_facts(meeting, definitions, extractor)
                with transaction(conn):
                    record_meeting(conn, meeting, content_hash, "processed")
                    for fact in facts:
                        apply_fact(conn, meeting_id, fact)
                    record_meeting_attempt(conn, run_id, meeting_id, content_hash, "processed", len(facts))
                summaries["processed"] += 1
                summaries["facts"] += len(facts)
                _log_verbose(verbose, f"processed {meeting_id}: {len(facts)} facts")
            except Exception as exc:  # Keep ingestion fault-tolerant per meeting.
                summaries["failed"] += 1
                with transaction(conn):
                    if "meeting" in locals() and isinstance(meeting, dict) and meeting.get("id") == meeting_id and content_hash:
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
    parser.add_argument("--extractor", choices=sorted(SUPPORTED_EXTRACTORS), default="deterministic")
    parser.add_argument("--verbose", action="store_true", help="Print per-meeting progress to stderr.")
    args = parser.parse_args()
    print(
        json.dumps(
            ingest(args.base_url, args.db, args.updated_after, args.facts_config, args.extractor, args.verbose),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
