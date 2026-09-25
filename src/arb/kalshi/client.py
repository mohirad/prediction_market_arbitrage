"""Kalshi REST client. Base URL, pagination, orderbook shape, and signing
all verified live 2026-09-20 -- see docs/api_notes.md. Candlestick field
names are the one item there marked not-directly-verified; re-check the
response shape against a live call before depending on exact field names
beyond what's parsed here.
"""
from __future__ import annotations

from typing import Any, Iterator
from urllib.parse import urlparse

from arb.http import RateLimitedSession
from arb.kalshi.auth import KalshiSigner
from arb.kalshi.models import KalshiMarket
from arb.kalshi.parsing import parse_kalshi_orderbook
from arb.orderbook import Book

# Rate limits verified 2026-09-20: Basic tier is 200 read / 100 write
# tokens/sec, most calls cost 10 tokens -> ~20 req/s sustainable read rate.
# We default conservative; override via RateLimitedSession kwargs for a
# higher tier.
DEFAULT_READ_RATE = 15.0
DEFAULT_BURST = 15


class KalshiClient:
    def __init__(self, api_base: str, signer: KalshiSigner | None = None,
                 rate_per_sec: float = DEFAULT_READ_RATE, burst: int = DEFAULT_BURST):
        self._api_base = api_base
        # Kalshi signs the full request path including the "/trade-api/v2"
        # prefix (verified: string-to-sign uses "the full request path").
        self._path_prefix = urlparse(api_base).path.rstrip("/")
        self._signer = signer
        self._session = RateLimitedSession(base_url=api_base, rate_per_sec=rate_per_sec, burst=burst)

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if self._signer is None:
            return {}
        full_path = self._path_prefix + path
        return self._signer.sign(method, full_path)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._session.get(path, params=params, extra_headers=self._auth_headers)
        resp.raise_for_status()
        return resp.json()

    def list_markets(self, status: str = "open", limit: int = 1000,
                      series_ticker: str | None = None) -> Iterator[KalshiMarket]:
        """Cursor-paginates through all markets matching `status`.
        status in {unopened, open, paused, closed, settled} (verified)."""
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"status": status, "limit": min(limit, 1000)}
            if cursor:
                params["cursor"] = cursor
            if series_ticker:
                params["series_ticker"] = series_ticker
            data = self._get("/markets", params=params)
            for m in data.get("markets", []):
                yield KalshiMarket.from_api(m)
            cursor = data.get("cursor")
            if not cursor:
                return

    def get_orderbook(self, ticker: str) -> tuple[Book, Book]:
        """Returns (yes_book, no_book)."""
        data = self._get(f"/markets/{ticker}/orderbook")
        return parse_kalshi_orderbook(data)

    def get_events(self, status: str | None = None, series_ticker: str | None = None,
                    with_nested_markets: bool = False, limit: int = 200) -> Iterator[dict]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": min(limit, 200), "with_nested_markets": with_nested_markets}
            if status:
                params["status"] = status
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor
            data = self._get("/events", params=params)
            yield from data.get("events", [])
            cursor = data.get("cursor")
            if not cursor:
                return

    def get_series(self, series_ticker: str) -> dict:
        return self._get(f"/series/{series_ticker}")

    def get_candlesticks(self, series_ticker: str, ticker: str, period_interval: int,
                          start_ts: int, end_ts: int) -> list[dict]:
        """period_interval in minutes: 1, 60, or 1440 (verified)."""
        if period_interval not in (1, 60, 1440):
            raise ValueError("period_interval must be 1, 60, or 1440 minutes")
        data = self._get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            params={"period_interval": period_interval, "start_ts": start_ts, "end_ts": end_ts},
        )
        return data.get("candlesticks", [])
