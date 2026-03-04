from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import LINK_LABELS, SELL_WALLET_LABELS
from .fingerprints import state_fingerprint

SPECIAL_INPUT_ACTIONS = ("<manual-input>", "<input>")
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>)\]\}\"']+")
HANDLE_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{3,})")
CARD_RE = re.compile(r"\b\d{4}(?:[ \-]?\d{4}){3}\b")
OPERATOR_HINTS = (
    "support",
    "оператор",
    "помощ",
    "тикет",
    "ticket",
    "админ",
    "admin",
    "поддержк",
)
LINK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "faq": ("faq",),
    "channel": ("канал", "channel"),
    "chat": ("чат", "chat"),
    "review_form": ("форма", "оставить отзыв", "review form"),
    "reviews": ("отзыв", "reviews", "review"),
    "manager": ("менеджер", "manager"),
    "terms": ("услов", "terms"),
}
BTC_ADDRESS_RE = re.compile(r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{20,}\b")
ETH_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TRX_ADDRESS_RE = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{25,34}\b")
LTC_ADDRESS_RE = re.compile(r"\b[LM3][a-km-zA-HJ-NP-Z1-9]{26,40}\b")
XMR_ADDRESS_RE = re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{90,110}\b")
TON_ADDRESS_RE = re.compile(r"\b(?:EQ|UQ)[A-Za-z0-9_-]{40,60}\b")

FLOW_CATALOG_RE_MAP = {
    "BTC": BTC_ADDRESS_RE,
    "ETH": ETH_ADDRESS_RE,
    "TRX": TRX_ADDRESS_RE,
    "USDT": TRX_ADDRESS_RE,  # Often TRC20
    "LTC": LTC_ADDRESS_RE,
    "XMR": XMR_ADDRESS_RE,
    "TON": TON_ADDRESS_RE,
}


@dataclass
class FlowCatalog:
    raw_dir: Path
    media_dir: Path
    states: dict[str, dict[str, Any]]
    edges: list[dict[str, str]]
    events: list[dict[str, Any]]
    links: list[str]
    fingerprints: dict[str, str]
    transition_index: dict[str, dict[str, list[str]]]
    observed_counts: dict[tuple[str, str, str], int]
    start_state_id: str
    operator_url_aliases: tuple[str, ...]
    operator_handle_aliases: tuple[str, ...]
    detected_requisites: tuple[str, ...]
    link_url_aliases: dict[str, tuple[str, ...]]
    sell_wallet_aliases: dict[str, tuple[str, ...]]
    default_operator_url: str

    @classmethod
    def from_directory(cls, raw_dir: Path, media_dir: Path) -> "FlowCatalog":
        flow = _load_json(raw_dir / "flow.json")
        edges = _load_json(raw_dir / "edges.json")
        events = _load_json(raw_dir / "events.json")
        links = _load_json(raw_dir / "links.json")

        if not isinstance(flow, dict):
            raise RuntimeError("flow.json must be a dict")
        if not isinstance(edges, list):
            raise RuntimeError("edges.json must be a list")
        if not isinstance(events, list):
            raise RuntimeError("events.json must be a list")
        if not isinstance(links, list):
            raise RuntimeError("links.json must be a list")

        states: dict[str, dict[str, Any]] = {
            str(sid): dict(state) for sid, state in flow.items() if isinstance(state, dict)
        }
        normalized_edges = _normalize_edges(edges)
        transition_index = _build_transition_index(normalized_edges)
        observed_counts = _build_observed_counts(events)
        start_state_id = _resolve_start_state(events, states)
        fingerprints = {sid: state_fingerprint(state) for sid, state in states.items()}
        operator_url_aliases, operator_handle_aliases = _detect_operator_aliases(states)
        detected_requisites = _detect_requisites(states)
        link_url_aliases = _detect_link_aliases(states, operator_url_aliases)
        sell_wallet_aliases = _detect_sell_wallet_aliases(states)
        default_operator_url = operator_url_aliases[0] if operator_url_aliases else ""

        return cls(
            raw_dir=raw_dir,
            media_dir=media_dir,
            states=states,
            edges=normalized_edges,
            events=events,
            links=[str(x) for x in links],
            fingerprints=fingerprints,
            transition_index=transition_index,
            observed_counts=observed_counts,
            start_state_id=start_state_id,
            operator_url_aliases=operator_url_aliases,
            operator_handle_aliases=operator_handle_aliases,
            detected_requisites=detected_requisites,
            link_url_aliases=link_url_aliases,
            sell_wallet_aliases=sell_wallet_aliases,
            default_operator_url=default_operator_url,
        )

    def resolve_action(self, state_id: str, action_text: str, *, is_text_input: bool = False) -> str | None:
        action_map = self.transition_index.get(state_id) or {}
        action = (action_text or "").strip()

        candidates: list[str] = []
        if action:
            candidates.append(action)
        if is_text_input:
            candidates.extend(SPECIAL_INPUT_ACTIONS)

        for candidate in candidates:
            targets = action_map.get(candidate)
            if targets:
                return self._pick_target(state_id, candidate, targets)

        return None

    def resolve_system_next(self, state_id: str) -> str | None:
        action_map = self.transition_index.get(state_id) or {}
        targets = action_map.get("<next-message>")
        if not targets:
            return None
        return self._pick_target(state_id, "<next-message>", targets)

    def state_accepts_input(self, state_id: str) -> bool:
        action_map = self.transition_index.get(state_id) or {}
        return any(key in action_map for key in SPECIAL_INPUT_ACTIONS)

    def state_has_buttons(self, state_id: str) -> bool:
        state = self.states.get(state_id) or {}
        rows = state.get("button_rows")
        if isinstance(rows, list) and rows:
            return True
        buttons = state.get("buttons")
        return isinstance(buttons, list) and bool(buttons)

    def _pick_target(self, state_id: str, action: str, targets: list[str]) -> str:
        if len(targets) == 1:
            return targets[0]
        ranked = sorted(
            targets,
            key=lambda dst: (
                self.observed_counts.get((state_id, action, dst), 0),
                -targets.index(dst),
            ),
            reverse=True,
        )
        return ranked[0]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_edges(edges: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in edges:
        if not isinstance(row, dict):
            continue
        src = str(row.get("from") or "")
        action = str(row.get("action") or "")
        dst = str(row.get("to") or "")
        if not src or not dst:
            continue
        out.append({"from": src, "action": action, "to": dst})
    return out


def _build_transition_index(edges: list[dict[str, str]]) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in edges:
        src = row["from"]
        action = row["action"]
        dst = row["to"]
        lst = index[src][action]
        if dst not in lst:
            lst.append(dst)
    return {src: dict(actions) for src, actions in index.items()}


def _build_observed_counts(events: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    counts: Counter[tuple[str, str, str]] = Counter()
    prev_state: str | None = None

    for event in events:
        if not isinstance(event, dict):
            continue
        curr_state = str(event.get("state_id") or "")
        action = str(event.get("from_action") or "")
        if prev_state and curr_state and action:
            counts[(prev_state, action, curr_state)] += 1
        prev_state = curr_state or prev_state

    return dict(counts)


def _resolve_start_state(events: list[dict[str, Any]], states: dict[str, dict[str, Any]]) -> str:
    start_hits: Counter[str] = Counter()
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("from_action") or "") != "/start":
            continue
        state_id = str(event.get("state_id") or "")
        if state_id:
            start_hits[state_id] += 1

    if start_hits:
        return start_hits.most_common(1)[0][0]

    if states:
        return next(iter(states.keys()))
    raise RuntimeError("No states found in captured flow")


def _iter_button_rows(state: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows = state.get("button_rows")
    parsed: list[list[dict[str, Any]]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, list):
                continue
            parsed_row = [btn for btn in row if isinstance(btn, dict)]
            if parsed_row:
                parsed.append(parsed_row)
    if parsed:
        return parsed

    fallback = state.get("buttons")
    if isinstance(fallback, list) and fallback:
        return [[btn for btn in fallback if isinstance(btn, dict)]]
    return []


def _state_text_blob(state: dict[str, Any]) -> str:
    parts = [
        str(state.get("text") or ""),
        str(state.get("text_html") or ""),
        str(state.get("text_markdown") or ""),
    ]
    return "\n".join(parts)


def _normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if value.endswith("/"):
        value = value[:-1]
    return value


def _is_operator_context(button_text: str) -> bool:
    button_lower = (button_text or "").lower()
    return any(hint in button_lower for hint in OPERATOR_HINTS)


def _extract_tg_handle(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"t.me", "telegram.me"}:
        return ""
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    return path.split("/")[0]


def _detect_operator_aliases(states: dict[str, dict[str, Any]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    url_aliases: set[str] = set()
    handle_aliases: set[str] = set()

    for state in states.values():
        for row in _iter_button_rows(state):
            for btn in row:
                url = _normalize_url(str(btn.get("url") or ""))
                if not url:
                    continue
                button_text = str(btn.get("text") or "")
                if _extract_tg_handle(url) and _is_operator_context(button_text):
                    url_aliases.add(url)

    if not url_aliases:
        for state in states.values():
            for row in _iter_button_rows(state):
                for btn in row:
                    url = _normalize_url(str(btn.get("url") or ""))
                    button_text = str(btn.get("text") or "")
                    if url and "тикет" in button_text.lower():
                        url_aliases.add(url)

    for url in url_aliases:
        handle = _extract_tg_handle(url)
        if not handle:
            continue
        lower = handle.lower()
        handle_aliases.add(lower)
        no_underscores = lower.replace("_", "")
        if no_underscores:
            handle_aliases.add(no_underscores)

    if handle_aliases:
        normalized_known = {h.replace("_", "") for h in handle_aliases}
        for state in states.values():
            text = _state_text_blob(state)
            for handle in HANDLE_RE.findall(text):
                candidate = handle.lower()
                if candidate.replace("_", "") in normalized_known:
                    handle_aliases.add(candidate)

    return tuple(sorted(url_aliases)), tuple(sorted(handle_aliases))


def _detect_requisites(states: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    requisites: set[str] = set()
    for state in states.values():
        text = _state_text_blob(state)
        for match in CARD_RE.findall(text):
            requisites.add(match)
    return tuple(sorted(requisites))


def _detect_link_aliases(
    states: dict[str, dict[str, Any]],
    operator_url_aliases: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, set[str]] = {key: set() for key in LINK_LABELS}

    for operator_url in operator_url_aliases:
        if operator_url:
            aliases["operator"].add(operator_url)

    for state in states.values():
        for row in _iter_button_rows(state):
            for btn in row:
                url = _normalize_url(str(btn.get("url") or ""))
                if not url:
                    continue
                text = str(btn.get("text") or "")
                key = _match_link_key(text)
                if key:
                    aliases[key].add(url)

    return {key: tuple(sorted(values)) for key, values in aliases.items()}


def _match_link_key(button_text: str) -> str | None:
    text = (button_text or "").strip().lower()
    if not text:
        return None

    if _is_operator_context(text):
        return "operator"

    for key, label in LINK_LABELS.items():
        if label.lower() in text:
            return key
        if key.replace("_", " ") in text:
            return key

    for keyword in LINK_KEYWORDS["review_form"]:
        if keyword in text:
            return "review_form"

    for key in ("faq", "channel", "chat", "reviews", "manager", "terms"):
        for keyword in LINK_KEYWORDS.get(key, ()):
            if keyword in text:
                return key
    return None


def _detect_sell_wallet_aliases(states: dict[str, dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, set[str]] = {key: set() for key in SELL_WALLET_LABELS}

    for state in states.values():
        text = _state_text_blob(state)
        for address in BTC_ADDRESS_RE.findall(text):
            aliases["btc"].add(address)
        for address in LTC_ADDRESS_RE.findall(text):
            aliases["ltc"].add(address)
        for address in XMR_ADDRESS_RE.findall(text):
            aliases["xmr"].add(address)
        for address in TON_ADDRESS_RE.findall(text):
            aliases["ton"].add(address)
        for address in ETH_ADDRESS_RE.findall(text):
            target_key = "usdt_bsc" if _is_usdt_bsc_context(text) else "eth"
            aliases[target_key].add(address)
        for address in TRX_ADDRESS_RE.findall(text):
            target_key = "usdt_trc20" if _is_usdt_trc_context(text) else "trx"
            aliases[target_key].add(address)

    return {key: tuple(sorted(values)) for key, values in aliases.items()}


def _is_usdt_trc_context(text: str) -> bool:
    lowered = (text or "").lower()
    return "usdt" in lowered and ("trc20" in lowered or "trx" in lowered)


def _is_usdt_bsc_context(text: str) -> bool:
    lowered = (text or "").lower()
    return "usdt" in lowered and ("bsc" in lowered or "bep20" in lowered)
