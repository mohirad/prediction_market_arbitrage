#!/usr/bin/env python3
"""Launches the interactive divergence-scanner app locally and opens it
in a browser. The Refresh button in the app calls back into this running
server to re-scan both platforms and re-rank candidates on demand.

Usage:
    python scripts/serve_dashboard.py [--port 8765] [--no-open]
        [--min-similarity 0.35] [--max-pairs 100]
        [--kalshi-max-pages 8] [--poly-max-pages 3]
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arb.config import load_settings
from arb.dashboard.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="don't auto-open a browser tab")
    parser.add_argument("--min-similarity", type=float, default=0.5,
                         help="default match-confidence threshold (the app's UI can override per-request)")
    parser.add_argument("--max-pairs", type=int, default=500)
    parser.add_argument("--kalshi-max-pages", type=int, default=12,
                         help="Kalshi /events pages to scan (200 events/page)")
    parser.add_argument("--poly-max-pages", type=int, default=5,
                         help="Polymarket /markets pages to scan (100 markets/page, sorted by volume)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    if not settings.arbitrage.anthropic_api_key:
        logging.warning(
            "ANTHROPIC_API_KEY not set -- the Opportunities tab will be unavailable "
            "(the discovery table above it works fine without it)."
        )
    server = run_server(
        host=args.host, port=args.port, sqlite_path=settings.db.sqlite_path,
        kalshi_api_base=settings.kalshi.api_base, polymarket_gamma_base=settings.polymarket.gamma_base,
        polymarket_clob_base=settings.polymarket.clob_base,
        kalshi_max_pages=args.kalshi_max_pages, poly_max_pages=args.poly_max_pages,
        min_similarity=args.min_similarity, max_pairs=args.max_pairs,
        anthropic_api_key=settings.arbitrage.anthropic_api_key, anthropic_model=settings.arbitrage.anthropic_model,
        min_rule_confidence=settings.arbitrage.min_rule_confidence,
        capital_limit_cents=settings.arbitrage.capital_limit_cents,
    )
    url = f"http://{args.host}:{args.port}/"
    logging.info("serving at %s (Ctrl+C to stop)", url)

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
