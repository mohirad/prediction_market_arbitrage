"""Live discovery pipeline: scan both platforms for real, traded markets,
fuzzy-match candidates across platforms, rank by volume-weighted price
divergence, and persist a snapshot of every displayed pair (so every
number on the page still traces back to a DB row, per the project's
engineering constraints -- see the schema.sql comment on `snapshots`).

This replaces data/matches.yaml-driven evaluation as the main app's data
source: instead of a small hand-verified pair list, it surfaces
candidates for a human to judge, exactly as Phase 1 in the project brief
describes ("candidate generation" is a distinct, earlier step than
"strict verification"). Nothing here writes to matches.yaml.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from arb.db.connection import insert_snapshot, transaction, upsert_market
from arb.kalshi.client import KalshiClient
from arb.matching.discover import (
    CandidatePair,
    fetch_kalshi_candidates,
    fetch_polymarket_candidates,
    generate_candidates,
)
from arb.polymarket.gamma_client import GammaClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryResult:
    generated_at: datetime
    kalshi_scanned: int
    polymarket_scanned: int
    pairs: list[CandidatePair]
    error: str | None = None


def _ensure_market_stub(conn, platform: str, market_id: str, title: str, category: str | None) -> None:
    exists = conn.execute(
        "SELECT 1 FROM markets WHERE platform=? AND market_id=?", (platform, market_id)
    ).fetchone()
    if exists:
        return
    upsert_market(conn, platform=platform, market_id=market_id, title=title, description=None,
                   resolution_text=None, close_ts=None, resolve_ts=None, category=category,
                   yes_token_id=None, no_token_id=None, raw_json="{}")


def _persist_candidate_pair(conn, cp: CandidatePair, ts_ms: int) -> None:
    with transaction(conn):
        _ensure_market_stub(conn, "kalshi", cp.kalshi.ticker, cp.kalshi.title, cp.kalshi.category)
        insert_snapshot(conn, {
            "ts": ts_ms, "platform": "kalshi", "market_id": cp.kalshi.ticker, "side": "yes",
            "best_bid_pbp": cp.kalshi.yes_bid_pbp, "best_ask_pbp": cp.kalshi.yes_ask_pbp,
            "mid_pbp": cp.kalshi.mid_pbp,
        })
        _ensure_market_stub(conn, "polymarket", cp.polymarket.condition_id, cp.polymarket.question, None)
        insert_snapshot(conn, {
            "ts": ts_ms, "platform": "polymarket", "market_id": cp.polymarket.condition_id,
            "side": cp.polymarket.yes_label, "mid_pbp": cp.polymarket.yes_price_pbp,
        })


def discover_and_rank(
    conn, kalshi: KalshiClient, gamma: GammaClient, *,
    kalshi_max_pages: int = 12, kalshi_page_size: int = 200,
    poly_max_pages: int = 5, poly_page_size: int = 100,
    min_similarity: float = 0.5, max_pairs: int = 500,
) -> DiscoveryResult:
    generated_at = datetime.now(timezone.utc)
    try:
        kcands = fetch_kalshi_candidates(kalshi, max_pages=kalshi_max_pages, page_size=kalshi_page_size)
        pcands = fetch_polymarket_candidates(gamma, max_pages=poly_max_pages, page_size=poly_page_size)
    except Exception as exc:  # noqa: BLE001 -- must surface as a usable API error, not a 500 traceback
        logger.warning("candidate fetch failed: %s", exc)
        return DiscoveryResult(generated_at=generated_at, kalshi_scanned=0, polymarket_scanned=0,
                                pairs=[], error=str(exc))

    pairs = generate_candidates(kcands, pcands, min_similarity=min_similarity, max_pairs=max_pairs)

    ts_ms = int(time.time() * 1000)
    for cp in pairs:
        try:
            _persist_candidate_pair(conn, cp, ts_ms)
        except Exception:  # noqa: BLE001 -- a persistence failure shouldn't hide the (already-computed) results
            logger.exception("failed to persist snapshot for %s / %s", cp.kalshi.ticker, cp.polymarket.condition_id)

    return DiscoveryResult(generated_at=generated_at, kalshi_scanned=len(kcands),
                            polymarket_scanned=len(pcands), pairs=pairs)
