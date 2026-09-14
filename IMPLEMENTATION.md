# Implementation notes

## Run the React review UI

From the project directory:

```bash
uv run corridor-review --demo
```

Open http://127.0.0.1:8765. The React bundle is checked in, so starting the UI does
not require Node or an API key. `--demo` uses the nine ambiguous transcript
scenarios in a separate temporary database, with explicitly labeled offline-rule
results. This previews the UI; it does not evaluate the LLM.

To browse your ingested database instead:

```bash
uv run corridor-review --db data/client_context.sqlite3
```

If port 8765 is already in use, stop the earlier server or pass `--port 8768`.
Without installing CLI entry points, the equivalent command is:

```bash
PYTHONPATH=src .venv/bin/python -m corridor_context.review_server --demo
```

Select a customer, inspect proposed values, and expand source evidence. Search
filters customers; Refresh fetches newly ingested records. Empty and error states
are handled. Each open review has a correction input: preview an agent proposal,
then explicitly apply it, or ignore the item without changing facts. Resolved
items remain accessible in the Resolved view.

## Develop the React app

React uses Material UI with a shared theme in `frontend/src/theme.js`. Customer
navigation and review cards live in `frontend/src/components/`; data fetching is
isolated in `frontend/src/hooks/useReviews.js`. Component styling uses the theme
and `sx` props, with only a minimal global CSS file. Dependencies are pinned in
`frontend/package-lock.json`. esbuild produces the self-contained assets served
by Python from `src/corridor_context/review_ui/`.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
# While editing React/CSS, in a second terminal:
npm --prefix frontend run dev
```

Keep the Python review server running. Watch mode rebuilds changed React/CSS;
refresh the browser to see edits. Re-run build after changing `frontend/index.html`.
Commit source and rebuilt assets together. The frontend never receives an API key.

## Ingest using LLM attribution and extraction

Create a local `.env` file using `.env.example`:

```dotenv
OPENAI_API_KEY=your-key
OPENAI_ATTRIBUTION_MODEL=gpt-5-mini
```

Start the transcript API, then ingest in another terminal:

```bash
node api_stub_server.js
uv run corridor-ingest --extractor llm --attribution llm --verbose
```

Both flags default to `llm`. One structured Responses API call per changed meeting
produces employer decisions, source-based explanations, evidence indexes, and
candidate facts. No regex attribution veto runs on this path. A clear pronoun can
be accepted when the full conversation establishes its referent. Mixed-employer
or uncertain passages remain held in full for review.

The selected customer still comes from trusted `target_client_key` metadata, or
exactly one configured attendee employer. The model cannot change the target or
create employers. `config/employers.json` contains stable keys, canonical names,
aliases, and exact attendee email domains. Set `CORRIDOR_EMPLOYERS_CONFIG` to use
another directory. Keep keys stable when renaming employers.

The response is validated before writes: complete ordered segment coverage,
recognized employer keys, valid evidence indexes, exact source quotations,
configured fact types, supported schema constraints, and target ownership for
accepted candidates. Invalid output, refusals, incomplete responses, missing keys,
and API failures fail that meeting and preserve its last committed client facts.
The meeting remains retryable; there is no silent fallback to rules.

Validated decisions, model name, policy version, explanations, and evidence are
stored separately from transcript input in `meetings.attribution_json`.
Database writes check the persisted decisions and proposed value without calling
the model again. The meeting hash includes model, attribution mode, policy,
employer directory, and fact definitions, so changes to these inputs trigger
reprocessing. The legacy confidence field is a fixed compatibility value, not a
calibrated probability; review the evidence and decision instead.

The request follows the official [Structured Outputs contract](https://developers.openai.com/api/docs/guides/structured-outputs).
Structured output constrains the response shape; it does not guarantee correct
employer reasoning. Evaluate live responses against representative transcripts
before production deployment. Automated LLM tests use mocked responses.

Dry-run a meeting with decisions and proposed review candidates:

```bash
uv run corridor-extract mtg_acme2026052001 --include-attribution
```

## Explicit offline baseline

The old regex policy lives in `attribution_rules.py` only as a baseline for demos
and regression tests. Offline extraction must be explicitly requested:

```bash
uv run corridor-ingest --extractor deterministic --attribution rules
uv run corridor-extract mtg_acme2026052001 --extractor deterministic --attribution rules --include-attribution
```

`--extractor deterministic --attribution llm` uses LLM attribution with the existing
value rules. `--extractor llm --attribution rules` is rejected to avoid mixing
incompatible decision formats. The normal LLM mode extracts values and attribution
in a single call, including review candidates for all configured fact types.

## Ambiguous transcript scenarios

`fixtures/ambiguous_transcripts.json` contains nine meetings covering former
employers, mixed headcounts, subsidiaries, unknown companies and broker pricing,
unidentified speakers, multiple customer attendees, alias/topic recovery,
unstructured statements, and a meeting about a different employer. The explicit
offline baseline produces 17 held passages and 19 proposed values. These counts
are baseline expectations, not promised LLM outputs.

```bash
STUB_DATA_FILE=fixtures/ambiguous_transcripts.json PORT=8001 node api_stub_server.js
# In another terminal, use real LLM analysis:
uv run corridor-ingest --base-url http://127.0.0.1:8001 --db data/ambiguity_test.sqlite3
uv run corridor-review --db data/ambiguity_test.sqlite3 --port 8767
```

The original `dummy_data.txt`, `dummy_data_initial.txt`, and
`dummy_data_updated.txt` fixtures remain available for the freshness demo.

## Storage and review evidence

SQLite uses WAL mode and per-meeting write transactions. `fact_versions` contains
accepted current and historical facts. `fact_definitions` is synced from
`config/facts.json`; each distinct config state is tracked in
`fact_definition_versions`. New configured types can be extracted by the LLM;
the offline baseline requires a matching rule.

`attribution_reviews` stores held passages and immutable snapshots of speaker,
timestamps, neighboring conversation, and any LLM-cited evidence. Its customer
key is the review destination, not an assertion of ownership.
`review_fact_candidates` stores proposed type, label, JSON value, display value,
source quote, model/version, and proposed subject key when known. LLM candidates
are saved directly from validated analysis, rather than being re-extracted with
rules. Unstructured passages remain visible for manual interpretation.

Old passage-only review records backfill once using offline rules. If the
original source no longer matches the stored meeting, surrounding context is
marked unavailable. Review records identify LLM versus offline/legacy provenance.
Identical evidence is deduplicated; a different model/policy analysis may create
a separate review record for audit. Source revisions retire superseded evidence from current selection while retaining fact history. Re-ingestion after a policy change can alter current selection; inspect a historical rebuild before deploying a policy change.

`ingestion_runs` and `meeting_processing_attempts` preserve success, skip, and
failure outcomes. Candidate records and accepted facts commit together, in
separate tables. Existing schemas migrate additively.

## Read APIs and MCP

The local review server binds only to loopback and serves React plus read
endpoints: `GET /api/customers` returns names, keys, and counts;
`GET /api/customers/{key}` returns scoped review records. Unknown keys return 404.
Each read uses a SQLite snapshot. There is no login or multi-user authorization
model in this takehome.

```bash
uv run corridor-context "Acme Benefits" --history
uv run corridor-mcp
```

MCP tool `get_client_context` accepts `{"client":"Acme Benefits","includeHistory":false}`
and returns current facts, normalized client key, context version, source meeting,
excerpts/timestamps, extractor/model version, and optional history. Missing/empty
client input is invalid; unknown customers return not found; storage failures
return internal errors. Reads use persisted context, never review candidates.

## Tests

```bash
uv run python -m unittest discover -s tests
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

Backend tests cover offline scenarios, LLM validation, a context-resolved pronoun,
wrong employers, fabricated evidence, missing segment coverage, invalid values,
API failure preservation, one-call ingestion, persisted decisions, and candidate
isolation. React tests cover evidence rendering and escaping transcript content.

Material UI styling uses an Emotion cache with a fresh nonce supplied by the
Python server for each HTML response. Script restrictions remain unchanged;
inline style attributes are allowed for component transitions. Restart the Python
server after upgrading from the earlier plain React UI so nonce support is active.

## Full meeting transcript in reviews

“View source & full transcript” shows every meeting passage in order, with speaker,
start/end timestamps, and the flagged passage highlighted. New review records save
an immutable full transcript in their evidence snapshot so subsequent meeting
edits cannot change the reviewed version. Existing nearby evidence is retained.

Older records without a full snapshot show the latest stored transcript only when
its meeting still resolves to the review's customer. The UI explicitly labels
that version as latest, not the original captured transcript. If the source
passage no longer matches, it is not highlighted. If no suitable full transcript
is available, the UI says so and retains the previously saved context.


## Human-directed corrections and resolution

Each review has **Correction instructions**, **Your name**, **Preview correction**,
and **Ignore without changes** controls. Example: “Set employee count to 25. The
200 employees refer to the previous employer.” The agent returns a before/after
proposal, an ignore proposal, or a clarification question. Only clicking **Apply
correction** writes the proposed changes. Editing instructions invalidates the
visible preview. A stale-proposal conflict requires generating a new proposal.

Agent planning uses the server-side `OPENAI_API_KEY` and the attribution model
setting. Without a key, the UI explains how to enable it; Ignore remains usable.
The demo isolates writes in its temporary database. Live LLM quality is not covered
by the mocked integration tests. Reviewer names are self-reported local audit
labels, not authenticated identities; this remains a loopback development app.

Supported operations are setting a configured fact, removing its current value
while retaining history, and resolving/ignoring without changing facts. Changes
are scoped to the review's customer. Instructions about another employer should
result in ignore or clarification, never reassignment. Pricing changes replace
the whole array, so the proposal must preserve plans not being changed.

`correction_proposals` retains instructions, reviewer, model, validated changes,
and the customer fact/definition baseline. `review_resolutions` records the final
action, instructions, reviewer, summary, and before/after snapshots. Each review
has at most one resolution. Repeated application of the same proposal is
idempotent; an already-resolved item cannot be silently resolved again.

Apply rechecks the current facts and definitions against the preview baseline.
Concurrent changes return 409 and leave facts untouched. Facts, source provenance,
context versions, and resolution records commit atomically. A human correction
uses its own source record rather than pretending that corrected text appeared
in the meeting. MCP provenance includes `sourceType: human_correction` and
`reviewResolutionId`; existing history is preserved. Removing a current fact also
advances the customer's context version. Future ingestion follows normal version
update rules; human corrections are not permanent locks on a fact.

Write routes (all JSON POSTs):

- `/api/customers/{key}/reviews/{id}/proposals`: `instruction`, `reviewer`, `requestId`.
- `/api/customers/{key}/reviews/{id}/apply`: saved `proposalId` only.
- `/api/customers/{key}/reviews/{id}/ignore`: optional `instruction`, `reviewer`, `requestId`.

The customer-list response includes a session `csrfToken`; React sends it in
`X-CSRF-Token`. Writes also validate Host, Origin when present, content type,
body size, customer/review association, fact types, values, and supporting
instruction excerpts. The agent cannot execute arbitrary SQL or modify another
customer. API errors use 400 for invalid input, 403 for origin/token failures,
404 for missing scoped records, 409 for stale/conflicting operations, and 503
when the agent is unavailable. No background LLM call is made by Ignore or Apply.

The Open view and customer badges count unresolved items. Resolved shows both
corrected and ignored items, including reviewer, instructions, summary, and time.

### Per-item agent recommendations

Every open review has **Suggest resolution**, requiring no reviewer instructions. This calls
`POST /api/customers/{key}/reviews/{id}/suggestion` with the existing CSRF protection. The agent
receives the target employer, current facts and schemas, flagged passage, saved context and full
available transcript (with historical/fallback provenance). It recommends updating facts,
ignoring the item, or asking a specific clarification question, with exact evidence quotes.
The prompt distinguishes preferences from confirmed budgets and resolves pronouns using context.

Recommendations are generated on demand and stored in SQLite `review_suggestions`; loading a
customer never triggers model calls. Repeated requests reuse saved advice. A change to customer
facts, definitions, employer configuration or source context marks advice stale and requires
regeneration. Changes during generation reject the result. Recommendations do not modify facts
or resolve reviews. **Use suggested instructions** lets the human adopt/edit actionable advice,
then use the existing preview-and-apply flow. Clarification recommendations never prefill answers.

Agent calls require `OPENAI_API_KEY`. Tests mock agent output and check persistence, evidence
validation, customer isolation, stale-state rejection and approval flow; they do not establish
live-model semantic accuracy. The $550 subsidiary/preference example and explicit Apex budget
example are included in the recommendation prompt as contrasting cases.

Actionable recommendations also offer **Accept AI suggestion**. The client submits only the saved
suggestion ID, reviewer and retry ID to `POST .../accept-suggestion`. The server checks customer
scope and suggestion freshness, translates the saved instructions through the validated correction
planner, and applies using the existing atomic, audited correction path. A changed action or
clarification is returned for further human review. Repeated acceptance requests return the existing
resolution. Clarification and stale suggestions cannot be accepted. The manual edit/preview flow remains available.

### Shared UI and MCP database

The current review workspace is persisted in `data/client_context.sqlite3`. It was copied from
the temporary demo workspace, preserving its sample review records and any saved state. The original
temporary database was retained. This switch does not turn unconfirmed candidates into accepted facts.

Start the UI on the same file used by MCP:

```bash
cd /Users/harrisvijayagopal/Documents/SWE/corridor_takehome
uv run corridor-review --db /Users/harrisvijayagopal/Documents/SWE/corridor_takehome/data/client_context.sqlite3 --port 8765
```

`mcp-config.json` contains a standard `mcpServers` entry with absolute command/database paths.
Add that entry using the other agent application's MCP settings; the application-specific import
location varies. It starts a local stdio server (not an HTTP endpoint). The UI remains at port 8765;
the agent launches its own MCP process against the same SQLite file. No API key is required for MCP reads.

Ask the connected agent to call `get_client_context` with:

```json
{"client":"Apex Manufacturing","includeHistory":true}
```

Each tool call reads persisted facts again. Accepted updates appear on the next call with their
human-correction provenance and prior history; no MCP restart or re-ingestion is required. The
agent must call the tool again rather than rely on an earlier answer. An employer with no accepted
facts/client record returns not found until its first fact is accepted. Ignore resolves a review
but does not create a client fact. `tests/test_mcp_updates.py` verifies fresh reads in one continuing
MCP subprocess before and after a correction using an isolated database and mocked model output.

Editing correction instructions or reviewer details keeps the existing preview visible, marks it out of date, and disables Apply. Use **Update preview** to regenerate it. The preview also shows the instructions used, so it remains clear which text produced the displayed changes. A failed refresh leaves the old preview visible and blocked.

After a successful Apply, Accept AI suggestion, or Ignore, the UI opens a confirmation dialog using the committed resolution returned by the server. It displays customer, summary, reviewer/time, and before/after values computed from the saved audit snapshots, or explicitly states that no fact values changed. The dialog survives the review list refresh and offers Done and View resolved items. Failed requests do not show a success confirmation.

Recommendation evidence uses request-scoped passage IDs constrained to the available sources in the structured output schema. The server validates these IDs and resolves them to original stored transcript text before saving/displaying evidence. The agent no longer reproduces exact quotes; punctuation or whitespace drift cannot cause a quote-match failure. Unknown or empty citations are still rejected without saving a suggestion or modifying facts. Existing saved recommendations remain compatible.

## Timeline reconciliation and older transcripts

Facts now have three separate time dimensions:

- `statementAt` (`observed_at` in SQLite) comes from the source segment timestamp, or explicit meeting `meeting_date`/`occurred_at`. Record creation and update timestamps are not meeting dates. A new fact with no reliable statement time fails ingestion and remains retryable rather than guessing from upload time.
- `effectiveFrom` and optional exclusive `effectiveTo` describe a stated period. The LLM extracts these separately from the value, with supporting `temporalEvidence` validated against the exact source excerpt. Without an explicit period, selection assumes applicability begins at statement time. Relative/uncertain dates should be held for human review. A benefit-cycle date value alone is not the effective date of the assertion.
- `learnedAt` is when this fact version was persisted (`extracted_at`). `sourceUpdatedAt` separately captures the API record's modification timestamp for that source revision. Neither determines which employer fact is current.

Every accepted assertion is appended. For a requested time, the reconciler filters out future,
expired, superseded-source, and retracted claims; selects the latest applicable effective start;
and uses latest statement time to break ties, followed by the latest source meeting `created_at`
when those timestamps match. Local ingestion time never establishes precedence. If source creation
metadata is unavailable or invalid, that tie-breaker is omitted. Still equally ranked different values are returned under
`conflicts`, with no winner in `facts`. Equivalent tied values select stable source identity rather
than arrival order. This is an explicit policy, not a claim that every business conflict is decidable
from timestamps. A finite temporary period ends exclusively and the underlying open-ended claim
can become applicable again.

Example: May says 25 employees; June says 30; May is uploaded in September. Current stays 30,
and May's evidence is retained. A July statement correcting May to 26 for May–June changes the
historical May result without changing June. A January 2027 change is stored now but not selected
until January. Readers select from the timeline inside one SQLite read transaction, so transitions
happen on the next read without cron or re-ingestion. The `is_current` column is a write-time
projection; API/MCP reads use the reconciler rather than relying on that projection as the clock moves.

Revised transcripts carry a source revision hash. Prior versions stay in history but no longer
participate in selection once the replacement revision commits—even if a fact was removed from the
new transcript. A failed revision preserves the last successful source and remains retryable. An
older API revision of the same meeting is skipped. Human removals create dated retractions, preventing
late older transcripts from resurrecting a removed fact; a later statement can establish a new value.
Human set corrections may specify an explicit period and display it in the preview. Dated removals
are not supported and require clarification.

MCP `get_client_context` adds optional `asOf`:

```json
{"client":"Acme Benefits","includeHistory":true,"asOf":"2026-05-20T23:59:59Z"}
```

CLI equivalent:

```bash
uv run corridor-context "Acme Benefits" --history --as-of 2026-05-20T23:59:59Z
```

Date-only inputs mean midnight UTC. `asOf` is **effective time using all evidence known now**, not
"what the system knew on that upload date." `contextVersion` advances for persisted evidence;
`selectionVersion` also identifies clock-driven changes to the selected facts/conflicts. History
includes future and superseded evidence, with `isCurrent` relative to the requested effective time.

Schema migration is additive and transactional. Legacy rows recover statement time only from actual
source/meeting timestamps; unknown-time rows remain in history but are not auto-selected. Existing
human retractions are recovered from resolution audits. Exact old source revision metadata cannot
be recreated where it was never stored. Back up the database before migration; old pending previews
may need regeneration because their baseline predates the timeline fields.

Validation includes out-of-order arrival, late edits to old meetings, temporal corrections, bounded
future periods, same-time disagreement, timezone offsets, missing timestamps, invalid periods,
source revisions/removal/failure/retry, retractions, and MCP as-of reads. LLM extraction tests use
mocked responses; schema/evidence checks do not prove live-model temporal interpretation accuracy.
