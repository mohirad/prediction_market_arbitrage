"""Kalshi RSA-PSS request signing.

Verified 2026-09-20 against docs.kalshi.com/getting_started/api_keys
(see docs/api_notes.md): headers are KALSHI-ACCESS-KEY / -TIMESTAMP (ms) /
-SIGNATURE, and the string-to-sign is `timestamp + METHOD + path` with the
method uppercased and the path excluding any query string. RSA-PSS /
SHA-256, salt length = digest length. The RSA key's required bit-length is
NOT stated in the docs -- we don't validate or assume one here, we just
sign with whatever key the user provides.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


@dataclass(frozen=True)
class KalshiSigner:
    key_id: str
    private_key: rsa.RSAPrivateKey

    @classmethod
    def from_pem_file(cls, key_id: str, pem_path: str) -> "KalshiSigner":
        pem_bytes = Path(pem_path).read_bytes()
        key = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError(f"{pem_path} is not an RSA private key")
        return cls(key_id=key_id, private_key=key)

    def sign(self, method: str, path_without_query: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        message = (timestamp_ms + method.upper() + path_without_query).encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }
