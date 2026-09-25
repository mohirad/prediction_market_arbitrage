import pytest

from arb.kalshi.auth import KalshiSigner
from arb.kalshi.client import KalshiClient
from cryptography.hazmat.primitives.asymmetric import rsa


def test_list_markets_paginates_via_cursor(monkeypatch):
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")
    pages = [
        {"markets": [{"ticker": "A", "title": "t1", "status": "open"}], "cursor": "page2"},
        {"markets": [{"ticker": "B", "title": "t2", "status": "open"}], "cursor": ""},
    ]
    calls = []

    def fake_get(path, params=None):
        calls.append(params)
        return pages.pop(0)

    monkeypatch.setattr(client, "_get", fake_get)
    markets = list(client.list_markets(status="open"))
    assert [m.ticker for m in markets] == ["A", "B"]
    assert calls[0].get("cursor") is None
    assert calls[1]["cursor"] == "page2"


def test_get_events_paginates(monkeypatch):
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")
    pages = [
        {"events": [{"event_ticker": "E1"}], "cursor": "next"},
        {"events": [{"event_ticker": "E2"}], "cursor": None},
    ]

    def fake_get(path, params=None):
        return pages.pop(0)

    monkeypatch.setattr(client, "_get", fake_get)
    events = list(client.get_events(status="open"))
    assert [e["event_ticker"] for e in events] == ["E1", "E2"]


def test_get_candlesticks_rejects_bad_interval():
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")
    with pytest.raises(ValueError):
        client.get_candlesticks("SER", "TICK", period_interval=5, start_ts=0, end_ts=1)


def test_get_candlesticks_valid_interval(monkeypatch):
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"candlesticks": [{"end_period_ts": 100}]}

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.get_candlesticks("SER", "TICK", period_interval=60, start_ts=0, end_ts=1000)
    assert result == [{"end_period_ts": 100}]
    assert captured["path"] == "/series/SER/markets/TICK/candlesticks"
    assert captured["params"]["period_interval"] == 60


def test_get_orderbook_delegates_to_parser(monkeypatch):
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")

    def fake_get(path, params=None):
        assert path == "/markets/FOO/orderbook"
        return {"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": []}}

    monkeypatch.setattr(client, "_get", fake_get)
    yes_book, no_book = client.get_orderbook("FOO")
    assert yes_book.best_bid is not None


def test_signed_path_includes_trade_api_prefix():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = KalshiSigner(key_id="kid", private_key=private_key)
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2", signer=signer)
    headers = client._auth_headers("GET", "/markets")
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert client._path_prefix == "/trade-api/v2"


def test_no_signer_means_no_auth_headers():
    client = KalshiClient(api_base="https://external-api.kalshi.com/trade-api/v2")
    assert client._auth_headers("GET", "/markets") == {}
