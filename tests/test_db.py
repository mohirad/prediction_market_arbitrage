import sqlite3

import pytest

from arb.db.connection import connect, insert_snapshot, log_poll_error, transaction, upsert_market


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "test.sqlite3"))
    yield c
    c.close()


def test_schema_applies_and_tables_exist(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"markets", "snapshots", "candles", "matched_pairs", "poll_errors"} <= tables


def test_upsert_market_then_update(conn):
    upsert_market(
        conn, platform="kalshi", market_id="ABC-1", title="Will X happen?",
        description="d", resolution_text="r", close_ts=1000, resolve_ts=1000,
        category="politics", yes_token_id=None, no_token_id=None, raw_json="{}",
    )
    row = conn.execute("SELECT * FROM markets WHERE platform='kalshi' AND market_id='ABC-1'").fetchone()
    assert row["title"] == "Will X happen?"

    upsert_market(
        conn, platform="kalshi", market_id="ABC-1", title="Will X happen? (updated)",
        description="d", resolution_text="r", close_ts=1000, resolve_ts=1000,
        category="politics", yes_token_id=None, no_token_id=None, raw_json="{}",
    )
    row = conn.execute("SELECT * FROM markets WHERE platform='kalshi' AND market_id='ABC-1'").fetchone()
    assert row["title"] == "Will X happen? (updated)"
    count = conn.execute("SELECT COUNT(*) c FROM markets").fetchone()["c"]
    assert count == 1


def test_insert_snapshot_requires_known_market(conn):
    # FK enforcement is off by default in sqlite3 unless PRAGMA is honored
    # per-connection; verify it's actually on via our connect() helper.
    with pytest.raises(sqlite3.IntegrityError):
        insert_snapshot(conn, {
            "ts": 1, "platform": "kalshi", "market_id": "NOPE", "side": "yes",
            "best_bid_pbp": 5000, "best_ask_pbp": 5100, "mid_pbp": 5050,
        })


def test_insert_snapshot_happy_path(conn):
    upsert_market(
        conn, platform="kalshi", market_id="ABC-1", title="t", description=None,
        resolution_text=None, close_ts=None, resolve_ts=None, category=None,
        yes_token_id=None, no_token_id=None, raw_json="{}",
    )
    insert_snapshot(conn, {
        "ts": 12345, "platform": "kalshi", "market_id": "ABC-1", "side": "yes",
        "best_bid_pbp": 5000, "best_ask_pbp": 5100, "mid_pbp": 5050,
        "depth_1c_bid_c": 100, "depth_1c_ask_c": 100,
    })
    row = conn.execute("SELECT * FROM snapshots WHERE market_id='ABC-1'").fetchone()
    assert row["mid_pbp"] == 5050
    assert row["depth_5c_bid_c"] is None  # unset columns default to NULL


def test_transaction_rolls_back_on_error(conn):
    upsert_market(
        conn, platform="kalshi", market_id="ABC-1", title="t", description=None,
        resolution_text=None, close_ts=None, resolve_ts=None, category=None,
        yes_token_id=None, no_token_id=None, raw_json="{}",
    )
    with pytest.raises(ValueError):
        with transaction(conn):
            insert_snapshot(conn, {
                "ts": 1, "platform": "kalshi", "market_id": "ABC-1", "side": "yes",
                "best_bid_pbp": 5000, "best_ask_pbp": 5100, "mid_pbp": 5050,
            })
            raise ValueError("boom")
    count = conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
    assert count == 0


def test_log_poll_error(conn):
    log_poll_error(conn, platform="polymarket", market_id="xyz", endpoint="/book",
                    status_code=503, message="service unavailable")
    row = conn.execute("SELECT * FROM poll_errors").fetchone()
    assert row["status_code"] == 503
