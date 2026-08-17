from __future__ import annotations

from typing import Iterable, List

from .facts import extract_facts as extract_deterministic_facts
from .llm_extractor import extract_facts_with_llm
from .types import ExtractedFact, FactDefinition

SUPPORTED_EXTRACTORS = {"deterministic", "llm"}


def extract_meeting_facts(meeting: dict, definitions: Iterable[FactDefinition], extractor: str = "deterministic") -> List[ExtractedFact]:
    if extractor == "deterministic":
        return extract_deterministic_facts(meeting, definitions)
    if extractor == "llm":
        return extract_facts_with_llm(meeting, definitions)
    raise ValueError(f"Unsupported extractor {extractor!r}; expected one of {sorted(SUPPORTED_EXTRACTORS)}")
