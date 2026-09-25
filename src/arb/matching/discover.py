"""Live candidate discovery: pull real (non-junk) markets from both
platforms, rank by trading volume, and fuzzy-match titles/questions
across platforms so a human can eyeball the price gap and judge for
themselves whether a pair is a legitimate arbitrage candidate.

This is deliberately NOT the full Phase 1 pipeline (embeddings +
strict resolution-criteria verification) -- it's a lightweight text
similarity matcher meant to surface candidates for a human to review,
not to auto-approve anything. Nothing found here is written to
data/matches.yaml automatically.

Kalshi gotcha (live-verified 2026-09-20): GET /markets?status=open with
no series filter is dominated end-to-end by auto-generated multivariate
combo markets (every one of the first 8000 results, paging through
cursors, had strike_type="custom" and zero volume) -- there is no
volume-sort param and paging through the junk to find real markets isn't
viable. GET /events?with_nested_markets=true does NOT have this problem
(confirmed: clean, real events with real volume) and is what this module
uses instead.

Polymarket gotcha: GET /markets DOES support order=volume24hr&ascending=
false directly (also live-verified), so no local paging/sorting is
needed there -- just ask for the top N by volume.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from arb.kalshi.client import KalshiClient
from arb.money import decimal_str_to_pbp, parse_dollars_to_cents
from arb.polymarket.gamma_client import GammaClient
from arb.polymarket.models import _extract_yes_no_token_ids

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset({
    "will", "the", "a", "an", "to", "of", "in", "on", "by", "be", "is", "are",
    "for", "before", "after", "and", "or", "than", "at", "from", "this",
    "that", "with", "as", "it", "its", "which", "who", "what", "when",
    "does", "do", "did", "have", "has", "reach", "hit",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return frozenset(t for t in tokens if len(t) > 1 and t not in _STOPWORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def build_idf(token_sets: list[frozenset[str]]) -> dict[str, float]:
    """Inverse-document-frequency weight for each token over this batch's
    own candidate pool (no external corpus needed). This is the fix for
    plain Jaccard's biggest failure mode here: with dozens of "Will
    <person> be the Democratic nominee?" markets in the same batch, every
    one of them shares "democratic"/"presidential"/"nominee"/"2028" --
    those get idf~0 and stop contributing, while a candidate's actual
    identity (a surname that appears in only one or two titles) gets a
    high weight and correctly dominates the score."""
    n = len(token_sets)
    df: dict[str, int] = {}
    for ts in token_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def weighted_similarity(a: frozenset[str], b: frozenset[str], idf: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    inter, union = a & b, a | b
    num = sum(idf.get(t, 1.0) for t in inter)
    den = sum(idf.get(t, 1.0) for t in union)
    return num / den if den else 0.0


_IDENTIFYING_IDF_PERCENTILE = 0.75  # see _idf_threshold()


def _idf_threshold(idf: dict[str, float], percentile: float = _IDENTIFYING_IDF_PERCENTILE) -> float:
    """The idf value marking the boundary, within THIS batch, between
    generic/template vocabulary shared by many markets ("win", "next",
    "election", "prime", "minister" -- common across dozens of same-
    shaped markets) and vocabulary rare enough in the batch to plausibly
    name one specific subject (a state, a country, a surname). Derived
    from the batch's own idf distribution (a percentile, not a fixed
    constant) so it adapts to batch size and composition automatically.

    Percentile over the *distinct* idf values, not the raw per-token
    list: document frequency 1 is by far the most common bucket (most
    words in a batch of short titles appear in only one title), so
    every one of those tokens shares the single highest idf value --
    weighting by token count would let that one bucket dominate the
    percentile and push the cutoff up near the max, filtering out
    genuinely-identifying words (e.g. a surname with df=2 or 3) that
    just aren't quite as rare as the df=1 majority."""
    values = sorted(set(idf.values()))
    if not values:
        return 0.0
    index = min(len(values) - 1, int(percentile * (len(values) - 1)))
    return values[index]


def _exclusive_identifying_tokens(tokens: frozenset[str], other: frozenset[str],
                                   idf: dict[str, float], threshold: float) -> frozenset[str]:
    """Tokens present on this side only (not the other side) that also
    clear the identifying-idf threshold -- i.e. words rare enough in the
    batch to plausibly assert a specific, non-shared subject rather than
    just being extra descriptive wording."""
    return frozenset(t for t in (tokens - other) if idf.get(t, 1.0) >= threshold)


@dataclass(frozen=True)
class KalshiCandidate:
    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    category: str | None
    yes_bid_pbp: int | None
    yes_ask_pbp: int | None
    mid_pbp: int | None
    volume_24h_cents: int
    volume_total_cents: int
    close_time: str | None
    rules_text: str = ""   # rules_primary + rules_secondary verbatim, for the arbitrage engine's rule extraction

    def url(self) -> str:
        return f"https://kalshi.com/markets/{self.series_ticker.lower()}"

    @property
    def no_mid_pbp(self) -> int | None:
        """Kalshi doesn't quote a separate NO market -- NO is just 1-YES
        -- so this is the exact complement of mid_pbp, not an estimate."""
        return None if self.mid_pbp is None else 10_000 - self.mid_pbp


@dataclass(frozen=True)
class PolymarketCandidate:
    condition_id: str
    question: str
    yes_label: str               # outcome label treated as "yes" (literal "Yes", or outcome[0] fallback)
    no_label: str | None         # complementary outcome label ("No", or outcome[1] fallback), if any
    yes_price_pbp: int | None
    no_price_pbp: int | None     # Polymarket's own priced NO outcome, not derived as 1-yes -- its YES
                                  # and NO tokens trade on separate order books and need not sum to $1
    volume_24h_cents: int
    volume_total_cents: int
    event_slug: str | None
    market_slug: str | None
    end_date: str | None
    resolution_text: str = ""      # description + resolutionSource verbatim, for the arbitrage engine
    yes_token_id: str | None = None  # CLOB token id, for real order-book fetches; None if polarity unconfirmed
    no_token_id: str | None = None

    def url(self) -> str:
        if self.event_slug and self.market_slug:
            return f"https://polymarket.com/event/{self.event_slug}/{self.market_slug}"
        return "https://polymarket.com"


@dataclass(frozen=True)
class CandidatePair:
    kalshi: KalshiCandidate
    polymarket: PolymarketCandidate
    similarity: float
    yes_gap_pbp: int | None      # kalshi YES mid - polymarket YES price
    no_gap_pbp: int | None       # kalshi NO mid  - polymarket NO price (independently priced, see above)
    rank_score: int              # max(|yes_gap|, |no_gap|) weighted by shared volume -- see generate_candidates


def _mid(bid: int | None, ask: int | None) -> int | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) // 2


def fetch_kalshi_candidates(client: KalshiClient, max_pages: int = 8, page_size: int = 200,
                             max_candidates: int = 1000) -> list[KalshiCandidate]:
    """/events has no volume-sort param, so this pulls a bounded sample
    (max_pages * page_size events), then locally drops zero-volume/
    zero-price entries and keeps the top `max_candidates` by 24h volume
    -- both to bound matching cost downstream and because a market with
    no trading isn't a meaningful "misaligned by volume" candidate."""
    out: list[KalshiCandidate] = []
    max_events = max_pages * page_size
    events_seen = 0
    for event in client.get_events(status="open", with_nested_markets=True, limit=page_size):
        # get_events() paginates via cursor internally with no cap of its
        # own; stop pulling once we've seen enough events for this call.
        if events_seen >= max_events:
            break
        events_seen += 1
        category = event.get("category")
        series_ticker = event.get("series_ticker", "")
        for m in event.get("markets", []):
            try:
                yes_bid = decimal_str_to_pbp(m["yes_bid_dollars"]) if m.get("yes_bid_dollars") else None
                yes_ask = decimal_str_to_pbp(m["yes_ask_dollars"]) if m.get("yes_ask_dollars") else None
            except ValueError:
                yes_bid = yes_ask = None
            mid = _mid(yes_bid, yes_ask)
            volume_24h = parse_dollars_to_cents(m.get("volume_24h_fp", "0") or "0")
            if mid is None or volume_24h <= 0:
                continue
            title = m.get("title") or event.get("title", "")
            # Multi-outcome events (e.g. "Who will be the nominee?") reuse
            # the SAME title on every per-candidate market -- the actual
            # subject ("Alexandria Ocasio-Cortez") only lives in
            # yes_sub_title (live-verified 2026-09-21). Without folding
            # this in, every candidate in such an event looks identical
            # to the matcher (and to a human reading the table).
            sub_title = m.get("yes_sub_title") or ""
            if sub_title and sub_title.lower() not in title.lower():
                title = f"{title} ({sub_title})"
            rules_text = "\n".join(filter(None, [m.get("rules_primary"), m.get("rules_secondary")]))
            out.append(KalshiCandidate(
                ticker=m.get("ticker", ""), event_ticker=event.get("event_ticker", ""),
                series_ticker=series_ticker, title=title,
                category=category, yes_bid_pbp=yes_bid, yes_ask_pbp=yes_ask, mid_pbp=mid,
                volume_24h_cents=volume_24h,
                volume_total_cents=parse_dollars_to_cents(m.get("volume_fp", "0") or "0"),
                close_time=m.get("close_time"), rules_text=rules_text,
            ))
    out.sort(key=lambda c: c.volume_24h_cents, reverse=True)
    return out[:max_candidates]


def _polymarket_yes_no(m: dict[str, Any]) -> tuple[str, str | None, int | None, int | None]:
    """Picks which outcome is "yes" and which is "no". Prefers explicit
    "Yes"/"No" labels (common for binary event markets); falls back to
    outcome[0]/outcome[1] (e.g. team-vs-team markets have no Yes/No
    labels at all) -- both labels are always returned alongside the
    prices so the UI can show exactly what's being compared instead of
    hiding the assumption. Returns (yes_label, no_label, yes_pbp, no_pbp)."""
    import json
    try:
        outcomes = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else m.get("outcomes")
        prices = json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices")
    except (KeyError, ValueError, TypeError):
        return "?", None, None, None
    if not outcomes or not prices or len(outcomes) != len(prices):
        return "?", None, None, None

    def to_pbp(price_str: Any) -> int | None:
        try:
            return decimal_str_to_pbp(price_str)
        except ValueError:
            return None

    yes_idx = next((i for i, label in enumerate(outcomes) if str(label).strip().lower() == "yes"), None)
    no_idx = next((i for i, label in enumerate(outcomes) if str(label).strip().lower() == "no"), None)
    if yes_idx is not None:
        yes_label, yes_pbp = outcomes[yes_idx], to_pbp(prices[yes_idx])
        no_label, no_pbp = (outcomes[no_idx], to_pbp(prices[no_idx])) if no_idx is not None else (None, None)
        return yes_label, no_label, yes_pbp, no_pbp

    yes_label, yes_pbp = outcomes[0], to_pbp(prices[0])
    no_label, no_pbp = (outcomes[1], to_pbp(prices[1])) if len(outcomes) > 1 else (None, None)
    return yes_label, no_label, yes_pbp, no_pbp


def fetch_polymarket_candidates(gamma: GammaClient, max_pages: int = 3, page_size: int = 100) -> list[PolymarketCandidate]:
    """Gamma's order=volume24hr does the sorting server-side, so unlike
    Kalshi this just takes the first max_pages*page_size results."""
    raw = gamma.list_markets_raw(active=True, closed=False, page_size=page_size, max_pages=max_pages,
                                  order="volume24hr", ascending=False)
    out: list[PolymarketCandidate] = []
    for m in raw:
        yes_label, no_label, yes_pbp, no_pbp = _polymarket_yes_no(m)
        volume_24h = parse_dollars_to_cents(m.get("volume24hr", 0) or 0)
        if yes_pbp is None or volume_24h <= 0:
            continue
        events = m.get("events") or []
        event_slug = events[0].get("slug") if events else None
        resolution_text = "\n".join(filter(None, [m.get("description"), m.get("resolutionSource")]))
        yes_token_id, no_token_id = _extract_yes_no_token_ids(m)
        out.append(PolymarketCandidate(
            condition_id=m.get("conditionId", ""), question=m.get("question", ""),
            yes_label=yes_label, no_label=no_label, yes_price_pbp=yes_pbp, no_price_pbp=no_pbp,
            volume_24h_cents=volume_24h,
            volume_total_cents=parse_dollars_to_cents(m.get("volume", 0) or 0),
            event_slug=event_slug, market_slug=m.get("slug"), end_date=m.get("endDate"),
            resolution_text=resolution_text, yes_token_id=yes_token_id, no_token_id=no_token_id,
        ))
    return out


def generate_candidates(kalshi_list: list[KalshiCandidate], poly_list: list[PolymarketCandidate], *,
                         min_similarity: float = 0.5, max_pairs: int = 500) -> list[CandidatePair]:
    """For each Kalshi candidate, keeps its single best-matching
    Polymarket candidate if the IDF-weighted similarity (see
    weighted_similarity) clears `min_similarity`. Ranked by `rank_score`
    so "most misaligned, by volume" naturally sorts to the top; the
    caller/UI can re-sort by any column instead.

    `min_similarity` is tuned against weighted_similarity's scale, not
    plain Jaccard's. The default was raised from an earlier 0.35 to 0.5
    to bias this view toward pairs that are plausibly the *same market*
    (the goal of this page) rather than merely topically related --
    same-template-different-subject false positives (e.g. two different
    people's otherwise-identically-worded "will X be the nominee"
    markets) mostly stop clearing 0.5. This is still a heuristic text
    matcher, not verification, and raising the bar trades away some true
    matches phrased very differently across platforms; every match's
    similarity score and both full titles are surfaced so a human (that's
    you) makes the final call, and the threshold is adjustable in the UI
    per Phase 1 in the project brief ("candidate generation" is not
    "approval").

    Clearing `min_similarity` is necessary but not sufficient: bag-of-
    words overlap alone can't tell "Senate race in Iowa" from "Senate
    race in Kansas" apart, since every other word matches. So each
    candidate Kalshi/Polymarket pair also has to clear an identity gate
    (see _exclusive_identifying_tokens): if BOTH sides independently
    contribute their own batch-rare, non-shared vocabulary (a state, a
    person, a country -- as opposed to one side merely having extra
    descriptive wording the other lacks), that means each side is
    naming a different specific subject, and the pair is rejected
    outright regardless of its raw similarity score."""
    k_valid = [k for k in kalshi_list if k.mid_pbp is not None]
    p_valid = [p for p in poly_list if p.yes_price_pbp is not None]
    k_tokenized = [(k, tokenize(k.title)) for k in k_valid]
    p_tokenized = [(p, tokenize(p.question)) for p in p_valid]

    idf = build_idf([ts for _, ts in k_tokenized] + [ts for _, ts in p_tokenized])
    identifying_threshold = _idf_threshold(idf)

    pairs: list[CandidatePair] = []
    for k, k_tokens in k_tokenized:
        if not k_tokens:
            continue
        scored = sorted(
            ((weighted_similarity(k_tokens, p_tokens, idf), p, p_tokens) for p, p_tokens in p_tokenized),
            key=lambda row: row[0], reverse=True,
        )
        best_poly, best_sim = None, 0.0
        for sim, p, p_tokens in scored:
            if sim < min_similarity:
                break  # sorted descending -- no later candidate can pass either
            k_only = _exclusive_identifying_tokens(k_tokens, p_tokens, idf, identifying_threshold)
            p_only = _exclusive_identifying_tokens(p_tokens, k_tokens, idf, identifying_threshold)
            if k_only and p_only:
                continue  # same template, different subject -- not a match
            best_poly, best_sim = p, sim
            break
        if best_poly is None:
            continue

        yes_gap = k.mid_pbp - best_poly.yes_price_pbp
        no_gap = None
        if k.no_mid_pbp is not None and best_poly.no_price_pbp is not None:
            no_gap = k.no_mid_pbp - best_poly.no_price_pbp

        gap_magnitudes = [abs(g) for g in (yes_gap, no_gap) if g is not None]
        shared_volume_cents = min(k.volume_24h_cents, best_poly.volume_24h_cents)
        rank_score = max(gap_magnitudes) * shared_volume_cents
        pairs.append(CandidatePair(
            kalshi=k, polymarket=best_poly, similarity=round(best_sim, 3),
            yes_gap_pbp=yes_gap, no_gap_pbp=no_gap, rank_score=rank_score,
        ))

    pairs.sort(key=lambda cp: cp.rank_score, reverse=True)
    return pairs[:max_pairs]
