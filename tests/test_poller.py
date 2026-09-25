import textwrap

import pytest

from arb.db.connection import connect
from arb.money import cents_to_pbp
from arb.orderbook import Book, Level
from arb.snapshots.poller import (
    KalshiLeg,
    PolymarketLeg,
    WatchedPair,
    load_watchlist,
    poll_pair,
    run_loop,
)


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "test.sqlite3"))
    yield c
    c.close()


class FakeKalshiClient:
    def __init__(self, books=None, error=None):
        self.books = books
        self.error = error
        self.calls = []

    def get_orderbook(self, ticker):
        self.calls.append(ticker)
        if self.error:
            raise self.error
        return self.books


class FakeClobClient:
    def __init__(self, book=None, error=None):
        self.book = book
        self.error = error
        self.calls = []

    def get_book(self, token_id):
        self.calls.append(token_id)
        if self.error:
            raise self.error
        return self.book


def make_pair():
    return WatchedPair(
        pair_id="p1",
        kalshi=KalshiLeg(ticker="TICK-1", series_ticker="SER"),
        polymarket=PolymarketLeg(yes_token_id="yestok", no_token_id="notok"),
    )


def sample_book(bid_cents, ask_cents):
    return Book(bids=(Level(cents_to_pbp(bid_cents), 10),), asks=(Level(cents_to_pbp(ask_cents), 10),))


def test_poll_pair_happy_path_writes_all_four_snapshots(conn):
    yes_book, no_book = sample_book(45, 47), sample_book(53, 55)
    kalshi = FakeKalshiClient(books=(yes_book, no_book))
    clob = FakeClobClient(book=sample_book(40, 42))

    poll_pair(conn, kalshi, clob, make_pair())

    rows = conn.execute("SELECT platform, market_id, side, best_bid_pbp FROM snapshots ORDER BY platform, market_id, side").fetchall()
    assert len(rows) == 4
    platforms_sides = {(r["platform"], r["market_id"], r["side"]) for r in rows}
    assert ("kalshi", "TICK-1", "yes") in platforms_sides
    assert ("kalshi", "TICK-1", "no") in platforms_sides
    assert ("polymarket", "yestok", "yes") in platforms_sides
    assert ("polymarket", "notok", "no") in platforms_sides


def test_poll_pair_creates_market_stub_rows(conn):
    kalshi = FakeKalshiClient(books=(sample_book(45, 47), sample_book(53, 55)))
    clob = FakeClobClient(book=sample_book(40, 42))
    poll_pair(conn, kalshi, clob, make_pair())
    markets = conn.execute("SELECT platform, market_id FROM markets").fetchall()
    assert len(markets) == 3  # kalshi ticker + 2 polymarket token ids


def test_poll_pair_kalshi_failure_does_not_block_polymarket(conn):
    kalshi = FakeKalshiClient(error=RuntimeError("kalshi down"))
    clob = FakeClobClient(book=sample_book(40, 42))

    poll_pair(conn, kalshi, clob, make_pair())

    poly_rows = conn.execute("SELECT * FROM snapshots WHERE platform='polymarket'").fetchall()
    assert len(poly_rows) == 2
    errors = conn.execute("SELECT * FROM poll_errors WHERE platform='kalshi'").fetchall()
    assert len(errors) == 1
    assert "kalshi down" in errors[0]["message"]


def test_poll_pair_polymarket_failure_does_not_block_kalshi(conn):
    kalshi = FakeKalshiClient(books=(sample_book(45, 47), sample_book(53, 55)))
    clob = FakeClobClient(error=RuntimeError("clob down"))

    poll_pair(conn, kalshi, clob, make_pair())

    kalshi_rows = conn.execute("SELECT * FROM snapshots WHERE platform='kalshi'").fetchall()
    assert len(kalshi_rows) == 2
    errors = conn.execute("SELECT * FROM poll_errors WHERE platform='polymarket'").fetchall()
    assert len(errors) == 2  # yes leg + no leg both failed


def test_run_loop_bounded_iterations_polls_expected_number_of_times(conn):
    kalshi = FakeKalshiClient(books=(sample_book(45, 47), sample_book(53, 55)))
    clob = FakeClobClient(book=sample_book(40, 42))
    run_loop(conn, kalshi, clob, [make_pair()], interval_seconds=0, iterations=3)
    assert kalshi.calls == ["TICK-1", "TICK-1", "TICK-1"]


def test_load_watchlist_parses_yaml(tmp_path):
    yaml_path = tmp_path / "watchlist.yaml"
    yaml_path.write_text(textwrap.dedent("""
        pairs:
          - pair_id: foo
            kalshi:
              ticker: TICK-1
              series_ticker: SER
            polymarket:
              yes_token_id: "111"
              no_token_id: "222"
    """))
    pairs = load_watchlist(str(yaml_path))
    assert len(pairs) == 1
    assert pairs[0].pair_id == "foo"
    assert pairs[0].kalshi.ticker == "TICK-1"
    assert pairs[0].polymarket.yes_token_id == "111"
