from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_fingerprint(state: dict[str, Any]) -> str:
    text = str(state.get("text") or "")
    text_html = str(state.get("text_html") or "")
    text_markdown = str(state.get("text_markdown") or "")
    entities_json = _stable_json(state.get("entities") or [])
    button_rows_json = _stable_json(state.get("button_rows") or [])
    media = state.get("media")
    media_value = ""
    if isinstance(media, dict):
        media_value = str(media.get("relpath") or "")
    text_links_json = _stable_json(state.get("text_links") or [])

    payload = (
        text
        + text_html
        + text_markdown
        + entities_json
        + button_rows_json
        + media_value
        + text_links_json
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
