import json
from datetime import datetime, timezone

from arb.arbitrage.models import ContractRef
from arb.arbitrage.pipeline import MarketInput, evaluate_pair
from arb.dashboard.opportunities_live import OpportunityScanResult
from arb.dashboard.opportunities_serialize import opportunity_to_dict, scan_result_to_payload
from arb.orderbook import Book, Level


def no_fee(shares, price):
    return 0


def book(price_pbp, size=100_000):
    return Book(bids=(), asks=(Level(price=price_pbp, size=size),))


class FakeLLM:
    def complete_json(self, *, system, user, max_tokens=2000):
        if "extracting structured resolution rules" in system:
            return {"subject": "s", "event": "e", "action_required": "a", "date_range": "d",
                     "winning_condition": "w", "resolution_source": "src"}
        if "skeptical adversary" in system:
            return {"additional_states": []}
        return {
            "compatible": True, "incompatibility_reason": None,
            "semantic_similarity": 0.95, "rule_equivalence_score": 0.95,
            "resolution_source_compatible": True, "expiry_compatible": True,
            "states": [
                {"name": "happens", "description": "happens",
                 "contract_a_resolution": "yes", "contract_b_resolution": "yes"},
                {"name": "doesnt", "description": "doesnt",
                 "contract_a_resolution": "no", "contract_b_resolution": "no"},
            ],
        }


def make_opportunity():
    contract_a = ContractRef("kalshi", "A1", "Will A happen?")
    contract_b = ContractRef("polymarket", "B1", "Will B happen?")
    market_a = MarketInput(contract=contract_a, resolution_text="r", yes_book=book(4000), no_book=book(6500),
                            fee_fn=no_fee, url="https://kalshi.com/a")
    market_b = MarketInput(contract=contract_b, resolution_text="r", yes_book=book(6200), no_book=book(5500),
                            fee_fn=no_fee, url="https://polymarket.com/b")
    return evaluate_pair(FakeLLM(), market_a, market_b, capital_limit_cents=1_000_000)


def test_opportunity_to_dict_is_json_serializable_with_expected_shape():
    opp = make_opportunity()
    data = opportunity_to_dict(opp)
    json.dumps(data)  # must not raise

    assert data["classification"] == "true_arbitrage"
    assert data["contract_a"]["platform"] == "kalshi"
    assert data["contract_b"]["platform"] == "polymarket"
    assert data["guaranteed_profit_dollars"] > 0
    assert data["guaranteed_roi_pct"] > 0
    assert len(data["positions"]) == 2
    assert len(data["payoff_matrix"]) == 2
    assert {row["state"] for row in data["payoff_matrix"]} == {"happens", "doesnt"}


def test_scan_result_to_payload_is_json_serializable():
    opp = make_opportunity()
    result = OpportunityScanResult(generated_at=datetime.now(timezone.utc), evaluated=1,
                                    skipped_no_liquidity_data=0, opportunities=(opp,), errors=())
    payload = scan_result_to_payload(result)
    json.dumps(payload)
    assert payload["evaluated"] == 1
    assert len(payload["opportunities"]) == 1
