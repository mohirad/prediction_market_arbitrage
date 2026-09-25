#!/usr/bin/env python3
"""One-shot static export: fetches live books for every approved pair in
data/matches.yaml, runs the hard-arb pricing engine, and writes a
snapshot HTML file. For the live, interactive version with a working
Refresh button, use scripts/serve_dashboard.py instead -- a static file
can't re-fetch anything after it's written.

Usage:
    python scripts/build_dashboard.py [--matches data/matches.yaml] [--open]
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arb.config import load_settings
from arb.dashboard.live import evaluate_all
from arb.dashboard.render import render_dashboard
from arb.db.connection import connect
from arb.kalshi.client import KalshiClient
from arb.matching.store import load_matches
from arb.polymarket.clob_client import ClobClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", default="data/matches.yaml")
    parser.add_argument("--out", default="dashboard.html")
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser when done")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    conn = connect(settings.db.sqlite_path)
    kalshi = KalshiClient(api_base=settings.kalshi.api_base)
    clob = ClobClient(clob_base=settings.polymarket.clob_base)

    pairs = load_matches(args.matches, approved_only=True)
    logging.info("evaluating %d approved pair(s)", len(pairs))

    results = evaluate_all(conn, kalshi, clob, pairs)
    html_out = render_dashboard(results)
    Path(args.out).write_text(html_out)
    logging.info("wrote %s", args.out)

    if args.open:
        webbrowser.open(f"file://{Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
