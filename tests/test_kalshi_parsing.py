from fractions import Fraction

from arb.money import cents_to_pbp
from arb.kalshi.parsing import parse_kalshi_orderbook


def test_parse_orderbook_derives_complementary_asks():
    raw = {
        "orderbook_fp": {
            "yes_dollars": [["0.4700", "50.00"], ["0.4600", "20.00"]],
            "no_dollars": [["0.5000", "30.00"], ["0.4900", "10.00"]],
        }
    }
    yes_book, no_book = parse_kalshi_orderbook(raw)

    assert yes_book.best_bid == cents_to_pbp(47)
    assert yes_book.bids[1].price == cents_to_pbp(46)
    # yes asks derived from no bids, complemented: 1-0.50=0.50, 1-0.49=0.51
    assert yes_book.best_ask == cents_to_pbp(50)
    assert yes_book.asks[1].price == cents_to_pbp(51)
    assert yes_book.asks[0].size == 30

    assert no_book.best_bid == cents_to_pbp(50)
    # no asks derived from yes bids, complemented: 1-0.47=0.53, 1-0.46=0.54
    assert no_book.best_ask == cents_to_pbp(53)
    assert no_book.asks[0].size == 50


def test_parse_orderbook_handles_missing_sides():
    raw = {"orderbook_fp": {"yes_dollars": [["0.3000", "10.00"]], "no_dollars": []}}
    yes_book, no_book = parse_kalshi_orderbook(raw)
    assert yes_book.best_bid == cents_to_pbp(30)
    assert yes_book.best_ask is None  # no no_dollars to derive yes asks from
    assert no_book.best_bid is None
    assert no_book.best_ask == cents_to_pbp(70)


def test_parse_orderbook_empty_book():
    raw = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    yes_book, no_book = parse_kalshi_orderbook(raw)
    assert yes_book.mid is None
    assert no_book.mid is None


def test_parse_orderbook_accepts_unwrapped_dict():
    raw = {"yes_dollars": [["0.5000", "1.00"]], "no_dollars": [["0.4800", "1.00"]]}
    yes_book, _ = parse_kalshi_orderbook(raw)
    assert yes_book.best_bid == cents_to_pbp(50)


def test_parse_orderbook_handles_real_fractional_sizes():
    # live Kalshi books have been observed with fractional fp sizes like
    # "8.50" -- must not crash or truncate them.
    raw = {"orderbook_fp": {"yes_dollars": [["0.0100", "437.40"]], "no_dollars": [["0.0100", "710.00"], ["0.0800", "8.50"]]}}
    yes_book, no_book = parse_kalshi_orderbook(raw)
    assert yes_book.bids[0].size == Fraction("437.40")
    assert no_book.bids[0].size == Fraction("8.50")  # 0.08 sorts before 0.01, best bid first
