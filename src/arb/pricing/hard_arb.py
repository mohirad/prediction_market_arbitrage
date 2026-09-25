"""Hard-arb evaluation: buy YES on the cheap platform, NO on the expensive
one, net of fees, at a chosen size. Positive net edge = locked profit
(one of the two legs always pays out $1/contract at settlement, so the
payoff is size*100 cents regardless of outcome -- see PHASE 2 in the
project brief for the derivation).

Kalshi is always the polarity reference frame: `kalshi_yes_maps_to` on a
MatchedPair says whether Kalshi's YES equals Polymarket's YES ("yes") or
Polymarket's NO ("no", inverted). Both directions are evaluated; the
caller picks whichever has the better net edge (or shows both).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from arb.fees import kalshi_taker_fee_cents, polymarket_taker_fee_cents
from arb.money import Cents, PriceBp
from arb.orderbook import Book, walk_for_size


@dataclass(frozen=True)
class LegInstruction:
    platform: str      # "kalshi" | "polymarket"
    side: str           # "yes" | "no" -- the side to actually buy on that platform's own UI
    market_id: str        # kalshi ticker, or polymarket token_id being bought
    contracts: int          # requested trade size
    filled_contracts: Fraction  # what the book could actually fill (<= contracts; may be fractional)
    avg_price: PriceBp | None   # size-weighted avg execution price walked from the book
    cost_cents: Cents
    fee_cents: Cents
    fully_fillable: bool


@dataclass(frozen=True)
class ArbEvaluation:
    direction: str       # "buy_kalshi_yes" | "buy_polymarket_yes"
    size: int
    cheap_leg: LegInstruction
    expensive_leg: LegInstruction
    gross_proceeds_cents: Cents   # size * 100c -- what one winning leg pays out
    total_cost_cents: Cents
    total_fee_cents: Cents
    net_edge_cents: Cents          # positive = locked profit for the whole size
    net_edge_bps: int              # net_edge / gross_proceeds, in basis points
    fully_fillable: bool


def _leg(platform: str, side: str, market_id: str, book_asks, size: int, fee_fn) -> LegInstruction:
    result = walk_for_size(book_asks, size, side="buy")
    fee = fee_fn(result.filled_size, result.avg_price) if result.avg_price is not None else Cents(0)
    return LegInstruction(
        platform=platform, side=side, market_id=market_id, contracts=size,
        filled_contracts=Fraction(result.filled_size), avg_price=result.avg_price,
        cost_cents=result.cost_cents, fee_cents=fee, fully_fillable=result.fully_filled,
    )


def evaluate_hard_arb(
    *, kalshi_ticker: str, kalshi_yes_book: Book, kalshi_no_book: Book,
    poly_yes_token: str, poly_no_token: str, poly_yes_book: Book, poly_no_book: Book,
    size: int, kalshi_yes_maps_to: str, kalshi_series_ticker: str | None = None,
    poly_category: str | None = None, poly_taker_fee_bps: int | None = None,
) -> list[ArbEvaluation]:
    """Returns both directions' evaluations, cheapest-cost-first isn't
    guaranteed -- sort by net_edge_cents yourself if you only want the best."""
    if kalshi_yes_maps_to not in ("yes", "no"):
        raise ValueError(f"kalshi_yes_maps_to must be 'yes' or 'no', got {kalshi_yes_maps_to!r}")
    aligned = kalshi_yes_maps_to == "yes"

    def kalshi_fee(contracts, price):
        return kalshi_taker_fee_cents(contracts, price, series_ticker=kalshi_series_ticker)

    def poly_fee(contracts, price):
        return polymarket_taker_fee_cents(contracts, price, taker_base_fee_bps=poly_taker_fee_bps,
                                           category=poly_category)

    # common-NO on Polymarket = poly.no if aligned, else poly.yes
    poly_common_no_book, poly_common_no_side, poly_common_no_token = (
        (poly_no_book, "no", poly_no_token) if aligned else (poly_yes_book, "yes", poly_yes_token)
    )
    # common-YES on Polymarket = poly.yes if aligned, else poly.no
    poly_common_yes_book, poly_common_yes_side, poly_common_yes_token = (
        (poly_yes_book, "yes", poly_yes_token) if aligned else (poly_no_book, "no", poly_no_token)
    )

    evaluations = []

    # Direction A: buy common-YES cheap on Kalshi, common-NO on Polymarket
    cheap = _leg("kalshi", "yes", kalshi_ticker, kalshi_yes_book.asks, size, kalshi_fee)
    expensive = _leg("polymarket", poly_common_no_side, poly_common_no_token,
                      poly_common_no_book.asks, size, poly_fee)
    evaluations.append(_build_evaluation("buy_kalshi_yes", size, cheap, expensive))

    # Direction B: buy common-YES cheap on Polymarket, common-NO on Kalshi
    cheap = _leg("polymarket", poly_common_yes_side, poly_common_yes_token,
                  poly_common_yes_book.asks, size, poly_fee)
    expensive = _leg("kalshi", "no", kalshi_ticker, kalshi_no_book.asks, size, kalshi_fee)
    evaluations.append(_build_evaluation("buy_polymarket_yes", size, cheap, expensive))

    return evaluations


def _build_evaluation(direction: str, size: int, cheap: LegInstruction, expensive: LegInstruction) -> ArbEvaluation:
    gross = Cents(size * 100)
    total_cost = Cents(cheap.cost_cents + expensive.cost_cents)
    total_fee = Cents(cheap.fee_cents + expensive.fee_cents)
    net_edge = Cents(gross - total_cost - total_fee)
    net_edge_bps = int(Fraction(net_edge, gross) * 10_000) if gross else 0
    return ArbEvaluation(
        direction=direction, size=size, cheap_leg=cheap, expensive_leg=expensive,
        gross_proceeds_cents=gross, total_cost_cents=total_cost, total_fee_cents=total_fee,
        net_edge_cents=net_edge, net_edge_bps=net_edge_bps,
        fully_fillable=cheap.fully_fillable and expensive.fully_fillable,
    )
