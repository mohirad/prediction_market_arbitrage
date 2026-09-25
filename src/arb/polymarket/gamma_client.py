"""Polymarket Gamma API client (market/event metadata). Base URL, params
and fields verified live 2026-09-20 -- see docs/api_notes.md."""
from __future__ import annotations

from typing import Any, Iterator

from arb.http import RateLimitedSession
from arb.polymarket.models import PolymarketMarket

# Verified rate limits: Gamma general 4000/10s, /markets 300/10s -> 30 req/s
# ceiling; default conservative.
DEFAULT_RATE = 10.0
DEFAULT_BURST = 10


class GammaClient:
    def __init__(self, gamma_base: str, rate_per_sec: float = DEFAULT_RATE, burst: int = DEFAULT_BURST):
        self._session = RateLimitedSession(base_url=gamma_base, rate_per_sec=rate_per_sec, burst=burst)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._session.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def list_markets(self, active: bool = True, closed: bool = False,
                      page_size: int = 100) -> Iterator[PolymarketMarket]:
        offset = 0
        while True:
            data = self._get("/markets", params={
                "active": active, "closed": closed, "limit": page_size, "offset": offset,
            })
            items = data if isinstance(data, list) else data.get("markets", [])
            if not items:
                return
            for obj in items:
                yield PolymarketMarket.from_gamma(obj)
            if len(items) < page_size:
                return
            offset += page_size

    def list_events(self, active: bool = True, closed: bool = False,
                     page_size: int = 100) -> Iterator[dict]:
        offset = 0
        while True:
            data = self._get("/events", params={
                "active": active, "closed": closed, "limit": page_size, "offset": offset,
            })
            items = data if isinstance(data, list) else data.get("events", [])
            if not items:
                return
            yield from items
            if len(items) < page_size:
                return
            offset += page_size

    def list_markets_raw(self, active: bool = True, closed: bool = False, page_size: int = 100,
                          max_pages: int = 5, order: str | None = None, ascending: bool | None = None
                          ) -> list[dict]:
        """Raw market dicts (no Yes/No-label parsing -- for discovery/
        ranking use, where a market's outcomes may not be "Yes"/"No" at
        all, e.g. team-vs-team markets). `order`/`ascending` are
        live-verified to work on /markets the same way they do on
        /events (e.g. order="volume24hr", ascending=False for
        highest-volume-first), letting the caller pull top-volume markets
        directly instead of paging through everything and sorting locally."""
        out: list[dict] = []
        for page in range(max_pages):
            params: dict[str, Any] = {
                "active": active, "closed": closed, "limit": page_size, "offset": page * page_size,
            }
            if order:
                params["order"] = order
            if ascending is not None:
                params["ascending"] = ascending
            data = self._get("/markets", params=params)
            items = data if isinstance(data, list) else data.get("markets", [])
            if not items:
                break
            out.extend(items)
            if len(items) < page_size:
                break
        return out
