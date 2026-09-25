"""Serializes PairResult/ArbEvaluation (Fraction/PriceBp/Cents-typed) into
plain JSON-safe dicts for the interactive app's /api/opportunities
response. Money stays in cents as the source of truth; a `_dollars`
convenience field is added alongside each `_cents` field purely for
display so the frontend never has to know the cents-to-dollars scale
factor itself.
"""
from __future__ import annotations

from fractions import Fraction

from arb.dashboard.render import PairResult
from arb.money import Cents, PriceBp
from arb.pricing.hard_arb import LegInstruction


def _frac_to_num(f: Fraction) -> float | int:
    return f.numerator if f.denominator == 1 else round(float(f), 4)


def _price_to_cents_display(p: PriceBp | None) -> float | None:
    return None if p is None else int(p) / 100


def _leg_dict(leg: LegInstruction, url: str) -> dict:
    return {
        "platform": leg.platform,
        "side": leg.side,
        "action": "BUY YES" if leg.side == "yes" else "BUY NO",
        "market_id": leg.market_id,
        "contracts": leg.contracts,
        "filled_contracts": _frac_to_num(leg.filled_contracts),
        "avg_price_cents": _price_to_cents_display(leg.avg_price),
        "cost_cents": int(leg.cost_cents),
        "cost_dollars": int(leg.cost_cents) / 100,
        "fee_cents": int(leg.fee_cents),
        "fee_dollars": int(leg.fee_cents) / 100,
        "fully_fillable": leg.fully_fillable,
        "url": url,
    }


def pair_result_to_dict(result: PairResult) -> dict:
    pair = result.pair
    d: dict = {
        "pair_id": pair.pair_id,
        "category": pair.category,
        "confidence": pair.confidence,
        "notes": pair.notes,
        "kalshi_url": pair.kalshi_url(),
        "polymarket_url": pair.polymarket_url(),
        "generated_at": result.generated_at.isoformat(),
        "error": result.error,
        "is_hard_arb": False,
        "size": None,
        "gross_proceeds_cents": None,
        "total_cost_cents": None,
        "total_fee_cents": None,
        "net_edge_cents": None,
        "net_edge_dollars": None,
        "net_edge_bps": None,
        "fully_fillable": None,
        "soft_divergence_cents": None,
        "legs": [],
    }
    if result.soft_divergence_bps is not None:
        d["soft_divergence_cents"] = result.soft_divergence_bps / 100

    arb = result.best_arb
    if arb is not None:
        d["is_hard_arb"] = arb.net_edge_cents > 0
        d["size"] = arb.size
        d["gross_proceeds_cents"] = int(arb.gross_proceeds_cents)
        d["total_cost_cents"] = int(arb.total_cost_cents)
        d["total_fee_cents"] = int(arb.total_fee_cents)
        d["net_edge_cents"] = int(arb.net_edge_cents)
        d["net_edge_dollars"] = int(arb.net_edge_cents) / 100
        d["net_edge_bps"] = arb.net_edge_bps
        d["fully_fillable"] = arb.fully_fillable
        cheap_url = pair.kalshi_url() if arb.cheap_leg.platform == "kalshi" else pair.polymarket_url()
        expensive_url = pair.kalshi_url() if arb.expensive_leg.platform == "kalshi" else pair.polymarket_url()
        d["legs"] = [_leg_dict(arb.cheap_leg, cheap_url), _leg_dict(arb.expensive_leg, expensive_url)]
    return d


def opportunities_payload(results: list[PairResult]) -> dict:
    pairs = [pair_result_to_dict(r) for r in results]
    ranked = sorted(pairs, key=lambda p: p["net_edge_cents"] if p["net_edge_cents"] is not None else -(10**12),
                     reverse=True)
    latest = max((r.generated_at for r in results), default=None)
    return {
        "generated_at": latest.isoformat() if latest is not None else None,
        "count": len(ranked),
        "hard_arb_count": sum(1 for p in ranked if p["is_hard_arb"]),
        "pairs": ranked,
    }
