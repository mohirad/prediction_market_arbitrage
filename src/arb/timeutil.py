"""Timezone-safe timestamp handling.

Matching two markets' settlement conditions requires getting "by date X"
vs "on date X" and the settlement timezone exactly right -- a naive
datetime here is how you end up thinking two markets settle on the same
instant when they're actually 4-8 hours apart (US market close vs UTC
midnight is a classic one). Every function here either returns a
timezone-aware datetime or raises; nothing silently assumes UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (as returned by Kalshi/Polymarket JSON)
    into a timezone-aware UTC datetime. Rejects naive-looking strings with
    no offset instead of silently assuming one."""
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone offset; refusing to assume UTC")
    return dt.astimezone(timezone.utc)


def unix_seconds_to_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def end_of_day_utc(date_str: str, tz_name: str) -> datetime:
    """Convert "end of <date_str> in <tz_name>" (e.g. a "by end of day
    March 15 ET" resolution deadline) into the equivalent UTC instant.
    date_str is "YYYY-MM-DD". End of day = the start of the next day,
    exclusive, in the given zone -- avoids ambiguity about 23:59:59 vs
    23:59:59.999999."""
    y, m, d = (int(x) for x in date_str.split("-"))
    local_start = datetime(y, m, d, tzinfo=ZoneInfo(tz_name))
    local_next_day = local_start + timedelta(days=1)
    return local_next_day.astimezone(timezone.utc)


def same_instant(a: datetime, b: datetime, tolerance: timedelta = timedelta(0)) -> bool:
    """Whether two timezone-aware datetimes refer to the same settlement
    instant, within `tolerance`. Raises on naive input rather than
    guessing a timezone for it."""
    if a.tzinfo is None or b.tzinfo is None:
        raise ValueError("same_instant requires timezone-aware datetimes")
    return abs(a - b) <= tolerance
