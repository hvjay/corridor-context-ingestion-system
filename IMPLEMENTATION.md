# Implementation Notes

## Run commands

Start the transcript stub in one terminal:

```bash
node api_stub_server.js
```

Fixture data lives in `fixtures/`:

- `dummy_data.txt`: original sample transcript set used by default by the stub API.
- `dummy_data_initial.txt`: initial freshness-demo state.
- `dummy_data_updated.txt`: updated freshness-demo state with an Acme Benefits employee-count correction.

Ingest transcripts into SQLite:

```bash
uv run corridor-ingest --extractor deterministic
uv run corridor-ingest --extractor llm --verbose
```

Read a client context directly:

```bash
uv run corridor-context "Acme Benefits" --history
```

Dry-run extraction for one meeting without writing facts to the DB:

```bash
uv run corridor-extract mtg_acme2026052001 --extractor deterministic
OPENAI_API_KEY=... uv run corridor-extract mtg_acme2026052001 --extractor llm
```

Run the MCP stdio server:

```bash
uv run corridor-mcp
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Storage model

The database lives at `data/client_context.sqlite3` by default. SQLite runs in WAL mode so readers can keep reading a consistent snapshot while ingestion writes in transactions. Each pipeline execution creates an `ingestion_runs` row, and every processed, skipped, or failed meeting creates a `meeting_processing_attempts` row linked to that run.

`fact_definitions` stores fact types as data and is synced from `config/facts.json` whenever the pipeline starts. New facts can be added without changing `fact_versions`; set `active = true` when normal ingestion should extract them. Set `active = false` to keep a definition documented while skipping it in daily ingestion. Use `uv run corridor-ingest --facts-config path/to/facts.json` to test an alternate fact list. Existing DB rows are updated to match the config, including the `active` flag. Each distinct definition state is also appended to `fact_definition_versions`, and extracted facts store `fact_definition_version_id` so a reader can audit which definition/config produced a value.

`fact_versions` stores every extracted value. Current values are marked with `is_current = 1`; older values remain for audit history. Ingestion flips the previous current row and inserts the new current row inside one transaction.

## MCP tool contract

Tool name: `get_client_context`

Input:

```json
{
  "client": "Acme Benefits",
  "includeHistory": false
}
```

Output includes the normalized client key, context version, current facts, source meeting IDs, source excerpts/timestamps, extractor version, confidence, and optional history.

Errors:

- Invalid request: missing or empty `client`.
- Not found: no persisted client context for the requested client.
- Internal error: database or server failure.

## Dynamic facts

To add a new fact such as `average_employee_age`, add an object to `config/facts.json` with a stable `fact_type`, value schema, extractor name/version, and `active` flag. Future ingestion will extract it when active once a matching extractor exists. Manual backfill can be implemented by reprocessing stored transcripts for selected clients/date ranges using the same `fact_versions` table.

## Extractor modes

The deterministic extractor in `facts.py` remains available and is the default. Use `--extractor llm` to call the optional OpenAI structured-output extractor in `llm_extractor.py`; it requires `OPENAI_API_KEY` and can be compared with `uv run corridor-extract ...` before writing anything to SQLite. Add `--verbose` to `corridor-ingest` to print per-meeting progress to stderr while keeping the final JSON summary on stdout. The LLM returns structured facts, but database writes still go through the same validation/provenance/transaction path.

Create a local `.env` file for API-backed extraction:

```bash
OPENAI_API_KEY=sk-...
# Optional:
OPENAI_EXTRACT_MODEL=gpt-5-mini
```

The CLI commands load `.env` automatically via `python-dotenv`; `.env` is ignored by git.

## Fault tolerance audit trail

`ingestion_runs` records each on-demand or cron invocation with status, extractor mode, counters, and any run-level error. `meeting_processing_attempts` records every per-meeting outcome, including skipped meetings and failures. Client context updates remain all-or-nothing per meeting: facts are only updated in the same transaction as the successful meeting record and attempt row. Failed attempts are retained for retries/debugging without changing the last committed current context.
