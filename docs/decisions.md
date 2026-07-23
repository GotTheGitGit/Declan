# Declan — Decision Record

> **Append-only.** Records in this file must never be deleted or modified.
> To change a decision, add a new record that supersedes the old one and
> reference it explicitly (e.g. "Supersedes D-003").

---

## D-001 — Adjusted vs raw prices (2026-07-16)

**Decision:** Ingest both series. `close` = raw market close (ground truth, never
modified). `adj_close` = dividend/split-adjusted close (FinMind `TaiwanStockPriceAdj`).

**Rule:** Adjusted prices are the default for all return-based calculations (CAGR,
Sharpe, momentum, correlation, total return). Raw prices are used whenever actual
traded prices matter (fills, limit-up/down detection, position marking).

## D-002 — Universe definition (2026-07-16)

**Decision:** `config/universe.yaml` is typed: `type: static` (explicit ticker list)
or `type: index_constituents` (with `index` and `historical: true|false`). The engine
must not assume a static universe. Backtests use historical constituent lists when
available to avoid survivorship bias.

**M1 scope:** static universe (~0050 constituents). `index_constituents` resolution
is a later milestone; only the schema supports it now.

## D-003 — Institutional flow units (2026-07-16)

**Decision:** All sources are normalized to **shares**, once, inside the ingest
layer. Canonical columns: `foreign_net_shares`, `trust_net_shares`,
`dealer_net_shares` (signed). Renames the DECLAN.md draft columns (`foreign_net`
etc.) to make the unit explicit.

**Aggregation:** foreign = 外資及陸資 + 外資自營商; dealer = 自營商(自行買賣) + 自營商(避險).

## D-004 — Position ownership (2026-07-16)

**Decision:** Holdings are configuration-driven via `config/holdings.yaml`
(ticker → qty, avg_cost). No CRUD CLI in M1. May be replaced by a transaction
journal later.

## D-005 — Backfill scope (2026-07-16)

**Decision:** M1 universe is small (TWSE 50, ~50–100 stocks), 5-year backfill.
Architecture must support expanding to the full market. Rate-limit mitigation is
checkpointed incremental backfill (D-008), never repeated full downloads.

## D-006 — Trading calendar (2026-07-16)

**Decision:** No manually maintained holiday calendar. Market-open days are derived
from market data (index/universe series availability: a row exists ⇔ market open).
An official holiday source may be added later if needed.

## D-007 — Raw storage idempotency (2026-07-16)

**Decision:** DuckDB writes are always idempotent (`INSERT OR REPLACE` on primary
keys). Raw Parquet partitions — keyed `(source, year, dataset, ticker)` — are
overwritten **atomically** (write temp file, `os.replace`) when upstream restates
data. No raw-file versioning; simplicity over history for a single-user platform.

## D-008 — Ingest bookkeeping & checkpointed backfill (2026-07-16)

**Decision:** An `ingest_log` table records every fetch (source, dataset, ticker,
date range, rows, status, timestamp). Backfills are resumable: the job fetches only
ranges not already logged as successful, unless `--force`.

## D-009 — Cross-source validation (2026-07-16)

**Decision:** After each ingest, a validation step cross-checks a sample of
(ticker, date) closes between FinMind and TWSE as first-class pipeline behavior,
logging mismatches. Skippable explicitly (`--no-validate`) when offline.

## D-010 — Full schema created in M1 (2026-07-16)

**Decision:** All DuckDB tables (including `news`, `backtest_runs`, `daily_reports`,
`research_runs`) are created in M1 even though only `prices`, `institutional_flows`,
and `ingest_log` receive data. Avoids migration machinery for a single-user file DB.
A `schema_version` table tracks DDL version.

## D-011 — Canonical dataframe schema assertions (2026-07-16)

**Decision:** The canonical column/type contract is defined once in code
(`src/declan/ingest/base.py`) and asserted at every boundary: adapter output,
Parquet write, DuckDB load. Silent column drift from API changes must fail loudly.

## D-012 — Single-writer DB guard (2026-07-16)

**Decision:** DuckDB is single-writer; writers acquire a lock file next to the DB
file. Read paths open read-only without the lock. Prevents scheduler-vs-CLI
corruption later.

## D-013 — `research_runs` table (2026-07-16)

**Decision:** Separate from `backtest_runs` (performance), `research_runs` stores
research **context** for reproducibility: hypothesis, objective, strategy name and
version, universe, data version, date range, notes. Every research experiment gets
a record. Human-readable counterpart: `docs/research_log.md`.

## D-014 — Reusable ingest job (2026-07-16)

**Decision:** Ingestion logic lives in `src/declan/jobs/ingest.py`, called by both
the CLI (`declan ingest`) and, later, scheduled jobs (`jobs/daily.py`). Scheduled
jobs compose job functions; they never duplicate them.

## D-015 — Adjusted prices are best-effort on free FinMind tier (2026-07-18)

**Context:** FinMind's `TaiwanStockPriceAdj` dataset returns HTTP 400 on
non-sponsor tokens; `TaiwanStockPrice` works on the free tier. Discovered during
the first live M1 smoke test.

**Decision:** The FinMind adapter treats the Adj dataset as best-effort: on a
permission-style 4xx it logs one warning, disables further Adj requests for the
run, and falls back to `adj_close = close` (refines D-001; raw close remains
ground truth). The ingest run must not fail because of Adj availability.

**Consequence:** Until the token is upgraded (or adjustment is computed locally
from `TaiwanStockDividend`), `adj_close` equals raw close, so return-based
metrics ignore dividends/splits across ex-dates. Revisit before backtesting (M4)
— options: FinMind sponsor tier, or a local adjustment-factor builder.

## D-016 — Research-first is permanent (2026-07-19)

**Decision:** Order execution and broker integration are permanently out of the
core. Declan's responsibility ends at research, rankings, portfolio analysis,
backtests, and reports. Execution, if ever built, is an optional external plugin,
never a core dependency. (Confirms and hardens the DECLAN.md stance.)

## D-017 — Indicators are primitives; features are the reusable layer (2026-07-19)

**Decision:** `indicators/` holds primitive building blocks (SMA, EMA, RSI, raw
flow ops). A `features/` layer composes them into reusable, strategy-facing
features (e.g. `distance_to_sma20`, `momentum_score`, `volume_ratio`,
`institutional_flow_strength`, `market_regime`). Strategies and reports consume
features, not ad-hoc recomputation. M2 introduces the split; it grows over time.

## D-018 — Factor-model architecture is the post-M4 direction (2026-07-19)

**Decision:** The long-term data flow is Market Data → Indicators → Features →
Factor Models → Strategies → Backtests → Portfolio Analysis. Factors (Momentum,
Mean Reversion, Value, Quality, Volatility) become independent modules; strategies
are combinations of factor outputs rather than bespoke calculations. Introduced as
a concept after M4; not built in M2. Direction only.

## D-019 — Daily report classifies market regime deterministically (2026-07-19)

**Decision:** The daily report includes a Market Regime classification
(Bull / Neutral / Bear) computed by deterministic rules (no LLM). It is a
first-class feature (`market_regime`) reusable by later strategies, not just report
text. (Answers roadmap Q-1 / M2 recommendation.)

## D-020 — Universe: current TWSE 50 now, point-in-time later (2026-07-19)

**Decision:** `config/universe.yaml` is refreshed to the official current TWSE 50
(0050) constituent list. Implementation may use today's membership only; the
`index_constituents` typed schema (D-002) is retained so point-in-time historical
membership can be added before full-market survivorship-bias-free backtests.
(Answers Q-2.)

## D-021 — Scheduler is APScheduler (2026-07-19)

**Decision:** M3 uses an in-application APScheduler process (`declan run`) rather
than OS cron/launchd — portable across macOS/Linux/Windows, easier to test, matches
DECLAN.md. (Answers Q-3.)

## D-022 — Adjusted prices via local dividend adjustment (2026-07-19)

**Decision:** Build `adj_close` locally from FinMind's dividend/rights dataset
(free tier) via a pure adjustment-factor function, replacing the D-015 fallback
(`adj_close == close`). Avoids vendor lock-in and yields reproducible research; a
paid adjusted-price source could later swap in as the adjustment source. Built in
M4 (before backtest returns depend on it). (Answers Q-4; supersedes the D-015
stopgap once implemented.)

## D-023 — Backtest fills at T+1 open, no look-ahead (2026-07-19)

**Decision:** Signals computed at T close execute at T+1 open. No same-close fills.
Realism over flattering results; look-ahead bias is disallowed engine-wide.
(Answers Q-5.)

## D-024 — Sharpe risk-free rate configurable, default TW 1y deposit (2026-07-19)

**Decision:** Annualized Sharpe uses a configurable risk-free rate (in `costs.yaml`
or `analysis_spec.md`), defaulting to the Taiwan 1-year time-deposit rate;
overridable to zero. (Answers Q-6.)

## D-025 — Every LLM markets output states confidence + assumptions + invalidation (2026-07-19)

**Decision:** All LLM analysis (news and daily) must include explicit confidence,
its assumptions, and what would invalidate the view — in addition to the
mechanism-not-magnitude and "what would change this view" rules already in the
spec. Enforced in prompts and validated on output. (Answers M5 recommendation;
extends the DECLAN.md LLM rules.)

## D-026 — LLM daily budget cap, deterministic fallback (2026-07-19)

**Decision:** A configurable daily LLM spend cap (default NT$30/day) is enforced by
a cost ledger (`llm_calls` table). When the cap is hit, further LLM calls stop and
the pipeline still produces the full deterministic report. (Answers Q-8.)

## D-027 — Report chart assets are vendored for offline viewing (2026-07-19)

**Decision:** M6 HTML reports vendor their JS chart libraries (ECharts,
lightweight-charts) into the repo rather than loading from CDN, so daily reports
render fully offline. Repo-size increase accepted. (Answers Q-10.)

## D-028 — Dashboard deferred (2026-07-19)

**Decision:** The browser dashboard is explicitly last priority: Research Engine →
Backtest Engine → LLM → Dashboard. It is visualization, not core; built only after
the research pipeline is solid, if at all. (Answers Q-11.)

## D-029 — M4 ships two demo strategies (2026-07-19)

**Decision:** M4 validates the backtest engine with both `trust_momentum_v1` and
`mean_reversion_v1`, exercising opposite signal classes (trend-following vs
reversion) for stronger coverage. (Answers M4 recommendation.)
