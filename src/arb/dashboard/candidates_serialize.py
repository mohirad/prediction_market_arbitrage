"""Serializes CandidatePair/DiscoveryResult into JSON-safe dicts for the
app's /api/candidates response. Deliberately has no trade-instruction or
fee-math fields -- this view is for judging whether a pair is even a
sane arbitrage candidate, not for executing one (see arb.pricing.hard_arb
/ the matches.yaml-driven flow for that, once a pair's been hand-approved).
"""
from __future__ import annotations

from arb.dashboard.discover_live import DiscoveryResult
from arb.matching.discover import CandidatePair


def _candidate_pair_to_dict(cp: CandidatePair) -> dict:
    k, p = cp.kalshi, cp.polymarket
    yes_gap_cents = cp.yes_gap_pbp / 100 if cp.yes_gap_pbp is not None else None
    no_gap_cents = cp.no_gap_pbp / 100 if cp.no_gap_pbp is not None else None
    return {
        "kalshi": {
            "ticker": k.ticker,
            "title": k.title,
            "category": k.category,
            "url": k.url(),
            "yes_price_cents": k.mid_pbp / 100 if k.mid_pbp is not None else None,
            "no_price_cents": k.no_mid_pbp / 100 if k.no_mid_pbp is not None else None,
            "bid_cents": k.yes_bid_pbp / 100 if k.yes_bid_pbp is not None else None,
            "ask_cents": k.yes_ask_pbp / 100 if k.yes_ask_pbp is not None else None,
            "volume_24h_dollars": k.volume_24h_cents / 100,
            "volume_total_dollars": k.volume_total_cents / 100,
            "close_time": k.close_time,
        },
        "polymarket": {
            "question": p.question,
            "yes_label": p.yes_label,
            "no_label": p.no_label,
            "url": p.url(),
            "yes_price_cents": p.yes_price_pbp / 100 if p.yes_price_pbp is not None else None,
            "no_price_cents": p.no_price_pbp / 100 if p.no_price_pbp is not None else None,
            "volume_24h_dollars": p.volume_24h_cents / 100,
            "volume_total_dollars": p.volume_total_cents / 100,
            "end_date": p.end_date,
        },
        "similarity": cp.similarity,
        "yes_gap_cents": yes_gap_cents,
        "no_gap_cents": no_gap_cents,
        "yes_gap_abs_cents": abs(yes_gap_cents) if yes_gap_cents is not None else None,
        "no_gap_abs_cents": abs(no_gap_cents) if no_gap_cents is not None else None,
        "rank_score": cp.rank_score,
    }


def discovery_result_to_payload(result: DiscoveryResult) -> dict:
    return {
        "generated_at": result.generated_at.isoformat(),
        "kalshi_scanned": result.kalshi_scanned,
        "polymarket_scanned": result.polymarket_scanned,
        "count": len(result.pairs),
        "error": result.error,
        "pairs": [_candidate_pair_to_dict(cp) for cp in result.pairs],
    }
