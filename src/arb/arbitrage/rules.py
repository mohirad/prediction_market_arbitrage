"""LLM-assisted extraction of structured resolution rules from a market's
raw title + resolution-criteria text (project brief section 2). The LLM
proposes a structured RuleSpec; nothing here or downstream trusts the
LLM's own opinion of whether two markets are equivalent -- that
comparison happens in states.py, and the final classification is always
payoff.classify(), pure deterministic Python.
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.arbitrage.llm_client import LLMClient, LLMResponseError

_SYSTEM_PROMPT = """You are extracting structured resolution rules from a prediction-market contract's raw text, for a system that will later check whether two contracts on different platforms genuinely refer to the same real-world event. Be precise and literal: do not infer anything the text does not state, and do not guess at a field's value if the text is silent -- use null or an empty list instead. Respond with ONLY a JSON object, no prose, no markdown code fences, matching exactly this shape:

{
  "subject": "the specific person/team/entity/asset this contract is about",
  "event": "the specific real-world event or measurement this contract concerns",
  "action_required": "what must happen for this contract to resolve YES",
  "date_range": "the exact window in which the event must occur, including timezone if the text states one",
  "jurisdiction": "governing jurisdiction/body, or null if not applicable",
  "winning_condition": "the precise, literal condition that triggers a YES resolution",
  "exceptions": ["list of exceptions to the winning condition explicitly stated in the rules"],
  "resolution_source": "the named source of truth used to resolve this market (e.g. a specific data feed, official body, or news outlet named in the rules)",
  "cancellation_rules": "what happens to this contract if the underlying event is cancelled or postponed, or null if not stated",
  "edge_cases": ["any other edge cases explicitly called out in the rules text, e.g. ties, disputes, late changes"]
}"""

_REQUIRED_FIELDS = ("subject", "event", "action_required", "date_range", "winning_condition", "resolution_source")


@dataclass(frozen=True)
class RuleSpec:
    subject: str
    event: str
    action_required: str
    date_range: str
    winning_condition: str
    resolution_source: str
    jurisdiction: str | None = None
    cancellation_rules: str | None = None
    exceptions: tuple[str, ...] = ()
    edge_cases: tuple[str, ...] = ()

    @staticmethod
    def from_json(data: dict) -> "RuleSpec":
        missing = [k for k in _REQUIRED_FIELDS if not data.get(k)]
        if missing:
            raise LLMResponseError(f"rule extraction missing required fields: {missing}")
        return RuleSpec(
            subject=str(data["subject"]),
            event=str(data["event"]),
            action_required=str(data["action_required"]),
            date_range=str(data["date_range"]),
            winning_condition=str(data["winning_condition"]),
            resolution_source=str(data["resolution_source"]),
            jurisdiction=data.get("jurisdiction"),
            cancellation_rules=data.get("cancellation_rules"),
            exceptions=tuple(data.get("exceptions") or ()),
            edge_cases=tuple(data.get("edge_cases") or ()),
        )


def extract_rule_spec(llm: LLMClient, *, platform: str, title: str, resolution_text: str) -> RuleSpec:
    user = (
        f"Platform: {platform}\n"
        f"Contract title: {title}\n\n"
        f"Resolution rules (verbatim):\n{resolution_text or '(none provided)'}\n\n"
        "Extract the structured rule spec as instructed."
    )
    data = llm.complete_json(system=_SYSTEM_PROMPT, user=user)
    return RuleSpec.from_json(data)
