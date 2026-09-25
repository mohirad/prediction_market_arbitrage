import pytest

from arb.money import cents_to_pbp
from arb.orderbook import Book, Level, band_1c, band_5c, walk_for_size


def L(cents: int, size: int) -> Level:
    return Level(cents_to_pbp(cents), size)


def test_book_rejects_misordered_bids():
    with pytest.raises(ValueError):
        Book(bids=(L(50, 10), L(60, 10)), asks=())


def test_book_rejects_misordered_asks():
    with pytest.raises(ValueError):
        Book(bids=(), asks=(L(60, 10), L(50, 10)))


def test_book_best_and_mid():
    book = Book(bids=(L(48, 10), L(47, 10)), asks=(L(52, 10), L(53, 10)))
    assert book.best_bid == cents_to_pbp(48)
    assert book.best_ask == cents_to_pbp(52)
    assert book.mid == cents_to_pbp(50)


def test_book_empty_side_mid_is_none():
    book = Book(bids=(), asks=(L(52, 10),))
    assert book.mid is None


def test_walk_for_size_single_level_exact_fill():
    asks = (L(50, 100),)
    result = walk_for_size(asks, 40, side="buy")
    assert result.filled_size == 40
    assert result.avg_price == cents_to_pbp(50)
    assert result.cost_cents == 2000  # 40 contracts * 50c = $20.00 = 2000c
    assert result.fully_filled


def test_walk_for_size_across_multiple_levels_buy_rounds_up():
    # 3 @ 33.33c + 2 @ 40c -> weighted avg, cost must round UP for a buy
    asks = (Level(3333, 3), Level(4000, 2))
    result = walk_for_size(asks, 5, side="buy")
    assert result.filled_size == 5
    # exact cost = 3*33.33 + 2*40 = 99.99 + 80 = 179.99c -> ceil to 180c
    assert result.cost_cents == 180
    assert result.fully_filled


def test_walk_for_size_sell_rounds_down():
    bids = (Level(3333, 3), Level(3000, 2))
    result = walk_for_size(bids, 5, side="sell")
    # exact proceeds = 3*33.33 + 2*30 = 99.99 + 60 = 159.99c -> floor to 159c
    assert result.cost_cents == 159
    assert result.fully_filled


def test_walk_for_size_insufficient_depth():
    asks = (L(50, 10),)
    result = walk_for_size(asks, 25, side="buy")
    assert result.filled_size == 10
    assert not result.fully_filled
    assert result.cost_cents == 500


def test_walk_for_size_empty_book():
    result = walk_for_size((), 10, side="buy")
    assert result.filled_size == 0
    assert result.avg_price is None
    assert result.cost_cents == 0
    assert not result.fully_filled


def test_walk_for_size_rejects_bad_side():
    with pytest.raises(ValueError):
        walk_for_size((L(50, 10),), 5, side="bogus")


def test_walk_for_size_rejects_nonpositive_size():
    with pytest.raises(ValueError):
        walk_for_size((L(50, 10),), 0, side="buy")


def test_depth_band_stops_at_boundary():
    # best ask 50c; levels at 50, 55 (within 5c band), 56 (outside)
    asks = (L(50, 10), L(55, 10), L(56, 10))
    best = cents_to_pbp(50)
    assert band_1c(asks, best) == 500       # only the 50c level: 10*50c=500c
    assert band_5c(asks, best) == 500 + 550  # 50c + 55c levels


def test_depth_band_bid_side_direction():
    # bids move down away from best; band should include only nearby ones
    bids = (L(50, 10), L(46, 10), L(40, 10))
    best = cents_to_pbp(50)
    assert band_5c(bids, best) == 500 + 460  # 50c and 46c, not 40c
