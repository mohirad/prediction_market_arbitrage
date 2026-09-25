"""LLM-assisted world-state proposal (project brief section 3) and the
adversarial hole-detector pass (section 12). The LLM proposes candidate
mutually-exclusive, collectively-exhaustive resolution states for a pair
of contracts, plus a confidence readout on whether they're even the same
event at all; nothing here decides an arbitrage classification -- that's
always payoff.classify() on the resulting payoff matrix, downstream.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from arb.arbitrage.llm_client import LLMClient, LLMResponseError
from arb.arbitrage.models import ContractRef, Resolution, WorldState
from arb.arbitrage.rules import RuleSpec

_VALID_RESOLUTIONS = {"yes", "no", "void"}

_MATCH_SYSTEM_PROMPT = """You compare two prediction-market contracts (possibly on different platforms) and determine (1) whether they plausibly concern the same real-world event closely enough to be worth hedging against each other, and (2) every mutually exclusive, collectively exhaustive way the real world could resolve BOTH contracts together.

Do not assume two contracts are equivalent just because their titles, subjects, or dates look similar -- read the structured rules and think about whether there is any realistic sequence of events that would resolve them differently. A classic failure: "X wins their party's nomination" is NOT equivalent to "X wins the general election", because "X wins the nomination but loses the general election" is a real possible state where both a naive hedge's legs can lose. Always check for states like this: the subject can satisfy one contract's condition without satisfying the other's; the event could occur after one contract's deadline but before the other's; the two contracts might use different, disagreeing resolution sources; the underlying event could be cancelled, postponed, or the subject replaced/withdrawn; there could be a recount, dispute, or one venue voids its contract while the other does not.

Respond with ONLY a JSON object, no prose, no markdown fences, in exactly this shape:

{
  "compatible": true,
  "incompatibility_reason": null,
  "semantic_similarity": 0.0,
  "rule_equivalence_score": 0.0,
  "resolution_source_compatible": true,
  "expiry_compatible": true,
  "states": [
    {"name": "short_snake_case_id", "description": "one sentence describing this world state",
     "contract_a_resolution": "yes", "contract_b_resolution": "no"}
  ]
}

"compatible": false means the two contracts are about materially different things and no state list should be trusted (still return an empty "states" array in that case). "semantic_similarity" and "rule_equivalence_score" are each 0.0-1.0. Every entry in "states" must set both "contract_a_resolution" and "contract_b_resolution" to one of "yes", "no", or "void", and the states listed must be mutually exclusive and collectively exhaustive (they must cover every realistic way the world could go, and no two states can both be true at once)."""

_HOLE_SYSTEM_PROMPT = """You are a skeptical adversary trying to break a proposed hedge between two prediction-market contracts. You will be given the two contracts' structured rules and the list of world states already identified. Your job is to find any additional, realistic resolution state that is NOT already covered by that list and that would change the combined payout -- specifically check: the subject satisfies one contract's condition but not the other's; the event happens after one contract's deadline but before the other's; the two platforms use different, disagreeing resolution sources for the same nominal event; the event is cancelled, postponed, or the subject is replaced/withdrawn; there is a recount or legal/official dispute over the outcome; one venue voids or cancels its contract while the other does not.

Respond with ONLY a JSON object, no prose, no markdown fences:

{
  "additional_states": [
    {"name": "short_snake_case_id", "description": "one sentence describing this newly found state",
     "contract_a_resolution": "yes", "contract_b_resolution": "no"}
  ]
}

Return an empty "additional_states" array if you cannot find any state missing from the existing list -- do not invent a state just to have something to report."""


@dataclass(frozen=True)
class MatchProposal:
    compatible: bool
    incompatibility_reason: str | None
    semantic_similarity: float
    rule_equivalence_score: float
    resolution_source_compatible: bool
    expiry_compatible: bool
    states: tuple[WorldState, ...]

    @property
    def confidence(self) -> float:
        """Combined 0..1 confidence, used to gate TRUE_ARBITRAGE /
        CAPITAL_PRESERVING_HEDGE in payoff.classify(). Deliberately a
        min(), not an average: one weak signal (e.g. incompatible
        resolution sources) should not be diluted by three strong ones
        when the question is "can this be trusted as guaranteed"."""
        source = 1.0 if self.resolution_source_compatible else 0.0
        expiry = 1.0 if self.expiry_compatible else 0.0
        return min(self.semantic_similarity, self.rule_equivalence_score, source, expiry)


def _rule_spec_prompt_dict(rule: RuleSpec) -> dict:
    return {
        "subject": rule.subject, "event": rule.event, "action_required": rule.action_required,
        "date_range": rule.date_range, "jurisdiction": rule.jurisdiction,
        "winning_condition": rule.winning_condition, "exceptions": list(rule.exceptions),
        "resolution_source": rule.resolution_source, "cancellation_rules": rule.cancellation_rules,
        "edge_cases": list(rule.edge_cases),
    }


def _resolution_from_str(value: object, *, field: str, state_name: str) -> Resolution:
    if not isinstance(value, str) or value.lower() not in _VALID_RESOLUTIONS:
        raise LLMResponseError(f"state {state_name!r} has invalid {field}: {value!r}")
    return Resolution(value.lower())


def _states_from_json(raw_states: object, contract_a: ContractRef, contract_b: ContractRef,
                       *, source: str) -> tuple[WorldState, ...]:
    if not isinstance(raw_states, list):
        raise LLMResponseError("'states' must be a list")
    states = []
    for entry in raw_states:
        name = entry.get("name")
        if not name:
            raise LLMResponseError("a state is missing 'name'")
        res_a = _resolution_from_str(entry.get("contract_a_resolution"), field="contract_a_resolution", state_name=name)
        res_b = _resolution_from_str(entry.get("contract_b_resolution"), field="contract_b_resolution", state_name=name)
        states.append(WorldState(
            name=str(name), description=str(entry.get("description") or ""),
            resolutions={contract_a: res_a, contract_b: res_b}, source=source,
        ))
    return tuple(states)


def propose_match(llm: LLMClient, contract_a: ContractRef, rule_a: RuleSpec,
                   contract_b: ContractRef, rule_b: RuleSpec) -> MatchProposal:
    user = (
        f"Contract A ({contract_a.platform}): {contract_a.label}\n"
        f"Structured rules A: {json.dumps(_rule_spec_prompt_dict(rule_a))}\n\n"
        f"Contract B ({contract_b.platform}): {contract_b.label}\n"
        f"Structured rules B: {json.dumps(_rule_spec_prompt_dict(rule_b))}\n\n"
        "Determine compatibility and enumerate world states as instructed."
    )
    data = llm.complete_json(system=_MATCH_SYSTEM_PROMPT, user=user)

    for field in ("compatible", "semantic_similarity", "rule_equivalence_score",
                  "resolution_source_compatible", "expiry_compatible"):
        if field not in data:
            raise LLMResponseError(f"match proposal missing required field: {field}")

    states = _states_from_json(data.get("states") or [], contract_a, contract_b, source="llm")
    return MatchProposal(
        compatible=bool(data["compatible"]),
        incompatibility_reason=data.get("incompatibility_reason"),
        semantic_similarity=float(data["semantic_similarity"]),
        rule_equivalence_score=float(data["rule_equivalence_score"]),
        resolution_source_compatible=bool(data["resolution_source_compatible"]),
        expiry_compatible=bool(data["expiry_compatible"]),
        states=states,
    )


def detect_holes(llm: LLMClient, contract_a: ContractRef, rule_a: RuleSpec,
                  contract_b: ContractRef, rule_b: RuleSpec,
                  existing_states: tuple[WorldState, ...]) -> tuple[WorldState, ...]:
    """The adversarial pass from section 12: asks Claude to specifically
    try to find a resolution state the initial proposal missed. Returns
    only the NEW states found (empty if none) -- the caller (pipeline.py)
    merges them into the state list and recomputes the payoff matrix, so
    a hole found here can still change the final classification."""
    existing_summary = [{"name": s.name, "description": s.description} for s in existing_states]
    user = (
        f"Contract A ({contract_a.platform}): {contract_a.label}\n"
        f"Structured rules A: {json.dumps(_rule_spec_prompt_dict(rule_a))}\n\n"
        f"Contract B ({contract_b.platform}): {contract_b.label}\n"
        f"Structured rules B: {json.dumps(_rule_spec_prompt_dict(rule_b))}\n\n"
        f"States already identified: {json.dumps(existing_summary)}\n\n"
        "Try to find a missing state as instructed."
    )
    data = llm.complete_json(system=_HOLE_SYSTEM_PROMPT, user=user)
    return _states_from_json(data.get("additional_states") or [], contract_a, contract_b, source="hole_detector")
