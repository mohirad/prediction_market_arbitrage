"""Fee models for both platforms.

Kalshi: fee = ceil(fee_multiplier * C * P * (1-P)) dollars/contract.
Multiplier and index-series exception are UNVERIFIED against a primary
source (kalshi.com/fee-schedule returned 429 on every fetch attempt during
research -- see docs/api_notes.md action item #1). Do not trade real money
on these numbers until that's confirmed directly.

Polymarket: same C*rate*P*(1-P) shape, maker fee = 0 (documented), taker
rate varies by category. Prefer each market's own `takerBaseFee` bps field
when the API provides one -- the category table here is a fallback/
cross-check only, also not confirmed from a primary numeric source beyond
the docs.polymarket.com/trading/fees page summary.
"""
from __future__ import annotations

from fractions import Fraction

from arb.money import Cents, PriceBp, kalshi_fee_cents

KALSHI_DEFAULT_FEE_MULTIPLIER = Fraction(7, 100)  # UNVERIFIED, see module docstring
KALSHI_INDEX_SERIES_FEE_MULTIPLIER = Fraction(35, 1000)  # UNVERIFIED; S&P/Nasdaq-style series
KALSHI_INDEX_SERIES_TICKERS = frozenset({"INX", "NASDAQ100"})  # UNVERIFIED exact ticker set


def kalshi_taker_fee_cents(contracts: int, price: PriceBp, series_ticker: str | None = None) -> Cents:
    multiplier = (
        KALSHI_INDEX_SERIES_FEE_MULTIPLIER
        if series_ticker in KALSHI_INDEX_SERIES_TICKERS
        else KALSHI_DEFAULT_FEE_MULTIPLIER
    )
    return kalshi_fee_cents(contracts, price, multiplier)


def kalshi_maker_fee_cents(contracts: int, price: PriceBp, series_ticker: str | None = None) -> Cents:
    return Cents(0)  # UNVERIFIED but consistently reported as 0 in secondary sources


# Polymarket category -> taker fee rate (UNVERIFIED beyond docs summary; see module docstring)
POLYMARKET_CATEGORY_TAKER_RATE: dict[str, Fraction] = {
    "crypto": Fraction(7, 100),
    "sports": Fraction(5, 100),
    "finance": Fraction(4, 100),
    "politics": Fraction(4, 100),
    "mentions": Fraction(4, 100),
    "tech": Fraction(4, 100),
    "economics": Fraction(5, 100),
    "culture": Fraction(5, 100),
    "weather": Fraction(5, 100),
    "geopolitics": Fraction(0, 100),
    "world-events": Fraction(0, 100),
}
POLYMARKET_DEFAULT_TAKER_RATE = Fraction(5, 100)


def polymarket_taker_fee_cents(shares: int, price: PriceBp, *, taker_base_fee_bps: int | None = None,
                                category: str | None = None) -> Cents:
    """Prefer `taker_base_fee_bps` (from the market's own Gamma/CLOB
    record) over the category table when available."""
    if taker_base_fee_bps is not None:
        multiplier = Fraction(taker_base_fee_bps, 10_000)
    else:
        multiplier = POLYMARKET_CATEGORY_TAKER_RATE.get(
            (category or "").lower(), POLYMARKET_DEFAULT_TAKER_RATE
        )
    return kalshi_fee_cents(shares, price, multiplier)  # identical C*rate*P*(1-P) shape


def polymarket_maker_fee_cents(shares: int, price: PriceBp) -> Cents:
    return Cents(0)  # documented: "Makers are never charged fees"
