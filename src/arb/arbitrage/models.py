"""Core data model for the arbitrage engine: world states, positions,
payoff matrices, and opportunity classifications. Pure data plus a few
derived properties -- no I/O, no LLM calls, nothing here decides
anything (see payoff.py for the actual classification logic).

All money is Fraction cents, never float, consistent with arb.money's
integer-only-math rule -- a payoff matrix is exactly the kind of place a
float rounding error could silently turn a real arbitrage into a fake
one or vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from arb.money import Cents


class Resolution(str, Enum):
    """How a single contract resolves under one particular world state."""
    YES = "yes"
    NO = "no"
    VOID = "void"  # contract is cancelled/voided under this state -- price is refunded, fee is not


@dataclass(frozen=True)
class ContractRef:
    """One tradeable YES/NO contract on one platform."""
    platform: str      # "kalshi" | "polymarket"
    market_id: str      # kalshi ticker, or polymarket condition_id
    label: str            # human-readable market title/question, for display

    def __hash__(self) -> int:
        return hash((self.platform, self.market_id))

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, ContractRef)
                and self.platform == other.platform and self.market_id == other.market_id)


@dataclass(frozen=True)
class WorldState:
    """One mutually-exclusive, collectively-exhaustive real-world
    resolution scenario, together with how every contract in the
    combination resolves under it. `resolutions` must have exactly one
    entry per ContractRef referenced by the positions being evaluated --
    build_payoff_matrix() raises if any are missing, deliberately: a
    state that doesn't say how a contract resolves cannot be scored."""
    name: str
    description: str
    resolutions: dict[ContractRef, Resolution]
    source: str = "llm"  # "llm" | "hole_detector" | "manual"


@dataclass(frozen=True)
class Position:
    """A proposed buy of `shares` of one contract's `side`, at the
    execution price actually walked from that side's live order book
    (see execution.py) -- never the displayed headline price."""
    contract: ContractRef
    side: str                      # "yes" | "no"
    shares: int
    avg_price_cents: Fraction        # size-weighted average execution price, dollars/share * 100
    fee_cents: Cents
    max_shares: int                    # liquidity ceiling at this price (book depth), for the optimizer

    def __post_init__(self) -> None:
        if self.side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes' or 'no', got {self.side!r}")
        if self.shares <= 0:
            raise ValueError("shares must be positive")

    @property
    def cost_cents(self) -> Fraction:
        return self.avg_price_cents * self.shares + self.fee_cents


@dataclass(frozen=True)
class PayoffRow:
    state: WorldState
    payout_cents: Fraction   # total payout of the whole position combo under this state
    profit_cents: Fraction     # payout - total_cost


class Classification(str, Enum):
    TRUE_ARBITRAGE = "true_arbitrage"
    CAPITAL_PRESERVING_HEDGE = "capital_preserving_hedge"
    CONDITIONAL_ARBITRAGE = "conditional_arbitrage"
    PRICE_DISCREPANCY = "price_discrepancy"
    REJECTED_MATCH = "rejected_match"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"


@dataclass(frozen=True)
class PayoffMatrix:
    positions: tuple[Position, ...]
    rows: tuple[PayoffRow, ...]
    total_cost_cents: Fraction

    @property
    def worst_case_profit_cents(self) -> Fraction:
        return min(row.profit_cents for row in self.rows)

    @property
    def best_case_profit_cents(self) -> Fraction:
        return max(row.profit_cents for row in self.rows)

    @property
    def worst_case_state(self) -> WorldState:
        return min(self.rows, key=lambda r: r.profit_cents).state

    @property
    def guaranteed_roi(self) -> Fraction | None:
        """None (not zero) when total cost is zero -- ROI is undefined,
        not infinite; callers must handle this explicitly rather than
        divide by zero or silently show 0%."""
        if self.total_cost_cents == 0:
            return None
        return self.worst_case_profit_cents / self.total_cost_cents
