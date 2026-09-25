"""Local HTTP server for the interactive discovery + opportunities app.

Stdlib only (http.server) -- this is a single-user local tool, not a
service, so there's no reason to pull in a web framework dependency for
it. Routes:

  GET /                   the single-page app (static/index.html)
  GET /api/candidates     live: scans both platforms, fuzzy-matches
                          candidates, ranks by volume-weighted price
                          divergence, persists a snapshot of every
                          displayed pair, returns JSON. This is what the
                          discovery table's Refresh button calls.
  GET /api/opportunities  live: takes the top candidate pairs, runs each
                          through the arb.arbitrage verification engine
                          (LLM rule extraction + world-state proposal +
                          hole detector + LP stake optimizer + payoff-
                          matrix classification), returns JSON. This is
                          what the Opportunities tab's Refresh button
                          calls -- each call costs several real LLM API
                          calls per candidate, so it's bounded to a small
                          `max_candidates` rather than scanning everything.

Every request does real network I/O against Kalshi and Polymarket (and,
for /api/opportunities, Anthropic) -- there's no caching layer hiding a
stale number behind a fast response.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from arb.arbitrage.llm_client import AnthropicLLMClient
from arb.config import load_settings
from arb.dashboard.candidates_serialize import discovery_result_to_payload
from arb.dashboard.discover_live import discover_and_rank
from arb.dashboard.opportunities_live import scan_opportunities
from arb.dashboard.opportunities_serialize import opportunity_to_db_row, scan_result_to_payload
from arb.db.connection import connect, insert_arbitrage_opportunity
from arb.kalshi.client import KalshiClient
from arb.matching.discover import fetch_kalshi_candidates, fetch_polymarket_candidates, generate_candidates
from arb.polymarket.clob_client import ClobClient
from arb.polymarket.gamma_client import GammaClient

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")


class CandidatesHandler(BaseHTTPRequestHandler):
    # Set by run_server() before the server starts; shared across requests
    # since each request builds its own client/connection (stateless per call).
    sqlite_path = "data/arb.sqlite3"
    kalshi_api_base = "https://external-api.kalshi.com/trade-api/v2"
    polymarket_gamma_base = "https://gamma-api.polymarket.com"
    polymarket_clob_base = "https://clob.polymarket.com"
    kalshi_max_pages = 12
    kalshi_page_size = 200
    poly_max_pages = 5
    poly_page_size = 100
    min_similarity = 0.5
    max_pairs = 500

    # Opportunities scanner (arb.arbitrage) config.
    anthropic_api_key: str | None = None
    anthropic_model = "claude-sonnet-5"
    min_rule_confidence = 0.9
    capital_limit_cents = 1_000_000
    # Tighter than the discovery table's default -- only plausibly-same-
    # event pairs are worth spending several real LLM calls verifying.
    opportunities_min_similarity = 0.6
    opportunities_max_candidates = 10

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802 -- stdlib method name
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/candidates":
            self._serve_candidates(parse_qs(parsed.query))
        elif parsed.path == "/api/opportunities":
            self._serve_opportunities(parse_qs(parsed.query))
        else:
            self.send_error(404, "not found")

    def _serve_static(self, filename: str, content_type: str) -> None:
        file_path = STATIC_DIR / filename
        if not file_path.is_file():
            self.send_error(404, "not found")
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_candidates(self, query: dict[str, list[str]]) -> None:
        try:
            min_similarity = float(query["min_similarity"][0]) if "min_similarity" in query else self.min_similarity
            max_pairs = int(query["max_pairs"][0]) if "max_pairs" in query else self.max_pairs
            min_similarity = max(0.0, min(1.0, min_similarity))
            max_pairs = max(1, min(2000, max_pairs))

            conn = connect(self.sqlite_path)
            kalshi = KalshiClient(api_base=self.kalshi_api_base)
            gamma = GammaClient(gamma_base=self.polymarket_gamma_base)
            result = discover_and_rank(
                conn, kalshi, gamma,
                kalshi_max_pages=self.kalshi_max_pages, kalshi_page_size=self.kalshi_page_size,
                poly_max_pages=self.poly_max_pages, poly_page_size=self.poly_page_size,
                min_similarity=min_similarity, max_pairs=max_pairs,
            )
            self._send_json(200, discovery_result_to_payload(result))
        except Exception as exc:  # noqa: BLE001 -- must always return a response, not crash the server
            logger.exception("failed to build /api/candidates")
            self._send_json(500, {"error": str(exc)})

    def _serve_opportunities(self, query: dict[str, list[str]]) -> None:
        if not self.anthropic_api_key:
            self._send_json(400, {
                "error": "ANTHROPIC_API_KEY is not configured. Set it in .env (see .env.example) to use the "
                         "Opportunities scanner -- the discovery table above does not need it.",
            })
            return
        try:
            max_candidates = int(query["max_candidates"][0]) if "max_candidates" in query else self.opportunities_max_candidates
            max_candidates = max(1, min(50, max_candidates))  # bounds real LLM-call cost per request
            min_similarity = (float(query["min_similarity"][0]) if "min_similarity" in query
                               else self.opportunities_min_similarity)
            min_similarity = max(0.0, min(1.0, min_similarity))
            capital_limit_cents = (int(query["capital_limit_cents"][0]) if "capital_limit_cents" in query
                                    else self.capital_limit_cents)

            kalshi = KalshiClient(api_base=self.kalshi_api_base)
            gamma = GammaClient(gamma_base=self.polymarket_gamma_base)
            clob = ClobClient(clob_base=self.polymarket_clob_base)
            llm = AnthropicLLMClient(api_key=self.anthropic_api_key, model=self.anthropic_model)

            kcands = fetch_kalshi_candidates(kalshi)
            pcands = fetch_polymarket_candidates(gamma)
            candidate_pairs = generate_candidates(kcands, pcands, min_similarity=min_similarity,
                                                   max_pairs=max_candidates * 3)  # slack for liquidity-skip losses

            result = scan_opportunities(
                kalshi, clob, llm, candidate_pairs, max_candidates=max_candidates,
                capital_limit_cents=capital_limit_cents, min_rule_confidence=self.min_rule_confidence,
            )

            conn = connect(self.sqlite_path)
            for opp in result.opportunities:
                try:
                    insert_arbitrage_opportunity(conn, opportunity_to_db_row(opp))
                except Exception:  # noqa: BLE001 -- a persistence failure shouldn't hide already-computed results
                    logger.exception("failed to persist opportunity %s / %s", opp.contract_a.market_id, opp.contract_b.market_id)

            self._send_json(200, scan_result_to_payload(result))
        except Exception as exc:  # noqa: BLE001 -- must always return a response, not crash the server
            logger.exception("failed to build /api/opportunities")
            self._send_json(500, {"error": str(exc)})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = "127.0.0.1", port: int = 8765, *, sqlite_path: str | None = None,
               kalshi_api_base: str | None = None, polymarket_gamma_base: str | None = None,
               polymarket_clob_base: str | None = None,
               kalshi_max_pages: int | None = None, kalshi_page_size: int | None = None,
               poly_max_pages: int | None = None, poly_page_size: int | None = None,
               min_similarity: float | None = None, max_pairs: int | None = None,
               anthropic_api_key: str | None = None, anthropic_model: str | None = None,
               min_rule_confidence: float | None = None, capital_limit_cents: int | None = None,
               opportunities_min_similarity: float | None = None,
               opportunities_max_candidates: int | None = None) -> ThreadingHTTPServer:
    if sqlite_path:
        CandidatesHandler.sqlite_path = sqlite_path
    if kalshi_api_base:
        CandidatesHandler.kalshi_api_base = kalshi_api_base
    if polymarket_gamma_base:
        CandidatesHandler.polymarket_gamma_base = polymarket_gamma_base
    if polymarket_clob_base:
        CandidatesHandler.polymarket_clob_base = polymarket_clob_base
    if kalshi_max_pages is not None:
        CandidatesHandler.kalshi_max_pages = kalshi_max_pages
    if kalshi_page_size is not None:
        CandidatesHandler.kalshi_page_size = kalshi_page_size
    if poly_max_pages is not None:
        CandidatesHandler.poly_max_pages = poly_max_pages
    if poly_page_size is not None:
        CandidatesHandler.poly_page_size = poly_page_size
    if min_similarity is not None:
        CandidatesHandler.min_similarity = min_similarity
    if max_pairs is not None:
        CandidatesHandler.max_pairs = max_pairs
    if anthropic_api_key is not None:
        CandidatesHandler.anthropic_api_key = anthropic_api_key
    if anthropic_model is not None:
        CandidatesHandler.anthropic_model = anthropic_model
    if min_rule_confidence is not None:
        CandidatesHandler.min_rule_confidence = min_rule_confidence
    if capital_limit_cents is not None:
        CandidatesHandler.capital_limit_cents = capital_limit_cents
    if opportunities_min_similarity is not None:
        CandidatesHandler.opportunities_min_similarity = opportunities_min_similarity
    if opportunities_max_candidates is not None:
        CandidatesHandler.opportunities_max_candidates = opportunities_max_candidates

    return ThreadingHTTPServer((host, port), CandidatesHandler)
