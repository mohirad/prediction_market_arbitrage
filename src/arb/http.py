"""Shared HTTP plumbing: token-bucket rate limiting + exponential backoff.

Both platform clients wrap their requests through RateLimitedSession so
retry/backoff/caching behavior is identical and only written once.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)


class TokenBucket:
    """Simple thread-safe token bucket for outbound request pacing."""

    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait = deficit / self.rate
            time.sleep(wait)


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class RateLimitedSession:
    base_url: str
    rate_per_sec: float = 5.0
    burst: int = 10
    max_retries: int = 6
    base_backoff: float = 0.5
    max_backoff: float = 30.0
    timeout: float = 10.0
    default_headers: dict[str, str] = field(default_factory=dict)
    session: requests.Session = field(default_factory=requests.Session)
    _bucket: TokenBucket = field(init=False)

    def __post_init__(self) -> None:
        self._bucket = TokenBucket(self.rate_per_sec, self.burst)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: Callable[[str, str], dict[str, str]] | None = None,
    ) -> requests.Response:
        """extra_headers(method, path) -> headers, called fresh on every
        attempt so per-request signatures (Kalshi RSA-PSS) get a live
        timestamp on retries instead of a stale, possibly-expired one."""
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._bucket.acquire()
            headers = dict(self.default_headers)
            if extra_headers:
                headers.update(extra_headers(method, path))
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=headers, timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                self._sleep_backoff(attempt, retry_after=None)
                continue

            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                self._sleep_backoff(attempt, retry_after=retry_after)
                continue
            return resp
        raise RuntimeError(f"exhausted retries for {method} {url}") from last_exc

    def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = self.base_backoff
        else:
            delay = min(self.max_backoff, self.base_backoff * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)  # jitter
        logger.warning("backing off %.2fs (attempt %d)", delay, attempt)
        time.sleep(delay)

    def get(self, path: str, **kw) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return self.request("POST", path, **kw)
