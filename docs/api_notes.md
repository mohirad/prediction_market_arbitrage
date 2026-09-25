# API verification notes

Captured 2026-09-20 by live doc lookups (not training data) against
docs.kalshi.com / help.kalshi.com and docs.polymarket.com, **then live
smoke-tested against the real production APIs** (see "Live smoke-test
corrections" below — several doc-derived details above didn't match
actual production responses and were fixed in the client code). Re-verify
the items still flagged **UNVERIFIED** before trusting them for real
money — code built against an unverified fact has a
`# UNVERIFIED, see docs/api_notes.md` comment next to it.

## Live smoke-test corrections (2026-09-20, real API calls, not docs)

These caught real bugs that the doc-only research pass missed:

- Kalshi `GET /markets/{ticker}/orderbook` wraps the book under the key
  **`orderbook_fp`**, not `orderbook`.
- Kalshi `status=open` as a query filter returns markets whose `status`
  *field* reads **`"active"`**, not `"open"` -- filter values and the
  returned status vocabulary are not the same strings. Don't compare
  `market["status"] == "open"` anywhere.
- Kalshi orderbook level quantities (`yes_dollars`/`no_dollars` second
  element) are **not always whole numbers** -- observed real resting
  sizes like `"8.50"` and `"42.50"`. Same for Polymarket CLOB book sizes,
  observed up to 2 decimal places (e.g. `"10465279.8"`). Both clients now
  parse sizes via `Fraction(str)` (exact, never floored/truncated) and
  `arb.orderbook.Level.size` is `Fraction`, not `int`.
- Kalshi candlesticks response shape confirmed live: `end_period_ts`,
  `open_interest_fp`, `volume_fp`, and nested `price` / `yes_bid` /
  `yes_ask` objects each with `open_dollars`/`high_dollars`/
  `low_dollars`/`close_dollars`(+`mean_dollars` on `price`) -- richer
  than the flat OHLC the doc-only pass guessed at.
- Polymarket `GET /midpoint` response key is **`mid`**, not `mid_price`
  as claimed by the doc-only research pass (e.g. `{"mid":"0.0375"}`).
- Polymarket `GET /prices-history` `history[].p` is returned as a **JSON
  number**, not a string, in practice -- client converts via `repr()`
  before parsing to avoid float artifacts (safe because Python's `repr`
  of a float parsed from short-decimal JSON round-trips exactly).
- Polymarket `clobTokenIds` position (YES-first) is confirmed workable in
  practice by cross-checking against the `outcomes` field (`["Yes","No"]`
  in the observed live sample) -- the client does this cross-check and
  returns `None` for both token ids if the labels don't confirm order,
  rather than guessing.
- Kalshi WS: connecting to `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
  **without signed `KALSHI-ACCESS-*` headers on the handshake gets an
  immediate HTTP 401**, even when only subscribing to market-data channels
  (`ticker`, `orderbook_delta`). This resolves the earlier
  public-vs-private UNVERIFIED flag: treat the whole WS connection as
  requiring auth. Not exercised past the handshake requirement itself
  (no real API key/private key pair available in this environment) --
  the subscribe flow after a successful signed handshake is still
  unverified end-to-end.

## Kalshi

- Base URL: `https://external-api.kalshi.com/trade-api/v2` (demo:
  `https://external-api.demo.kalshi.co/trade-api/v2`).
- WS URL: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`.
- `GET /markets`: `cursor` + `limit` (default 100, **max 1000**) pagination.
  `status` accepts `unopened|open|paused|closed|settled`.
- `GET /markets/{ticker}/orderbook`: returns `orderbook_fp` with
  `yes_dollars` / `no_dollars` level arrays only (no separate ask array — a
  yes bid at $0.15 implies a no ask at $0.85). Each level is
  `[dollars_string, fp_quantity_string]`, e.g. `["0.1500", "100.00"]`.
  **Prices are dollar strings, not integer cents** — Kalshi's 2026
  fixed-point/tapered-pricing migration allows off-cent ticks (as fine as
  $0.0001) near the 0/1 edges. Do not assume 1-99c integer range anywhere;
  parse with `arb.money.decimal_str_to_pbp`.
- `GET /events`: `limit` (default/max 200), `cursor`, `with_nested_markets`,
  `with_milestones`, `status`, `series_ticker`, `tickers`, `min_close_ts`,
  `min_updated_ts`.
- `GET /series/{series_ticker}`: series (recurring-event template) metadata.
- Candlesticks: `GET /series/{series_ticker}/markets/{ticker}/candlesticks`.
  `period_interval` in **minutes**: `1|60|1440`. OHLC now dollar-string
  fixed point (`open_dollars` etc.), plus `volume_fp`, `open_interest_fp`.
  Exact full field list **UNVERIFIED** (pieced together, not a direct
  fetched schema) — re-check response shape against a live call.
- WS subscribe: `{"id":2,"cmd":"subscribe","params":{"channels":["orderbook_delta"],"market_tickers":["TICKER"]}}`.
  `orderbook_delta` and `ticker` channels both confirmed to exist.
  Public/private classification of `orderbook_delta` **UNVERIFIED**.
- Signing (private endpoints): headers `KALSHI-ACCESS-KEY`,
  `KALSHI-ACCESS-TIMESTAMP` (ms), `KALSHI-ACCESS-SIGNATURE`. String-to-sign
  = `timestamp + METHOD + path` (uppercase method, path with no query
  string). RSA-PSS, SHA-256, `salt_length=PSS.DIGEST_LENGTH`. RSA key
  bit-length **UNVERIFIED** (docs don't state it — don't hardcode 2048).
- Fees: **UNVERIFIED against a primary source** — the official fee-schedule
  PDF and `kalshi.com/fee-schedule` both returned HTTP 429 on every fetch
  attempt during this check. Secondary sources consistently give
  `fee = ceil(0.07 * C * P * (1-P))` dollars/contract, with a halved 0.035
  multiplier for S&P/Nasdaq index series, and maker fee = 0. Implemented
  with these values but gated behind an explicit `verified=False` flag and
  a runtime warning — **do not go live on real fee math until this is
  confirmed from kalshi.com/fee-schedule directly.**
- Rate limits (REST, tokens/sec by tier): Basic 200 read / 100 write,
  Advanced 300/300, Expert 600/600, Premier 1000/1000, Paragon 2000/2000,
  Prime 4000/4000, Prestige 10000/8000. Most calls cost 10 tokens each. 429
  with no `Retry-After` header — our backoff must not depend on that
  header for Kalshi. WS-specific rate limits **UNVERIFIED**.

## Polymarket

- Gamma base: `https://gamma-api.polymarket.com`. `GET /markets`:
  `limit`, `offset`, `closed`, `active`. Fields: `clobTokenIds` (JSON
  encoded **as a string**, must `json.loads`), `question`, `description`,
  `resolutionSource`, `endDate`, `active`, `closed`, `outcomes`,
  `outcomePrices`, `makerBaseFee`/`takerBaseFee` (bps, nullable).
  `GET /events` mirrors this with `title`, `markets[]`, `tags[]`, etc.
  **UNVERIFIED**: whether `clobTokenIds[0]` is reliably YES — no primary
  quote confirms ordering. Do not rely on position; the CLOB market
  response's `tokens[]` array carries an explicit `outcome` label
  (`"Yes"`/`"No"`) — use that instead once verified live.
- CLOB base: `https://clob.polymarket.com`. `GET /book?token_id=...`
  returns `bids`/`asks` as **arrays of `{"price": "0.45", "size": "100"}`
  objects**, not bare `[price, size]` pairs — parse accordingly. Also
  carries `tick_size`, `min_order_size`, `neg_risk`, `last_trade_price`.
  `GET /midpoint?token_id=...` → `{"mid_price": "0.45"}`.
  `GET /prices-history?market=<token_id>&startTs=&endTs=&interval=&fidelity=`
  → `{"history": [{"t": <unix_ts>, "p": <price>}]}`. **PARTIALLY VERIFIED**:
  one source suggests this is migrating to `GET /v2/prices-history` on
  `data-api.polymarket.com` with different params/pagination — confirm
  which host is authoritative before depending on long history pulls.
- Fees: maker fee = 0 (documented: "Makers are never charged fees").
  Taker fee = `C * feeRate * p * (1-p)`, feeRate by category: crypto 7%,
  sports 5%, finance/politics/mentions/tech 4%, economics/culture/weather
  5%, geopolitics/world-events 0% (fee-free). Prefer reading the per-market
  `makerBaseFee`/`takerBaseFee` bps fields directly over hardcoding the
  category table, and treat the table as a fallback/cross-check only. Min
  fee $0.00001, else waived. Source: docs.polymarket.com/trading/fees.
- Non-fee costs: Polymarket's Relayer makes approvals/transfers/order
  placement gasless when using a Relayer API key / deposit wallet. Direct
  EOA on-chain actions still cost Polygon gas (~$0.01-0.05/tx per
  third-party estimates — **not from official docs**). No documented
  deposit/withdrawal fee for USDC on Polygon; bridging to mainnet (not
  needed for normal use) is the costly path, magnitude **UNVERIFIED**.
- Rate limits (per-IP via Cloudflare): Gamma general 4000/10s, `/events`
  500/10s, `/markets` 300/10s. Data API v1 general 1000/10s, v2 general
  800/10s, `/v2/prices-history` 200/10s. CLOB order placement 5000/10s
  burst, 120000/10min sustained.
- Auth: public market/book data needs no credentials. Orders require L1
  (EIP-712 wallet signature minting L2 creds) + L2 HMAC-SHA256 request
  signing with headers `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`,
  `POLY_API_KEY`, `POLY_PASSPHRASE`.
- Prices: decimal strings 0-1, confirmed across `/book`, `/midpoint`,
  `/prices-history`.

## Action items before any real-money use

1. Re-fetch `kalshi.com/fee-schedule` directly (was rate-limited, not
   permanently blocked) and confirm the 0.07 / 0.035 multipliers and that
   maker fee is 0.
2. Confirm Kalshi RSA key bit-length requirement from a real key-creation
   flow.
3. Confirm Polymarket `clobTokenIds` YES/NO ordering (or switch fully to
   the CLOB `tokens[].outcome` label, once its schema is verified live).
4. Confirm which Polymarket price-history endpoint/host is current.
