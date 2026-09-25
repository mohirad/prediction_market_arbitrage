from arb.polymarket.gamma_client import GammaClient


def test_list_markets_stops_on_short_page(monkeypatch):
    client = GammaClient(gamma_base="https://gamma-api.polymarket.com")
    pages = [
        [{"conditionId": f"c{i}", "question": "q", "active": True, "closed": False} for i in range(2)],
        [],
    ]
    calls = []

    def fake_get(path, params=None):
        calls.append(dict(params))
        return pages.pop(0)

    monkeypatch.setattr(client, "_get", fake_get)
    markets = list(client.list_markets(page_size=2))
    assert len(markets) == 2
    assert calls[0]["offset"] == 0


def test_list_markets_single_short_page_stops_immediately(monkeypatch):
    client = GammaClient(gamma_base="https://gamma-api.polymarket.com")

    def fake_get(path, params=None):
        return [{"conditionId": "c0", "question": "q", "active": True, "closed": False}]

    monkeypatch.setattr(client, "_get", fake_get)
    markets = list(client.list_markets(page_size=100))
    assert len(markets) == 1


def test_list_events_handles_dict_wrapper(monkeypatch):
    client = GammaClient(gamma_base="https://gamma-api.polymarket.com")
    monkeypatch.setattr(client, "_get", lambda path, params=None: {"events": [{"id": "e1"}]})
    events = list(client.list_events(page_size=100))
    assert events == [{"id": "e1"}]
