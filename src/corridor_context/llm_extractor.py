from __future__ import annotations

import json
import os
from typing import Any, Iterable, List
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .env import load_local_env
from .facts import extract_client_name
from .types import ExtractedFact, FactDefinition, SourceSpan

EXTRACTOR_NAME = "openai_structured_outputs"
EXTRACTOR_VERSION = "2026-08-13.1"
DEFAULT_MODEL = "gpt-5-mini"


def extract_facts_with_llm(meeting: dict, definitions: Iterable[FactDefinition]) -> List[ExtractedFact]:
    load_local_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when --extractor llm is used")

    active_definitions = [definition for definition in definitions if definition.active]
    if not active_definitions:
        return []

    result = _call_openai(api_key, meeting, active_definitions)
    client_name = result.get("client_name") or extract_client_name(meeting)
    if not client_name:
        return []

    segments = meeting.get("transcript", [])
    allowed_fact_types = {definition.fact_type for definition in active_definitions}
    facts: List[ExtractedFact] = []
    for item in result.get("facts", []):
        fact_type = item.get("fact_type")
        if fact_type not in allowed_fact_types:
            continue
        segment_index = item.get("segment_index")
        if not isinstance(segment_index, int) or not 0 <= segment_index < len(segments):
            continue
        segment = segments[segment_index]
        excerpt = item.get("source_excerpt") or ""
        if not excerpt or excerpt not in segment.get("text", ""):
            continue
        observed_at = item.get("observed_at") or segment.get("start_time") or meeting.get("updated_at") or meeting.get("created_at")
        try:
            value = json.loads(item.get("value_json", "null"))
        except json.JSONDecodeError:
            continue
        if value is None:
            continue
        facts.append(
            ExtractedFact(
                client_name=client_name,
                fact_type=fact_type,
                normalized_value=value,
                display_value=item.get("display_value") or str(value),
                source=SourceSpan(
                    excerpt=excerpt,
                    start_time=item.get("source_start_time") or segment.get("start_time"),
                    end_time=item.get("source_end_time") or segment.get("end_time"),
                ),
                confidence=float(item.get("confidence", 0.8)),
                reason=item.get("reason") or "Extracted by LLM using configured fact definitions.",
                update_kind=item.get("update_kind") or "update",
                observed_at=observed_at,
                extractor_name=EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSION,
            )
        )
    return facts


def _call_openai(api_key: str, meeting: dict, definitions: list[FactDefinition]) -> dict[str, Any]:
    body = {
        "model": os.environ.get("OPENAI_EXTRACT_MODEL", DEFAULT_MODEL),
        "input": [
            {
                "role": "system",
                "content": (
                    "Extract client context facts from meeting transcripts. "
                    "Return only facts explicitly supported by transcript text. "
                    "Use the configured fact_type keys exactly. "
                    "For each fact, segment_index must be the zero-based index of the transcript segment "
                    "in meeting.transcript that supports the fact. source_excerpt must be an exact substring "
                    "from that same transcript segment's text. value_json must be a JSON-encoded string whose "
                    "decoded value matches the configured value_schema. Do not infer facts without a supporting segment."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "fact_definitions": [
                            {
                                "fact_type": definition.fact_type,
                                "display_name": definition.display_name,
                                "description": definition.description,
                                "value_kind": definition.value_kind,
                                "value_schema": definition.value_schema,
                            }
                            for definition in definitions
                        ],
                        "meeting": meeting,
                    },
                    sort_keys=True,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "client_context_extraction",
                "strict": True,
                "schema": _response_schema(),
            }
        },
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"OpenAI extraction request failed with HTTP {exc.code}: {body}") from exc
    return json.loads(_extract_response_text(payload))


def _extract_response_text(payload: dict[str, Any]) -> str:
    if "output_text" in payload:
        return payload["output_text"]
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") in {"output_text", "text"} and "text" in content:
                return content["text"]
    raise RuntimeError(f"OpenAI response did not include output text: {payload}")


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["client_name", "facts"],
        "properties": {
            "client_name": {"type": "string"},
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "fact_type",
                        "value_json",
                        "display_value",
                        "source_excerpt",
                        "segment_index",
                        "confidence",
                        "reason",
                        "update_kind",
                    ],
                    "properties": {
                        "fact_type": {"type": "string"},
                        "value_json": {
                            "type": "string",
                            "description": "JSON-encoded normalized value matching the configured fact value_schema. Examples: 26, \"2026-07-01\", [{\"plan_name\":\"Pioneer PPO\",\"price_pepm\":638}]",
                        },
                        "display_value": {"type": "string"},
                        "source_excerpt": {"type": "string"},
                        "segment_index": {"type": "integer"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                        "update_kind": {"type": "string", "enum": ["initial", "update", "correction", "reaffirmation"]},
                    },
                },
            },
        },
    }
