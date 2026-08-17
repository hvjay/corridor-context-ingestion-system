from __future__ import annotations

import argparse
import json
from pathlib import Path

from .context import get_client_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Read persisted client context.")
    parser.add_argument("client")
    parser.add_argument("--db", type=Path, default=Path("data/client_context.sqlite3"))
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(get_client_context(args.db, args.client, args.history), indent=2, sort_keys=True))
    except (KeyError, ValueError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
