"""Loads the versioned matched-pair YAML store (Phase 1 minimal slice).

Candidate generation (embeddings + fuzzy matching) and the strict
verification pass aren't built yet -- pairs currently go in by hand after
manual verification (see data/matches.yaml's `notes` field on each pair
for how it was verified). This loader is the shape that an automated
pipeline would eventually write into, so nothing downstream needs to
change when that's built.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KalshiLegRef:
    ticker: str
    series_ticker: str
    event_ticker: str | None = None


@dataclass(frozen=True)
class PolymarketLegRef:
    yes_token_id: str
    no_token_id: str
    event_slug: str | None = None
    market_slug: str | None = None


@dataclass(frozen=True)
class MatchedPair:
    pair_id: str
    category: str | None
    kalshi: KalshiLegRef
    polymarket: PolymarketLegRef
    kalshi_yes_maps_to: str  # "yes" (aligned) or "no" (inverted)
    confidence: float
    human_approved: bool
    notes: str | None

    def kalshi_url(self) -> str:
        # Best-effort: Kalshi's marketing site was rate-limited (HTTP 429)
        # for every attempt to verify its exact frontend URL scheme during
        # development (see docs/api_notes.md). This links to the series
        # listing page, which does reliably exist -- the specific strike
        # may need one more click to find on it.
        return f"https://kalshi.com/markets/{self.kalshi.series_ticker.lower()}"

    def polymarket_url(self) -> str:
        if self.polymarket.event_slug and self.polymarket.market_slug:
            return f"https://polymarket.com/event/{self.polymarket.event_slug}/{self.polymarket.market_slug}"
        return "https://polymarket.com"


def load_matches(path: str = "data/matches.yaml", *, approved_only: bool = True) -> list[MatchedPair]:
    import yaml
    data = yaml.safe_load(Path(path).read_text()) or {}
    pairs = []
    for p in data.get("pairs", []):
        if approved_only and not p.get("human_approved", False):
            continue
        k = p["kalshi"]
        pm = p["polymarket"]
        pairs.append(MatchedPair(
            pair_id=p["pair_id"],
            category=p.get("category"),
            kalshi=KalshiLegRef(ticker=k["ticker"], series_ticker=k["series_ticker"],
                                 event_ticker=k.get("event_ticker")),
            polymarket=PolymarketLegRef(yes_token_id=pm["yes_token_id"], no_token_id=pm["no_token_id"],
                                         event_slug=pm.get("event_slug"), market_slug=pm.get("market_slug")),
            kalshi_yes_maps_to=p["kalshi_yes_maps_to"],
            confidence=float(p["confidence"]),
            human_approved=bool(p.get("human_approved", False)),
            notes=p.get("notes"),
        ))
    return pairs
