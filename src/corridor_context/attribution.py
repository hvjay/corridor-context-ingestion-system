"""Conservative employer attribution shared by extraction and persistence.

The directory is trusted configuration. Transcript text and model output cannot
create employers or aliases. Uncertain passages are retained for review.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

POLICY_VERSION = "employer-attribution-v1"
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[2] / "config" / "employers.json"


def employer_directory() -> list[dict]:
    rows = json.loads(Path(os.environ.get("CORRIDOR_EMPLOYERS_CONFIG", DEFAULT_DIRECTORY)).read_text())
    keys, names, domains = set(), set(), set()
    for row in rows:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["key"]) or row["key"] in keys:
            raise ValueError("Employer keys must be nonempty and unique")
        keys.add(row["key"])
        for name in [row["name"], *row.get("aliases", [])]:
            if not name.strip() or name.casefold() in names:
                raise ValueError("Employer names and aliases must be nonempty and unique")
            names.add(name.casefold())
        for domain in row.get("domains", []):
            if not domain or domain.casefold() in domains:
                raise ValueError("Employer email domains must be nonempty and unique")
            domains.add(domain.casefold())
    return rows


def mentions(text: str, employer: dict) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I)
               for name in [employer["name"], *employer.get("aliases", [])])


def email_employer(email: str, directory: list[dict]):
    domain = email.casefold().rsplit("@", 1)[-1] if "@" in email else ""
    return next((row for row in directory if domain in [d.casefold() for d in row.get("domains", [])]), None)


def resolve_target(meeting: dict, directory=None) -> dict:
    directory = employer_directory() if directory is None else directory
    # Optional upstream-selected key takes precedence over transcript mentions.
    key = meeting.get("target_client_key")
    if key is not None:
        target = next((row for row in directory if row["key"] == key), None)
        if target is None:
            raise ValueError("Unknown target_client_key; configure the employer before ingestion")
        return target
    attendees = {row["key"] for person in meeting.get("attendees", [])
                 if (row := email_employer(person.get("email", ""), directory))}
    if len(attendees) == 1:
        return next(row for row in directory if row["key"] in attendees)
    raise ValueError("Employer attribution needs target_client_key or exactly one configured attendee employer")


def assess_segments(meeting: dict, target=None, directory=None) -> list[dict]:
    """Offline baseline for fixtures. Production uses analyze_attribution."""
    from .attribution_rules import assess_segments as rules
    return rules(meeting, target, directory)


def validate_fact(meeting: dict, fact, decisions=None) -> None:
    """Recheck full source passages at the write boundary, including model output."""
    target = resolve_target(meeting)
    if fact.client_name != target["name"]:
        raise ValueError("Fact employer does not match meeting target")
    segments = meeting.get("transcript", [])
    decisions = assess_segments(meeting, target) if decisions is None else decisions
    matches = [i for i, segment in enumerate(segments)
               if fact.source.excerpt and fact.source.excerpt in segment.get("text", "")
               and fact.source.start_time == segment.get("start_time")
               and fact.source.end_time == segment.get("end_time")]
    if not matches or any(decisions[i]["status"] != "accepted" for i in matches):
        raise ValueError("Fact source is missing or employer attribution requires review")

    for index in matches:
        decision = decisions[index]
        if decision.get("engine") == "llm" and not any(
            c["fact_type"] == fact.fact_type and c["value"] == fact.normalized_value
            and c.get("subject_client_key") == target["key"]
            for c in decision.get("candidates", [])
        ):
            raise ValueError("Fact was not supported by persisted LLM attribution")
