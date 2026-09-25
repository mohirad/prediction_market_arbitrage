"""Thin wrapper around the Anthropic API for structured-JSON extraction
calls. Every call in this package asks Claude to return JSON matching a
schema described in the prompt; this wrapper does the network call and
JSON parse only -- it validates nothing about the *content* (that's
rules.py/states.py's job, deterministically) beyond "is this parseable
JSON at all". Keeping it this thin also makes it trivial to fake in
tests: anything with a matching `complete_json` method works, no mocking
framework needed (see tests/test_rules.py, tests/test_states.py).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"


class LLMClient(Protocol):
    def complete_json(self, *, system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]: ...


class LLMResponseError(RuntimeError):
    """Raised when the LLM's response isn't valid/parseable JSON, or is
    missing fields a caller requires. Callers must treat this as "cannot
    verify automatically" (-> MANUAL_VERIFICATION_REQUIRED upstream),
    never silently fall back to assuming a match is safe."""


def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"LLM did not return valid JSON: {exc}\n---\n{text[:500]}") from exc


@dataclass
class AnthropicLLMClient:
    """Real client. Imports the `anthropic` package and reads
    ANTHROPIC_API_KEY lazily in __post_init__, so importing this module
    never requires the package or a key unless this class is actually
    instantiated -- tests and any caller that only wants the fake never
    pay that cost."""
    api_key: str | None = None
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        import anthropic  # local import: see class docstring

        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set and no api_key provided")
        self._client = anthropic.Anthropic(api_key=key)

    def complete_json(self, *, system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return _parse_json_response(text)
