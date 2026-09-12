from __future__ import annotations

from typing import Iterable, List

from .facts import extract_facts as extract_deterministic_facts
from .llm_attribution import analyze_attribution, facts_from_analysis
from .types import ExtractedFact, FactDefinition

SUPPORTED_EXTRACTORS = {"deterministic", "llm"}


def extract_meeting_facts(meeting: dict, definitions: Iterable[FactDefinition], extractor: str = "llm", decisions=None) -> List[ExtractedFact]:
    if extractor == "deterministic":
        return extract_deterministic_facts(meeting, definitions, decisions)
    if extractor == "llm":
        return facts_from_analysis(meeting, analyze_attribution(meeting, definitions) if decisions is None else decisions)
    raise ValueError(f"Unsupported extractor {extractor!r}; expected one of {sorted(SUPPORTED_EXTRACTORS)}")
