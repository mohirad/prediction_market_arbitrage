import pytest

from arb.arbitrage.llm_client import LLMResponseError
from arb.arbitrage.rules import RuleSpec, extract_rule_spec


class FakeLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, user, max_tokens=2000):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return self.response


def test_extract_rule_spec_parses_full_response():
    llm = FakeLLMClient({
        "subject": "J.D. Vance", "event": "2028 Republican presidential nomination",
        "action_required": "Vance must be declared the Republican Party's nominee",
        "date_range": "before the 2028 Republican National Convention concludes",
        "jurisdiction": "Republican National Committee",
        "winning_condition": "Vance is formally nominated at the RNC",
        "exceptions": ["withdrawal before the convention voids this contract"],
        "resolution_source": "official RNC announcement",
        "cancellation_rules": "voided if the convention does not occur",
        "edge_cases": ["contested convention", "brokered convention"],
    })
    spec = extract_rule_spec(llm, platform="kalshi", title="Will Vance win the GOP nomination?",
                              resolution_text="...")
    assert spec.subject == "J.D. Vance"
    assert spec.event == "2028 Republican presidential nomination"
    assert spec.jurisdiction == "Republican National Committee"
    assert spec.exceptions == ("withdrawal before the convention voids this contract",)
    assert spec.edge_cases == ("contested convention", "brokered convention")
    assert len(llm.calls) == 1
    assert "kalshi" in llm.calls[0]["user"]


def test_extract_rule_spec_defaults_optional_fields():
    llm = FakeLLMClient({
        "subject": "Bitcoin", "event": "BTC/USD price", "action_required": "BTC reaches $100,000",
        "date_range": "by Dec 31 2026", "winning_condition": "BTC/USD >= 100000",
        "resolution_source": "Coinbase spot price",
    })
    spec = extract_rule_spec(llm, platform="polymarket", title="Will BTC hit 100k?", resolution_text="")
    assert spec.jurisdiction is None
    assert spec.cancellation_rules is None
    assert spec.exceptions == ()
    assert spec.edge_cases == ()


def test_extract_rule_spec_raises_on_missing_required_field():
    llm = FakeLLMClient({"subject": "X"})  # missing everything else
    with pytest.raises(LLMResponseError, match="missing required fields"):
        extract_rule_spec(llm, platform="kalshi", title="t", resolution_text="r")


def test_rule_spec_from_json_rejects_empty_string_as_missing():
    with pytest.raises(LLMResponseError):
        RuleSpec.from_json({
            "subject": "", "event": "e", "action_required": "a", "date_range": "d",
            "winning_condition": "w", "resolution_source": "s",
        })
