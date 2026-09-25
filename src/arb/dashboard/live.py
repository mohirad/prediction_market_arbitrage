"""Live evaluation: fetch both platforms' books for every approved pair,
persist a snapshot of each (so every number is traceable to a DB row),
and run the hard-arb pricing engine. Shared by the static dashboard
builder and the interactive server's /api/opportunities endpoint so
"refresh" always means the same thing in both places.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from arb.dashboard.render import PairResult
from arb.db.connection import insert_snapshot, transaction, upsert_market
from arb.kalshi.client import KalshiClient
from arb.matching.store import MatchedPair
from arb.money import spread_pbp
from arb.polarity import Polarity, to_common_yes_price
from arb.polymarket.clob_client import ClobClient
from arb.pricing.hard_arb import evaluate_hard_arb
from arb.snapshots.poller import book_fields, raw_book_json

logger = logging.getLogger(__name__)

# How many contracts we probe the book with before finding the size both
# legs can actually fill. A stand-in for Phase 4's real position-limit /
# risk-cap logic, which isn't built yet -- see docs/api_notes.md.
PROBE_SIZE = 500


def _persist_snapshot(conn, platform: str, market_id: str, side: str, book) -> None:
    with transaction(conn):
        exists = conn.execute(
            "SELECT 1 FROM markets WHERE platform=? AND market_id=?", (platform, market_id)
        ).fetchone()
        if not exists:
            upsert_market(conn, platform=platform, market_id=market_id, title=market_id,
                           description=None, resolution_text=None, close_ts=None, resolve_ts=None,
                           category=None, yes_token_id=None, no_token_id=None, raw_json="{}")
        row = {"ts": int(time.time() * 1000), "platform": platform, "market_id": market_id,
               "side": side, "raw_book_json": raw_book_json(book), **book_fields(book)}
        insert_snapshot(conn, row)


def _best_fillable_size(evaluations, requested: int) -> int:
    sizes = [min(e.cheap_leg.filled_contracts, e.expensive_leg.filled_contracts) for e in evaluations]
    best = max(sizes) if sizes else 0
    return min(requested, int(best))


def evaluate_pair(conn, kalshi: KalshiClient, clob: ClobClient, pair: MatchedPair) -> PairResult:
    now = datetime.now(timezone.utc)
    try:
        kalshi_yes_book, kalshi_no_book = kalshi.get_orderbook(pair.kalshi.ticker)
        poly_yes_book = clob.get_book(pair.polymarket.yes_token_id)
        poly_no_book = clob.get_book(pair.polymarket.no_token_id)
    except Exception as exc:  # noqa: BLE001 -- one pair's fetch failure shouldn't kill the batch
        logger.warning("fetch failed for %s: %s", pair.pair_id, exc)
        return PairResult(pair=pair, generated_at=now, best_arb=None, soft_divergence_bps=None, error=str(exc))

    _persist_snapshot(conn, "kalshi", pair.kalshi.ticker, "yes", kalshi_yes_book)
    _persist_snapshot(conn, "kalshi", pair.kalshi.ticker, "no", kalshi_no_book)
    _persist_snapshot(conn, "polymarket", pair.polymarket.yes_token_id, "yes", poly_yes_book)
    _persist_snapshot(conn, "polymarket", pair.polymarket.no_token_id, "no", poly_no_book)

    # Probe at a generous size to see real fillable depth, then re-quote
    # at the actual size both legs can fill so the final numbers are
    # honestly "fully fillable" rather than a partial-fill estimate.
    probe = evaluate_hard_arb(
        kalshi_ticker=pair.kalshi.ticker, kalshi_yes_book=kalshi_yes_book, kalshi_no_book=kalshi_no_book,
        poly_yes_token=pair.polymarket.yes_token_id, poly_no_token=pair.polymarket.no_token_id,
        poly_yes_book=poly_yes_book, poly_no_book=poly_no_book, size=PROBE_SIZE,
        kalshi_yes_maps_to=pair.kalshi_yes_maps_to, kalshi_series_ticker=pair.kalshi.series_ticker,
        poly_category=pair.category,
    )
    size = _best_fillable_size(probe, PROBE_SIZE)
    evaluations = evaluate_hard_arb(
        kalshi_ticker=pair.kalshi.ticker, kalshi_yes_book=kalshi_yes_book, kalshi_no_book=kalshi_no_book,
        poly_yes_token=pair.polymarket.yes_token_id, poly_no_token=pair.polymarket.no_token_id,
        poly_yes_book=poly_yes_book, poly_no_book=poly_no_book, size=max(size, 1),
        kalshi_yes_maps_to=pair.kalshi_yes_maps_to, kalshi_series_ticker=pair.kalshi.series_ticker,
        poly_category=pair.category,
    )
    best = max(evaluations, key=lambda e: e.net_edge_cents)

    soft_gap = None
    if kalshi_yes_book.mid is not None and poly_yes_book.mid is not None:
        polarity = Polarity.ALIGNED if pair.kalshi_yes_maps_to == "yes" else Polarity.INVERTED
        poly_common_yes_mid = to_common_yes_price(poly_yes_book.mid, polarity)
        soft_gap = spread_pbp(kalshi_yes_book.mid, poly_common_yes_mid)

    return PairResult(pair=pair, generated_at=now, best_arb=best, soft_divergence_bps=soft_gap)


def evaluate_all(conn, kalshi: KalshiClient, clob: ClobClient, pairs: list[MatchedPair]) -> list[PairResult]:
    return [evaluate_pair(conn, kalshi, clob, pair) for pair in pairs]
