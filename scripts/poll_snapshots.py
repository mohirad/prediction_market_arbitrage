#!/usr/bin/env python3
"""CLI entrypoint: poll the watchlist and persist snapshots on a loop.

Usage:
    python scripts/poll_snapshots.py [--watchlist config/watchlist.yaml] [--once]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arb.config import load_settings
from arb.db.connection import connect
from arb.kalshi.auth import KalshiSigner
from arb.kalshi.client import KalshiClient
from arb.polymarket.clob_client import ClobClient
from arb.snapshots.poller import load_watchlist, poll_all, run_loop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="config/watchlist.yaml")
    parser.add_argument("--once", action="store_true", help="poll a single cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    conn = connect(settings.db.sqlite_path)

    signer = None
    if settings.kalshi.api_key_id and settings.kalshi.private_key_path:
        signer = KalshiSigner.from_pem_file(settings.kalshi.api_key_id, settings.kalshi.private_key_path)
    kalshi = KalshiClient(api_base=settings.kalshi.api_base, signer=signer)
    clob = ClobClient(clob_base=settings.polymarket.clob_base)

    pairs = load_watchlist(args.watchlist)
    logging.info("loaded %d watchlist pairs from %s", len(pairs), args.watchlist)

    if args.once:
        poll_all(conn, kalshi, clob, pairs)
    else:
        run_loop(conn, kalshi, clob, pairs, interval_seconds=settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
