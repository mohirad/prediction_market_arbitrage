"""Cross-market arbitrage verification engine.

This package is deliberately separate from arb.matching (candidate
discovery) and arb.pricing.hard_arb (the older, simpler "one leg always
pays $1" model). It implements the general case: given N contracts that
*might* be the same real-world event, enumerate every mutually exclusive,
collectively exhaustive way the world could actually resolve, build a
payoff matrix of every proposed position under every state, and only
call something arbitrage when the worst state still profits.

Module split mirrors the project brief's own required separation:
  models.py     data only -- WorldState, Position, PayoffMatrix, etc.
  payoff.py     deterministic math -- payoff matrix + classification.
                Never calls an LLM.
  optimize.py   deterministic math -- linear-programming stake sizing
                (maximize the minimum profit across all states).
                Never calls an LLM.
  execution.py  deterministic math -- order-book walking across sizes,
                max profitable size before slippage kills the edge.
                Never calls an LLM.
  llm_client.py thin Claude wrapper, fakeable for tests.
  rules.py      LLM-assisted: extracts structured resolution rules from
                raw market text.
  states.py     LLM-assisted: proposes candidate world states (incl.
                adversarial hole-detector states) from two rule specs.
  pipeline.py   orchestrates all of the above; this is the only module
                where LLM output and deterministic verification meet --
                the LLM proposes states/rules, payoff.py and optimize.py
                always do the final math and the final classification.
"""
