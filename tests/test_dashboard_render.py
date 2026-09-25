from datetime import datetime, timezone

from arb.dashboard.render import PairResult, render_dashboard
from arb.matching.store import KalshiLegRef, MatchedPair, PolymarketLegRef
from arb.money import cents_to_pbp
from arb.orderbook import Book, Level
from arb.pricing.hard_arb import evaluate_hard_arb


def make_pair(pair_id="btc-100k"):
    return MatchedPair(
        pair_id=pair_id, category="crypto",
        kalshi=KalshiLegRef(ticker="KXBTCMAXY-26DEC31-99999.99", series_ticker="KXBTCMAXY"),
        polymarket=PolymarketLegRef(yes_token_id="y1", no_token_id="n1",
                                     event_slug="ev", market_slug="mk"),
        kalshi_yes_maps_to="yes", confidence=0.9, human_approved=True,
        notes="test note",
    )


def profitable_evals():
    def book(ask_cents, size):
        return Book(bids=(), asks=(Level(cents_to_pbp(ask_cents), size),))
    return evaluate_hard_arb(
        kalshi_ticker="KXBTCMAXY-26DEC31-99999.99", kalshi_yes_book=book(40, 100), kalshi_no_book=book(65, 100),
        poly_yes_token="y1", poly_no_token="n1", poly_yes_book=book(62, 100), poly_no_book=book(40, 100),
        size=10, kalshi_yes_maps_to="yes", poly_category="crypto",
    )


def unprofitable_evals():
    def book(ask_cents, size):
        return Book(bids=(), asks=(Level(cents_to_pbp(ask_cents), size),))
    return evaluate_hard_arb(
        kalshi_ticker="T1", kalshi_yes_book=book(50, 100), kalshi_no_book=book(50, 100),
        poly_yes_token="y1", poly_no_token="n1", poly_yes_book=book(50, 100), poly_no_book=book(50, 100),
        size=10, kalshi_yes_maps_to="yes", poly_category="crypto",
    )


def test_render_hard_arb_card_shows_profit_and_legs():
    evals = profitable_evals()
    best = max(evals, key=lambda e: e.net_edge_cents)
    result = PairResult(pair=make_pair(), generated_at=datetime.now(timezone.utc),
                         best_arb=best, soft_divergence_bps=None)
    out = render_dashboard([result])
    assert "HARD ARB" in out
    assert "btc-100k" in out
    assert "KXBTCMAXY-26DEC31-99999.99" in out
    assert "BUY YES" in out and "BUY NO" in out
    assert "https://polymarket.com/event/ev/mk" in out
    assert "https://kalshi.com/markets/kxbtcmaxy" in out
    assert "test note" in out


def test_render_soft_divergence_card_has_no_locked_profit_language():
    evals = unprofitable_evals()
    best = max(evals, key=lambda e: e.net_edge_cents)
    result = PairResult(pair=make_pair(), generated_at=datetime.now(timezone.utc),
                         best_arb=best, soft_divergence_bps=25)
    out = render_dashboard([result])
    assert "no locked arb" in out
    assert "not risk-free" in out


def test_render_error_card():
    result = PairResult(pair=make_pair(), generated_at=datetime.now(timezone.utc),
                         best_arb=None, soft_divergence_bps=None, error="connection timeout")
    out = render_dashboard([result])
    assert "Could not fetch live data" in out
    assert "connection timeout" in out


def test_render_empty_list_shows_placeholder():
    out = render_dashboard([])
    assert "No approved matched pairs" in out


def test_render_ranks_hard_arb_above_soft_divergence():
    hard = PairResult(pair=make_pair("hard-pair"), generated_at=datetime.now(timezone.utc),
                       best_arb=max(profitable_evals(), key=lambda e: e.net_edge_cents),
                       soft_divergence_bps=None)
    soft = PairResult(pair=make_pair("soft-pair"), generated_at=datetime.now(timezone.utc),
                       best_arb=max(unprofitable_evals(), key=lambda e: e.net_edge_cents),
                       soft_divergence_bps=10)
    out = render_dashboard([soft, hard])
    assert out.index("hard-pair") < out.index("soft-pair")


def test_render_escapes_html_in_pair_id():
    pair = make_pair(pair_id="<script>alert(1)</script>")
    result = PairResult(pair=pair, generated_at=datetime.now(timezone.utc), best_arb=None, soft_divergence_bps=None, error="x")
    out = render_dashboard([result])
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
