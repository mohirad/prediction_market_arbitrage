"""Kalshi WebSocket client for orderbook_delta + ticker channels.

URL and subscribe message format verified live 2026-09-20 (see
docs/api_notes.md). `orderbook_delta` gives incremental book updates; we
don't maintain book state from deltas ourselves here (Phase 0 is REST
polling for snapshots) -- this client exists so a later phase can move
watchlist markets from 30-60s REST polling to push updates without
redesigning the transport.

Live-verified 2026-09-20: connecting WITHOUT signed headers gets an
immediate HTTP 401 on the handshake, even for market-data-only channels
like `ticker`/`orderbook_delta` -- so a KalshiSigner is required, not
optional, despite this being public market data. (Not fully exercised
end-to-end here for lack of a real API key/private key pair; the
REST auth path this reuses -- KalshiSigner.sign -- is live-verified via
REST, see kalshi/client.py and docs/api_notes.md.)
"""
from __future__ import annotations

import itertools
import json
import logging
from typing import Any, AsyncIterator

import websockets

from arb.kalshi.auth import KalshiSigner

logger = logging.getLogger(__name__)

_WS_PATH = "/trade-api/ws/v2"  # signed the same way as REST paths, per docs/api_notes.md


class KalshiWebSocketClient:
    def __init__(self, ws_base: str, signer: KalshiSigner | None = None):
        self._ws_base = ws_base
        self._signer = signer
        self._id_counter = itertools.count(1)

    def _connect_headers(self) -> dict[str, str]:
        if self._signer is None:
            raise RuntimeError(
                "Kalshi WS requires a signed connection (live-verified: "
                "unsigned connects get HTTP 401) -- pass a KalshiSigner"
            )
        return self._signer.sign("GET", _WS_PATH)

    async def stream(self, channels: list[str], market_tickers: list[str]) -> AsyncIterator[dict[str, Any]]:
        """Connects, subscribes, and yields parsed JSON messages forever.
        Caller is responsible for reconnect-on-drop (kept out of this
        generator so retry/backoff policy lives in one place: arb.http's
        exponential-backoff conventions should be mirrored by the caller
        for consistency, once a Phase-3+ live-trading path needs this)."""
        headers = self._connect_headers()
        async with websockets.connect(self._ws_base, additional_headers=headers) as ws:
            subscribe_msg = {
                "id": next(self._id_counter),
                "cmd": "subscribe",
                "params": {"channels": channels, "market_tickers": market_tickers},
            }
            await ws.send(json.dumps(subscribe_msg))
            async for raw in ws:
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("non-JSON WS message dropped: %r", raw[:200])
