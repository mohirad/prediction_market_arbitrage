-- Snapshot store. SQLite for now; the schema avoids anything
-- timescale-specific so it can move to Postgres+Timescale later by
-- swapping the connection layer and adding a hypertable on (ts).
--
-- All prices/fees are stored as INTEGER (cents or pbp -- see arb.money).
-- Never store a REAL/FLOAT column for anything that represents money.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS markets (
    platform        TEXT    NOT NULL CHECK (platform IN ('kalshi', 'polymarket')),
    market_id       TEXT    NOT NULL,       -- kalshi ticker, or polymarket condition_id
    title           TEXT    NOT NULL,
    description     TEXT,
    resolution_text TEXT,                   -- resolution source / criteria, verbatim
    close_ts        INTEGER,                -- unix seconds, market close
    resolve_ts      INTEGER,                -- unix seconds, expected resolution
    category        TEXT,
    -- polymarket-specific: the two ERC-1155 token ids for this market
    yes_token_id    TEXT,
    no_token_id     TEXT,
    raw_json        TEXT    NOT NULL,       -- full API response, for auditability
    fetched_ts      INTEGER NOT NULL,
    PRIMARY KEY (platform, market_id)
);

-- One row per poll per market: top-of-book + depth bands + volume/OI.
-- This is the thing every displayed number must trace back to.
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,       -- unix ms, when this snapshot was taken
    platform        TEXT    NOT NULL CHECK (platform IN ('kalshi', 'polymarket')),
    market_id       TEXT    NOT NULL,
    -- Usually "yes"/"no", but the discovery scanner also snapshots
    -- non-binary-labeled outcomes (e.g. Polymarket team-vs-team markets
    -- have outcomes like "Giants"/"Rams") -- so this is the outcome
    -- label as reported by the platform, not a strict yes/no enum.
    side            TEXT    NOT NULL,
    best_bid_pbp    INTEGER,                -- probability basis points, 0-10000
    best_ask_pbp    INTEGER,
    mid_pbp         INTEGER,
    depth_1c_bid_c  INTEGER,                -- cents notional resting within 1c of best bid
    depth_1c_ask_c  INTEGER,
    depth_5c_bid_c  INTEGER,
    depth_5c_ask_c  INTEGER,
    depth_10c_bid_c INTEGER,
    depth_10c_ask_c INTEGER,
    volume_24h_c    INTEGER,                -- cents
    open_interest_c INTEGER,                -- cents
    raw_book_json   TEXT,                   -- full walked book, for replay/audit
    FOREIGN KEY (platform, market_id) REFERENCES markets(platform, market_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_ts
    ON snapshots (platform, market_id, side, ts);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts
    ON snapshots (ts);

-- Candlestick history pulled from each platform's native history endpoint,
-- used to backfill spread series in Phase 3 before live polling started.
CREATE TABLE IF NOT EXISTS candles (
    platform        TEXT    NOT NULL CHECK (platform IN ('kalshi', 'polymarket')),
    market_id       TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('yes', 'no')),
    period_start_ts INTEGER NOT NULL,       -- unix seconds, bucket start
    period_seconds  INTEGER NOT NULL,
    open_pbp        INTEGER,
    high_pbp        INTEGER,
    low_pbp         INTEGER,
    close_pbp       INTEGER,
    volume_c        INTEGER,
    raw_json        TEXT,
    PRIMARY KEY (platform, market_id, side, period_start_ts, period_seconds)
);

-- Approved/candidate matched pairs get mirrored here from the YAML file
-- (YAML stays the source of truth / reviewable diff; this table is just
-- so SQL joins against snapshots/candles are easy). Rewritten wholesale
-- whenever the YAML changes -- never hand-edited.
CREATE TABLE IF NOT EXISTS matched_pairs (
    pair_id             TEXT    PRIMARY KEY,   -- stable slug, matches YAML key
    kalshi_market_id    TEXT    NOT NULL,
    polymarket_market_id TEXT   NOT NULL,
    kalshi_yes_maps_to  TEXT    NOT NULL CHECK (kalshi_yes_maps_to IN ('yes', 'no')),
    confidence          REAL    NOT NULL,       -- match confidence score, not money -- REAL is fine here
    human_approved      INTEGER NOT NULL DEFAULT 0,
    yaml_version        TEXT    NOT NULL,
    notes               TEXT
);

-- One row per opportunity produced by a live /api/opportunities scan
-- (arb.arbitrage.pipeline.evaluate_pair), so every number the
-- Opportunities tab ever showed is traceable and can be analyzed later,
-- same principle as `snapshots` above. positions_json/payoff_matrix_json
-- hold the full recommended trade and state-by-state payoff table shown
-- on the detail page -- kept as JSON rather than further normalized
-- (world_states/payoff_matrices as their own tables) since nothing here
-- yet re-queries them relationally; revisit if/when it does.
CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                      INTEGER NOT NULL,       -- unix ms, when this opportunity was verified
    kalshi_market_id        TEXT    NOT NULL,
    polymarket_market_id    TEXT    NOT NULL,
    classification          TEXT    NOT NULL,
    confidence              REAL    NOT NULL,
    capital_required_c      INTEGER,                -- cents
    guaranteed_profit_c     INTEGER,                -- cents
    guaranteed_roi_bps      INTEGER,                -- guaranteed_roi * 10000
    max_profitable_shares   INTEGER,
    reject_reason           TEXT,
    positions_json          TEXT,
    payoff_matrix_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_arb_opportunities_ts
    ON arbitrage_opportunities (ts);

CREATE INDEX IF NOT EXISTS idx_arb_opportunities_markets
    ON arbitrage_opportunities (kalshi_market_id, polymarket_market_id, ts);

CREATE TABLE IF NOT EXISTS poll_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    platform    TEXT    NOT NULL,
    market_id   TEXT,
    endpoint    TEXT    NOT NULL,
    status_code INTEGER,
    message     TEXT
);
