from arb.arbitrage.execution import (
    STANDARD_SIZE_CENTS,
    max_profitable_shares,
    quote_shares,
    quote_standard_sizes,
    shares_for_dollar_size,
)
from arb.money import Cents
from arb.orderbook import Book, Level


def no_fee(shares, price):
    return Cents(0)


def make_book(ask_levels):
    return Book(bids=(), asks=tuple(Level(price=p, size=s) for p, s in ask_levels))


def test_quote_shares_walks_multiple_levels():
    book = make_book([(4000, 50), (4100, 100)])
    q = quote_shares(book, 100, no_fee)
    assert q.filled_shares == 100
    assert q.fully_fillable
    # 50 @ 4000 + 50 @ 4100 = weighted avg 4050
    assert q.avg_price_pbp == 4050


def test_quote_shares_partial_fill_when_book_runs_dry():
    book = make_book([(4000, 10)])
    q = quote_shares(book, 100, no_fee)
    assert q.filled_shares == 10
    assert not q.fully_fillable


def test_shares_for_dollar_size_uses_best_ask():
    book = make_book([(5000, 1_000_000)])  # 50c/share
    shares = shares_for_dollar_size(book, 100_00)  # $100
    assert shares == 200  # $100 / $0.50


def test_shares_for_dollar_size_empty_book_is_zero():
    book = make_book([])
    assert shares_for_dollar_size(book, 100_00) == 0


def test_quote_standard_sizes_skips_unquotable_sizes():
    book = make_book([(5000, 1_000_000)])
    quotes = quote_standard_sizes(book, no_fee)
    assert set(quotes.keys()) == set(STANDARD_SIZE_CENTS)
    for size_cents, quote in quotes.items():
        assert quote.fully_fillable


def test_max_profitable_shares_finds_the_boundary():
    # profit is positive up to and including 500 shares, then negative
    def evaluate(shares):
        return 100 - (shares / 5)  # zero at shares=500

    assert max_profitable_shares(evaluate, lo=1, hi=1000) == 499  # last share fully positive (100-499/5=0.2>0, 500 -> exactly 0)


def test_max_profitable_shares_zero_when_not_even_profitable_at_lo():
    assert max_profitable_shares(lambda s: -1, lo=1, hi=1000) == 0


def test_max_profitable_shares_returns_hi_when_always_profitable():
    assert max_profitable_shares(lambda s: 1000, lo=1, hi=1000) == 1000
