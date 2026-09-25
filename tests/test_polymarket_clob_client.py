from fractions import Fraction

from arb.money import cents_to_pbp
from arb.polymarket.clob_client import ClobClient


def test_get_book_parses_object_levels_and_sorts(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")

    def fake_get(path, params=None):
        assert path == "/book"
        assert params == {"token_id": "tok1"}
        return {
            "bids": [{"price": "0.44", "size": "10"}, {"price": "0.46", "size": "5"}],
            "asks": [{"price": "0.51", "size": "8"}, {"price": "0.49", "size": "3"}],
        }

    monkeypatch.setattr(client, "_get", fake_get)
    book = client.get_book("tok1")
    assert book.best_bid == cents_to_pbp(46)
    assert book.best_ask == cents_to_pbp(49)
    assert book.bids[1].price == cents_to_pbp(44)


def test_get_book_handles_real_fractional_sizes(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {
        "bids": [{"price": "0.001", "size": "10465279.8"}],
        "asks": [{"price": "0.5", "size": "2240739.84"}],
    })
    book = client.get_book("tok1")
    assert book.bids[0].size == Fraction("10465279.8")
    assert book.asks[0].size == Fraction("2240739.84")


def test_get_book_empty_sides(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {"bids": [], "asks": []})
    book = client.get_book("tok1")
    assert book.mid is None


def test_get_midpoint(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {"mid": "0.635"})
    assert client.get_midpoint("tok1") == 6350


def test_get_midpoint_missing(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {})
    assert client.get_midpoint("tok1") is None


def test_get_prices_history_string_prices(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {
        "history": [{"t": 1000, "p": "0.50"}, {"t": 2000, "p": "0.52"}]
    })
    hist = client.get_prices_history("tok1", 0, 3000)
    assert hist == [(1000, cents_to_pbp(50)), (2000, cents_to_pbp(52))]


def test_get_prices_history_numeric_prices(monkeypatch):
    client = ClobClient(clob_base="https://clob.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {
        "history": [{"t": 1000, "p": 0.5}]
    })
    hist = client.get_prices_history("tok1", 0, 3000)
    assert hist == [(1000, cents_to_pbp(50))]
