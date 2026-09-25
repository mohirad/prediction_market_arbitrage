import json
from datetime import datetime, timezone

from arb.dashboard.candidates_serialize import discovery_result_to_payload
from arb.dashboard.discover_live import DiscoveryResult
from arb.matching.discover import CandidatePair, KalshiCandidate, PolymarketCandidate


def make_pair():
    k = KalshiCandidate(ticker="T1", event_ticker="E1", series_ticker="SER", title="Will BTC reach 100k?",
                         category="Crypto", yes_bid_pbp=3950, yes_ask_pbp=4050, mid_pbp=4000,
                         volume_24h_cents=500_000, volume_total_cents=5_000_000, close_time="2026-12-31T23:59:00Z")
    p = PolymarketCandidate(condition_id="0xabc", question="Will Bitcoin reach $100,000?", yes_label="Yes",
                             no_label="No", yes_price_pbp=4200, no_price_pbp=5800,
                             volume_24h_cents=300_000, volume_total_cents=2_000_000,
                             event_slug="ev", market_slug="mk", end_date="2026-12-31T05:00:00Z")
    return CandidatePair(kalshi=k, polymarket=p, similarity=0.68, yes_gap_pbp=-200, no_gap_pbp=200, rank_score=1000)


def test_payload_is_json_serializable_and_has_expected_shape():
    result = DiscoveryResult(generated_at=datetime.now(timezone.utc), kalshi_scanned=600,
                              polymarket_scanned=300, pairs=[make_pair()])
    payload = discovery_result_to_payload(result)
    json.dumps(payload)

    assert payload["count"] == 1
    assert payload["kalshi_scanned"] == 600
    assert payload["polymarket_scanned"] == 300
    assert payload["error"] is None

    pair = payload["pairs"][0]
    assert pair["kalshi"]["ticker"] == "T1"
    assert pair["kalshi"]["yes_price_cents"] == 40.0
    assert pair["kalshi"]["no_price_cents"] == 60.0
    assert pair["kalshi"]["url"] == "https://kalshi.com/markets/ser"
    assert pair["polymarket"]["yes_label"] == "Yes"
    assert pair["polymarket"]["no_label"] == "No"
    assert pair["polymarket"]["url"] == "https://polymarket.com/event/ev/mk"
    assert pair["yes_gap_cents"] == -2.0
    assert pair["yes_gap_abs_cents"] == 2.0
    assert pair["no_gap_cents"] == 2.0
    assert pair["no_gap_abs_cents"] == 2.0
    assert pair["similarity"] == 0.68


def test_payload_error_case_has_empty_pairs():
    result = DiscoveryResult(generated_at=datetime.now(timezone.utc), kalshi_scanned=0,
                              polymarket_scanned=0, pairs=[], error="timeout")
    payload = discovery_result_to_payload(result)
    json.dumps(payload)
    assert payload["error"] == "timeout"
    assert payload["pairs"] == []
