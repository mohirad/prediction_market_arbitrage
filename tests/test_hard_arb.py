from arb.money import cents_to_pbp
from arb.orderbook import Book, Level
from arb.pricing.hard_arb import evaluate_hard_arb


def book(ask_cents, ask_size, bid_cents=None, bid_size=None):
    asks = (Level(cents_to_pbp(ask_cents), ask_size),)
    bids = (Level(cents_to_pbp(bid_cents), bid_size),) if bid_cents is not None else ()
    return Book(bids=bids, asks=asks)


def test_aligned_pair_finds_locked_profit_direction():
    # Kalshi YES cheap at 40c, Polymarket NO cheap at 40c -> total cost 80c
    # per contract, well under the $1 payout even after ~7% fees.
    kalshi_yes = book(40, 100)
    kalshi_no = book(65, 100)   # expensive Kalshi NO leg (not used in the profitable direction)
    poly_yes = book(62, 100)    # expensive Polymarket YES leg (not used in the profitable direction)
    poly_no = book(40, 100)

    evals = evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
        poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
        size=10, kalshi_yes_maps_to="yes", poly_category="crypto",
    )
    by_direction = {e.direction: e for e in evals}
    profitable = by_direction["buy_kalshi_yes"]
    assert profitable.cheap_leg.platform == "kalshi" and profitable.cheap_leg.side == "yes"
    assert profitable.expensive_leg.platform == "polymarket" and profitable.expensive_leg.side == "no"
    assert profitable.net_edge_cents > 0
    assert profitable.gross_proceeds_cents == 1000  # 10 contracts * 100c


def test_no_arb_when_prices_sum_near_one_plus_fees():
    # 50c + 50c = $1.00 exactly, fees push it negative -> no locked profit either direction.
    kalshi_yes = book(50, 100)
    kalshi_no = book(50, 100)
    poly_yes = book(50, 100)
    poly_no = book(50, 100)

    evals = evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
        poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
        size=10, kalshi_yes_maps_to="yes", poly_category="crypto",
    )
    assert all(e.net_edge_cents < 0 for e in evals)


def test_inverted_polarity_swaps_which_side_to_buy():
    # Kalshi YES means "Fed cuts"; Polymarket YES means "Fed holds" (the
    # opposite) -- so Polymarket's NO is what's economically equivalent to
    # Kalshi's YES. Cheap: Kalshi YES at 30c. Cheap common-NO on Polymarket
    # is poly.YES (since inverted) at 40c -> 70c total, profitable.
    kalshi_yes = book(30, 100)
    kalshi_no = book(75, 100)
    poly_yes = book(40, 100)   # this IS the "common NO" leg under inversion
    poly_no = book(80, 100)    # this IS the "common YES" leg under inversion

    evals = evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
        poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
        size=5, kalshi_yes_maps_to="no", poly_category="crypto",
    )
    by_direction = {e.direction: e for e in evals}
    profitable = by_direction["buy_kalshi_yes"]
    assert profitable.expensive_leg.platform == "polymarket"
    assert profitable.expensive_leg.side == "yes"  # buying poly YES achieves common-NO here
    assert profitable.net_edge_cents > 0


def test_insufficient_depth_marks_not_fully_fillable():
    kalshi_yes = book(40, 3)   # only 3 contracts available
    kalshi_no = book(65, 100)
    poly_yes = book(62, 100)
    poly_no = book(40, 100)

    evals = evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
        poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
        size=10, kalshi_yes_maps_to="yes",
    )
    by_direction = {e.direction: e for e in evals}
    evaluation = by_direction["buy_kalshi_yes"]
    assert not evaluation.fully_fillable
    assert evaluation.cheap_leg.filled_contracts == 3


def test_rejects_bad_polarity_value():
    kalshi_yes = book(40, 10)
    kalshi_no = book(65, 10)
    poly_yes = book(62, 10)
    poly_no = book(40, 10)
    import pytest
    with pytest.raises(ValueError):
        evaluate_hard_arb(
            kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
            poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
            size=5, kalshi_yes_maps_to="maybe",
        )


def test_net_edge_bps_matches_net_edge_cents():
    kalshi_yes = book(40, 100)
    kalshi_no = book(65, 100)
    poly_yes = book(62, 100)
    poly_no = book(40, 100)

    evals = evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=kalshi_yes, kalshi_no_book=kalshi_no,
        poly_yes_token="ytok", poly_no_token="ntok", poly_yes_book=poly_yes, poly_no_book=poly_no,
        size=10, kalshi_yes_maps_to="yes",
    )
    e = next(e for e in evals if e.direction == "buy_kalshi_yes")
    # net_edge_bps should reconstruct back to (approximately) net_edge_cents
    reconstructed = e.net_edge_bps * e.gross_proceeds_cents // 10_000
    assert abs(reconstructed - e.net_edge_cents) <= 1
