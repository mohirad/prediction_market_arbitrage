from arb.dashboard.opportunities_live import scan_opportunities
from arb.matching.discover import CandidatePair, KalshiCandidate, PolymarketCandidate
from arb.orderbook import Book, Level


def make_k(ticker="T1", token_confirmed=True):
    return KalshiCandidate(ticker=ticker, event_ticker="E", series_ticker="SER", title="Will X happen?",
                            category="Crypto", yes_bid_pbp=3950, yes_ask_pbp=4050, mid_pbp=4000,
                            volume_24h_cents=100_000, volume_total_cents=1_000_000, close_time=None,
                            rules_text="If X happens, resolves YES.")


def make_p(condition_id="0xabc", with_tokens=True):
    return PolymarketCandidate(
        condition_id=condition_id, question="Will X happen?", yes_label="Yes", no_label="No",
        yes_price_pbp=4200, no_price_pbp=5800, volume_24h_cents=100_000, volume_total_cents=1_000_000,
        event_slug="ev", market_slug="mk", end_date=None, resolution_text="Resolves per official source.",
        yes_token_id="tok-yes" if with_tokens else None, no_token_id="tok-no" if with_tokens else None,
    )


def make_pair(with_tokens=True):
    return CandidatePair(kalshi=make_k(), polymarket=make_p(with_tokens=with_tokens),
                          similarity=0.9, yes_gap_pbp=-200, no_gap_pbp=200, rank_score=1000)


class FakeKalshiClient:
    def get_orderbook(self, ticker):
        return (Book(bids=(), asks=(Level(price=4000, size=100_000),)),
                Book(bids=(), asks=(Level(price=6500, size=100_000),)))


class FakeClobClient:
    def get_book(self, token_id):
        price = 5500 if token_id == "tok-no" else 6200
        return Book(bids=(), asks=(Level(price=price, size=100_000),))


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


def test_scan_opportunities_evaluates_pairs_with_confirmed_tokens():
    result = scan_opportunities(FakeKalshiClient(), FakeClobClient(), FakeLLM(), [make_pair()],
                                 max_candidates=10, capital_limit_cents=1_000_000, min_rule_confidence=0.9)
    assert result.evaluated == 1
    assert result.skipped_no_liquidity_data == 0
    assert result.errors == ()


def test_scan_opportunities_skips_pairs_without_confirmed_token_polarity():
    result = scan_opportunities(FakeKalshiClient(), FakeClobClient(), FakeLLM(), [make_pair(with_tokens=False)],
                                 max_candidates=10, capital_limit_cents=1_000_000, min_rule_confidence=0.9)
    assert result.evaluated == 0
    assert result.skipped_no_liquidity_data == 1


def test_scan_opportunities_respects_max_candidates():
    pairs = [make_pair(), make_pair(), make_pair()]
    result = scan_opportunities(FakeKalshiClient(), FakeClobClient(), FakeLLM(), pairs,
                                 max_candidates=1, capital_limit_cents=1_000_000, min_rule_confidence=0.9)
    assert result.evaluated == 1


def test_scan_opportunities_captures_errors_without_crashing(monkeypatch):
    import arb.dashboard.opportunities_live as mod

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "evaluate_pair", boom)
    result = scan_opportunities(FakeKalshiClient(), FakeClobClient(), FakeLLM(), [make_pair()],
                                 max_candidates=10, capital_limit_cents=1_000_000, min_rule_confidence=0.9)
    assert result.evaluated == 0
    assert len(result.errors) == 1
    assert "boom" in result.errors[0]
