"""Parse Kalshi's orderbook_fp response into normalized YES/NO Books.

Kalshi's orderbook has no separate "ask" arrays (verified 2026-09-20, see
docs/api_notes.md): `yes_dollars` lists resting YES-buy orders (YES bids),
and `no_dollars` lists resting NO-buy orders (NO bids). A NO bid at price
p is economically the same offer as a YES ask at (1-p) for the same size
(selling YES == buying NO's complement), so:
  - YES bids  = yes_dollars, as-is
  - YES asks  = no_dollars, price-complemented to (1 - p)
  - NO bids   = no_dollars, as-is
  - NO asks   = yes_dollars, price-complemented to (1 - p)
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from arb.money import PBP_PER_DOLLAR, PriceBp, decimal_str_to_pbp
from arb.orderbook import Book, Level


def _parse_levels(raw_levels: list[list[str]]) -> list[Level]:
    # qty is a fixed-point string (Kalshi's "fp" quantities, e.g. "8.50")
    # and CAN be fractional -- parse via Fraction, never float() or int().
    return [Level(decimal_str_to_pbp(price_str), Fraction(qty_str))
            for price_str, qty_str in (raw_levels or [])]


def _complement(levels: list[Level]) -> list[Level]:
    return [Level(PriceBp(PBP_PER_DOLLAR - int(lvl.price)), lvl.size) for lvl in levels]


def parse_kalshi_orderbook(raw: dict[str, Any]) -> tuple[Book, Book]:
    """Returns (yes_book, no_book).

    Live-verified 2026-09-20: GET /markets/{ticker}/orderbook wraps the
    book under the key "orderbook_fp" (not "orderbook" as some secondary
    sources imply). We also accept "orderbook" and an unwrapped dict for
    robustness, but orderbook_fp is what production actually returns.
    """
    ob = raw.get("orderbook_fp") or raw.get("orderbook") or raw
    yes_bids = sorted(_parse_levels(ob.get("yes_dollars") or ob.get("yes", [])), key=lambda l: -l.price)
    no_bids = sorted(_parse_levels(ob.get("no_dollars") or ob.get("no", [])), key=lambda l: -l.price)

    yes_asks = sorted(_complement(no_bids), key=lambda l: l.price)
    no_asks = sorted(_complement(yes_bids), key=lambda l: l.price)

    yes_book = Book(bids=tuple(yes_bids), asks=tuple(yes_asks))
    no_book = Book(bids=tuple(no_bids), asks=tuple(no_asks))
    return yes_book, no_book
