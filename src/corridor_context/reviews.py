"""Persist unverified candidates separately from accepted client facts."""
from __future__ import annotations

import hashlib
import json
import re

from .attribution import POLICY_VERSION, employer_directory, mentions, resolve_target
from .facts import (_employee_count, _benefit_cycle_start_date, _preferred_plan_type,
                    _employer_budget, _incumbent_pricing)
from .types import SourceSpan

CANDIDATE_VERSION = "review-candidates-v1"
RULES = {
    "employee_count": _employee_count,
    "benefit_cycle_start_date": _benefit_cycle_start_date,
    "preferred_plan_type": _preferred_plan_type,
    "employer_budget_pepm": _employer_budget,
    "incumbent_plan_pricing": _incumbent_pricing,
}


def candidate_facts(excerpt, definitions):
    """These values are proposals, never ExtractedFacts eligible for apply_fact.

    Keep the full passage alongside each proposal. Split clauses only to recover
    multiple values, not to decide which employer owns them.
    """
    parts = re.split(r"(?<=[.!?;])\s+|,?\s+(?:and|but|whereas)\s+(?=[A-Z]|they\b|we\b)", excerpt)
    candidates, seen = [], set()
    for definition in definitions:
        rule = RULES.get(definition.fact_type)
        if not definition.active or rule is None:
            continue
        for part in parts:
            for fact in rule("Unconfirmed employer", part, SourceSpan(part, None, None), ""):
                key = (fact.fact_type, json.dumps(fact.normalized_value, sort_keys=True))
                if key not in seen:
                    seen.add(key)
                    candidates.append({"fact_type": fact.fact_type, "label": definition.display_name,
                                       "value": fact.normalized_value, "display_value": fact.display_value,
                                       "excerpt": part})
    # Review extraction can retain ordinary headcount wording that the stricter
    # accepted-fact rules omit. The source remains explicitly unverified.
    definition = next((d for d in definitions if d.active and d.fact_type == "employee_count"), None)
    if definition:
        for match in re.finditer(r"\b(\d+) (?:benefits-eligible |year-round, benefits-eligible )?employees\b", excerpt):
            value = int(match.group(1))
            key = ("employee_count", json.dumps(value))
            if key not in seen:
                seen.add(key)
                candidates.append({"fact_type": "employee_count", "label": definition.display_name,
                                   "value": value, "display_value": f"{value} employees", "excerpt": match.group(0)})
    return candidates


def _snapshot(meeting, index, excerpt, capture_transcript=True):
    segments = meeting.get("transcript", [])
    if 0 <= index < len(segments) and segments[index].get("text") == excerpt:
        snapshot = {"segment": segments[index], "context": [
            {"index": i, **segments[i]} for i in range(max(0, index - 1), min(len(segments), index + 2))]}
        if capture_transcript:
            snapshot["transcript"] = [{**segment, "index": i} for i, segment in enumerate(segments)]
        return snapshot
    # A legacy meeting may have been overwritten. Never invent original context.
    return {"segment": {"text": excerpt}, "context": []}


def populate_candidates(conn, review_id, excerpt, definitions, candidates=None, version=CANDIDATE_VERSION):
    candidates = candidate_facts(excerpt, definitions) if candidates is None else candidates
    for ordinal, candidate in enumerate(candidates):
        conn.execute("""INSERT OR IGNORE INTO review_fact_candidates
            (review_id, ordinal, fact_type, label, value_json, display_value, source_excerpt, extractor_version, subject_client_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (review_id, ordinal, candidate["fact_type"], candidate["label"],
             json.dumps(candidate["value"], sort_keys=True), candidate["display_value"],
             candidate["excerpt"], version, candidate.get("subject_client_key")))
    conn.execute("UPDATE attribution_reviews SET candidate_version = ? WHERE id = ?", (version, review_id))


def record_reviews(conn, run_id, meeting, target, decisions, definitions):
    for decision in decisions:
        if decision["status"] != "review":
            continue
        snapshot = _snapshot(meeting, decision["segment_index"], decision["excerpt"])
        if decision.get("engine") == "llm":
            snapshot["attribution"] = {k: decision[k] for k in ("engine", "model", "version", "reason", "evidence_segment_indexes")}
            snapshot["evidence"] = [{"index": i, **meeting["transcript"][i]} for i in decision["evidence_segment_indexes"]]
        fingerprint = json.dumps([meeting["id"], target["key"], decision["segment_index"], snapshot], sort_keys=True)
        key = hashlib.sha256(fingerprint.encode()).hexdigest()
        row = conn.execute("SELECT id FROM attribution_reviews WHERE review_key = ?", (key,)).fetchone()
        if row:
            continue
        legacy_snapshot = {k: v for k, v in snapshot.items() if k != "transcript"}
        legacy_key = hashlib.sha256(json.dumps([meeting["id"], target["key"], decision["segment_index"], legacy_snapshot], sort_keys=True).encode()).hexdigest()
        legacy = conn.execute("SELECT snapshot_json FROM attribution_reviews WHERE review_key = ?", (legacy_key,)).fetchone()
        if legacy and "transcript" not in json.loads(legacy["snapshot_json"] or "{}"):
            continue
        review_id = conn.execute("""INSERT INTO attribution_reviews
            (ingestion_run_id, meeting_id, target_client_key, segment_index, source_excerpt,
             reason, policy_version, review_key, snapshot_json, target_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, meeting["id"], target["key"], decision["segment_index"], decision["excerpt"],
             decision["reason"], decision.get("version", POLICY_VERSION), key, json.dumps(snapshot), target["name"])).lastrowid
        version = decision["version"] + ":" + decision["model"] if decision.get("engine") == "llm" else CANDIDATE_VERSION
        populate_candidates(conn, review_id, decision["excerpt"], definitions, decision.get("candidates"), version)


def backfill_reviews(conn, definitions):
    """Populate previously recorded passages once, without modifying client facts."""
    rows = conn.execute("""SELECT ar.*, m.payload_json FROM attribution_reviews ar
        LEFT JOIN meetings m ON m.id = ar.meeting_id WHERE ar.candidate_version IS NULL""").fetchall()
    names = {e["key"]: e["name"] for e in employer_directory()}
    for row in rows:
        snapshot = _snapshot(json.loads(row["payload_json"] or "{}"), row["segment_index"], row["source_excerpt"], capture_transcript=False)
        conn.execute("UPDATE attribution_reviews SET snapshot_json = ?, target_name = ? WHERE id = ?",
                     (json.dumps(snapshot), names.get(row["target_client_key"], row["target_client_key"]), row["id"]))
        fingerprint = json.dumps([row["meeting_id"], row["target_client_key"], row["segment_index"], snapshot], sort_keys=True)
        key = hashlib.sha256(fingerprint.encode()).hexdigest()
        if not conn.execute("SELECT id FROM attribution_reviews WHERE review_key = ?", (key,)).fetchone():
            conn.execute("UPDATE attribution_reviews SET review_key = ? WHERE id = ?", (key, row["id"]))
        populate_candidates(conn, row["id"], row["source_excerpt"], definitions)


def customers(conn):
    names = {e["key"]: e["name"] for e in employer_directory()}
    for row in conn.execute("SELECT client_key, display_name FROM clients"):
        names.setdefault(row["client_key"], row["display_name"])
    counts = {}
    for row in conn.execute("""SELECT ar.target_client_key, MAX(ar.target_name) AS name,
                               SUM(CASE WHEN rr.id IS NULL THEN 1 ELSE 0 END) AS count
                               FROM attribution_reviews ar LEFT JOIN review_resolutions rr ON rr.review_id = ar.id
                               GROUP BY ar.target_client_key"""):
        names.setdefault(row["target_client_key"], row["name"] or row["target_client_key"])
        counts[row["target_client_key"]] = row["count"]
    return [{"key": key, "name": name, "review_count": counts.get(key, 0)}
            for key, name in sorted(names.items(), key=lambda item: item[1])]


def transcript_for_review(row, snapshot):
    if "transcript" in snapshot:
        return {"origin": "captured", "segments": snapshot["transcript"], "flagged_index": row["segment_index"]}
    # Legacy reviews only saved nearby passages. Expose the current meeting
    # explicitly as a newer source, never as the historical captured version.
    meeting = json.loads(row["latest_payload_json"] or "{}")
    try:
        same_customer = resolve_target(meeting)["key"] == row["target_client_key"]
    except ValueError:
        same_customer = False
    if not same_customer:
        return {"origin": "unavailable", "segments": [], "flagged_index": None}
    segments = meeting.get("transcript", [])
    index = row["segment_index"]
    matches = 0 <= index < len(segments) and segments[index].get("text") == row["source_excerpt"]
    return {"origin": "latest", "segments": [{**segment, "index": i} for i, segment in enumerate(segments)],
            "flagged_index": index if matches else None}


def customer_reviews(conn, key):
    customer = next((row for row in customers(conn) if row["key"] == key), None)
    if customer is None:
        raise KeyError("Customer not found")
    from .suggestions import saved_suggestion
    directory = employer_directory()
    rows = conn.execute("""SELECT ar.*, m.payload_json AS latest_payload_json
        FROM attribution_reviews ar LEFT JOIN meetings m ON m.id = ar.meeting_id
        WHERE ar.target_client_key = ? ORDER BY ar.id DESC""", (key,)).fetchall()
    results = []
    for row in rows:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        candidates = [dict(c) for c in conn.execute("""SELECT fact_type, label, value_json, display_value,
                      source_excerpt, extractor_version, subject_client_key FROM review_fact_candidates WHERE review_id = ? ORDER BY ordinal""", (row["id"],))]
        for candidate in candidates:
            candidate["value"] = json.loads(candidate.pop("value_json"))
        resolution_row = conn.execute("SELECT action, instruction, reviewer, summary, created_at FROM review_resolutions WHERE review_id = ?", (row["id"],)).fetchone()
        resolution = dict(resolution_row) if resolution_row else None
        results.append({"suggestion": saved_suggestion(conn, key, row["id"]), "status": resolution["action"] if resolution else "open", "resolution": resolution, "id": row["id"], "meeting_id": row["meeting_id"], "segment_index": row["segment_index"],
                        "excerpt": row["source_excerpt"], "reason": row["reason"], "created_at": row["created_at"],
                        "snapshot": snapshot, "transcript": transcript_for_review(row, snapshot), "candidates": candidates,
                        "mentioned_employers": [e["name"] for e in directory if mentions(row["source_excerpt"], e)]})
    return {"customer": customer, "reviews": results}
