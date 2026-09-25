from fractions import Fraction

import pytest

from arb.arbitrage.models import Classification, ContractRef, Position, Resolution, WorldState
from arb.arbitrage.payoff import build_payoff_matrix, classify, find_holes


def make_contract(platform="kalshi", market_id="T1", label="Will X happen?"):
    return ContractRef(platform=platform, market_id=market_id, label=label)


def make_position(contract, side, shares, price_cents, fee_cents=0, max_shares=None):
    return Position(contract=contract, side=side, shares=shares, avg_price_cents=Fraction(price_cents),
                     fee_cents=fee_cents, max_shares=max_shares if max_shares is not None else shares)


def test_contract_ref_equality_ignores_label():
    a = make_contract(label="Title A")
    b = make_contract(label="Different title text")
    assert a == b
    assert hash(a) == hash(b)
    assert make_contract(market_id="T2") != a


def test_classic_two_leg_hedge_is_true_arbitrage():
    # Buy Kalshi YES @ 40c and Polymarket NO @ 55c, same event, 2 states
    # (event happens / doesn't) -- textbook locked profit: one leg always
    # pays 100c/share, total cost 95c/share < 100c payout.
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    pos_k = make_position(k, "yes", 100, 40)
    pos_p = make_position(p, "no", 100, 55)

    happens = WorldState(name="event_happens", description="event happens",
                          resolutions={k: Resolution.YES, p: Resolution.YES})
    doesnt = WorldState(name="event_doesnt_happen", description="event doesn't happen",
                         resolutions={k: Resolution.NO, p: Resolution.NO})

    matrix = build_payoff_matrix((pos_k, pos_p), (happens, doesnt))
    assert matrix.total_cost_cents == 9500
    assert matrix.worst_case_profit_cents == 500  # both states pay 10000c, cost 9500c
    assert classify(matrix, rule_confidence=1.0) == Classification.TRUE_ARBITRAGE
    assert find_holes(matrix) == ()


def test_brief_worked_example_is_not_arbitrage():
    """Exact numbers from the project brief: capital $10,000, payouts
    (10200, 0, 13000) across three states -- must be rejected as TRUE
    ARBITRAGE because state B returns $0."""
    a_contract = make_contract("kalshi", "A")
    b_contract = make_contract("polymarket", "B")
    # Position A: pays $10,200 if it wins, else $0 -- modeled as 102 shares @ 0c with the
    # payout expressed directly via a synthetic 2-state-per-leg setup is awkward, so instead
    # build the matrix by hand using the PayoffRow-level total, matching the brief's own table.
    from arb.arbitrage.models import PayoffMatrix, PayoffRow

    pos_a = make_position(a_contract, "yes", 100, 50)  # cost irrelevant to this test's total
    pos_b = make_position(b_contract, "yes", 100, 50)
    total_cost = Fraction(10_000_00)  # $10,000 in cents, per the brief
    state_a = WorldState("A", "A", {a_contract: Resolution.YES, b_contract: Resolution.NO})
    state_b = WorldState("B", "B", {a_contract: Resolution.NO, b_contract: Resolution.NO})
    state_c = WorldState("C", "C", {a_contract: Resolution.NO, b_contract: Resolution.YES})
    rows = (
        PayoffRow(state_a, Fraction(10_200_00), Fraction(10_200_00) - total_cost),
        PayoffRow(state_b, Fraction(0), Fraction(0) - total_cost),
        PayoffRow(state_c, Fraction(13_000_00), Fraction(13_000_00) - total_cost),
    )
    matrix = PayoffMatrix(positions=(pos_a, pos_b), rows=rows, total_cost_cents=total_cost)

    assert matrix.worst_case_profit_cents == -total_cost  # state B: total loss of capital
    classification = classify(matrix, rule_confidence=1.0)
    assert classification != Classification.TRUE_ARBITRAGE
    assert classification == Classification.CONDITIONAL_ARBITRAGE  # state C alone would profit
    assert matrix.worst_case_state.name == "B"


def test_vance_nomination_vs_presidency_hole_is_detected():
    """"Vance wins nomination" is not equivalent to "Vance wins presidency"
    -- the world state "wins nomination, loses general" must cause both
    a naive hedge's legs to lose, and classify() must catch it."""
    nomination = make_contract("kalshi", "NOM", "Vance wins Republican nomination")
    presidency = make_contract("polymarket", "PRES", "Vance wins presidency")
    # Naive hedge: buy NO on nomination (cheap) + buy YES on presidency (cheap) --
    # looks like a hedge if you assume nomination <=> presidency, but it isn't.
    pos_nom_no = make_position(nomination, "no", 100, 90)
    pos_pres_yes = make_position(presidency, "yes", 100, 8)

    no_nom = WorldState("no_nomination", "does not become nominee",
                         resolutions={nomination: Resolution.NO, presidency: Resolution.NO})
    nom_loses_general = WorldState("nominee_loses_general", "becomes nominee but loses presidency",
                                    resolutions={nomination: Resolution.YES, presidency: Resolution.NO})
    nom_wins_general = WorldState("nominee_wins_general", "becomes nominee and wins presidency",
                                   resolutions={nomination: Resolution.YES, presidency: Resolution.YES})

    matrix = build_payoff_matrix((pos_nom_no, pos_pres_yes), (no_nom, nom_loses_general, nom_wins_general))
    holes = find_holes(matrix)
    assert len(holes) == 1
    assert holes[0].name == "nominee_loses_general"
    # both legs lose: NO-on-nomination loses (nominee=YES), YES-on-presidency loses (pres=NO)
    hole_row = next(r for r in matrix.rows if r.state.name == "nominee_loses_general")
    assert hole_row.payout_cents == 0
    assert classify(matrix, rule_confidence=1.0) != Classification.TRUE_ARBITRAGE


def test_capital_preserving_hedge_worst_case_exactly_breaks_even():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    pos_k = make_position(k, "yes", 100, 45)
    pos_p = make_position(p, "no", 100, 55)  # total cost exactly 10000c == payout
    happens = WorldState("happens", "happens", {k: Resolution.YES, p: Resolution.YES})
    doesnt = WorldState("doesnt", "doesnt", {k: Resolution.NO, p: Resolution.NO})
    matrix = build_payoff_matrix((pos_k, pos_p), (happens, doesnt))
    assert matrix.worst_case_profit_cents == 0
    assert classify(matrix, rule_confidence=1.0) == Classification.CAPITAL_PRESERVING_HEDGE


def test_low_rule_confidence_downgrades_true_arbitrage_to_manual_verification():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    pos_k = make_position(k, "yes", 100, 40)
    pos_p = make_position(p, "no", 100, 55)
    happens = WorldState("happens", "happens", {k: Resolution.YES, p: Resolution.YES})
    doesnt = WorldState("doesnt", "doesnt", {k: Resolution.NO, p: Resolution.NO})
    matrix = build_payoff_matrix((pos_k, pos_p), (happens, doesnt))

    assert classify(matrix, rule_confidence=0.5, min_rule_confidence=0.9) == Classification.MANUAL_VERIFICATION_REQUIRED
    assert classify(matrix, rule_confidence=0.95, min_rule_confidence=0.9) == Classification.TRUE_ARBITRAGE


def test_price_discrepancy_when_every_state_loses():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    pos_k = make_position(k, "yes", 100, 60)
    pos_p = make_position(p, "no", 100, 60)  # cost 12000c, payout always 10000c
    happens = WorldState("happens", "happens", {k: Resolution.YES, p: Resolution.YES})
    doesnt = WorldState("doesnt", "doesnt", {k: Resolution.NO, p: Resolution.NO})
    matrix = build_payoff_matrix((pos_k, pos_p), (happens, doesnt))
    assert classify(matrix, rule_confidence=1.0) == Classification.PRICE_DISCREPANCY


def test_void_state_refunds_price_not_fee():
    k = make_contract("kalshi", "K1")
    pos_k = make_position(k, "yes", 100, 40, fee_cents=50)
    voided = WorldState("voided", "market cancelled", {k: Resolution.VOID})
    matrix = build_payoff_matrix((pos_k,), (voided,))
    # payout = refunded price (4000c), cost = price + fee (4050c) -> lose exactly the fee
    assert matrix.rows[0].payout_cents == 4000
    assert matrix.rows[0].profit_cents == -50


def test_build_payoff_matrix_rejects_incomplete_state():
    k = make_contract("kalshi", "K1")
    p = make_contract("polymarket", "P1")
    pos_k = make_position(k, "yes", 100, 40)
    pos_p = make_position(p, "no", 100, 55)
    incomplete = WorldState("bad", "missing polymarket resolution", {k: Resolution.YES})
    with pytest.raises(ValueError, match="no resolution"):
        build_payoff_matrix((pos_k, pos_p), (incomplete,))


def test_build_payoff_matrix_rejects_empty_states():
    k = make_contract("kalshi", "K1")
    pos_k = make_position(k, "yes", 100, 40)
    with pytest.raises(ValueError, match="no world states"):
        build_payoff_matrix((pos_k,), ())
