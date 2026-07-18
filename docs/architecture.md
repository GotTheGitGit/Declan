# Declan — System Architecture

> Living document. Update freely as the system evolves; locked decisions live in
> `decisions.md` (append-only). See `DECLAN.md` for build conventions.

## Overview

Declan is a local, single-user, batch-driven research pipeline for TWSE stocks.
Daily after market close it ingests prices, institutional flows (三大法人), and news;
stores them durably; computes deterministic indicators; and produces a report.
LLMs sit only at the interpretation edge. Nothing trades.

**Core stance: the engine is generic, the domain knowledge is data.** Strategies,
costs, universe, news rubric, and analysis specs are user-authored files under
`config/` and `prompts/`; Python interprets them.

## Layers

1. **Ingestion** (`src/declan/ingest/`) — one adapter per source, each normalizing
   to a canonical schema. Raw responses land as Parquet (immutable audit trail),
   then load into DuckDB (rebuildable at any time).
2. **Computation** (`indicators/`, `backtest/`) — pure functions; dataframes in,
   dataframes out; no I/O. Unit-tested against hand-computed values.
3. **Interpretation** (`llm/`) — LLMs consume structured context bundles assembled
   by deterministic code; never raw DB access.
4. **Delivery** (`report/`, `notify/`) — Jinja2 Markdown/HTML rendering, ntfy push.

Orchestration (`jobs/` + scheduler) wires layers together; `cli.py` is a thin
entry point over the same job functions.

## Data flow (Milestone 1 scope marked)

```
                        ┌─────────────── MILESTONE 1 ───────────────┐
 FinMind API ──┐        │   ingest/finmind.py                       │
 TWSE OpenAPI ─┼──────► │   ingest/twse_openapi.py (cross-check)    │
               │        │        │ normalize to canonical schema    │
               │        │        ▼                                  │
               │        │   data/raw/{source}/{year}/{dataset}/     │  immutable raw
               │        │        │ idempotent upsert                │
               │        │        ▼                                  │
               │        │   data/declan.duckdb                      │
               │        │   (prices, institutional_flows,           │
               │        │    ingest_log)                            │
               │        └───────┬───────────────────────────────────┘
               │                │
             ┌─┴────────────────┼──────────────────────┐
             ▼                  ▼                      ▼
   indicators/ (M2)     backtest/ engine (M4)    LLM context bundle (M5)
             │                  │                      │
             └────────┬─────────┴──────────────────────┘
                      ▼
             report/ (M2/M6)  ──►  notify/ntfy.py (M3)  ──►  Theo's phone
```

## Storage

- **Raw Parquet:** `data/raw/{source}/{year}/{dataset}/{ticker}.parquet`.
  Partitions are overwritten atomically (tmp file + `os.replace`) on restatement.
  DuckDB is always rebuildable from this tree (`declan rebuild`).
- **DuckDB:** single file `data/declan.duckdb`. All writes `INSERT OR REPLACE` on
  primary keys → idempotent. Full schema (all tables) created at first run;
  `schema_version` table tracks DDL version. Writers hold a lock file
  (`data/declan.duckdb.lock`); readers open read-only.

### Tables (M1 populates the first three)

| Table | Purpose |
|---|---|
| `prices` | OHLCV + `adj_close` per (ticker, date). `close` raw truth; `adj_close` for return math (D-001) |
| `institutional_flows` | `foreign_net_shares`, `trust_net_shares`, `dealer_net_shares` — signed shares (D-003) |
| `ingest_log` | every fetch: source, dataset, ticker, range, rows, status → checkpointed backfill (D-008) |
| `news` | filtered/scored news (M5) |
| `positions` | holdings snapshot, loaded from `config/holdings.yaml` (D-004) |
| `backtest_runs` | performance metrics per run (M4) |
| `research_runs` | research context: hypothesis, objective, strategy version, universe, data version (D-013) |
| `daily_reports` | report registry (M2+) |

## Canonical dataframe contracts

Defined once in `src/declan/ingest/base.py`, asserted at every boundary
(adapter output → parquet write → duckdb load), per D-011:

- `PRICES_SCHEMA`: ticker (str), date, open, high, low, close, adj_close (float), volume (int)
- `FLOWS_SCHEMA`: ticker (str), date, foreign_net_shares, trust_net_shares, dealer_net_shares (int)

Tickers are 4-digit TWSE codes everywhere; `.TW` suffixes only inside the
yfinance adapter boundary (not built in M1).

## Ingestion pipeline (M1)

`jobs/ingest.py::run_ingest` — reusable by CLI and future scheduled jobs:

1. Load universe (`config/universe.yaml`, typed static/index_constituents — D-002).
2. Per (ticker, dataset): consult `ingest_log` for the last successful end date;
   fetch only the missing range (checkpointed backfill, D-008; `--force` refetches).
3. Adapter fetch → parse → normalize (units to shares, ROC dates → Gregorian,
   dedupe, sort) → schema assert.
4. Write raw Parquet partitions (atomic overwrite) → upsert DuckDB → log to
   `ingest_log`.
5. Validation (D-009): sample (ticker, date) closes cross-checked against the
   secondary source; mismatches reported. `--no-validate` to skip offline.

Sources are injected as objects satisfying `PriceSource`/`FlowSource` protocols so
tests run fully offline against fixtures.

## Module map (M1)

| Module | Responsibility |
|---|---|
| `config.py` | paths, `.env`, typed loaders for universe/holdings/costs |
| `ingest/base.py` | canonical schemas, source protocols, `assert_schema` |
| `ingest/finmind.py` | FinMind client (prices, adj prices, flows), rate limit + retry, parsers |
| `ingest/twse_openapi.py` | TWSE endpoints for fallback/cross-check, ROC-date parsing |
| `ingest/normalize.py` | pure cleaning helpers: dates, numbers, tickers, unit conversion, finalize |
| `store/schema.py` | full DDL + `create_all` + schema version |
| `store/db.py` | connection factory, write lock, `upsert` helper |
| `store/parquet_io.py` | atomic partition write/read, raw-tree iteration |
| `jobs/ingest.py` | orchestration, checkpointing, validation, rebuild |
| `cli.py` | `declan ingest / rebuild / status` |

## TWSE specifics enforced in code

Trading hours 09:00–13:30 TST, daily job at 15:30 TST (scheduler, M2+). ±10% daily
price limit — backtests must flag limit-day fills (M4). Board lot 1,000 shares.
T+2 settlement. Fees/tax modeled from `config/costs.yaml`. Timestamps stored UTC,
displayed Asia/Taipei. Trading calendar derived from market data, not maintained
by hand (D-006).
