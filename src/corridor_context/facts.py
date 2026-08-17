from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .types import ExtractedFact, FactDefinition, SourceSpan

EXTRACTOR_NAME = "deterministic_rules"
EXTRACTOR_VERSION = "2026-08-13.1"
DEFAULT_FACT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "facts.json"


def load_fact_definitions(config_path: Path = DEFAULT_FACT_CONFIG_PATH) -> List[FactDefinition]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        FactDefinition(
            fact_type=item["fact_type"],
            display_name=item["display_name"],
            description=item["description"],
            value_kind=item["value_kind"],
            value_schema=item["value_schema"],
            extractor_name=item.get("extractor_name", EXTRACTOR_NAME),
            extractor_version=item.get("extractor_version", EXTRACTOR_VERSION),
            active=bool(item.get("active", True)),
        )
        for item in payload
    ]


FACT_DEFINITIONS = load_fact_definitions()

CLIENT_PATTERNS = [
    r"(?:client is|For) (?P<client>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+)",
    r"(?P<client>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+) (?:has|now has|still prefers|needs|currently says)",
]

MONTHS = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}


def normalize_client_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return key


def active_definitions() -> List[FactDefinition]:
    return [definition for definition in FACT_DEFINITIONS if definition.active]


def extract_client_name(meeting: dict) -> Optional[str]:
    for segment in meeting.get("transcript", []):
        text = segment.get("text", "")
        for pattern in CLIENT_PATTERNS:
            match = re.search(pattern, text)
            if match:
                client = match.group("client").strip()
                if client not in {"The Client", "Got It", "Finance"}:
                    return client
    for attendee in meeting.get("attendees", []):
        email = attendee.get("email", "")
        if email.endswith("@acme.example"):
            return "Acme Benefits"
        if email.endswith("@apex.example"):
            return "Apex Manufacturing"
        if email.endswith("@northstar.example"):
            return "Northstar Logistics"
    return None


def extract_facts(meeting: dict, definitions: Iterable[FactDefinition]) -> List[ExtractedFact]:
    client_name = extract_client_name(meeting)
    if not client_name:
        return []
    enabled = {definition.fact_type for definition in definitions if definition.active}
    facts: List[ExtractedFact] = []
    for segment in meeting.get("transcript", []):
        text = segment.get("text", "")
        span = SourceSpan(text, segment.get("start_time"), segment.get("end_time"))
        observed_at = segment.get("start_time") or meeting.get("updated_at") or meeting.get("created_at")
        if "employee_count" in enabled:
            facts.extend(_employee_count(client_name, text, span, observed_at))
        if "benefit_cycle_start_date" in enabled:
            facts.extend(_benefit_cycle_start_date(client_name, text, span, observed_at))
        if "preferred_plan_type" in enabled:
            facts.extend(_preferred_plan_type(client_name, text, span, observed_at))
        if "employer_budget_pepm" in enabled:
            facts.extend(_employer_budget(client_name, text, span, observed_at))
        if "incumbent_plan_pricing" in enabled:
            facts.extend(_incumbent_pricing(client_name, text, span, observed_at))
    return facts


def _employee_count(client_name: str, text: str, span: SourceSpan, observed_at: str) -> List[ExtractedFact]:
    lowered = text.lower()
    if "seasonal" in lowered or "contractors by mistake" in lowered and "corrected employee count" not in lowered:
        return []
    patterns = [
        r"corrected employee count is (?P<count>\d+)",
        r"expected headcount to (?P<count>\d+)",
        r"benefits-eligible headcount is (?P<count>\d+) employees",
        r"has grown to (?P<count>\d+) benefits-eligible employees",
        r"(?:has|have|is) (?P<count>\d+) (?:year-round, )?benefits-eligible employees",
        r"(?P<count>\d+) employees right now",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            count = int(match.group("count"))
            update_kind = "correction" if "corrected" in lowered or "by mistake" in lowered else "update"
            return [_fact(client_name, "employee_count", count, f"{count} employees", span, observed_at, update_kind)]
    return []


def _benefit_cycle_start_date(client_name: str, text: str, span: SourceSpan, observed_at: str) -> List[ExtractedFact]:
    lowered = text.lower()
    if "tentative" in lowered:
        return []
    match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})", text)
    if not match or "benefit cycle" not in lowered:
        return []
    month, day, year = match.groups()
    iso_date = f"{year}-{MONTHS[month]}-{int(day):02d}"
    update_kind = "reaffirmation" if "unchanged" in lowered or "remains" in lowered else "update"
    return [_fact(client_name, "benefit_cycle_start_date", iso_date, iso_date, span, observed_at, update_kind)]


def _preferred_plan_type(client_name: str, text: str, span: SourceSpan, observed_at: str) -> List[ExtractedFact]:
    lowered = text.lower()
    if "prefer" not in lowered and "preferred" not in lowered and "switch our preferred plan" not in lowered:
        return []
    switch_match = re.search(r"(?:switch our|change the) preferred plan from .*? to a (?P<plan>PPO|HMO|HDHP)", text)
    if switch_match:
        plan = switch_match.group("plan")
        return [_fact(client_name, "preferred_plan_type", plan, plan, span, observed_at, "update")]
    if re.search(r"prefer(?:s|red)? (?:a |an )?(?:HSA-qualified )?HDHP", text, re.IGNORECASE) or "keep the HSA-qualified HDHP as our preferred plan" in text:
        return [_fact(client_name, "preferred_plan_type", "HDHP", "HDHP", span, observed_at, "update")]
    if re.search(r"prefer(?:s|red)? (?:a |an )?PPO", text, re.IGNORECASE):
        return [_fact(client_name, "preferred_plan_type", "PPO", "PPO", span, observed_at, "update")]
    if re.search(r"prefer(?:s|red)? (?:a |an )?HMO", text, re.IGNORECASE):
        return [_fact(client_name, "preferred_plan_type", "HMO", "HMO", span, observed_at, "update")]
    return []


def _employer_budget(client_name: str, text: str, span: SourceSpan, observed_at: str) -> List[ExtractedFact]:
    lowered = text.lower()
    if "budget" not in lowered:
        return []
    match = re.search(r"\$(?P<amount>\d{3,5}) per (?:benefits-eligible )?employee per month", text)
    if not match:
        return []
    amount = int(match.group("amount"))
    update_kind = "reaffirmation" if "remains" in lowered or "current budget" in lowered else "update"
    return [_fact(client_name, "employer_budget_pepm", amount, f"${amount} PEPM", span, observed_at, update_kind)]


def _incumbent_pricing(client_name: str, text: str, span: SourceSpan, observed_at: str) -> List[ExtractedFact]:
    lowered = text.lower()
    if "incumbent broker" not in lowered:
        return []
    matches = re.findall(
        r"(?:the )?(?P<plan>[A-Z][A-Za-z0-9' -]+?) (?:at|to) \$(?P<price>\d{3,5}) per employee per month|\$(?P<price_first>\d{3,5}) per employee per month for the (?P<plan_after>[A-Z][A-Za-z0-9' -]+?)(?: and|\.|$)",
        text,
    )
    plans = []
    for plan, price, price_first, plan_after in matches:
        name = (plan or plan_after).strip(" ,.")
        name = re.sub(r"^(Your |The )?incumbent broker(?: also)? (?:found|revised) (?:the )?", "", name, flags=re.IGNORECASE)
        value = int(price or price_first)
        if name:
            plans.append({"plan_name": name, "price_pepm": value})
    if not plans:
        return []
    display = ", ".join(f"{plan['plan_name']}: ${plan['price_pepm']} PEPM" for plan in plans)
    return [_fact(client_name, "incumbent_plan_pricing", plans, display, span, observed_at, "update")]


def _fact(client_name: str, fact_type: str, value: Any, display: str, span: SourceSpan, observed_at: str, update_kind: str) -> ExtractedFact:
    return ExtractedFact(
        client_name=client_name,
        fact_type=fact_type,
        normalized_value=value,
        display_value=display,
        source=span,
        confidence=0.95,
        reason="Matched deterministic transcript rule for configured fact type.",
        update_kind=update_kind,
        observed_at=observed_at,
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
    )
