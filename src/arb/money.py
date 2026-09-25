"""Integer-only money and price math.

No floats anywhere in this module, or in any code that does money math
downstream of it. All monetary amounts are integer cents (USD). All prices
are integer "probability basis points" (pbp): a contract that pays out $1
on YES is priced 0-10000 pbp, where 10000 pbp == $1.00 == 100 cents.

pbp was chosen (instead of cents) as the canonical price unit because it
divides evenly into both platforms' native tick sizes:
  - Kalshi: mostly whole cents, but its fixed-point/tapered-pricing model
    allows ticks as fine as $0.0001 near the 0/1 edges -> 1 pbp = $0.0001,
    the finest documented Kalshi tick, so nothing is lost.
  - Polymarket CLOB ticks are 0.01 or 0.001                -> 10-100 pbp
so conversions in both directions are always exact integer operations,
never a rounding decision hidden inside a float. Both platforms hand us
prices as JSON strings (dollar-strings for Kalshi, decimal strings for
Polymarket) -- see decimal_str_to_pbp, which every REST client uses
instead of ever calling float() on a price.
"""
from __future__ import annotations

from fractions import Fraction
from math import ceil
from typing import NewType

Cents = NewType("Cents", int)
PriceBp = NewType("PriceBp", int)  # 0..10000, probability basis points

PBP_PER_DOLLAR = 10_000
PBP_PER_CENT = 100


def cents_to_pbp(cents: int) -> PriceBp:
    if not (0 <= cents <= 100):
        raise ValueError(f"price cents out of range 0-100: {cents}")
    return PriceBp(cents * PBP_PER_CENT)


def pbp_to_cents_exact(pbp: int) -> Cents:
    """Convert pbp back to whole cents. Raises if pbp isn't cent-aligned."""
    if pbp % PBP_PER_CENT != 0:
        raise ValueError(f"pbp {pbp} is not an exact multiple of a cent")
    return Cents(pbp // PBP_PER_CENT)


def decimal_str_to_pbp(price: str) -> PriceBp:
    """Parse a Polymarket-style decimal price string ("0.635") into pbp.

    Takes a string (never a float) to avoid binary-float rounding on the
    way in from JSON. Raises if the API ever hands us more precision than
    pbp (4 decimal places) can represent exactly.
    """
    frac = Fraction(price)
    scaled = frac * PBP_PER_DOLLAR
    if scaled.denominator != 1:
        raise ValueError(f"price {price!r} has sub-pbp precision, cannot represent exactly")
    pbp = int(scaled)
    if not (0 <= pbp <= PBP_PER_DOLLAR):
        raise ValueError(f"price {price!r} out of range 0-1")
    return PriceBp(pbp)


def json_number_to_str(value: object) -> str:
    """Some Gamma API fields are inconsistently typed across otherwise
    identical-looking endpoints -- e.g. one market's "volume" comes back
    as a JSON string and another's "volume24hr" as a JSON float in the
    very same response (live-verified 2026-09-20). This normalizes
    either into a decimal string safe to hand to Fraction()/
    decimal_str_to_pbp(), using repr() for numeric types since Python's
    float repr is the shortest string that round-trips to the same
    float, which recovers the original JSON digits exactly for the
    short decimals these APIs actually send."""
    if isinstance(value, str):
        return value
    return repr(value)


def parse_dollars_to_cents(value: object) -> int:
    """Parses a dollar amount (volume, liquidity, etc; string or numeric,
    see json_number_to_str) into whole cents, floored. Not used for
    trade-execution money (that stays exact via kalshi_fee_cents/
    notional_cents_ceil) -- this is for display/ranking stats where a
    sub-cent rounding doesn't matter."""
    frac = Fraction(json_number_to_str(value)) * 100
    return frac.numerator // frac.denominator


def notional_cents(contracts: int, price: PriceBp) -> Cents:
    """Dollar notional (in cents) of `contracts` at `price`, rounded down.

    Rounding direction matters for cost estimates: rounding a *cost* down
    would understate what you pay, so callers computing cost should use
    notional_cents_ceil instead. This floor variant is for valuation
    (e.g. current market value of a held position), where understating is
    the conservative side.
    """
    return Cents((contracts * price) // PBP_PER_CENT)


def notional_cents_ceil(contracts: int, price: PriceBp) -> Cents:
    total = contracts * price
    return Cents(-(-total // PBP_PER_CENT))  # ceil division, integers only


def kalshi_fee_cents(contracts: int, price: PriceBp, fee_multiplier: Fraction) -> Cents:
    """Kalshi per-fill fee, in cents, rounded up.

    fee = ceil(fee_multiplier * contracts * P * (1 - P)), fee_multiplier and
    P are exact Fractions so the only rounding in the whole computation is
    the final ceil (which is how Kalshi itself rounds). P is derived from
    pbp so it stays exact instead of going through a float division.
    """
    if not (0 <= price <= PBP_PER_DOLLAR):
        raise ValueError(f"price out of range: {price}")
    p = Fraction(price, PBP_PER_DOLLAR)
    fee_dollars = fee_multiplier * contracts * p * (1 - p)
    fee_cents_frac = fee_dollars * 100
    return Cents(ceil(fee_cents_frac))


def spread_pbp(price_a: PriceBp, price_b: PriceBp) -> int:
    """Signed gap between two prices for the same outcome, in pbp (= basis points)."""
    return int(price_a) - int(price_b)
