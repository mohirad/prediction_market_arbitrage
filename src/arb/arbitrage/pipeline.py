"""Orchestrates the full verification pipeline for one candidate pair of
contracts: rule extraction -> world-state proposal -> adversarial hole
detection -> stake-size optimization -> payoff-matrix construction ->
classification. This is the only module where LLM output and
deterministic math meet -- the LLM (rules.py, states.py) proposes rules
and states; payoff.py and optimize.py always do the final math, and this
module never overrides their verdict.

Every call to evaluate_pair() re-fetches nothing itself (the caller
passes in already-fetched order books) but re-derives everything from
those books from scratch -- no cached opportunity is ever re-served
without recomputing prices, fees, states, and the classification, which
is the "safety checks" requirement from project brief section 16.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from typing import Callable

from arb.arbitrage.execution import (
    STANDARD_SIZE_CENTS,
    ExecutionQuote,
    max_profitable_shares,
    quote_shares,
    quote_standard_sizes,
    shares_for_dollar_size,
    total_ask_depth_shares,
)
from arb.arbitrage.llm_client import LLMClient, LLMResponseError
from arb.arbitrage.models import Classification, ContractRef, PayoffMatrix, Position, WorldState
from arb.arbitrage.optimize import OptimizableLeg, optimize_stakes
from arb.arbitrage.payoff import DEFAULT_MIN_RULE_CONFIDENCE, build_payoff_matrix, classify, find_holes
from arb.arbitrage.rules import RuleSpec, extract_rule_spec
from arb.arbitrage.states import MatchProposal, detect_holes, propose_match
from arb.money import PBP_PER_CENT
from arb.orderbook import Book

REFERENCE_QUOTE_CENTS = 1_000_00  # $1,000 -- used only to get a representative per-share
                                    # price/fee for the LP; the LP's chosen size is then
                                    # re-walked for real before anything is shown as final.


@dataclass(frozen=True)
class MarketInput:
    """Everything the pipeline needs about one side of a candidate pair.
    `fee_fn` is deliberately per-market (not global) since fee schedules
    differ by platform and, on Kalshi, by series."""
    contract: ContractRef
    resolution_text: str
    yes_book: Book
    no_book: Book
    fee_fn: Callable[[int, int], int]
    url: str


@dataclass(frozen=True)
class ArbitrageOpportunity:
    contract_a: ContractRef
    contract_b: ContractRef
    url_a: str
    url_b: str
    classification: Classification
    confidence: float
    incompatibility_reason: str | None
    rule_a: RuleSpec | None
    rule_b: RuleSpec | None
    states: tuple[WorldState, ...]
    holes: tuple[WorldState, ...]
    positions: tuple[Position, ...]
    payoff_matrix: PayoffMatrix | None
    total_cost_cents: Fraction | None
    guaranteed_profit_cents: Fraction | None
    guaranteed_roi: Fraction | None
    worst_case_state_name: str | None
    max_profitable_shares: int | None
    standard_size_quotes: dict[int, ExecutionQuote] | None
    verified_at: datetime
    reject_reason: str | None = None


def _rejected(contract_a: ContractRef, contract_b: ContractRef, market_a: MarketInput, market_b: MarketInput, *,
              reason: str, confidence: float = 0.0, rule_a: RuleSpec | None = None, rule_b: RuleSpec | None = None,
              classification: Classification = Classification.REJECTED_MATCH) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        contract_a=contract_a, contract_b=contract_b, url_a=market_a.url, url_b=market_b.url,
        classification=classification, confidence=confidence, incompatibility_reason=reason,
        rule_a=rule_a, rule_b=rule_b, states=(), holes=(), positions=(), payoff_matrix=None,
        total_cost_cents=None, guaranteed_profit_cents=None, guaranteed_roi=None, worst_case_state_name=None,
        max_profitable_shares=None, standard_size_quotes=None,
        verified_at=datetime.now(timezone.utc), reject_reason=reason,
    )


def _build_legs(market_a: MarketInput, market_b: MarketInput, capital_limit_cents: int,
                 ) -> list[OptimizableLeg]:
    legs = []
    for market in (market_a, market_b):
        for side, book in (("yes", market.yes_book), ("no", market.no_book)):
            shares = shares_for_dollar_size(book, REFERENCE_QUOTE_CENTS)
            if shares <= 0:
                continue
            quote = quote_shares(book, shares, market.fee_fn)
            if quote.avg_price_pbp is None or quote.filled_shares <= 0:
                continue
            price_per_share = Fraction(quote.total_cost_cents, quote.filled_shares)
            liquidity = min(total_ask_depth_shares(book), shares_for_dollar_size(book, capital_limit_cents) or 1)
            if liquidity <= 0:
                continue
            legs.append(OptimizableLeg(contract=market.contract, side=side,
                                        price_cents_per_share=price_per_share, max_shares=Fraction(liquidity)))
    return legs


def _rewalk_positions(allocations, market_a: MarketInput, market_b: MarketInput) -> tuple[Position, ...]:
    positions = []
    for alloc in allocations:
        shares_int = int(alloc.shares)  # floor to whole, executable shares
        if shares_int <= 0:
            continue
        leg = alloc.leg
        market = market_a if leg.contract == market_a.contract else market_b
        book = market.yes_book if leg.side == "yes" else market.no_book
        quote = quote_shares(book, shares_int, market.fee_fn)
        if quote.filled_shares <= 0 or quote.avg_price_pbp is None:
            continue
        positions.append(Position(
            contract=leg.contract, side=leg.side, shares=quote.filled_shares,
            avg_price_cents=Fraction(quote.avg_price_pbp, PBP_PER_CENT), fee_cents=quote.fee_cents,
            max_shares=quote.filled_shares,
        ))
    return tuple(positions)


def _fallback_reference_positions(legs: list[OptimizableLeg], market_a: MarketInput, market_b: MarketInput,
                                   states: tuple[WorldState, ...]) -> tuple[Position, ...] | None:
    """When the optimizer finds no positive edge, maximizing the worst
    case correctly means "trade zero" -- but the brief still wants a
    PRICE DISCREPANCY / CONDITIONAL ARBITRAGE row to *show*, not silence.
    This builds a reference-size (~$1,000/leg) position for each
    single-leg-per-contract combo and keeps whichever has the best
    worst-case payoff under the actual states (never picked by raw
    dollar cost -- every combo costs approximately the same ~$2,000 by
    construction, since each leg is independently sized to ~$1,000, so
    cost alone can't distinguish a real hedge from a directional bet).
    Only used for display/classification when the optimizer already
    chose to trade nothing; never overrides a profitable allocation."""
    legs_a = [leg for leg in legs if leg.contract == market_a.contract]
    legs_b = [leg for leg in legs if leg.contract == market_b.contract]
    if not legs_a or not legs_b:
        return None

    best: tuple[Position, Position] | None = None
    best_worst_case: Fraction | None = None
    for leg_a in legs_a:
        for leg_b in legs_b:
            book_a = market_a.yes_book if leg_a.side == "yes" else market_a.no_book
            book_b = market_b.yes_book if leg_b.side == "yes" else market_b.no_book
            shares_a = min(int(leg_a.max_shares), max(1, shares_for_dollar_size(book_a, REFERENCE_QUOTE_CENTS)))
            shares_b = min(int(leg_b.max_shares), max(1, shares_for_dollar_size(book_b, REFERENCE_QUOTE_CENTS)))
            qa = quote_shares(book_a, shares_a, market_a.fee_fn)
            qb = quote_shares(book_b, shares_b, market_b.fee_fn)
            if qa.filled_shares <= 0 or qb.filled_shares <= 0 or qa.avg_price_pbp is None or qb.avg_price_pbp is None:
                continue
            pos_a = Position(leg_a.contract, leg_a.side, qa.filled_shares, Fraction(qa.avg_price_pbp, PBP_PER_CENT),
                              qa.fee_cents, qa.filled_shares)
            pos_b = Position(leg_b.contract, leg_b.side, qb.filled_shares, Fraction(qb.avg_price_pbp, PBP_PER_CENT),
                              qb.fee_cents, qb.filled_shares)
            worst_case = build_payoff_matrix((pos_a, pos_b), states).worst_case_profit_cents
            if best_worst_case is None or worst_case > best_worst_case:
                best, best_worst_case = (pos_a, pos_b), worst_case
    return best


def _size_scan_two_leg(positions: tuple[Position, ...], states: tuple[WorldState, ...], market_a: MarketInput,
                        market_b: MarketInput, rule_confidence: float, min_rule_confidence: float,
                        ) -> tuple[int | None, dict[int, ExecutionQuote] | None]:
    """Only handles the common 2-position case (one leg per contract) --
    scaling an N>2-leg allocation proportionally while re-walking real
    books is future work; those opportunities still get a full
    classification, just no size-scan/max-size fields."""
    if len(positions) != 2:
        return None, None
    pos_a = next((p for p in positions if p.contract == market_a.contract), None)
    pos_b = next((p for p in positions if p.contract == market_b.contract), None)
    if pos_a is None or pos_b is None:
        return None, None
    book_a = market_a.yes_book if pos_a.side == "yes" else market_a.no_book
    book_b = market_b.yes_book if pos_b.side == "yes" else market_b.no_book

    ratio = Fraction(pos_b.shares, pos_a.shares) if pos_a.shares else Fraction(1)

    def evaluate(shares_a: int) -> float:
        shares_b = max(1, round(shares_a * ratio))
        qa = quote_shares(book_a, shares_a, market_a.fee_fn)
        qb = quote_shares(book_b, shares_b, market_b.fee_fn)
        if qa.filled_shares <= 0 or qb.filled_shares <= 0:
            return -1.0
        p_a = Position(pos_a.contract, pos_a.side, qa.filled_shares, Fraction(qa.avg_price_pbp, PBP_PER_CENT),
                        qa.fee_cents, qa.filled_shares)
        p_b = Position(pos_b.contract, pos_b.side, qb.filled_shares, Fraction(qb.avg_price_pbp, PBP_PER_CENT),
                        qb.fee_cents, qb.filled_shares)
        matrix = build_payoff_matrix((p_a, p_b), states)
        return float(matrix.worst_case_profit_cents)

    ceiling = max(total_ask_depth_shares(book_a), pos_a.shares, 1)
    max_shares_a = max_profitable_shares(evaluate, lo=max(1, pos_a.shares), hi=ceiling)

    quotes_a = quote_standard_sizes(book_a, market_a.fee_fn, STANDARD_SIZE_CENTS)
    return max_shares_a, quotes_a


def evaluate_pair(
    llm: LLMClient, market_a: MarketInput, market_b: MarketInput, *,
    capital_limit_cents: int, min_rule_confidence: float = DEFAULT_MIN_RULE_CONFIDENCE,
) -> ArbitrageOpportunity:
    contract_a, contract_b = market_a.contract, market_b.contract

    try:
        rule_a = extract_rule_spec(llm, platform=contract_a.platform, title=contract_a.label,
                                    resolution_text=market_a.resolution_text)
        rule_b = extract_rule_spec(llm, platform=contract_b.platform, title=contract_b.label,
                                    resolution_text=market_b.resolution_text)
    except LLMResponseError as exc:
        return _rejected(contract_a, contract_b, market_a, market_b, reason=f"rule extraction failed: {exc}",
                          classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    try:
        proposal: MatchProposal = propose_match(llm, contract_a, rule_a, contract_b, rule_b)
    except LLMResponseError as exc:
        return _rejected(contract_a, contract_b, market_a, market_b, reason=f"match proposal failed: {exc}",
                          rule_a=rule_a, rule_b=rule_b, classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    if not proposal.compatible:
        return _rejected(contract_a, contract_b, market_a, market_b,
                          reason=proposal.incompatibility_reason or "rules judged materially different",
                          confidence=proposal.confidence, rule_a=rule_a, rule_b=rule_b)

    if not proposal.states:
        return _rejected(contract_a, contract_b, market_a, market_b,
                          reason="no world states proposed", confidence=proposal.confidence,
                          rule_a=rule_a, rule_b=rule_b, classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    try:
        holes_found = detect_holes(llm, contract_a, rule_a, contract_b, rule_b, proposal.states)
    except LLMResponseError:
        holes_found = ()  # hole detector failing isn't fatal -- proceed with the states we do have
    all_states = proposal.states + holes_found

    legs = _build_legs(market_a, market_b, capital_limit_cents)
    if len(legs) < 2:
        return _rejected(contract_a, contract_b, market_a, market_b,
                          reason="insufficient order-book liquidity to price both sides",
                          confidence=proposal.confidence, rule_a=rule_a, rule_b=rule_b,
                          classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    optimized = optimize_stakes(legs, list(all_states), capital_limit_cents=Fraction(capital_limit_cents))
    if not optimized.feasible:
        return _rejected(contract_a, contract_b, market_a, market_b,
                          reason=f"stake optimization infeasible: {optimized.message}",
                          confidence=proposal.confidence, rule_a=rule_a, rule_b=rule_b,
                          classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    positions = _rewalk_positions(optimized.allocations, market_a, market_b)
    if not positions:
        # No positive edge -> the optimizer correctly chose zero size. Fall
        # back to a reference-size position so there's still something to
        # classify and show (as PRICE_DISCREPANCY/CONDITIONAL_ARBITRAGE),
        # rather than reporting nothing at all.
        positions = _fallback_reference_positions(legs, market_a, market_b, all_states) or ()
    if not positions:
        return _rejected(contract_a, contract_b, market_a, market_b,
                          reason="no executable position at real book depth",
                          confidence=proposal.confidence, rule_a=rule_a, rule_b=rule_b,
                          classification=Classification.MANUAL_VERIFICATION_REQUIRED)

    matrix = build_payoff_matrix(positions, all_states)
    holes = find_holes(matrix)
    classification = classify(matrix, rule_confidence=proposal.confidence, min_rule_confidence=min_rule_confidence)

    max_size, size_quotes = _size_scan_two_leg(positions, all_states, market_a, market_b,
                                                proposal.confidence, min_rule_confidence)

    return ArbitrageOpportunity(
        contract_a=contract_a, contract_b=contract_b, url_a=market_a.url, url_b=market_b.url,
        classification=classification, confidence=proposal.confidence, incompatibility_reason=None,
        rule_a=rule_a, rule_b=rule_b, states=all_states, holes=holes, positions=positions,
        payoff_matrix=matrix, total_cost_cents=matrix.total_cost_cents,
        guaranteed_profit_cents=matrix.worst_case_profit_cents,
        guaranteed_roi=matrix.guaranteed_roi, worst_case_state_name=matrix.worst_case_state.name,
        max_profitable_shares=max_size, standard_size_quotes=size_quotes,
        verified_at=datetime.now(timezone.utc),
    )
