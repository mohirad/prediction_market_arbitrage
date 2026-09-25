import pytest

from arb.arbitrage.llm_client import LLMResponseError
from arb.arbitrage.models import ContractRef, Resolution
from arb.arbitrage.rules import RuleSpec
from arb.arbitrage.states import detect_holes, propose_match


class FakeLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, user, max_tokens=2000):
        self.calls.append({"system": system, "user": user})
        return self.response


def make_rule(subject="Vance", event="e", winning="w", source="s"):
    return RuleSpec(subject=subject, event=event, action_required="a", date_range="d",
                     winning_condition=winning, resolution_source=source)


def test_propose_match_builds_states_and_confidence():
    contract_a = ContractRef("kalshi", "NOM", "Will Vance win the GOP nomination?")
    contract_b = ContractRef("polymarket", "PRES", "Will Vance win the presidency?")
    response = {
        "compatible": True, "incompatibility_reason": None,
        "semantic_similarity": 0.8, "rule_equivalence_score": 0.4,
        "resolution_source_compatible": True, "expiry_compatible": True,
        "states": [
            {"name": "no_nom", "description": "does not win nomination",
             "contract_a_resolution": "no", "contract_b_resolution": "no"},
            {"name": "nom_loses", "description": "wins nomination, loses general",
             "contract_a_resolution": "yes", "contract_b_resolution": "no"},
            {"name": "nom_wins", "description": "wins nomination and presidency",
             "contract_a_resolution": "yes", "contract_b_resolution": "yes"},
        ],
    }
    llm = FakeLLMClient(response)
    proposal = propose_match(llm, contract_a, make_rule(), contract_b, make_rule())

    assert proposal.compatible
    assert len(proposal.states) == 3
    nom_loses = next(s for s in proposal.states if s.name == "nom_loses")
    assert nom_loses.resolutions[contract_a] == Resolution.YES
    assert nom_loses.resolutions[contract_b] == Resolution.NO
    # confidence is min() of the components, not average -- 0.4 dominates here
    assert proposal.confidence == 0.4


def test_propose_match_confidence_zero_when_source_incompatible():
    contract_a = ContractRef("kalshi", "A", "A")
    contract_b = ContractRef("polymarket", "B", "B")
    response = {
        "compatible": True, "incompatibility_reason": None,
        "semantic_similarity": 0.99, "rule_equivalence_score": 0.99,
        "resolution_source_compatible": False, "expiry_compatible": True,
        "states": [],
    }
    llm = FakeLLMClient(response)
    proposal = propose_match(llm, contract_a, make_rule(), contract_b, make_rule())
    assert proposal.confidence == 0.0


def test_propose_match_incompatible_pair_reports_reason():
    contract_a = ContractRef("kalshi", "A", "Will X win the nomination?")
    contract_b = ContractRef("polymarket", "B", "Will X win the presidency?")
    response = {
        "compatible": False, "incompatibility_reason": "nomination does not imply winning the general election",
        "semantic_similarity": 0.7, "rule_equivalence_score": 0.1,
        "resolution_source_compatible": True, "expiry_compatible": False,
        "states": [],
    }
    llm = FakeLLMClient(response)
    proposal = propose_match(llm, contract_a, make_rule(), contract_b, make_rule())
    assert not proposal.compatible
    assert "nomination" in proposal.incompatibility_reason
    assert proposal.states == ()


def test_propose_match_raises_on_missing_field():
    llm = FakeLLMClient({"compatible": True})  # missing everything else
    with pytest.raises(LLMResponseError, match="missing required field"):
        propose_match(llm, ContractRef("kalshi", "A", "A"), make_rule(),
                      ContractRef("polymarket", "B", "B"), make_rule())


def test_propose_match_rejects_invalid_resolution_value():
    contract_a = ContractRef("kalshi", "A", "A")
    contract_b = ContractRef("polymarket", "B", "B")
    response = {
        "compatible": True, "incompatibility_reason": None,
        "semantic_similarity": 1.0, "rule_equivalence_score": 1.0,
        "resolution_source_compatible": True, "expiry_compatible": True,
        "states": [{"name": "s", "description": "d", "contract_a_resolution": "maybe", "contract_b_resolution": "no"}],
    }
    llm = FakeLLMClient(response)
    with pytest.raises(LLMResponseError, match="invalid"):
        propose_match(llm, contract_a, make_rule(), contract_b, make_rule())


def test_detect_holes_returns_new_states_only():
    contract_a = ContractRef("kalshi", "NOM", "Vance nomination")
    contract_b = ContractRef("polymarket", "PRES", "Vance presidency")
    llm = FakeLLMClient({
        "additional_states": [
            {"name": "nom_withdraws", "description": "wins nomination then withdraws before election",
             "contract_a_resolution": "yes", "contract_b_resolution": "no"},
        ],
    })
    existing = ()
    holes = detect_holes(llm, contract_a, make_rule(), contract_b, make_rule(), existing)
    assert len(holes) == 1
    assert holes[0].name == "nom_withdraws"
    assert holes[0].source == "hole_detector"


def test_detect_holes_empty_when_none_found():
    contract_a = ContractRef("kalshi", "A", "A")
    contract_b = ContractRef("polymarket", "B", "B")
    llm = FakeLLMClient({"additional_states": []})
    holes = detect_holes(llm, contract_a, make_rule(), contract_b, make_rule(), ())
    assert holes == ()
