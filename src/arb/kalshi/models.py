from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KalshiMarket:
    ticker: str
    title: str
    subtitle: str | None
    status: str
    close_ts: int | None
    category: str | None
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, obj: dict[str, Any]) -> "KalshiMarket":
        return cls(
            ticker=obj["ticker"],
            title=obj.get("title", ""),
            subtitle=obj.get("subtitle"),
            status=obj.get("status", ""),
            close_ts=obj.get("close_ts"),
            category=obj.get("category"),
            raw=obj,
        )
