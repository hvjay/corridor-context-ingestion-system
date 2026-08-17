from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen


def get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def list_meetings(base_url: str, updated_after: str | None = None) -> list[dict]:
    params = {}
    if updated_after:
        params["updated_after"] = updated_after
    suffix = f"?{urlencode(params)}" if params else ""
    return get_json(f"{base_url.rstrip('/')}/v1/meetings{suffix}")["meetings"]


def get_meeting(base_url: str, meeting_id: str) -> dict:
    return get_json(f"{base_url.rstrip('/')}/v1/meetings/{meeting_id}")
