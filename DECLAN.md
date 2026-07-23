# Declan — Personal Finance Research Assistant

## What this is

Declan is a single-user, local-first stock research assistant for the Taiwan stock market (TWSE).
It is **not** a trading bot and **not** a return-guarantee engine. It is:

1. A data pipeline that ingests TWSE prices, institutional flows, and news daily
2. A backtesting engine that evaluates user-authored strategies against 3–5 years of history
3. An LLM analysis layer that produces a daily Markdown/HTML report and interprets news impact
4. A notification layer (ntfy.sh) that pushes the daily report and breaking-news alerts

Owner: Theo (IT / data analytics background). Runs on his local machine. One user, no auth needed.

## Core design principles

- **Deterministic where possible, LLM only where necessary.** All computation (indicators,
  backtests, P&L, screening) is plain Python. LLMs are used only for news filtering/scoring
  (cheap model) and daily synthesis/interpretation (strong model).
- **Domain logic lives in config, not code.** Strategies, analysis specs, and news rubrics are
  user-authored files under `config/` and `prompts/`. The engine interprets them. Never hardcode
  a trading rule or a news category into Python.
- **Batch, not streaming.** Everything runs on a scheduler after market close. No Kafka, no
  microservices, no message queues.
- **Every LLM output about markets must state uncertainty.** Reports explain mechanisms
  ("conflict → oil supply risk → energy up, airlines down"), never predict magnitude or timing
  as fact. Declan gives research, not financial advice.

## Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | uv for env/deps |
| Analytical store | DuckDB (single file `data/declan.duckdb`) + Parquet for raw history | columnar, zero-server |
| Dataframes | polars (preferred) or pandas | |
| Scheduler | APScheduler (long-running process) or cron | daily job after TWSE close |
| Price/flow data | FinMind (primary), TWSE OpenAPI (openapi.twse.com.tw) as fallback/cross-check | yfinance `.TW` only as last resort |
| News | FinMind Taiwan news + RSS feeds (list in `config/news_sources.yaml`) | |
| Cheap LLM (news filter) | claude-haiku-4-5 via Anthropic API | scores every headline against `prompts/news_rubric.md` |
| Strong LLM (daily analysis) | claude-sonnet-4-6 via Anthropic API | receives structured context bundle, writes report |
| Notifications | ntfy.sh (self-chosen topic, set in `.env`) | daily report link + high-impact alerts |
| Report rendering | Jinja2 HTML template (designed once in Stitch) + ECharts + TradingView lightweight-charts | Markdown source, HTML render |
| Dashboard (later) | Streamlit or FastAPI serving `reports/` | milestone 6, not v1 |

Secrets (`ANTHROPIC_API_KEY`, `FINMIND_TOKEN`, `NTFY_TOPIC`) live in `.env`, loaded via
python-dotenv. Never commit `.env`.

## TWSE market specifics (must be respected everywhere)

- Trading hours 09:00–13:30 Taipei time; run the daily job at 15:30 TST after data settles.
- Daily price limit ±10% from previous close. Backtests must treat limit-up/limit-down days as
  potentially unfillable (flag fills at limit prices).
- Board lot = 1,000 shares. Position sizing rounds to lots (odd-lot support optional, later).
- Settlement T+2.
- Institutional flow = 三大法人 daily net buy/sell (foreign 外資, investment trust 投信,
  dealers 自營商), published daily by TWSE — this is a first-class signal, store all three
  series separately.
- Fees for backtest realism: brokerage 0.1425% per side (assume common online discount ~28–60%
  configurable), securities transaction tax 0.3% on sells. Model both; make rates configurable
  in `config/costs.yaml`.
- Tickers stored as 4-digit TWSE codes (e.g. `2330`); map to `2330.TW` only at the yfinance
  adapter boundary.

## Repository layout

```
declan/
  DECLAN.md                  # this file
  pyproject.toml
  .env.example
  config/
    universe.yaml            # which tickers/index constituents to track
    costs.yaml               # fees, tax, slippage assumptions
    strategies/              # USER-AUTHORED strategy specs (YAML, see schema below)
    analysis_spec.md         # USER-AUTHORED: metrics + monthly/quarterly analysis definition
    news_sources.yaml        # RSS/API news feeds
    watchlist.yaml           # tickers to surface in reports beyond holdings (M2)
  prompts/
    news_rubric.md           # USER-AUTHORED: taxonomy + impact scoring rubric for filter model
    daily_analyst.md         # system prompt for the strong-model daily report
  src/declan/
    ingest/                  # finmind.py, twse_openapi.py, news_poller.py
    store/                   # duckdb schema, migrations, parquet io
    indicators/              # SMA/EMA/RSI/volume/flows, registry (pure functions)
    features/                # reusable feature layer: snapshot, regime, rankings (D-017)
    backtest/                # engine.py (event loop), portfolio.py, metrics.py
    strategy/                # yaml loader + rule interpreter
    llm/                     # anthropic client, news_filter.py, daily_analyst.py
    report/                  # jinja templates, chart builders (echarts json specs)
    notify/                  # ntfy.py
    jobs/                    # daily.py, monthly.py, quarterly.py, news_watch.py
    cli.py                   # `declan ingest`, `declan backtest <strategy>`, `declan report`, `declan run`
  templates/                 # HTML report template (from Stitch design) + assets
  reports/                   # generated daily/monthly reports (gitignored)
  data/                      # duckdb file + parquet (gitignored)
  tests/
```

## Data schema (DuckDB)

```sql
prices(ticker TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
       adj_close DOUBLE, volume BIGINT, PRIMARY KEY(ticker, date));

institutional_flows(ticker TEXT, date DATE,
       foreign_net_shares BIGINT, trust_net_shares BIGINT,       -- shares, signed
       dealer_net_shares BIGINT,                                 -- (docs/decisions.md D-003)
       PRIMARY KEY(ticker, date));

news(id TEXT PRIMARY KEY, published_at TIMESTAMP, source TEXT, headline TEXT, url TEXT,
     tickers TEXT[],            -- affected tickers per filter model
     category TEXT,             -- from user rubric taxonomy
     impact_score TINYINT,      -- 1-10 per rubric
     filter_rationale TEXT,     -- one-line reason from cheap model
     escalated BOOLEAN,         -- sent to strong model?
     full_analysis TEXT);       -- strong-model analysis if escalated

positions(ticker TEXT, qty BIGINT, avg_cost DOUBLE, opened_at DATE, closed_at DATE);

backtest_runs(run_id TEXT PRIMARY KEY, strategy TEXT, params JSON, start DATE, "end" DATE,
              cagr DOUBLE, sharpe DOUBLE, max_drawdown DOUBLE, win_rate DOUBLE,
              trades INT, report_path TEXT, created_at TIMESTAMP);

daily_reports(date DATE PRIMARY KEY, md_path TEXT, html_path TEXT, sent BOOLEAN);
```

Raw ingests also land as Parquet under `data/raw/{source}/{year}/` before loading, so the
DuckDB file can always be rebuilt.

## Strategy YAML schema (engine must interpret, not hardcode)

```yaml
name: trust_momentum_v1
universe: config/universe.yaml        # or inline list
entry:
  all:                                # boolean tree: all / any / not
    - indicator: sma_cross            # close crosses above SMA(60)
      params: {fast: 20, slow: 60, direction: above}
    - flow: trust_net                 # 投信 net buying N consecutive days
      params: {days: 3, direction: positive}
exit:
  any:
    - indicator: trailing_stop
      params: {pct: 12}
    - holding_days: {max: 60}
sizing: {method: equal_weight, max_positions: 8, cash_buffer_pct: 10}
rebalance: daily_signals              # or weekly / monthly
```

The interpreter maps `indicator:`/`flow:` names to pure functions in `src/declan/indicators/`.
Adding a new indicator = one function + registry entry. Backtest metrics required for every
run: CAGR, annualized Sharpe, max drawdown, win rate, turnover, total costs paid — as defined
in `config/analysis_spec.md`.

## LLM pipeline

1. **News watch job** (every 30 min during waking hours): poll feeds → dedupe → send batches of
   headlines to the cheap model with `prompts/news_rubric.md` → store category/score/rationale.
   Score ≥ threshold (configurable, default 7) → escalate to strong model for full analysis of
   impact channels on tracked tickers → push ntfy alert with headline + 2-line mechanism summary.
2. **Daily job** (15:30 TST): ingest prices + flows → compute indicators → mark positions to
   market → assemble context bundle (JSON: today's moves, holdings P&L, notable flows, top
   escalated news) → strong model with `prompts/daily_analyst.md` writes the report →
   render HTML with charts → ntfy push with summary + link/path.
3. **Monthly/quarterly jobs**: rerun screens over the universe, refresh backtests for all
   strategies in `config/strategies/`, produce a review report per `config/analysis_spec.md`.

Strong-model prompt rules (encode in `prompts/daily_analyst.md`): explain mechanisms, cite the
data given, flag uncertainty explicitly, never state predicted prices/returns as fact, always
include a "what would change this view" line.

## Build milestones (implement in order; each must work end-to-end before the next)

1. **Ingest + store**: FinMind price/flow ingestion → Parquet → DuckDB; `declan ingest`
   backfills 5 years for the universe; idempotent re-runs.
2. **Indicators + daily report v0**: compute indicators, plain-Markdown daily report of market
   + holdings (no LLM yet); `declan report`.
3. **ntfy delivery**: push daily report summary; `.env`-configured topic.
4. **Backtest engine**: YAML strategy interpreter, event loop with TWSE costs/limits,
   metrics, per-run report; `declan backtest <name>`.
5. **LLM layer**: news poller + cheap-model filter + escalation + strong-model daily analysis
   wired into the report.
6. **HTML reports + dashboard**: Jinja template from Stitch design, ECharts +
   lightweight-charts, optional Streamlit browser.

## Conventions for Claude Code

- Type hints everywhere; ruff + pytest; pure functions for indicators/metrics (easy to test).
- Every module that touches money math gets unit tests with hand-computed expected values.
- Never fabricate market data in tests silently — use small fixture Parquet files under `tests/fixtures/`.
- Ask before adding any new external dependency or paid API.
- Do not implement order execution / broker integration. Declan researches; Theo trades manually.
- All timestamps stored UTC; display in Asia/Taipei.
