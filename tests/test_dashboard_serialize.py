import json
from datetime import datetime, timezone

from arb.dashboard.render import PairResult
from arb.dashboard.serialize import opportunities_payload, pair_result_to_dict
from arb.matching.store import KalshiLegRef, MatchedPair, PolymarketLegRef
from arb.money import cents_to_pbp
from arb.orderbook import Book, Level
from arb.pricing.hard_arb import evaluate_hard_arb


def make_pair(pair_id="btc-100k"):
    return MatchedPair(
        pair_id=pair_id, category="crypto",
        kalshi=KalshiLegRef(ticker="KXBTCMAXY-26DEC31-99999.99", series_ticker="KXBTCMAXY"),
        polymarket=PolymarketLegRef(yes_token_id="y1", no_token_id="n1", event_slug="ev", market_slug="mk"),
        kalshi_yes_maps_to="yes", confidence=0.9, human_approved=True, notes="note",
    )


def book(ask_cents, size):
    return Book(bids=(), asks=(Level(cents_to_pbp(ask_cents), size),))


def profitable_evals():
    return evaluate_hard_arb(
        kalshi_ticker="KXBTCMAXY-26DEC31-99999.99", kalshi_yes_book=book(40, 100), kalshi_no_book=book(65, 100),
        poly_yes_token="y1", poly_no_token="n1", poly_yes_book=book(62, 100), poly_no_book=book(40, 100),
        size=10, kalshi_yes_maps_to="yes", poly_category="crypto",
    )


def test_pair_result_to_dict_is_json_serializable_and_has_expected_shape():
    best = max(profitable_evals(), key=lambda e: e.net_edge_cents)
    result = PairResult(pair=make_pair(), generated_at=datetime.now(timezone.utc),
                         best_arb=best, soft_divergence_bps=5)
    d = pair_result_to_dict(result)
    json.dumps(d)  # must not raise

    assert d["pair_id"] == "btc-100k"
    assert d["is_hard_arb"] is True
    assert d["net_edge_cents"] > 0
    assert d["net_edge_dollars"] == d["net_edge_cents"] / 100
    assert len(d["legs"]) == 2
    assert d["legs"][0]["action"] in ("BUY YES", "BUY NO")
    assert d["kalshi_url"] == "https://kalshi.com/markets/kxbtcmaxy"
    assert d["polymarket_url"] == "https://polymarket.com/event/ev/mk"


def test_pair_result_to_dict_error_case_has_empty_legs():
    result = PairResult(pair=make_pair(), generated_at=datetime.now(timezone.utc),
                         best_arb=None, soft_divergence_bps=None, error="timeout")
    d = pair_result_to_dict(result)
    json.dumps(d)
    assert d["error"] == "timeout"
    assert d["legs"] == []
    assert d["is_hard_arb"] is False


def test_opportunities_payload_ranks_by_net_edge_and_is_json_safe():
    hard = PairResult(pair=make_pair("hard"), generated_at=datetime.now(timezone.utc),
                       best_arb=max(profitable_evals(), key=lambda e: e.net_edge_cents),
                       soft_divergence_bps=None)
    unprofitable = evaluate_hard_arb(
        kalshi_ticker="T2", kalshi_yes_book=book(50, 100), kalshi_no_book=book(50, 100),
        poly_yes_token="y2", poly_no_token="n2", poly_yes_book=book(50, 100), poly_no_book=book(50, 100),
        size=10, kalshi_yes_maps_to="yes",
    )
    soft = PairResult(pair=make_pair("soft"), generated_at=datetime.now(timezone.utc),
                       best_arb=max(unprofitable, key=lambda e: e.net_edge_cents), soft_divergence_bps=0)

    payload = opportunities_payload([soft, hard])
    json.dumps(payload)
    assert payload["count"] == 2
    assert payload["hard_arb_count"] == 1
    assert [p["pair_id"] for p in payload["pairs"]] == ["hard", "soft"]
    assert payload["generated_at"] is not None
