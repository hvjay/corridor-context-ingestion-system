from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import connect, get_active_fact_definitions, init_db
from .extractors import SUPPORTED_EXTRACTORS, extract_meeting_facts
from .transcript_api import get_meeting
from .env import load_local_env


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description="Dry-run fact extraction for one meeting without writing facts to the DB.")
    parser.add_argument("meeting_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", type=Path, default=Path("data/client_context.sqlite3"))
    parser.add_argument("--facts-config", type=Path)
    parser.add_argument("--extractor", choices=sorted(SUPPORTED_EXTRACTORS), default="deterministic")
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn, args.facts_config)
    definitions = get_active_fact_definitions(conn)
    conn.close()

    meeting = get_meeting(args.base_url, args.meeting_id)
    facts = extract_meeting_facts(meeting, definitions, args.extractor)
    print(json.dumps([_fact_to_json(fact) for fact in facts], indent=2, sort_keys=True))


def _fact_to_json(fact) -> dict:
    return {
        "client_name": fact.client_name,
        "fact_type": fact.fact_type,
        "value": fact.normalized_value,
        "display_value": fact.display_value,
        "source_excerpt": fact.source.excerpt,
        "source_start_time": fact.source.start_time,
        "source_end_time": fact.source.end_time,
        "confidence": fact.confidence,
        "reason": fact.reason,
        "update_kind": fact.update_kind,
        "observed_at": fact.observed_at,
        "extractor": {"name": fact.extractor_name, "version": fact.extractor_version},
    }


if __name__ == "__main__":
    main()
