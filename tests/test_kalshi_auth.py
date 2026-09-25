import base64
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from arb.kalshi.auth import KalshiSigner


@pytest.fixture()
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def test_sign_produces_expected_headers(keypair):
    private_key, _ = keypair
    signer = KalshiSigner(key_id="my-key-id", private_key=private_key)
    headers = signer.sign("GET", "/trade-api/v2/markets")

    assert headers["KALSHI-ACCESS-KEY"] == "my-key-id"
    assert set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
    # timestamp is fresh milliseconds
    ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
    assert abs(ts - int(time.time() * 1000)) < 5000


def test_signature_verifies_against_expected_message(keypair):
    private_key, public_key = keypair
    signer = KalshiSigner(key_id="k", private_key=private_key)
    headers = signer.sign("get", "/trade-api/v2/markets/FOO/orderbook")

    expected_message = (
        headers["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/v2/markets/FOO/orderbook"
    ).encode("utf-8")
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])

    # raises InvalidSignature if the string-to-sign format is wrong
    public_key.verify(
        signature, expected_message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_signature_rejects_wrong_method_case(keypair):
    private_key, public_key = keypair
    signer = KalshiSigner(key_id="k", private_key=private_key)
    headers = signer.sign("GET", "/trade-api/v2/markets")

    wrong_message = (headers["KALSHI-ACCESS-TIMESTAMP"] + "get" + "/trade-api/v2/markets").encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])

    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        public_key.verify(
            signature, wrong_message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
