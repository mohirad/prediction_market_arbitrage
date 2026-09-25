import pytest

from arb.dashboard.discover_live import discover_and_rank
from arb.db.connection import connect
from arb.matching.discover import KalshiCandidate, PolymarketCandidate


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "test.sqlite3"))
    yield c
    c.close()


def make_k(ticker="T1", title="Will Bitcoin reach 100000 by 2026", mid=4000):
    return KalshiCandidate(ticker=ticker, event_ticker="E1", series_ticker="SER", title=title,
                            category="Crypto", yes_bid_pbp=mid - 50, yes_ask_pbp=mid + 50, mid_pbp=mid,
                            volume_24h_cents=100_000, volume_total_cents=1_000_000, close_time=None)


def make_p(question="Will Bitcoin reach 100000 by 2026", price=4200, label="Yes"):
    return PolymarketCandidate(condition_id="0xabc", question=question, yes_label=label, no_label="No",
                                yes_price_pbp=price, no_price_pbp=10_000 - price,
                                volume_24h_cents=100_000, volume_total_cents=1_000_000,
                                event_slug="ev", market_slug="mk", end_date=None)


class FakeKalshiClient:
    pass


class FakeGammaClient:
    pass


def test_discover_and_rank_persists_snapshots_for_matched_pairs(conn, monkeypatch):
    import arb.dashboard.discover_live as mod

    monkeypatch.setattr(mod, "fetch_kalshi_candidates", lambda client, **kw: [make_k()])
    monkeypatch.setattr(mod, "fetch_polymarket_candidates", lambda client, **kw: [make_p()])

    result = discover_and_rank(conn, FakeKalshiClient(), FakeGammaClient(), min_similarity=0.1)

    assert result.error is None
    assert result.kalshi_scanned == 1
    assert result.polymarket_scanned == 1
    assert len(result.pairs) == 1

    kalshi_snap = conn.execute("SELECT * FROM snapshots WHERE platform='kalshi'").fetchone()
    assert kalshi_snap["market_id"] == "T1"
    assert kalshi_snap["mid_pbp"] == 4000

    poly_snap = conn.execute("SELECT * FROM snapshots WHERE platform='polymarket'").fetchone()
    assert poly_snap["side"] == "Yes"  # non-yes/no-constrained outcome label stored as-is
    assert poly_snap["mid_pbp"] == 4200


def test_discover_and_rank_creates_market_stubs(conn, monkeypatch):
    import arb.dashboard.discover_live as mod

    monkeypatch.setattr(mod, "fetch_kalshi_candidates", lambda client, **kw: [make_k()])
    monkeypatch.setattr(mod, "fetch_polymarket_candidates", lambda client, **kw: [make_p()])

    discover_and_rank(conn, FakeKalshiClient(), FakeGammaClient(), min_similarity=0.1)
    markets = conn.execute("SELECT platform, market_id FROM markets").fetchall()
    assert {(m["platform"], m["market_id"]) for m in markets} == {("kalshi", "T1"), ("polymarket", "0xabc")}


def test_discover_and_rank_handles_fetch_failure_gracefully(conn, monkeypatch):
    import arb.dashboard.discover_live as mod

    def boom(client, **kw):
        raise RuntimeError("kalshi is down")

    monkeypatch.setattr(mod, "fetch_kalshi_candidates", boom)
    monkeypatch.setattr(mod, "fetch_polymarket_candidates", lambda client, **kw: [make_p()])

    result = discover_and_rank(conn, FakeKalshiClient(), FakeGammaClient())
    assert result.error == "kalshi is down"
    assert result.pairs == []
    count = conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
    assert count == 0


def test_discover_and_rank_no_matches_above_threshold(conn, monkeypatch):
    import arb.dashboard.discover_live as mod

    monkeypatch.setattr(mod, "fetch_kalshi_candidates", lambda client, **kw: [make_k()])
    monkeypatch.setattr(mod, "fetch_polymarket_candidates",
                         lambda client, **kw: [make_p(question="Completely unrelated weather thing")])

    result = discover_and_rank(conn, FakeKalshiClient(), FakeGammaClient(), min_similarity=0.9)
    assert result.error is None
    assert result.pairs == []
    count = conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
    assert count == 0
