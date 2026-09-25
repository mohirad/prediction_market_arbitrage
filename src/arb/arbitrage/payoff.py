"""Deterministic payoff-matrix construction and the mathematical
arbitrage classification from the project brief. This module never calls
an LLM and never trusts one -- it only ever consumes WorldState objects
handed to it (however they were produced upstream) and Position objects
with real, order-book-walked execution prices, and applies the brief's
own definitions exactly:

    C     = total executable cost of all positions
    P_s   = total payout if state s occurs

    TRUE ARBITRAGE           only when  min(P_s) > C
    CAPITAL-PRESERVING HEDGE only when  min(P_s) >= C   (and not > C)
    CONDITIONAL ARBITRAGE    only when  min(P_s) < C  but  max(P_s) > C
    PRICE DISCREPANCY        otherwise (every state loses)

    Guaranteed Profit = min(P_s) - C
    Guaranteed ROI    = Guaranteed Profit / C

A worked example straight from the brief: positions A and B costing
$10,000 combined, paying (State A: $10,200 / $0, State B: $0 / $0, State
C: $0 / $13,000) combined ($10,200, $0, $13,000). Worst case is $0 < the
$10,000 cost, so this is NOT true arbitrage even though state C alone
would return 30% -- see test_payoff.py for this exact case reproduced.
"""
from __future__ import annotations

from fractions import Fraction

from arb.arbitrage.models import (
    Classification,
    PayoffMatrix,
    PayoffRow,
    Position,
    Resolution,
    WorldState,
)

# "Very high rule confidence" per project brief section 9. Confidence is
# a 0..1 score (see rules.py/states.py for how it's derived); below this,
# a result that would otherwise be TRUE_ARBITRAGE or CAPITAL_PRESERVING_
# HEDGE is downgraded to MANUAL_VERIFICATION_REQUIRED instead -- the math
# might be right, but the premise (these are really the same event) is
# not confirmed enough to say "guaranteed".
DEFAULT_MIN_RULE_CONFIDENCE = 0.9


def _position_payout_cents(position: Position, resolution: Resolution) -> Fraction:
    if resolution == Resolution.VOID:
        # Voided: the exchange refunds what you paid for the contract
        # (not the fee -- fees are not refunded on either platform).
        return position.avg_price_cents * position.shares
    wins = (
        (resolution == Resolution.YES and position.side == "yes")
        or (resolution == Resolution.NO and position.side == "no")
    )
    return Fraction(position.shares * 100) if wins else Fraction(0)


def build_payoff_matrix(positions: tuple[Position, ...], states: tuple[WorldState, ...]) -> PayoffMatrix:
    """Builds the full state x position payoff matrix. Raises if any
    state is missing a resolution for any position's contract -- an
    incomplete state can't be scored, and silently treating "missing" as
    "loses" (or "wins") would be exactly the kind of hidden assumption
    this engine exists to catch."""
    if not positions:
        raise ValueError("no positions to evaluate")
    if not states:
        raise ValueError("no world states -- cannot verify a hedge with zero resolution scenarios")

    total_cost = sum((p.cost_cents for p in positions), Fraction(0))
    rows = []
    for state in states:
        payout = Fraction(0)
        for p in positions:
            if p.contract not in state.resolutions:
                raise ValueError(
                    f"world state {state.name!r} has no resolution for contract "
                    f"{p.contract.platform}:{p.contract.market_id}"
                )
            payout += _position_payout_cents(p, state.resolutions[p.contract])
        rows.append(PayoffRow(state=state, payout_cents=payout, profit_cents=payout - total_cost))
    return PayoffMatrix(positions=tuple(positions), rows=tuple(rows), total_cost_cents=total_cost)


def find_holes(matrix: PayoffMatrix) -> tuple[WorldState, ...]:
    """States where the combined position loses money -- the thing that
    disqualifies TRUE ARBITRAGE / CAPITAL-PRESERVING HEDGE. Returned in
    payoff order (worst first) so a caller can show "this state is why"
    without recomputing anything."""
    losing = [row for row in matrix.rows if row.profit_cents < 0]
    losing.sort(key=lambda r: r.profit_cents)
    return tuple(row.state for row in losing)


def classify(
    matrix: PayoffMatrix, *, rule_confidence: float, min_rule_confidence: float = DEFAULT_MIN_RULE_CONFIDENCE,
) -> Classification:
    """The single place a classification label is decided. `rule_confidence`
    (0..1) is supplied by the caller (see rules.py/states.py) -- this
    function does not compute it, it only gates on it, keeping "how
    confident are we these are the same event" and "does the math work
    out" as two separately-testable concerns."""
    worst = matrix.worst_case_profit_cents
    best = matrix.best_case_profit_cents

    if worst > 0:
        raw = Classification.TRUE_ARBITRAGE
    elif worst >= 0:
        raw = Classification.CAPITAL_PRESERVING_HEDGE
    elif best > 0:
        raw = Classification.CONDITIONAL_ARBITRAGE
    else:
        raw = Classification.PRICE_DISCREPANCY

    claims_a_guarantee = raw in (Classification.TRUE_ARBITRAGE, Classification.CAPITAL_PRESERVING_HEDGE)
    if claims_a_guarantee and rule_confidence < min_rule_confidence:
        return Classification.MANUAL_VERIFICATION_REQUIRED
    return raw


def guaranteed_profit_cents(matrix: PayoffMatrix) -> Fraction:
    return matrix.worst_case_profit_cents


def guaranteed_roi(matrix: PayoffMatrix) -> Fraction | None:
    return matrix.guaranteed_roi
