from arb.arbitrage.models import ContractRef, Classification
from arb.arbitrage.pipeline import MarketInput, evaluate_pair
from arb.orderbook import Book, Level

RULE_SPEC_TEMPLATE = {
    "subject": "s", "event": "e", "action_required": "a", "date_range": "d",
    "winning_condition": "w", "resolution_source": "src",
}


def no_fee(shares, price):
    return 0


def book(price_pbp, size=100_000):
    return Book(bids=(), asks=(Level(price=price_pbp, size=size),))


def empty_book():
    return Book(bids=(), asks=())


class ScriptedLLMClient:
    def __init__(self, match_response, hole_response=None, rule_overrides=None):
        self.match_response = match_response
        self.hole_response = hole_response or {"additional_states": []}
        self.rule_overrides = rule_overrides or {}
        self.calls = []

    def complete_json(self, *, system, user, max_tokens=2000):
        self.calls.append(system[:30])
        if "extracting structured resolution rules" in system:
            for key, spec in self.rule_overrides.items():
                if key in user:
                    return spec
            return RULE_SPEC_TEMPLATE
        if "skeptical adversary" in system:
            return self.hole_response
        return self.match_response


def make_markets(a_yes=4000, a_no=6500, b_yes=6200, b_no=5500):
    contract_a = ContractRef("kalshi", "A1", "Will A happen?")
    contract_b = ContractRef("polymarket", "B1", "Will B happen?")
    market_a = MarketInput(contract=contract_a, resolution_text="rules a", yes_book=book(a_yes),
                            no_book=book(a_no), fee_fn=no_fee, url="https://kalshi.com/a")
    market_b = MarketInput(contract=contract_b, resolution_text="rules b", yes_book=book(b_yes),
                            no_book=book(b_no), fee_fn=no_fee, url="https://polymarket.com/b")
    return market_a, market_b


def two_state_match_response(confidence_fields=None):
    fields = confidence_fields or {
        "semantic_similarity": 0.95, "rule_equivalence_score": 0.95,
        "resolution_source_compatible": True, "expiry_compatible": True,
    }
    return {
        "compatible": True, "incompatibility_reason": None,
        "states": [
            {"name": "happens", "description": "event happens",
             "contract_a_resolution": "yes", "contract_b_resolution": "yes"},
            {"name": "doesnt", "description": "event doesn't happen",
             "contract_a_resolution": "no", "contract_b_resolution": "no"},
        ],
        **fields,
    }


def test_evaluate_pair_finds_true_arbitrage_end_to_end():
    market_a, market_b = make_markets()  # A yes 40c cheap, B no 55c cheap -> 95c combo
    llm = ScriptedLLMClient(two_state_match_response())
    result = evaluate_pair(llm, market_a, market_b, capital_limit_cents=1_000_000)

    assert result.classification == Classification.TRUE_ARBITRAGE
    assert result.guaranteed_profit_cents > 0
    assert result.total_cost_cents > 0
    sides = {p.contract.platform: p.side for p in result.positions}
    assert sides["kalshi"] == "yes"
    assert sides["polymarket"] == "no"
    assert result.max_profitable_shares is not None
    assert result.standard_size_quotes is not None


def test_evaluate_pair_rejects_incompatible_match():
    market_a, market_b = make_markets()
    response = {
        "compatible": False, "incompatibility_reason": "nomination does not imply winning the general election",
        "semantic_similarity": 0.8, "rule_equivalence_score": 0.2,
        "resolution_source_compatible": True, "expiry_compatible": False,
        "states": [],
    }
    llm = ScriptedLLMClient(response)
    result = evaluate_pair(llm, market_a, market_b, capital_limit_cents=1_000_000)
    assert result.classification == Classification.REJECTED_MATCH
    assert "nomination" in result.reject_reason
    assert result.positions == ()


def test_evaluate_pair_downgrades_low_confidence_to_manual_verification():
    market_a, market_b = make_markets()
    low_conf = two_state_match_response({
        "semantic_similarity": 0.9, "rule_equivalence_score": 0.5,
        "resolution_source_compatible": True, "expiry_compatible": True,
    })
    llm = ScriptedLLMClient(low_conf)
    result = evaluate_pair(llm, market_a, market_b, capital_limit_cents=1_000_000, min_rule_confidence=0.9)
    assert result.classification == Classification.MANUAL_VERIFICATION_REQUIRED
    assert result.guaranteed_profit_cents > 0  # math would say arbitrage; confidence gate overrides the label


def test_evaluate_pair_manual_verification_when_liquidity_insufficient():
    contract_a = ContractRef("kalshi", "A1", "Will A happen?")
    contract_b = ContractRef("polymarket", "B1", "Will B happen?")
    market_a = MarketInput(contract=contract_a, resolution_text="r", yes_book=empty_book(),
                            no_book=empty_book(), fee_fn=no_fee, url="u")
    market_b = MarketInput(contract=contract_b, resolution_text="r", yes_book=empty_book(),
                            no_book=empty_book(), fee_fn=no_fee, url="u")
    llm = ScriptedLLMClient(two_state_match_response())
    result = evaluate_pair(llm, market_a, market_b, capital_limit_cents=1_000_000)
    assert result.classification == Classification.MANUAL_VERIFICATION_REQUIRED
    assert "liquidity" in result.reject_reason


def test_evaluate_pair_rejects_when_price_discrepancy_too_expensive():
    # both sides expensive -> combined cost > 100c -> not arbitrage
    market_a, market_b = make_markets(a_yes=6000, b_no=6000)
    llm = ScriptedLLMClient(two_state_match_response())
    result = evaluate_pair(llm, market_a, market_b, capital_limit_cents=1_000_000)
    assert result.classification == Classification.PRICE_DISCREPANCY
