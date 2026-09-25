"""Executable-price simulation and max-profitable-size search.

Project brief section 6: never price an opportunity off the headline
bid/ask -- walk the real order book for every size considered. This
module is deterministic (no LLM) and platform-agnostic: it walks
`arb.orderbook.Book` the same way `arb.pricing.hard_arb` does, but
returns a quote for an arbitrary leg/size instead of assuming a specific
2-leg "one side always pays $1" structure, so pipeline.py can build
N-leg positions against N-state payoff matrices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from arb.money import PBP_PER_CENT, Cents, PriceBp
from arb.orderbook import Book, walk_for_size

# $100 / $500 / $1,000 / $2,500 / $5,000 / $10,000, per the brief's section 6.
STANDARD_SIZE_CENTS: tuple[int, ...] = (100_00, 500_00, 1_000_00, 2_500_00, 5_000_00, 10_000_00)

FeeFn = Callable[[int, PriceBp], Cents]


@dataclass(frozen=True)
class ExecutionQuote:
    requested_shares: int
    filled_shares: int
    avg_price_pbp: PriceBp | None
    cost_cents: Cents          # notional only, excludes fee
    fee_cents: Cents
    total_cost_cents: Cents      # cost + fee -- what the position actually costs
    fully_fillable: bool


def quote_shares(book: Book, shares: int, fee_fn: FeeFn) -> ExecutionQuote:
    """Walks the ask side for `shares` (this engine only ever opens
    positions by buying -- both platforms' UIs work the same way: you
    buy YES or buy NO, you don't sell short)."""
    if shares <= 0:
        raise ValueError("shares must be positive")
    result = walk_for_size(book.asks, shares, side="buy")
    fee = fee_fn(int(result.filled_size), result.avg_price) if result.avg_price is not None else Cents(0)
    return ExecutionQuote(
        requested_shares=shares, filled_shares=int(result.filled_size), avg_price_pbp=result.avg_price,
        cost_cents=result.cost_cents, fee_cents=fee, total_cost_cents=Cents(result.cost_cents + fee),
        fully_fillable=result.fully_filled,
    )


def total_ask_depth_shares(book: Book) -> int:
    """Total resting shares across every level of the ask side -- the real
    liquidity ceiling for a leg (project brief: "respect available
    liquidity"), not just an estimate from the best price."""
    return int(sum(level.size for level in book.asks))


def shares_for_dollar_size(book: Book, dollar_cents: int) -> int:
    """Approximate share count to target `dollar_cents` of notional,
    using the book's current best ask -- good enough to pick a size to
    then walk exactly with quote_shares(); not a precise inverse solve
    against a sloped book."""
    best_ask = book.best_ask
    if not best_ask:
        return 0
    return max(1, (dollar_cents * PBP_PER_CENT) // best_ask)


def quote_standard_sizes(book: Book, fee_fn: FeeFn, sizes_cents: tuple[int, ...] = STANDARD_SIZE_CENTS,
                          ) -> dict[int, ExecutionQuote]:
    """One quote per standard dollar size in `sizes_cents`, skipping sizes
    the book can't even approximate (empty book)."""
    quotes: dict[int, ExecutionQuote] = {}
    for size_cents in sizes_cents:
        shares = shares_for_dollar_size(book, size_cents)
        if shares <= 0:
            continue
        quotes[size_cents] = quote_shares(book, shares, fee_fn)
    return quotes


def max_profitable_shares(evaluate_fn: Callable[[int], float], *, lo: int = 1, hi: int) -> int:
    """Binary search for the largest whole-share size at which
    `evaluate_fn(shares)` (expected: worst-case profit at that size,
    monotonically non-increasing as size grows and slippage eats the
    edge) is still > 0. Returns 0 if even `lo` isn't profitable.

    Not assumed to be perfectly monotonic (real books have lumpy depth),
    so this does a plain bisection on the sign of evaluate_fn and accepts
    that a small non-monotonic pocket right at the boundary might be
    missed -- deliberately conservative (favors reporting a smaller max
    size) rather than an exhaustive scan of every share count."""
    if hi < lo:
        return 0
    if evaluate_fn(lo) <= 0:
        return 0
    if evaluate_fn(hi) > 0:
        return hi
    low, high = lo, hi
    while high - low > 1:
        mid = (low + high) // 2
        if evaluate_fn(mid) > 0:
            low = mid
        else:
            high = mid
    return low
