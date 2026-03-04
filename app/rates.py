from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .constants import FALLBACK_RATES


class RateService:
    def __init__(self, http_client: httpx.AsyncClient, ttl_seconds: int = 45):
        self.http_client = http_client
        self.ttl_seconds = ttl_seconds
        self._cached_rates: dict[str, float] = dict(FALLBACK_RATES)
        self._last_fetch_ts = 0.0

    async def _fetch_coingecko(self) -> dict[str, float] | None:
        try:
            ids = ",".join(COIN_ID_BY_SYMBOL.values())
            response = await self.http_client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": ids,
                    "vs_currencies": "rub",
                },
                timeout=10.0
            )
            if response.status_code != 200:
                return None
            payload: Any = response.json()
            if not isinstance(payload, dict):
                return None
            
            out: dict[str, float] = {}
            for symbol, coin_id in COIN_ID_BY_SYMBOL.items():
                val = payload.get(coin_id, {}).get("rub")
                if val is not None:
                    out[symbol.lower()] = float(val)
            return out
        except Exception:
            return None

    async def fetch_rates(self) -> dict[str, float]:
        rates = await self._fetch_coingecko()
        if rates is None:
            return dict(FALLBACK_RATES)
        return rates

    async def get_rates(self, force: bool = False) -> dict[str, float]:
        now = time.time()
        if not force and (now - self._last_fetch_ts) < self.ttl_seconds:
            return dict(self._cached_rates)
        try:
            fetched = await self.fetch_rates()
            self._cached_rates = fetched
            self._last_fetch_ts = now
        except Exception:
            if not self._cached_rates:
                self._cached_rates = dict(FALLBACK_RATES)
        return dict(self._cached_rates)


COIN_ID_BY_SYMBOL: dict[str, str] = {
    "BTC": "bitcoin",
    "LTC": "litecoin",
    "USDT": "tether",
    "ETH": "ethereum",
    "TRX": "tron",
    "TON": "the-open-network",
    "XMR": "monero",
}
