"""Linear-programming stake-size optimizer: given a fixed set of legs
(each a contract/side/price/liquidity-ceiling) and a fixed set of world
states, find the dollar allocation across legs that maximizes the worst-
case profit across every state -- the "maximin" formulation from the
project brief's section 5.

Variables:  x_1 .. x_n = shares bought of leg 1..n (continuous relaxation
            -- both platforms show fractional resting book size, and an
            LP is the right tool for "how much", not "in what order";
            round down to whole shares at execution time).
            z          = the worst-case profit being maximized.

Objective:  maximize z
Subject to: for every state s,
                sum_j (payout_per_share(j, s) - price_per_share(j)) * x_j  >=  z
            for every leg j,
                0 <= x_j <= max_shares_j                    (order-book depth)
            and:
                sum_j price_per_share(j) * x_j <= capital_limit_cents

scipy.optimize.linprog only minimizes, so this solves min(-z) and negates
back. This module never calls an LLM and never decides a classification
-- it only sizes a hedge that payoff.py has already been told is
plausible; the caller re-runs payoff.classify() on the resulting
positions same as it would for any manually-sized trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from scipy.optimize import linprog

from arb.arbitrage.models import ContractRef, Resolution, WorldState
from arb.money import Cents


@dataclass(frozen=True)
class OptimizableLeg:
    """One candidate position to size, before we know how many shares to
    buy. `price_cents_per_share` should already reflect the expected
    fee-inclusive cost per share (e.g. from execution.py's book-walked
    average at a representative size) -- the LP treats it as a linear
    per-share cost, which is only an approximation of Kalshi's quadratic
    C*P*(1-P) fee curve, documented here rather than hidden."""
    contract: ContractRef
    side: str                        # "yes" | "no"
    price_cents_per_share: Fraction
    max_shares: Fraction               # order-book depth ceiling at/around this price


@dataclass(frozen=True)
class LegAllocation:
    leg: OptimizableLeg
    shares: Fraction
    cost_cents: Fraction


@dataclass(frozen=True)
class OptimizedAllocation:
    feasible: bool
    message: str
    allocations: tuple[LegAllocation, ...]
    total_cost_cents: Fraction
    worst_case_profit_cents: Fraction


def _payout_per_share(leg: OptimizableLeg, resolution: Resolution) -> Fraction:
    if resolution == Resolution.VOID:
        return leg.price_cents_per_share  # refunded
    wins = (resolution == Resolution.YES and leg.side == "yes") or (resolution == Resolution.NO and leg.side == "no")
    return Fraction(100) if wins else Fraction(0)


def optimize_stakes(
    legs: list[OptimizableLeg], states: list[WorldState], *, capital_limit_cents: Fraction | int,
) -> OptimizedAllocation:
    if not legs:
        raise ValueError("no legs to optimize")
    if not states:
        raise ValueError("no world states to optimize against")
    for state in states:
        for leg in legs:
            if leg.contract not in state.resolutions:
                raise ValueError(
                    f"world state {state.name!r} has no resolution for contract "
                    f"{leg.contract.platform}:{leg.contract.market_id}"
                )

    n = len(legs)
    # Variables: [x_1 .. x_n, z]. Objective: minimize -z.
    c = [0.0] * n + [-1.0]

    a_ub: list[list[float]] = []
    b_ub: list[float] = []

    # For each state: -(sum_j (payout_js - price_j) * x_j) + z <= 0
    for state in states:
        row = []
        for leg in legs:
            payout = _payout_per_share(leg, state.resolutions[leg.contract])
            row.append(-float(payout - leg.price_cents_per_share))
        row.append(1.0)  # z coefficient
        a_ub.append(row)
        b_ub.append(0.0)

    # Capital constraint: sum_j price_j * x_j <= capital_limit
    capital_row = [float(leg.price_cents_per_share) for leg in legs] + [0.0]
    a_ub.append(capital_row)
    b_ub.append(float(capital_limit_cents))

    bounds = [(0.0, float(leg.max_shares)) for leg in legs] + [(None, None)]

    result = linprog(c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")

    if not result.success:
        return OptimizedAllocation(
            feasible=False, message=result.message, allocations=(),
            total_cost_cents=Fraction(0), worst_case_profit_cents=Fraction(0),
        )

    shares = result.x[:n]
    worst_case_z = Fraction(result.x[n]).limit_denominator(10_000)

    allocations = []
    total_cost = Fraction(0)
    for leg, raw_shares in zip(legs, shares):
        sh = Fraction(raw_shares).limit_denominator(10_000)
        if sh < 0:
            sh = Fraction(0)  # clamp LP solver noise
        cost = leg.price_cents_per_share * sh
        allocations.append(LegAllocation(leg=leg, shares=sh, cost_cents=cost))
        total_cost += cost

    return OptimizedAllocation(
        feasible=True, message="optimal", allocations=tuple(allocations),
        total_cost_cents=total_cost, worst_case_profit_cents=worst_case_z,
    )
