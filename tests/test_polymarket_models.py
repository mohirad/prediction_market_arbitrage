import json

from arb.polymarket.models import PolymarketMarket


def test_from_gamma_extracts_token_ids_when_outcomes_confirm_order():
    obj = {
        "conditionId": "0xabc",
        "question": "Will X happen?",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "active": True,
        "closed": False,
    }
    m = PolymarketMarket.from_gamma(obj)
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"


def test_from_gamma_handles_reversed_outcome_order():
    obj = {
        "conditionId": "0xabc",
        "question": "q",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(["No", "Yes"]),
        "active": True,
        "closed": False,
    }
    m = PolymarketMarket.from_gamma(obj)
    assert m.yes_token_id == "222"
    assert m.no_token_id == "111"


def test_from_gamma_refuses_to_guess_without_outcome_labels():
    obj = {
        "conditionId": "0xabc",
        "question": "q",
        "clobTokenIds": json.dumps(["111", "222"]),
        "active": True,
        "closed": False,
    }
    m = PolymarketMarket.from_gamma(obj)
    assert m.yes_token_id is None
    assert m.no_token_id is None


def test_from_gamma_handles_native_list_fields():
    obj = {
        "conditionId": "0xabc",
        "question": "q",
        "clobTokenIds": ["111", "222"],
        "outcomes": ["Yes", "No"],
        "active": True,
        "closed": False,
    }
    m = PolymarketMarket.from_gamma(obj)
    assert m.yes_token_id == "111"


def test_from_gamma_no_token_ids_at_all():
    obj = {"conditionId": "0xabc", "question": "q", "active": True, "closed": False}
    m = PolymarketMarket.from_gamma(obj)
    assert m.yes_token_id is None and m.no_token_id is None
