import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

import arb.dashboard.server as server_mod
from arb.dashboard.discover_live import DiscoveryResult
from arb.dashboard.server import run_server
from arb.matching.discover import CandidatePair, KalshiCandidate, PolymarketCandidate


def make_pair():
    k = KalshiCandidate(ticker="T1", event_ticker="E1", series_ticker="SER", title="Will BTC reach 100k?",
                         category="Crypto", yes_bid_pbp=3950, yes_ask_pbp=4050, mid_pbp=4000,
                         volume_24h_cents=500_000, volume_total_cents=5_000_000, close_time=None)
    p = PolymarketCandidate(condition_id="0xabc", question="Will Bitcoin reach $100,000?", yes_label="Yes",
                             no_label="No", yes_price_pbp=4200, no_price_pbp=5800,
                             volume_24h_cents=300_000, volume_total_cents=2_000_000,
                             event_slug="ev", market_slug="mk", end_date=None)
    return CandidatePair(kalshi=k, polymarket=p, similarity=0.68, yes_gap_pbp=-200, no_gap_pbp=200, rank_score=1000)


def start_server(monkeypatch, tmp_path, discover_fn):
    monkeypatch.setattr(server_mod, "discover_and_rank", discover_fn)
    monkeypatch.setattr(server_mod, "connect", lambda path: object())
    srv = run_server(host="127.0.0.1", port=0, sqlite_path=str(tmp_path / "t.sqlite3"))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


@pytest.fixture()
def live_server(monkeypatch, tmp_path):
    def fake_discover(conn, kalshi, gamma, **kw):
        return DiscoveryResult(generated_at=datetime.now(timezone.utc), kalshi_scanned=600,
                                polymarket_scanned=300, pairs=[make_pair()])

    srv = start_server(monkeypatch, tmp_path, fake_discover)
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def test_index_served_at_root(live_server):
    with urllib.request.urlopen(live_server + "/") as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "Refresh" in body
        assert resp.headers["Content-Type"].startswith("text/html")


def test_unknown_path_404s(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(live_server + "/does-not-exist")
    assert exc_info.value.code == 404


def test_api_candidates_returns_json(live_server):
    with urllib.request.urlopen(live_server + "/api/candidates") as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/json"
        assert resp.headers["Cache-Control"] == "no-store"
        payload = json.loads(resp.read())
    assert payload["count"] == 1
    assert payload["kalshi_scanned"] == 600
    assert payload["pairs"][0]["kalshi"]["ticker"] == "T1"


def test_api_candidates_survives_discover_failure(monkeypatch, tmp_path):
    def boom(conn, kalshi, gamma, **kw):
        raise RuntimeError("network exploded")

    srv = start_server(monkeypatch, tmp_path, boom)
    port = srv.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/candidates")
        assert exc_info.value.code == 500
        body = json.loads(exc_info.value.read())
        assert "network exploded" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_candidates_honors_query_param_overrides(monkeypatch, tmp_path):
    captured = {}

    def fake_discover(conn, kalshi, gamma, **kw):
        captured.update(kw)
        return DiscoveryResult(generated_at=datetime.now(timezone.utc), kalshi_scanned=1,
                                polymarket_scanned=1, pairs=[])

    srv = start_server(monkeypatch, tmp_path, fake_discover)
    port = srv.server_address[1]
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/candidates?min_similarity=0.6&max_pairs=5")
    finally:
        srv.shutdown()
        srv.server_close()
    assert captured["min_similarity"] == 0.6
    assert captured["max_pairs"] == 5


def test_api_candidates_clamps_out_of_range_query_params(monkeypatch, tmp_path):
    captured = {}

    def fake_discover(conn, kalshi, gamma, **kw):
        captured.update(kw)
        return DiscoveryResult(generated_at=datetime.now(timezone.utc), kalshi_scanned=1,
                                polymarket_scanned=1, pairs=[])

    srv = start_server(monkeypatch, tmp_path, fake_discover)
    port = srv.server_address[1]
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/candidates?min_similarity=5&max_pairs=99999")
    finally:
        srv.shutdown()
        srv.server_close()
    assert captured["min_similarity"] == 1.0
    assert captured["max_pairs"] == 2000


def test_run_server_applies_config_overrides(tmp_path):
    srv = run_server(host="127.0.0.1", port=0, sqlite_path=str(tmp_path / "x.sqlite3"),
                      min_similarity=0.5, max_pairs=42, kalshi_max_pages=3)
    try:
        assert server_mod.CandidatesHandler.min_similarity == 0.5
        assert server_mod.CandidatesHandler.max_pairs == 42
        assert server_mod.CandidatesHandler.kalshi_max_pages == 3
    finally:
        srv.server_close()


def test_run_server_applies_arbitrage_engine_overrides(tmp_path):
    srv = run_server(host="127.0.0.1", port=0, sqlite_path=str(tmp_path / "x.sqlite3"),
                      anthropic_api_key="sk-test", anthropic_model="claude-sonnet-5",
                      min_rule_confidence=0.8, capital_limit_cents=500_000,
                      opportunities_min_similarity=0.7, opportunities_max_candidates=5)
    try:
        assert server_mod.CandidatesHandler.anthropic_api_key == "sk-test"
        assert server_mod.CandidatesHandler.anthropic_model == "claude-sonnet-5"
        assert server_mod.CandidatesHandler.min_rule_confidence == 0.8
        assert server_mod.CandidatesHandler.capital_limit_cents == 500_000
        assert server_mod.CandidatesHandler.opportunities_min_similarity == 0.7
        assert server_mod.CandidatesHandler.opportunities_max_candidates == 5
    finally:
        srv.server_close()


def test_api_opportunities_400s_without_anthropic_key(monkeypatch, tmp_path):
    # CandidatesHandler config is class-level and persists across tests in
    # this process; run_server(anthropic_api_key=None) intentionally means
    # "don't override" (see its docstring-equivalent None-check), so a
    # prior test's key would otherwise leak in here. Force a clean slate.
    monkeypatch.setattr(server_mod.CandidatesHandler, "anthropic_api_key", None)
    monkeypatch.setattr(server_mod.CandidatesHandler, "anthropic_api_key", None)
    srv = start_server(monkeypatch, tmp_path, lambda conn, kalshi, gamma, **kw: None)
    port = srv.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/opportunities")
        assert exc_info.value.code == 400
        body = json.loads(exc_info.value.read())
        assert "ANTHROPIC_API_KEY" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_opportunities_returns_scan_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(server_mod, "fetch_kalshi_candidates", lambda client: [])
    monkeypatch.setattr(server_mod, "fetch_polymarket_candidates", lambda client: [])
    monkeypatch.setattr(server_mod, "generate_candidates", lambda k, p, **kw: [])

    def fake_scan(kalshi, clob, llm, pairs, **kw):
        from arb.dashboard.opportunities_live import OpportunityScanResult
        return OpportunityScanResult(generated_at=datetime.now(timezone.utc), evaluated=0,
                                      skipped_no_liquidity_data=0, opportunities=(), errors=())

    monkeypatch.setattr(server_mod, "scan_opportunities", fake_scan)
    monkeypatch.setattr(server_mod.CandidatesHandler, "anthropic_api_key", "sk-test")

    srv = start_server(monkeypatch, tmp_path, lambda conn, kalshi, gamma, **kw: None)
    port = srv.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/opportunities") as resp:
            assert resp.status == 200
            payload = json.loads(resp.read())
        assert payload["evaluated"] == 0
        assert payload["opportunities"] == []
    finally:
        srv.shutdown()
        srv.server_close()
