import json

from arb.matching.discover import (
    KalshiCandidate,
    PolymarketCandidate,
    fetch_kalshi_candidates,
    fetch_polymarket_candidates,
    generate_candidates,
    jaccard,
    tokenize,
)


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = tokenize("Will Bitcoin reach $100,000 by December 31, 2026?")
    assert "bitcoin" in tokens
    assert "100" in tokens
    assert "2026" in tokens
    assert "will" not in tokens
    assert "by" not in tokens


def test_jaccard_identical_and_disjoint():
    a = frozenset({"bitcoin", "100000", "2026"})
    assert jaccard(a, a) == 1.0
    b = frozenset({"weather", "temperature"})
    assert jaccard(a, b) == 0.0


def test_jaccard_empty_sets_are_zero_not_error():
    assert jaccard(frozenset(), frozenset({"x"})) == 0.0
    assert jaccard(frozenset(), frozenset()) == 0.0


class FakeKalshiClient:
    def __init__(self, events):
        self._events = events

    def get_events(self, **kwargs):
        yield from self._events


def make_event(ticker, series, title, category, markets):
    return {"event_ticker": ticker, "series_ticker": series, "title": title,
            "category": category, "markets": markets}


def make_market(ticker, title, yes_bid, yes_ask, vol24h, vol_total, yes_sub_title=None):
    m = {"ticker": ticker, "title": title, "yes_bid_dollars": yes_bid, "yes_ask_dollars": yes_ask,
         "volume_24h_fp": vol24h, "volume_fp": vol_total, "close_time": "2026-12-31T23:59:00Z"}
    if yes_sub_title is not None:
        m["yes_sub_title"] = yes_sub_title
    return m


def test_fetch_kalshi_candidates_drops_zero_volume_and_sorts_by_volume():
    events = [
        make_event("E1", "SER1", "Event one", "Crypto", [
            make_market("T1", "Will BTC reach 100k?", "0.40", "0.42", "500.00", "10000.00"),
        ]),
        make_event("E2", "SER2", "Event two", "Politics", [
            make_market("T2", "Zero volume market", "0.10", "0.12", "0.00", "0.00"),
        ]),
        make_event("E3", "SER3", "Event three", "Crypto", [
            make_market("T3", "Will ETH reach 5k?", "0.20", "0.22", "9000.00", "50000.00"),
        ]),
    ]
    client = FakeKalshiClient(events)
    candidates = fetch_kalshi_candidates(client, max_pages=1, page_size=200)
    assert [c.ticker for c in candidates] == ["T3", "T1"]  # sorted by volume desc, T2 dropped
    assert candidates[0].volume_24h_cents == 900000
    assert candidates[1].mid_pbp == 4100  # (4000+4200)/2


def test_fetch_kalshi_candidates_folds_in_yes_sub_title_for_multi_outcome_events():
    # A "Who will run?" event reuses the same generic title on every
    # per-candidate market -- the actual subject lives in yes_sub_title.
    events = [
        make_event("E1", "SER1", "Who will run for the nomination?", "Elections", [
            make_market("T1", "Who will run for the nomination?", "0.10", "0.12", "500.00", "1000.00",
                         yes_sub_title="Alexandria Ocasio-Cortez"),
            make_market("T2", "Who will run for the nomination?", "0.20", "0.22", "500.00", "1000.00",
                        yes_sub_title="Gavin Newsom"),
        ]),
    ]
    client = FakeKalshiClient(events)
    candidates = fetch_kalshi_candidates(client, max_pages=1, page_size=200)
    titles = {c.ticker: c.title for c in candidates}
    assert "Alexandria Ocasio-Cortez" in titles["T1"]
    assert "Gavin Newsom" in titles["T2"]
    assert titles["T1"] != titles["T2"]


def test_fetch_kalshi_candidates_does_not_duplicate_subtitle_already_in_title():
    events = [
        make_event("E1", "SER1", "Event", "Crypto", [
            make_market("T1", "Will Bitcoin reach 100k?", "0.4", "0.42", "500.00", "1000.00",
                         yes_sub_title="Bitcoin"),
        ]),
    ]
    client = FakeKalshiClient(events)
    candidates = fetch_kalshi_candidates(client, max_pages=1, page_size=200)
    assert candidates[0].title == "Will Bitcoin reach 100k?"  # not "... (Bitcoin)"


def test_fetch_kalshi_candidates_respects_page_cap():
    events = [make_event(f"E{i}", f"SER{i}", f"Event {i}", "Crypto",
                          [make_market(f"T{i}", f"Market {i}", "0.5", "0.5", "100.00", "100.00")])
              for i in range(10)]
    client = FakeKalshiClient(events)
    candidates = fetch_kalshi_candidates(client, max_pages=1, page_size=3)
    assert len(candidates) == 3  # only first 3 events consumed


class FakeGammaClient:
    def __init__(self, raw):
        self._raw = raw

    def list_markets_raw(self, **kwargs):
        return self._raw


def make_poly_market(question, outcomes, prices, vol24h, vol_total, slug="mkt", event_slug="evt"):
    return {
        "conditionId": "0xabc", "question": question,
        "outcomes": json.dumps(outcomes), "outcomePrices": json.dumps(prices),
        "volume24hr": vol24h, "volume": vol_total, "slug": slug,
        "events": [{"slug": event_slug}], "endDate": "2026-12-31T05:00:00Z",
    }


def test_fetch_polymarket_candidates_prefers_yes_label():
    raw = [make_poly_market("Will BTC reach 100k?", ["Yes", "No"], ["0.41", "0.59"], 5000.0, 20000.0)]
    gamma = FakeGammaClient(raw)
    candidates = fetch_polymarket_candidates(gamma)
    assert candidates[0].yes_label == "Yes"
    assert candidates[0].yes_price_pbp == 4100
    assert candidates[0].no_label == "No"
    assert candidates[0].no_price_pbp == 5900


def test_fetch_polymarket_candidates_falls_back_to_first_outcome():
    raw = [make_poly_market("Giants vs Rams", ["Giants", "Rams"], ["0.30", "0.70"], 5000.0, 20000.0)]
    gamma = FakeGammaClient(raw)
    candidates = fetch_polymarket_candidates(gamma)
    assert candidates[0].yes_label == "Giants"
    assert candidates[0].yes_price_pbp == 3000
    assert candidates[0].no_label == "Rams"
    assert candidates[0].no_price_pbp == 7000


def test_fetch_polymarket_candidates_drops_zero_volume():
    raw = [
        make_poly_market("A", ["Yes", "No"], ["0.5", "0.5"], 0.0, 0.0),
        make_poly_market("B", ["Yes", "No"], ["0.5", "0.5"], 100.0, 100.0),
    ]
    gamma = FakeGammaClient(raw)
    candidates = fetch_polymarket_candidates(gamma)
    assert len(candidates) == 1
    assert candidates[0].question == "B"


def test_fetch_polymarket_candidates_url_uses_event_and_market_slug():
    raw = [make_poly_market("A", ["Yes", "No"], ["0.5", "0.5"], 100.0, 100.0, slug="mk-a", event_slug="ev-a")]
    gamma = FakeGammaClient(raw)
    c = fetch_polymarket_candidates(gamma)[0]
    assert c.url() == "https://polymarket.com/event/ev-a/mk-a"


def make_k(ticker, title, mid, vol24h=1000):
    return KalshiCandidate(ticker=ticker, event_ticker="E", series_ticker="SER", title=title,
                            category="Crypto", yes_bid_pbp=mid - 50, yes_ask_pbp=mid + 50, mid_pbp=mid,
                            volume_24h_cents=vol24h, volume_total_cents=vol24h * 10, close_time=None)


def make_p(question, price, vol24h=1000, no_price=None):
    return PolymarketCandidate(condition_id="0x1", question=question, yes_label="Yes", no_label="No",
                                yes_price_pbp=price, no_price_pbp=no_price if no_price is not None else 10_000 - price,
                                volume_24h_cents=vol24h, volume_total_cents=vol24h * 10,
                                event_slug="ev", market_slug="mk", end_date=None)


def test_generate_candidates_matches_similar_titles():
    k = [make_k("T1", "Will Bitcoin reach 100000 by december 2026", 4000)]
    p = [make_p("Will Bitcoin reach 100000 by December 31 2026", 4200),
         make_p("Completely unrelated weather market", 5000)]
    pairs = generate_candidates(k, p, min_similarity=0.3)
    assert len(pairs) == 1
    assert pairs[0].kalshi.ticker == "T1"
    assert pairs[0].polymarket.question.startswith("Will Bitcoin")
    assert pairs[0].yes_gap_pbp == 4000 - 4200
    assert pairs[0].no_gap_pbp == (10_000 - 4000) - (10_000 - 4200)


def test_generate_candidates_skips_below_threshold():
    k = [make_k("T1", "Will Bitcoin reach 100000", 4000)]
    p = [make_p("Completely unrelated weather market in Chicago", 5000)]
    pairs = generate_candidates(k, p, min_similarity=0.5)
    assert pairs == []


def test_generate_candidates_ranks_by_volume_weighted_divergence():
    k = [
        make_k("BIG", "Will Bitcoin reach 100000 by 2026", 4000, vol24h=100_000),
        make_k("SMALL", "Will Ethereum reach 5000 by 2026", 4000, vol24h=10),
    ]
    p = [
        make_p("Will Bitcoin reach 100000 by 2026", 5000, vol24h=100_000),
        make_p("Will Ethereum reach 5000 by 2026", 5000, vol24h=10),
    ]
    pairs = generate_candidates(k, p, min_similarity=0.3)
    assert pairs[0].kalshi.ticker == "BIG"  # bigger volume * same divergence -> ranks first


def test_generate_candidates_rejects_same_template_different_state():
    # "Senate race in Iowa" vs "Senate race in Kansas" -- nearly every
    # word overlaps (Republicans/win/Senate/race), but the state, the
    # actual subject, differs. Should not surface as a candidate match.
    k = [make_k("IA", "Will Republicans win the Senate race in Iowa? (Republican party)", 5300)]
    p = [make_p("Will the Republicans win the Kansas Senate race in 2026?", 7050)]
    pairs = generate_candidates(k, p, min_similarity=0.35)
    assert pairs == []


def test_generate_candidates_rejects_same_template_different_person():
    # Same "next PM of Israel" template, different named candidate on
    # each side -- must not be treated as the same market.
    k = [make_k("KATZ", "Will Israel Katz be the next Prime Minister of Israel?", 410)]
    p = [make_p("Will Benjamin Netanyahu be the next Prime Minister of Israel?", 2950)]
    pairs = generate_candidates(k, p, min_similarity=0.35)
    assert pairs == []


def test_generate_candidates_keeps_match_with_one_sided_extra_wording():
    # Same person, same office, same country -- one side just has more
    # descriptive wording ("following the next Serbian general
    # election"). Neither side contributes a rare word the other side
    # contradicts, so this should still surface as a candidate match.
    k = [make_k("VUC", "Will Aleksandar Vucic become Prime Minister of Serbia "
                       "following the next Serbian general election?", 5350)]
    p = [make_p("Will Aleksandar Vucic be the next Prime Minister of Serbia?", 5280)]
    pairs = generate_candidates(k, p, min_similarity=0.35)
    assert len(pairs) == 1
    assert pairs[0].kalshi.ticker == "VUC"


def test_generate_candidates_respects_max_pairs():
    k = [make_k(f"T{i}", f"Will thing {i} happen by 2026", 4000) for i in range(5)]
    p = [make_p(f"Will thing {i} happen by 2026", 4500) for i in range(5)]
    pairs = generate_candidates(k, p, min_similarity=0.3, max_pairs=2)
    assert len(pairs) == 2
