"""Serializes ArbitrageOpportunity / OpportunityScanResult into JSON-safe
dicts for the app's /api/opportunities response: the main opportunity
table (project brief section 10) and the detail page's full payoff
matrix + Worst-Case P&L (section 11). Also builds the row persisted to
the arbitrage_opportunities table (section 15's data model), so every
opportunity the app ever showed is traceable later.
"""
from __future__ import annotations

import json
import time

from arb.arbitrage.pipeline import ArbitrageOpportunity
from arb.dashboard.opportunities_live import OpportunityScanResult


def _cents_to_dollars(value) -> float | None:
    return None if value is None else float(value) / 100


def _contract_dict(contract, url: str) -> dict:
    return {"platform": contract.platform, "market_id": contract.market_id, "label": contract.label, "url": url}


def _position_dict(position) -> dict:
    return {
        "platform": position.contract.platform, "market_id": position.contract.market_id,
        "label": position.contract.label, "side": position.side, "shares": position.shares,
        "avg_price_cents": float(position.avg_price_cents), "fee_cents": int(position.fee_cents),
        "cost_dollars": _cents_to_dollars(position.cost_cents),
    }


def _payoff_row_dict(row) -> dict:
    return {
        "state": row.state.name, "description": row.state.description, "source": row.state.source,
        "payout_dollars": _cents_to_dollars(row.payout_cents), "profit_dollars": _cents_to_dollars(row.profit_cents),
    }


def opportunity_to_dict(opp: ArbitrageOpportunity) -> dict:
    return {
        "contract_a": _contract_dict(opp.contract_a, opp.url_a),
        "contract_b": _contract_dict(opp.contract_b, opp.url_b),
        "classification": opp.classification.value,
        "confidence": opp.confidence,
        "reject_reason": opp.reject_reason,
        "capital_required_dollars": _cents_to_dollars(opp.total_cost_cents),
        "guaranteed_profit_dollars": _cents_to_dollars(opp.guaranteed_profit_cents),
        "guaranteed_roi_pct": float(opp.guaranteed_roi) * 100 if opp.guaranteed_roi is not None else None,
        "worst_case_state": opp.worst_case_state_name,
        "max_profitable_shares": opp.max_profitable_shares,
        "positions": [_position_dict(p) for p in opp.positions],
        "payoff_matrix": [_payoff_row_dict(r) for r in opp.payoff_matrix.rows] if opp.payoff_matrix else None,
        "holes": [{"name": h.name, "description": h.description} for h in opp.holes],
        "verified_at": opp.verified_at.isoformat(),
    }


def opportunity_to_db_row(opp: ArbitrageOpportunity) -> dict:
    return {
        "ts": int(time.time() * 1000),
        "kalshi_market_id": opp.contract_a.market_id if opp.contract_a.platform == "kalshi" else opp.contract_b.market_id,
        "polymarket_market_id": opp.contract_b.market_id if opp.contract_b.platform == "polymarket" else opp.contract_a.market_id,
        "classification": opp.classification.value,
        "confidence": opp.confidence,
        "capital_required_c": int(opp.total_cost_cents) if opp.total_cost_cents is not None else None,
        "guaranteed_profit_c": int(opp.guaranteed_profit_cents) if opp.guaranteed_profit_cents is not None else None,
        "guaranteed_roi_bps": int(opp.guaranteed_roi * 10_000) if opp.guaranteed_roi is not None else None,
        "max_profitable_shares": opp.max_profitable_shares,
        "reject_reason": opp.reject_reason,
        "positions_json": json.dumps([_position_dict(p) for p in opp.positions]),
        "payoff_matrix_json": json.dumps([_payoff_row_dict(r) for r in opp.payoff_matrix.rows]) if opp.payoff_matrix else None,
    }


def scan_result_to_payload(result: OpportunityScanResult) -> dict:
    return {
        "generated_at": result.generated_at.isoformat(),
        "evaluated": result.evaluated,
        "skipped_no_liquidity_data": result.skipped_no_liquidity_data,
        "errors": list(result.errors),
        "opportunities": [opportunity_to_dict(o) for o in result.opportunities],
    }
