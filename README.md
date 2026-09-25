# Cross-market divergence scanner: Kalshi <-> Polymarket

Finds matched markets where Kalshi and Polymarket price the same outcome
differently, measures whether the gap historically closes, and produces
sized trade recommendations. Built in phases; see `docs/api_notes.md` for
the live-verified API facts everything below is built against.

## Status

- **Phase 0 (data layer): done.** REST clients for both platforms, RSA-PSS
  request signing, rate-limited/backoff HTTP, SQLite snapshot schema,
  order-book walking + depth bands, a snapshot poller, and a Kalshi WS
  client (subscribe path verified; full authenticated stream unexercised
  for lack of a live API key). All of this has been round-tripped against
  the real production APIs, not just mocked -- see `docs/api_notes.md`
  for the several doc-vs-reality corrections that surfaced along the way.
- **Phase 1 (market matching): candidate-discovery slice done,** not the
  full pipeline. `arb.matching.discover` scans real, actively-traded
  markets on both platforms live (see "How discovery works" below) and
  fuzzy-matches them by IDF-weighted title/question similarity -- a
  lightweight stand-in for the embeddings pipeline the brief describes,
  good enough to surface real candidates (including some exact matches)
  but with no strict resolution-criteria verification. Every match ships
  with its similarity score and both full titles so a human makes the
  final call; nothing here auto-writes to `data/matches.yaml`.
  Separately, `data/matches.yaml` + `arb.matching.store` still hold a
  small hand-verified pair list (2 Bitcoin-threshold markets, actually
  checked against both platforms' resolution rules) -- that's the
  versioned/approved-flag structure a stricter pipeline would eventually
  populate, kept as a second, independent, more rigorous track.
- **Phase 2 (comparable pricing): hard-arb signal exists** in
  `arb.pricing.hard_arb` (walks both books for the real executable price,
  nets Kalshi + Polymarket fees) but the main app below doesn't call it
  -- the discovery view shows raw mid-price divergence, not a fee-netted
  trade edge, by design (see Status note above and the request that
  shaped this). It's still exercised by the `data/matches.yaml` track.
- **Phase 3 (convergence study): not started.**
- **Phase 4 (trade construction / paper trading): not started.**
- **Phase 5 (dashboard): a real interactive app, not a mockup.**
  `scripts/serve_dashboard.py` runs a small local web server (stdlib
  only, no framework) and opens a single-page app: a sortable table of
  matched pairs -- Kalshi's market next to the Polymarket market judged
  most likely to be the *same* market -- with a **Refresh button that
  re-scans both platforms live** on every press (`GET /api/candidates`).
  Each row shows both markets' YES and NO prices, both 24h volumes, a
  YES gap and a NO gap (each platform's YES priced against the other's
  YES, and same for NO -- independent numbers, not mirror images, since
  Polymarket's YES and NO trade on separate order books), a direct link
  to each market, and a match-confidence score. No trade instructions or
  profit math -- just the data, so you can judge each pair yourself.
  Sort by any column (YES gap, NO gap, either platform's volume, match
  confidence, etc.) by clicking its header.
- **Opportunities scanner (`arb.arbitrage`): core verification engine
  done and wired into the dashboard's second tab.** This is a separate,
  stricter track from the Discovery tab above -- see "The Opportunities
  scanner" below for what it does and, just as importantly, what it
  deliberately does not do yet.

## The Opportunities scanner: true-arbitrage verification

The Discovery tab answers "how far apart are these two prices?" The
**Opportunities** tab (same app, second tab) answers a much stricter
question: *is there a combination of positions across these two markets
that cannot lose money, under every way the world could actually turn
out?* A 30% price gap between two markets that both lose under some
realistic scenario is not arbitrage -- see `arb/arbitrage/payoff.py`'s
module docstring for the project brief's own worked example (Vance
winning the Republican nomination but losing the general election, which
would make a naive "buy nomination NO + buy presidency YES" hedge lose
on both legs).

**How it works**, end to end, for each candidate pair (`arb/arbitrage/`):

1. `rules.py` asks Claude to extract each market's resolution rules into
   a structured spec (subject, winning condition, resolution source,
   exceptions, cancellation rules, ...) from its raw rules text.
2. `states.py` asks Claude to judge whether the two contracts are even
   plausibly the same event, and if so, to enumerate every mutually
   exclusive, collectively exhaustive way the world could resolve both
   of them together -- then runs an adversarial **hole-detector** pass
   that specifically tries to find a state the first pass missed
   (candidate withdraws, recount, cancelled event, disagreeing
   resolution sources, mismatched deadlines, one venue voids and the
   other doesn't).
3. `execution.py` walks each side's *real, live order book* (never the
   headline bid/ask) to get an executable price and available liquidity.
4. `optimize.py` runs a linear program (`scipy.optimize.linprog`) that
   sizes every leg simultaneously to maximize the *worst-case* profit
   across all identified states, subject to order-book depth and a
   capital limit -- it is never told to split capital evenly.
5. `payoff.py` builds the full state x position payoff matrix from the
   optimizer's chosen (real, re-walked) position sizes and applies the
   brief's own arbitrage definitions exactly: `min(payout) > cost` is
   TRUE ARBITRAGE, `min(payout) >= cost` is a CAPITAL-PRESERVING HEDGE,
   otherwise CONDITIONAL ARBITRAGE or PRICE DISCREPANCY. This step never
   calls an LLM -- the LLM only ever *proposes* rules and states; this
   module is the only thing that ever decides a classification, and it
   is pure, tested Python (see `tests/test_payoff.py`, which reproduces
   the brief's worked example and the Vance hole as literal test cases).
6. A **rule-confidence gate** (`ARB_MIN_RULE_CONFIDENCE`, default 0.9)
   downgrades an otherwise-TRUE-ARBITRAGE or CAPITAL-PRESERVING-HEDGE
   result to **MANUAL VERIFICATION REQUIRED** if the LLM's own
   similarity/rule-equivalence/resolution-source/expiry signals aren't
   all strong -- "the math works" is never enough on its own.

Every number shown (positions, payoff matrix, guaranteed profit,
guaranteed ROI, max executable size) is recomputed from scratch on every
scan -- nothing is cached from a previous run -- and every opportunity
evaluated is persisted to the `arbitrage_opportunities` table so past
scans can be audited later.

**To use it:** get an API key at console.anthropic.com, set
`ANTHROPIC_API_KEY` in `.env` (see `.env.example`), then open the
Opportunities tab and click "Scan for opportunities". Each scan runs
several real Claude API calls per candidate pair, so it's deliberately
bounded to a small "max candidates" (default 10, pulled from the
Discovery tab's top matches at a stricter 0.6 similarity floor) rather
than scanning the whole market the way the Discovery tab's Refresh does
-- expect it to take noticeably longer. Only **TRUE ARBITRAGE** shows by
default; the "show" filter reveals weaker classifications.

**What this build deliberately does not include yet** (see "Where should
I start" in this project's own build history -- the brief describes a
much larger production system, and this phase intentionally proves the
hardest part, the arbitrage math itself, before investing in infra for
it): no Next.js/FastAPI/Postgres/Redis/WebSocket rewrite -- this still
runs on the existing stdlib server + SQLite; no continuous background
scanning worker (the scan is triggered by the Refresh button, not a
cron); no browser/email/Telegram/Discord alerts; no auto-execution (this
is, and will stay, a discovery-and-verification tool the user trades
from manually, per the brief's own MVP section); the LP stake optimizer
uses a continuous relaxation (fractional shares, rounded down before
display) and a linear per-share fee approximation rather than Kalshi's
exact quadratic fee curve; the size-scan / max-executable-size
calculation only covers the common 2-position-per-pair case, not
N-leg combinations. None of these are structural blockers -- the engine
underneath (`arb/arbitrage/`) is decoupled from the stdlib server and
can be pointed at a different transport/scheduler/datastore later
without changing its math.

## Never used Terminal before? Start here

1. Open the **Terminal** app (press `Cmd + Space`, type `Terminal`, press Enter).
2. Type `cd ` (with a trailing space), then drag the `02_arbitrage` folder
   from Finder into the Terminal window -- it pastes the path for you.
   Press Enter.
3. Type `./run.sh` and press Enter.
4. Wait ~30-60 seconds while it sets itself up. A browser tab opens
   automatically showing the scanner -- that means it worked. It takes a
   few more seconds to finish its first live scan. Click the **Refresh**
   button anytime to re-scan. Click any column header (YES gap, NO gap,
   K vol 24h, Match, etc.) to re-sort the table by that column.
5. The app keeps running in that Terminal window the whole time (that's
   normal -- it's a live local server, not a one-off script). To stop it,
   click back into the Terminal window and press `Ctrl+C`. Run `./run.sh`
   again anytime to start it back up.

This only *reads* prices -- it never places a trade, so there's nothing
to break. Snapshots of what it saw are saved in `data/arb.sqlite3` inside
this folder.

Troubleshooting: `Permission denied` -> run `chmod +x run.sh` once, then
try again. `command not found: python3` -> Python isn't installed; ask
whoever set this up for help. Nothing opens in the browser -> go to
`http://127.0.0.1:8765/` manually. Any other red error text -> copy it
and ask for help fixing it.

## Quickstart (one command)

```
./run.sh
```

This creates `.venv` if missing, installs pinned dependencies, seeds
`.env` and `config/watchlist.yaml` from the `.example` files on first run
(safe to re-run any time -- every step is idempotent), runs the full test
suite, then starts the interactive app at `http://127.0.0.1:8765/` and
opens it in your browser: a sortable table of matched pairs found by
live-scanning both platforms, with YES/NO price gaps for each pair.
Press **Refresh** anytime to re-scan -- that's a real network call each
time (roughly 8-15 seconds; it's fetching and matching up to ~1000
Kalshi + ~500 Polymarket markets live), not a cached number, and shows
as many matched pairs as it finds (up to 500 by default, adjustable in
the app). No credentials are needed -- public market data requires no
auth on either platform. The server runs in the foreground; `Ctrl+C`
stops it.

## How discovery works

`arb.matching.discover` (see its module docstring for the full detail,
including two real API gotchas it works around):

1. **Kalshi**: `GET /events?with_nested_markets=true`, paged, category
   and volume taken straight from each nested market. (`GET /markets`
   with no series filter turns out to be dominated end-to-end by
   auto-generated multivariate combo markets with zero volume --
   confirmed by paging through 8000 of them live -- so this uses
   `/events` instead, which doesn't have that problem.)
2. **Polymarket**: `GET /markets?order=volume24hr&ascending=false`, which
   pulls the highest-volume markets directly, no local sorting needed.
3. Both sides are filtered to non-zero 24h volume and sorted by it.
4. Every Kalshi candidate is matched against its best-scoring Polymarket
   candidate using **IDF-weighted token similarity**, not plain word
   overlap -- plain Jaccard was tried first and produced dangerously
   wrong matches (e.g. pairing two different presidential candidates'
   otherwise-identically-worded "will X be the nominee" markets, since
   they share every word except the name). Weighting each word by how
   rare it is across the current batch fixes this: generic words
   ("democratic", "presidential", "2028") stop counting, while a
   specific name or number correctly dominates the score.
5. Matches below `min_similarity` (default 0.5 -- raised from an earlier
   0.35 to bias toward pairs that are plausibly the *same* market,
   adjustable in the app) are dropped. For each kept pair, a YES gap
   (Kalshi YES price - Polymarket YES price) and a NO gap (Kalshi NO
   price - Polymarket's own, independently traded NO price) are computed.
   Pairs are ranked by `max(|YES gap|, |NO gap|) x min(both sides' 24h
   volume)` -- "most misaligned on either side, weighted by how much
   volume is actually trading on both platforms" -- and every match's
   similarity score is shown so you can judge confidence yourself.

This is explicitly **not** verified matching -- it can and does surface
same-person-different-date pairs, generic-market false positives, etc.
Both full titles and the similarity score are always shown so you can
spot those at a glance; nothing here writes an approval anywhere.

## Manual setup

If you'd rather not use `run.sh`, the equivalent steps:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in credentials only if trading, not for read-only scanning
cp config/watchlist.example.yaml config/watchlist.yaml   # fill in real tickers/token ids

python -m pytest -q                          # run the test suite
python scripts/serve_dashboard.py            # run the interactive scanner (default: http://127.0.0.1:8765/)
python scripts/build_dashboard.py --open     # or: static export of the (separate) data/matches.yaml pairs
python scripts/poll_snapshots.py --once      # or: just poll the watchlist once
python scripts/poll_snapshots.py             # or: poll continuously (default 45s interval)
```

`serve_dashboard.py` also takes `--min-similarity`, `--max-pairs`,
`--kalshi-max-pages`, `--poly-max-pages` to tune the scan (see `--help`);
the app's own UI can override similarity/max-pairs per request too.

## Layout

```
src/arb/
  money.py          integer-only price/fee math (PriceBp, Cents, Fraction -- no floats)
  orderbook.py       platform-agnostic book: walk-for-size, depth bands
  polarity.py        YES/NO alignment between two matched markets
  timeutil.py         timezone-safe settlement-instant handling
  fees.py             Kalshi + Polymarket fee formulas (see UNVERIFIED flags)
  http.py             shared rate limiter + exponential backoff
  config.py           env-var settings
  db/                 SQLite schema + connection/insert helpers
  kalshi/              REST client, RSA-PSS auth, orderbook parsing, WS client
  polymarket/          Gamma (metadata) + CLOB (book/midpoint/history) clients
  snapshots/poller.py  polls a watchlist, persists snapshot rows
  matching/discover.py   live candidate discovery: fetch, IDF-weighted fuzzy match, rank (see "How discovery works")
  matching/store.py     loads data/matches.yaml (versioned, confidence + approval flag) -- separate, stricter track
  pricing/hard_arb.py   walks both books, nets fees, picks the profitable leg on each side -- used by the matches.yaml track
  arbitrage/models.py    WorldState/Position/PayoffMatrix/Classification data model -- see "The Opportunities scanner"
  arbitrage/payoff.py     deterministic payoff-matrix construction + the TRUE ARBITRAGE/etc classification math; no LLM
  arbitrage/optimize.py   LP stake-size optimizer (scipy.optimize.linprog), maximizes worst-case profit; no LLM
  arbitrage/execution.py  order-book-walked execution quotes across standard sizes + max-profitable-size search; no LLM
  arbitrage/llm_client.py Anthropic API wrapper (+ Protocol every module fakes in tests)
  arbitrage/rules.py      LLM-assisted structured resolution-rule extraction
  arbitrage/states.py     LLM-assisted world-state proposal + adversarial hole-detector pass
  arbitrage/pipeline.py   orchestrates rules -> states -> holes -> optimize -> payoff -> classify for one candidate pair
  dashboard/discover_live.py  orchestrates discover.py + persists a snapshot per displayed pair
  dashboard/candidates_serialize.py  CandidatePair -> JSON for the Discovery tab's API
  dashboard/opportunities_live.py  fetches real order books for the top candidates, runs each through arbitrage/pipeline.py
  dashboard/opportunities_serialize.py  ArbitrageOpportunity -> JSON for the Opportunities tab's API + DB row
  dashboard/server.py     stdlib HTTP server: GET / (app), GET /api/candidates, GET /api/opportunities (both live, no cache)
  dashboard/static/index.html   the single-page app: two tabs (Discovery, Opportunities), vanilla JS, no build step
  dashboard/live.py, serialize.py, render.py   the matches.yaml/hard-arb track's live-fetch + static-export code
  research/, trading/   Phase 3-4, empty so far
tests/                 one test file per module above, 190+ tests
docs/api_notes.md       every verified/unverified API fact, with sources
data/matches.yaml       hand-verified matched pairs (separate, stricter track -- see Phase 1 above)
config/watchlist.example.yaml   Phase-0 watchlist format (raw snapshotting, no matching needed)
scripts/serve_dashboard.py   runs the interactive scanner (see Quickstart)
scripts/build_dashboard.py   static export of the data/matches.yaml track
run.sh                   one-command setup + test + interactive app
```

## Engineering notes

- All money math is integer cents or `PriceBp` (basis points of
  probability, 0-10000) or exact `Fraction` -- never `float`. Order-book
  resting sizes are `Fraction` too (both platforms return fractional
  sizes on real books).
- Every snapshot row a display would show is traceable to a specific
  `snapshots` table row, including the raw walked book (`raw_book_json`).
- `docs/api_notes.md` lists everything still flagged **UNVERIFIED**
  (notably Kalshi's exact fee-schedule multiplier -- the primary source
  was rate-limited during research) -- do not trade real money against
  those numbers until they're confirmed from a primary source.
- The discovery scanner's matching is a heuristic, not verification --
  see "How discovery works" above. Treat every row as a lead to check,
  not a confirmed pair; resolution-criteria differences (settlement
  source, exact date/timezone, tie rules) are exactly what Phase 1's
  "strict verification pass" in the original brief exists to catch, and
  that pass isn't implemented for discovered candidates.
