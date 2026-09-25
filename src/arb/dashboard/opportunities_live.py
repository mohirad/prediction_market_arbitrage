"""Live orchestration for the Opportunities scanner: takes the top
candidate pairs from arb.matching.discover's discovery scan, fetches
real order books for both sides, and runs each pair through
arb.arbitrage.pipeline.evaluate_pair.

Deliberately bounded to a small number of candidates per call
(`max_candidates`): each candidate costs several real LLM calls (two
rule extractions, one match proposal, one hole-detector pass), so unlike
the divergence scanner's "re-scan everything on every click", this is
meant to run against a short, already-ranked candidate list, not the
whole market -- see server.py's /api/opportunities for the bound this
is called with.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from arb.arbitrage.llm_client import LLMClient
from arb.arbitrage.models import ContractRef
from arb.arbitrage.pipeline import ArbitrageOpportunity, MarketInput, evaluate_pair
from arb.fees import kalshi_taker_fee_cents, polymarket_taker_fee_cents
from arb.kalshi.client import KalshiClient
from arb.matching.discover import CandidatePair
from arb.polymarket.clob_client import ClobClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpportunityScanResult:
    generated_at: datetime
    evaluated: int
    skipped_no_liquidity_data: int
    opportunities: tuple[ArbitrageOpportunity, ...]
    errors: tuple[str, ...]


def _market_inputs_for_pair(kalshi: KalshiClient, clob: ClobClient, pair: CandidatePair,
                             ) -> tuple[MarketInput, MarketInput] | None:
    k, p = pair.kalshi, pair.polymarket
    if not p.yes_token_id or not p.no_token_id:
        # Polarity between Polymarket's clobTokenIds order and its Yes/No
        # labels couldn't be confirmed (see arb.polymarket.models) --
        # refuse to guess which token is YES rather than risk silently
        # buying the wrong side.
        return None
    try:
        k_yes_book, k_no_book = kalshi.get_orderbook(k.ticker)
        p_yes_book = clob.get_book(p.yes_token_id)
        p_no_book = clob.get_book(p.no_token_id)
    except Exception as exc:  # noqa: BLE001 -- one pair's fetch failure must not kill the whole scan
        logger.warning("order book fetch failed for %s / %s: %s", k.ticker, p.condition_id, exc)
        return None

    def kalshi_fee(contracts: int, price: int) -> int:
        return kalshi_taker_fee_cents(contracts, price, series_ticker=k.series_ticker)

    def poly_fee(contracts: int, price: int) -> int:
        return polymarket_taker_fee_cents(contracts, price, category=k.category)

    market_a = MarketInput(
        contract=ContractRef("kalshi", k.ticker, k.title), resolution_text=k.rules_text,
        yes_book=k_yes_book, no_book=k_no_book, fee_fn=kalshi_fee, url=k.url(),
    )
    market_b = MarketInput(
        contract=ContractRef("polymarket", p.condition_id, p.question), resolution_text=p.resolution_text,
        yes_book=p_yes_book, no_book=p_no_book, fee_fn=poly_fee, url=p.url(),
    )
    return market_a, market_b


def scan_opportunities(
    kalshi: KalshiClient, clob: ClobClient, llm: LLMClient, candidate_pairs: list[CandidatePair], *,
    max_candidates: int, capital_limit_cents: int, min_rule_confidence: float,
) -> OpportunityScanResult:
    generated_at = datetime.now(timezone.utc)
    opportunities: list[ArbitrageOpportunity] = []
    errors: list[str] = []
    skipped = 0

    for pair in candidate_pairs[:max_candidates]:
        markets = _market_inputs_for_pair(kalshi, clob, pair)
        if markets is None:
            skipped += 1
            continue
        market_a, market_b = markets
        try:
            opportunities.append(evaluate_pair(
                llm, market_a, market_b, capital_limit_cents=capital_limit_cents,
                min_rule_confidence=min_rule_confidence,
            ))
        except Exception as exc:  # noqa: BLE001 -- one bad pair must not kill the whole scan
            logger.exception("evaluate_pair failed for %s / %s", market_a.contract.market_id, market_b.contract.market_id)
            errors.append(f"{market_a.contract.market_id} / {market_b.contract.market_id}: {exc}")

    return OpportunityScanResult(
        generated_at=generated_at, evaluated=len(opportunities), skipped_no_liquidity_data=skipped,
        opportunities=tuple(opportunities), errors=tuple(errors),
    )
