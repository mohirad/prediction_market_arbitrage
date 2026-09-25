from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(sqlite_path: str) -> sqlite3.Connection:
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path, isolation_level=None)  # autocommit; we wrap txns explicitly
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text())
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_market(conn: sqlite3.Connection, *, platform: str, market_id: str, title: str,
                   description: str | None, resolution_text: str | None,
                   close_ts: int | None, resolve_ts: int | None, category: str | None,
                   yes_token_id: str | None, no_token_id: str | None, raw_json: str) -> None:
    conn.execute(
        """
        INSERT INTO markets (platform, market_id, title, description, resolution_text,
                              close_ts, resolve_ts, category, yes_token_id, no_token_id,
                              raw_json, fetched_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (platform, market_id) DO UPDATE SET
            title=excluded.title, description=excluded.description,
            resolution_text=excluded.resolution_text, close_ts=excluded.close_ts,
            resolve_ts=excluded.resolve_ts, category=excluded.category,
            yes_token_id=excluded.yes_token_id, no_token_id=excluded.no_token_id,
            raw_json=excluded.raw_json, fetched_ts=excluded.fetched_ts
        """,
        (platform, market_id, title, description, resolution_text, close_ts, resolve_ts,
         category, yes_token_id, no_token_id, raw_json, int(time.time())),
    )


def insert_snapshot(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = (
        "ts", "platform", "market_id", "side", "best_bid_pbp", "best_ask_pbp", "mid_pbp",
        "depth_1c_bid_c", "depth_1c_ask_c", "depth_5c_bid_c", "depth_5c_ask_c",
        "depth_10c_bid_c", "depth_10c_ask_c", "volume_24h_c", "open_interest_c", "raw_book_json",
    )
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO snapshots ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(row.get(c) for c in cols),
    )


def insert_arbitrage_opportunity(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = (
        "ts", "kalshi_market_id", "polymarket_market_id", "classification", "confidence",
        "capital_required_c", "guaranteed_profit_c", "guaranteed_roi_bps",
        "max_profitable_shares", "reject_reason", "positions_json", "payoff_matrix_json",
    )
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO arbitrage_opportunities ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(row.get(c) for c in cols),
    )


def log_poll_error(conn: sqlite3.Connection, *, platform: str, market_id: str | None,
                    endpoint: str, status_code: int | None, message: str) -> None:
    conn.execute(
        "INSERT INTO poll_errors (ts, platform, market_id, endpoint, status_code, message) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time() * 1000), platform, market_id, endpoint, status_code, message[:2000]),
    )
