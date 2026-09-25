from fractions import Fraction

import pytest

from arb.arbitrage.models import ContractRef, Resolution, WorldState
from arb.arbitrage.optimize import OptimizableLeg, optimize_stakes


def make_contract(platform, market_id):
    return ContractRef(platform=platform, market_id=market_id, label=market_id)


def two_state_hedge_states(k, p):
    return [
        WorldState("happens", "happens", {k: Resolution.YES, p: Resolution.YES}),
        WorldState("doesnt", "doesnt", {k: Resolution.NO, p: Resolution.NO}),
    ]


def test_optimizer_uses_full_capital_when_edge_is_positive():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    legs = [
        OptimizableLeg(k, "yes", Fraction(40), Fraction(100_000)),
        OptimizableLeg(p, "no", Fraction(55), Fraction(100_000)),
    ]
    states = two_state_hedge_states(k, p)
    result = optimize_stakes(legs, states, capital_limit_cents=Fraction(95_000))  # $950 cap
    assert result.feasible
    # cost per "unit" (1 share each side) = 95c, so max units ~= 95000/95 = 1000
    assert result.total_cost_cents <= 95_000
    assert result.worst_case_profit_cents > 0
    # every state should have >= worst_case_profit at the chosen allocation
    k_shares = next(a.shares for a in result.allocations if a.leg.contract == k)
    p_shares = next(a.shares for a in result.allocations if a.leg.contract == p)
    assert k_shares == p_shares  # symmetric 2-state hedge -> equal legs is optimal


def test_optimizer_respects_liquidity_ceiling():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    legs = [
        OptimizableLeg(k, "yes", Fraction(40), Fraction(10)),  # only 10 shares available
        OptimizableLeg(p, "no", Fraction(55), Fraction(100_000)),
    ]
    states = two_state_hedge_states(k, p)
    result = optimize_stakes(legs, states, capital_limit_cents=Fraction(10_000_000))
    assert result.feasible
    k_alloc = next(a for a in result.allocations if a.leg.contract == k)
    assert k_alloc.shares <= 10


def test_optimizer_returns_non_positive_z_when_no_edge_exists():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    legs = [
        OptimizableLeg(k, "yes", Fraction(60), Fraction(1_000)),
        OptimizableLeg(p, "no", Fraction(60), Fraction(1_000)),  # cost 120c > 100c payout
    ]
    states = two_state_hedge_states(k, p)
    result = optimize_stakes(legs, states, capital_limit_cents=Fraction(1_000_000))
    assert result.feasible
    assert result.worst_case_profit_cents <= 0


def test_optimize_stakes_rejects_incomplete_state():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    legs = [OptimizableLeg(k, "yes", Fraction(40), Fraction(100)),
            OptimizableLeg(p, "no", Fraction(55), Fraction(100))]
    bad_state = WorldState("bad", "bad", {k: Resolution.YES})  # missing p
    with pytest.raises(ValueError, match="no resolution"):
        optimize_stakes(legs, [bad_state], capital_limit_cents=Fraction(1000))


def test_three_state_hole_caps_the_optimizer_at_zero_or_below():
    """Mirrors the Vance nomination/presidency hole: a naive 2-leg
    "hedge" against a 3-state world where one state makes both legs lose
    should never let the LP report a positive worst-case z."""
    nomination = make_contract("kalshi", "NOM")
    presidency = make_contract("polymarket", "PRES")
    legs = [
        OptimizableLeg(nomination, "no", Fraction(90), Fraction(1_000)),
        OptimizableLeg(presidency, "yes", Fraction(8), Fraction(1_000)),
    ]
    states = [
        WorldState("no_nom", "no nomination", {nomination: Resolution.NO, presidency: Resolution.NO}),
        WorldState("nom_loses", "nominee loses general", {nomination: Resolution.YES, presidency: Resolution.NO}),
        WorldState("nom_wins", "nominee wins general", {nomination: Resolution.YES, presidency: Resolution.YES}),
    ]
    result = optimize_stakes(legs, states, capital_limit_cents=Fraction(1_000_000))
    assert result.feasible
    assert result.worst_case_profit_cents <= 0
