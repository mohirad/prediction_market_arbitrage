"""Polymarket CLOB API client. Base URL, /book shape (bids/asks as
{price,size} objects, not bare pairs), /midpoint and /prices-history
verified live 2026-09-20 -- see docs/api_notes.md. /prices-history's
long-term home (this host vs data-api.polymarket.com/v2) is flagged
PARTIALLY VERIFIED there; re-check before relying on deep history pulls.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from arb.http import RateLimitedSession
from arb.money import PriceBp, decimal_str_to_pbp
from arb.orderbook import Book, Level

# Verified: CLOB order-placement limits are generous (5000/10s burst);
# market-data-only limits aren't separately documented, stay conservative.
DEFAULT_RATE = 10.0
DEFAULT_BURST = 10


class ClobClient:
    def __init__(self, clob_base: str, rate_per_sec: float = DEFAULT_RATE, burst: int = DEFAULT_BURST):
        self._session = RateLimitedSession(base_url=clob_base, rate_per_sec=rate_per_sec, burst=burst)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._session.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_book(self, token_id: str) -> Book:
        data = self._get("/book", params={"token_id": token_id})
        # size is a decimal string and routinely fractional on real books
        # (e.g. "10465279.8" shares) -- parse via Fraction, never float().
        bids = sorted(
            (Level(decimal_str_to_pbp(lvl["price"]), Fraction(lvl["size"])) for lvl in data.get("bids", [])),
            key=lambda l: -l.price,
        )
        asks = sorted(
            (Level(decimal_str_to_pbp(lvl["price"]), Fraction(lvl["size"])) for lvl in data.get("asks", [])),
            key=lambda l: l.price,
        )
        return Book(bids=tuple(bids), asks=tuple(asks))

    def get_midpoint(self, token_id: str) -> PriceBp | None:
        # Live-verified 2026-09-20: response key is "mid", e.g. {"mid":"0.0375"}
        # -- NOT "mid_price" as some secondary docs sources claimed.
        data = self._get("/midpoint", params={"token_id": token_id})
        mid = data.get("mid")
        return decimal_str_to_pbp(mid) if mid is not None else None

    def get_prices_history(self, token_id: str, start_ts: int, end_ts: int,
                            interval: str = "max", fidelity: int = 1) -> list[tuple[int, PriceBp]]:
        data = self._get("/prices-history", params={
            "market": token_id, "startTs": start_ts, "endTs": end_ts,
            "interval": interval, "fidelity": fidelity,
        })
        out = []
        for point in data.get("history", []):
            price = point["p"]
            # API has been observed returning this as either a numeric or a
            # string; always route through a string to avoid binary-float
            # artifacts from json's float parsing before we scale to pbp.
            price_str = price if isinstance(price, str) else repr(price)
            out.append((int(point["t"]), decimal_str_to_pbp(price_str)))
        return out
