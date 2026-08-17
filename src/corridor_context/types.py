from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class SourceSpan:
    excerpt: str
    start_time: Optional[str]
    end_time: Optional[str]


@dataclass(frozen=True)
class ExtractedFact:
    client_name: str
    fact_type: str
    normalized_value: Any
    display_value: str
    source: SourceSpan
    confidence: float
    reason: str
    update_kind: str
    observed_at: str
    extractor_name: str
    extractor_version: str


@dataclass(frozen=True)
class FactDefinition:
    fact_type: str
    display_name: str
    description: str
    value_kind: str
    value_schema: JsonDict
    extractor_name: str
    extractor_version: str
    active: bool = True
