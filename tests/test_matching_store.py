import textwrap

from arb.matching.store import load_matches


def write_yaml(tmp_path, content):
    path = tmp_path / "matches.yaml"
    path.write_text(textwrap.dedent(content))
    return str(path)


def test_load_matches_filters_unapproved_by_default(tmp_path):
    path = write_yaml(tmp_path, """
        version: 1
        pairs:
          - pair_id: approved-1
            category: crypto
            kalshi: {ticker: T1, series_ticker: SER}
            polymarket: {yes_token_id: "1", no_token_id: "2"}
            kalshi_yes_maps_to: "yes"
            confidence: 0.9
            human_approved: true
            notes: ok
          - pair_id: pending-1
            category: crypto
            kalshi: {ticker: T2, series_ticker: SER}
            polymarket: {yes_token_id: "3", no_token_id: "4"}
            kalshi_yes_maps_to: "yes"
            confidence: 0.5
            human_approved: false
            notes: needs review
    """)
    pairs = load_matches(path)
    assert [p.pair_id for p in pairs] == ["approved-1"]


def test_load_matches_all_with_approved_only_false(tmp_path):
    path = write_yaml(tmp_path, """
        version: 1
        pairs:
          - pair_id: approved-1
            kalshi: {ticker: T1, series_ticker: SER}
            polymarket: {yes_token_id: "1", no_token_id: "2"}
            kalshi_yes_maps_to: "yes"
            confidence: 0.9
            human_approved: true
          - pair_id: pending-1
            kalshi: {ticker: T2, series_ticker: SER}
            polymarket: {yes_token_id: "3", no_token_id: "4"}
            kalshi_yes_maps_to: "no"
            confidence: 0.4
            human_approved: false
    """)
    pairs = load_matches(path, approved_only=False)
    assert {p.pair_id for p in pairs} == {"approved-1", "pending-1"}


def test_kalshi_url_uses_series_ticker(tmp_path):
    path = write_yaml(tmp_path, """
        pairs:
          - pair_id: p1
            kalshi: {ticker: KXFOO-1, series_ticker: KXFOO}
            polymarket: {yes_token_id: "1", no_token_id: "2"}
            kalshi_yes_maps_to: "yes"
            confidence: 0.9
            human_approved: true
    """)
    pair = load_matches(path)[0]
    assert pair.kalshi_url() == "https://kalshi.com/markets/kxfoo"


def test_polymarket_url_uses_event_and_market_slug(tmp_path):
    path = write_yaml(tmp_path, """
        pairs:
          - pair_id: p1
            kalshi: {ticker: KXFOO-1, series_ticker: KXFOO}
            polymarket: {yes_token_id: "1", no_token_id: "2", event_slug: ev, market_slug: mk}
            kalshi_yes_maps_to: "yes"
            confidence: 0.9
            human_approved: true
    """)
    pair = load_matches(path)[0]
    assert pair.polymarket_url() == "https://polymarket.com/event/ev/mk"
