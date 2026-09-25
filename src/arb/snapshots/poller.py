"""Snapshot poller: walks each watchlist pair's Kalshi + Polymarket books
every cycle and persists one snapshot row per (platform, market, side).

Every displayed number downstream must trace back to a row this module
wrote -- see the engineering constraints in the project brief. Nothing
here does trade sizing or fee math; it's pure observation and persistence.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from arb.db.connection import insert_snapshot, log_poll_error, transaction, upsert_market
from arb.kalshi.client import KalshiClient
from arb.orderbook import Book, band_1c, band_5c, band_10c
from arb.polymarket.clob_client import ClobClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KalshiLeg:
    ticker: str
    series_ticker: str | None = None


@dataclass(frozen=True)
class PolymarketLeg:
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True)
class WatchedPair:
    pair_id: str
    kalshi: KalshiLeg
    polymarket: PolymarketLeg


def load_watchlist(path: str) -> list[WatchedPair]:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    pairs = []
    for p in data.get("pairs", []):
        pairs.append(WatchedPair(
            pair_id=p["pair_id"],
            kalshi=KalshiLeg(ticker=p["kalshi"]["ticker"], series_ticker=p["kalshi"].get("series_ticker")),
            polymarket=PolymarketLeg(
                yes_token_id=p["polymarket"]["yes_token_id"],
                no_token_id=p["polymarket"]["no_token_id"],
            ),
        ))
    return pairs


def book_fields(book: Book) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "best_bid_pbp": book.best_bid,
        "best_ask_pbp": book.best_ask,
        "mid_pbp": book.mid,
    }
    if book.best_bid is not None:
        fields["depth_1c_bid_c"] = band_1c(book.bids, book.best_bid)
        fields["depth_5c_bid_c"] = band_5c(book.bids, book.best_bid)
        fields["depth_10c_bid_c"] = band_10c(book.bids, book.best_bid)
    if book.best_ask is not None:
        fields["depth_1c_ask_c"] = band_1c(book.asks, book.best_ask)
        fields["depth_5c_ask_c"] = band_5c(book.asks, book.best_ask)
        fields["depth_10c_ask_c"] = band_10c(book.asks, book.best_ask)
    return fields


def raw_book_json(book: Book) -> str:
    return json.dumps({
        "bids": [[lvl.price, str(lvl.size)] for lvl in book.bids],
        "asks": [[lvl.price, str(lvl.size)] for lvl in book.asks],
    })


def _ensure_market_stub(conn: sqlite3.Connection, platform: str, market_id: str, title: str) -> None:
    """Insert a minimal market row if one doesn't already exist, so the
    snapshots FK is satisfiable even before Phase 1 metadata sync runs.
    Never overwrites richer metadata a metadata sync already wrote."""
    exists = conn.execute(
        "SELECT 1 FROM markets WHERE platform=? AND market_id=?", (platform, market_id)
    ).fetchone()
    if exists:
        return
    upsert_market(
        conn, platform=platform, market_id=market_id, title=title, description=None,
        resolution_text=None, close_ts=None, resolve_ts=None, category=None,
        yes_token_id=None, no_token_id=None, raw_json="{}",
    )


def poll_pair(conn: sqlite3.Connection, kalshi: KalshiClient, clob: ClobClient,
              pair: WatchedPair) -> None:
    ts_ms = int(time.time() * 1000)

    try:
        yes_book, no_book = kalshi.get_orderbook(pair.kalshi.ticker)
        with transaction(conn):
            _ensure_market_stub(conn, "kalshi", pair.kalshi.ticker, pair.kalshi.ticker)
            for side, book in (("yes", yes_book), ("no", no_book)):
                row = {"ts": ts_ms, "platform": "kalshi", "market_id": pair.kalshi.ticker,
                       "side": side, "raw_book_json": raw_book_json(book), **book_fields(book)}
                insert_snapshot(conn, row)
    except Exception as exc:  # noqa: BLE001 -- one pair's failure must not stop the poll cycle
        logger.warning("kalshi poll failed for %s: %s", pair.kalshi.ticker, exc)
        with transaction(conn):
            log_poll_error(conn, platform="kalshi", market_id=pair.kalshi.ticker,
                            endpoint="/markets/{ticker}/orderbook", status_code=None, message=str(exc))

    for side, token_id in (("yes", pair.polymarket.yes_token_id), ("no", pair.polymarket.no_token_id)):
        try:
            book = clob.get_book(token_id)
            with transaction(conn):
                _ensure_market_stub(conn, "polymarket", token_id, token_id)
                row = {"ts": ts_ms, "platform": "polymarket", "market_id": token_id,
                       "side": side, "raw_book_json": raw_book_json(book), **book_fields(book)}
                insert_snapshot(conn, row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("polymarket poll failed for %s (%s): %s", token_id, side, exc)
            with transaction(conn):
                log_poll_error(conn, platform="polymarket", market_id=token_id,
                                endpoint="/book", status_code=None, message=str(exc))


def poll_all(conn: sqlite3.Connection, kalshi: KalshiClient, clob: ClobClient,
             pairs: list[WatchedPair]) -> None:
    for pair in pairs:
        poll_pair(conn, kalshi, clob, pair)


def run_loop(conn: sqlite3.Connection, kalshi: KalshiClient, clob: ClobClient,
             pairs: list[WatchedPair], interval_seconds: int, *, iterations: int | None = None) -> None:
    """iterations=None loops forever; set it in tests to bound the loop."""
    count = 0
    while iterations is None or count < iterations:
        started = time.monotonic()
        poll_all(conn, kalshi, clob, pairs)
        count += 1
        if iterations is not None and count >= iterations:
            return
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval_seconds - elapsed))
