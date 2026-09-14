# Backfilled May meeting

These synthetic fixtures exercise late discovery of a meeting whose upstream timestamps are also old. Run from the repository root, using a fresh test database for reproducible results.

1. Serve June's meeting (30 employees):

```bash
STUB_DATA_FILE=fixtures/dummy_data_backfill_initial.json PORT=8002 node api_stub_server.js
```

2. In another terminal, ingest it:

```bash
uv run corridor-ingest --base-url http://127.0.0.1:8002 --db data/backfill_test.sqlite3
```

3. Stop that stub with Ctrl-C and restart it with the backfilled snapshot:

```bash
STUB_DATA_FILE=fixtures/dummy_data_backfilled.json PORT=8002 node api_stub_server.js
```

4. Run the same ingestion command again. The June meeting is unchanged; the newly discovered May meeting says 25 employees. May has created_at 2026-05-01T10:00:00Z, updated_at 2026-05-01T11:00:00Z, and statement time 2026-05-01T10:05:00Z. June's corresponding dates are June 1. Neither fact specifies an effective period, so statement time determines applicability.

5. Compare current and historical context:

```bash
uv run python -m corridor_context.context_cli 'Acme Benefits' --db data/backfill_test.sqlite3 --history
uv run python -m corridor_context.context_cli 'Acme Benefits' --db data/backfill_test.sqlite3 --as-of 2026-05-15
```

Expected: current employee_count remains 30, May 15 returns 25, and history contains both statements. learnedAt reflects when each fact was saved locally, independently of the old upstream timestamps. Repeating ingestion skips both unchanged meetings.

The default commands use the LLM and require OPENAI_API_KEY. For an offline timeline check, add `--extractor deterministic --attribution rules` to both ingestion commands; these simple statements work with either mode.

Do not pass an updated_after cutoff later than May: the API would omit the old meeting. This tests reconciliation after discovery; discovering old records behind a polling watermark requires a full scan or a separate backfill scan. Both fixtures must be ingested in sequence to demonstrate late arrival; loading only the final snapshot does not demonstrate upload-order independence.

To query this isolated database through MCP, launch the MCP server with `--db` pointing to the absolute path of data/backfill_test.sqlite3. The existing main-database MCP connection will not show these test records.
