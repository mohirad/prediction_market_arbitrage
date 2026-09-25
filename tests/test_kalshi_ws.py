import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from arb.kalshi.auth import KalshiSigner
from arb.kalshi.ws import KalshiWebSocketClient


def test_connect_headers_requires_signer():
    client = KalshiWebSocketClient(ws_base="wss://external-api-ws.kalshi.com/trade-api/ws/v2")
    with pytest.raises(RuntimeError, match="requires a signed connection"):
        client._connect_headers()


def test_connect_headers_signs_the_ws_path():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = KalshiSigner(key_id="kid", private_key=private_key)
    client = KalshiWebSocketClient(ws_base="wss://external-api-ws.kalshi.com/trade-api/ws/v2", signer=signer)
    headers = client._connect_headers()
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "kid"


@pytest.mark.asyncio
async def test_stream_sends_correct_subscribe_message(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = KalshiSigner(key_id="kid", private_key=private_key)
    client = KalshiWebSocketClient(ws_base="wss://fake", signer=signer)

    sent = []

    class FakeWS:
        async def send(self, msg):
            sent.append(msg)

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeConnectCtx:
        async def __aenter__(self):
            return FakeWS()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("arb.kalshi.ws.websockets.connect", lambda *a, **kw: FakeConnectCtx())

    messages = [m async for m in client.stream(["ticker"], ["TICK-1"])]
    assert messages == []
    assert len(sent) == 1
    import json
    payload = json.loads(sent[0])
    assert payload["cmd"] == "subscribe"
    assert payload["params"] == {"channels": ["ticker"], "market_tickers": ["TICK-1"]}
    assert payload["id"] == 1
