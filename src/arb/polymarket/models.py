from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolymarketMarket:
    condition_id: str
    question: str
    description: str | None
    resolution_source: str | None
    end_date: str | None
    active: bool
    closed: bool
    yes_token_id: str | None
    no_token_id: str | None
    raw: dict[str, Any]

    @classmethod
    def from_gamma(cls, obj: dict[str, Any]) -> "PolymarketMarket":
        yes_id, no_id = _extract_yes_no_token_ids(obj)
        return cls(
            condition_id=obj.get("conditionId", obj.get("id", "")),
            question=obj.get("question", ""),
            description=obj.get("description"),
            resolution_source=obj.get("resolutionSource"),
            end_date=obj.get("endDate"),
            active=bool(obj.get("active", False)),
            closed=bool(obj.get("closed", False)),
            yes_token_id=yes_id,
            no_token_id=no_id,
            raw=obj,
        )


def _extract_yes_no_token_ids(obj: dict[str, Any]) -> tuple[str | None, str | None]:
    """clobTokenIds ordering (YES-first) is UNVERIFIED against primary docs
    (see docs/api_notes.md) -- we cross-check against the `outcomes` field
    when present and only trust position when the label confirms it, else
    we return both as unordered and let the caller flag for manual review."""
    raw_ids = obj.get("clobTokenIds")
    raw_outcomes = obj.get("outcomes")
    if not raw_ids:
        return None, None
    token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
    outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    if not token_ids or len(token_ids) != 2:
        return None, None

    if outcomes and len(outcomes) == 2:
        labels = [str(o).strip().lower() for o in outcomes]
        if labels[0] == "yes" and labels[1] == "no":
            return token_ids[0], token_ids[1]
        if labels[0] == "no" and labels[1] == "yes":
            return token_ids[1], token_ids[0]
    # Could not confirm via label -- caller must treat this pair as
    # needing manual polarity verification before it's ever tradable.
    return None, None
