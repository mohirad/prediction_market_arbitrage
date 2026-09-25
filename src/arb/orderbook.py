"""Platform-agnostic order book: normalize, walk for executable price,
measure depth bands. Both Kalshi and Polymarket clients parse their raw
responses into a Book, then everything downstream (snapshotting, hard-arb
sizing, soft-divergence pricing) works off this one representation.

All prices are PriceBp (0-10000). Resting sizes are exact contracts/shares
as Fraction, not int: live books on both platforms show fractional resting
size (e.g. Kalshi "8.50", Polymarket "10465279.8"), and flooring that to
an integer would understate real liquidity. Fraction keeps it exact
without ever going through float. Trade sizes we choose ourselves (Phase
2/4 sizing) stay whole-contract ints; Fraction transparently interops
with int in all the arithmetic below.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import NamedTuple, Union

from arb.money import PBP_PER_CENT, PBP_PER_DOLLAR, Cents, PriceBp

Size = Union[int, Fraction]


class Level(NamedTuple):
    price: PriceBp
    size: Size  # resting contracts/shares at this price; may be fractional


@dataclass(frozen=True)
class Book:
    """bids sorted highest price first, asks sorted lowest price first."""
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]

    def __post_init__(self) -> None:
        for i in range(1, len(self.bids)):
            if self.bids[i].price > self.bids[i - 1].price:
                raise ValueError("bids must be sorted highest-first")
        for i in range(1, len(self.asks)):
            if self.asks[i].price < self.asks[i - 1].price:
                raise ValueError("asks must be sorted lowest-first")

    @property
    def best_bid(self) -> PriceBp | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> PriceBp | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> PriceBp | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return PriceBp((self.best_bid + self.best_ask) // 2)


class WalkResult(NamedTuple):
    filled_size: Size           # contracts actually filled (<= requested; may be fractional)
    avg_price: PriceBp | None   # size-weighted average execution price, or
                                 # None if filled_size == 0 (book had nothing)
    cost_cents: Cents           # total cost/proceeds in cents, rounded to
                                 # the side that favors the book (see below)
    fully_filled: bool          # False if book didn't have enough depth


def walk_for_size(levels: tuple[Level, ...], size: int, *, side: str) -> WalkResult:
    """Walk `levels` (asks to BUY, bids to SELL) consuming up to `size`
    whole contracts, level by level, and return the size-weighted average
    price. `size` (the trade we're choosing to make) is a whole-contract
    int; resting `level.size` may be fractional, so the fill itself can
    come up short of an exact-int amount only if the book runs out first.

    side="buy": cost is rounded UP (you never pay less than the book asks).
    side="sell": proceeds are rounded DOWN (you never receive more than
    the book bids). Both roundings are the conservative side for the
    trader, matching how a real exchange settles fractional-cent notional.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if size <= 0:
        raise ValueError("size must be positive")

    remaining: Fraction = Fraction(size)
    total_price_x_size: Fraction = Fraction(0)  # pbp * contracts, exact
    filled: Fraction = Fraction(0)
    for level in levels:
        take = min(remaining, level.size)
        if take <= 0:
            continue
        total_price_x_size += level.price * take
        filled += take
        remaining -= take
        if remaining == 0:
            break

    if filled == 0:
        return WalkResult(0, None, Cents(0), False)

    avg_price = PriceBp(round(total_price_x_size / filled))  # informational; cost uses exact math

    cost_frac_cents = total_price_x_size / PBP_PER_CENT  # always Fraction: never float division
    if side == "buy":
        cost_cents = Cents(-(-cost_frac_cents.numerator // cost_frac_cents.denominator))  # ceil
    else:
        cost_cents = Cents(cost_frac_cents.numerator // cost_frac_cents.denominator)  # floor

    return WalkResult(filled, avg_price, cost_cents, filled == size)


def depth_within_band_cents(levels: tuple[Level, ...], best_price: PriceBp, band_pbp: int) -> Cents:
    """Total notional (cents) resting at prices within `band_pbp` of the
    best price on this side of the book. Used for the 1c/5c/10c depth
    columns in the snapshots table."""
    total: Fraction = Fraction(0)
    for level in levels:
        if abs(int(level.price) - int(best_price)) > band_pbp:
            break  # levels are sorted moving away from best, so we can stop
        total += level.price * level.size
    return Cents(total // PBP_PER_CENT)


def band_1c(levels: tuple[Level, ...], best_price: PriceBp) -> Cents:
    return depth_within_band_cents(levels, best_price, PBP_PER_CENT)


def band_5c(levels: tuple[Level, ...], best_price: PriceBp) -> Cents:
    return depth_within_band_cents(levels, best_price, 5 * PBP_PER_CENT)


def band_10c(levels: tuple[Level, ...], best_price: PriceBp) -> Cents:
    return depth_within_band_cents(levels, best_price, 10 * PBP_PER_CENT)
