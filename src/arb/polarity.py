"""Outcome polarity: matched markets on Kalshi and Polymarket don't always
agree on which side is "YES". A pair like "Will the Fed cut rates in
March?" (Kalshi YES = cut) vs "Fed holds rates in March" (Polymarket YES =
hold) are the same underlying event with inverted polarity. Getting this
wrong silently produces a fake arb (buying YES+YES on what's actually the
same side, or missing a real one) -- so it's a first-class, tested concept,
not a detail buried in the matching pipeline.
"""
from __future__ import annotations

from enum import Enum

from arb.money import PBP_PER_DOLLAR, PriceBp


class Polarity(Enum):
    ALIGNED = "aligned"    # other platform's YES == this platform's YES
    INVERTED = "inverted"  # other platform's YES == this platform's NO


def to_common_yes_price(price: PriceBp, polarity: Polarity) -> PriceBp:
    """Reproject a price quoted on the "other" side of a matched pair onto
    a common YES frame of reference (this pair's platform-A YES outcome)."""
    if polarity is Polarity.ALIGNED:
        return price
    return PriceBp(PBP_PER_DOLLAR - int(price))


def flip(polarity: Polarity) -> Polarity:
    return Polarity.INVERTED if polarity is Polarity.ALIGNED else Polarity.ALIGNED
