
# Declan — Milestone Roadmap (M2–M6)

> Working plan, updated as milestones complete. Locked decisions go to
> `decisions.md`; this file says what gets built next and in what order.
> Source spec: `DECLAN.md`. Status: M1 shipped 2026-07-18 (5y backfill,
> 50 tickers, validated against TWSE).
>
> Open questions needing Theo's input are marked **[Q-n]** and collected at
> the bottom for easy answering.

> **Reviewed 2026-07-19** (`response.md`): all milestones approved, no restructuring.
> Q-1..Q-11 answered and locked as D-016..D-029. Adopted changes: report gains
> Market Regime + Watchlist + Momentum Ranking + Mean Reversion Candidates (D-019);
> indicators split into a reusable feature layer (D-017); factor-model direction
> post-M4 (D-018); universe refreshed to official TWSE 50 (D-020); M4 ships two
> demo strategies (D-029). See `decisions.md` for the rest.

---

## M2 — Indicators + daily report v0 (no LLM)

**Goal:** `declan report` produces a useful plain-Markdown daily briefing of
market + holdings, fully offline from the local DB.

### New modules

| Module                     | Responsibility                                                                                                                                                                                 |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `indicators/registry.py` | `@indicator("sma")` decorator registry: name → pure function. This registry is the contract M4's strategy YAML interpreter will bind to — names chosen here become the strategy vocabulary |
| `indicators/price.py`    | `sma`, `ema`, `rsi` (Wilder), `returns` (1d/5d/20d/YTD), `high_low_52w` (proximity to 52-week high/low), `volume_zscore` (vs 60-day mean), `drawdown_from_peak`                  |
| `indicators/flows.py`    | `flow_streak` (N consecutive net-buy/sell days per investor type), `flow_sum` (rolling N-day net shares), `flow_vs_volume` (net shares / total volume — conviction proxy)               |
| `indicators/calendar.py` | trading calendar derived from price-data presence (D-006):`trading_days(start, end)`, `last_trading_day()`, `is_trading_day(date)`                                                       |
| `report/daily_md.py`     | render the v0 report from a`DailyContext` dataclass (deterministic, template-lite: f-strings/`textwrap`, no Jinja until M6)                                                                |
| `jobs/report.py`         | assemble`DailyContext`: read DB → compute indicators → mark holdings to market → write `reports/daily/YYYY-MM-DD.md` → upsert `daily_reports` row                                    |

All indicator functions: polars in/out, no I/O, unit-tested against hand-computed
values (money-math convention).

### Report v0 contents (one Markdown file per trading day)

1. **Market overview** — universe breadth (advancers/decliners), top 5 gainers/losers,
   volume anomalies (z-score > 2), names within 2% of 52w high/low.
2. **Institutional flows** — top foreign/trust net buys+sells (shares and % of volume),
   active streaks ≥ 3 days, any name where foreign & trust bought together **[Q-1]**.
3. **Holdings** — per position (from `config/holdings.yaml` → `positions` table):
   last close, day move, market value, unrealized P&L (vs `avg_cost`, raw close per
   D-001), RSI/SMA20/SMA60 posture, flow streak on the name.
4. **Data health** — last ingest date per dataset, gap warnings vs trading calendar,
   validation status.

### CLI

- `declan report [--date YYYY-MM-DD]` — defaults to last trading day; errors clearly
  if data for that date is missing (prompting an ingest).
- `declan indicators <ticker>` — quick table of current indicator values (debug aid).

### Tests / acceptance

- Hand-computed RSI(14)/SMA/EMA/z-score fixtures; flow streak edge cases (sign flips,
  gaps); holdings P&L arithmetic; golden-file test for the full report render.
- **Done when:** fresh clone + backfilled DB → `declan report` writes today's report
  in < 5 s, all offline; `pytest` green.

### Carry-over items folded into M2

- Load `holdings.yaml` → `positions` table as part of the report job (M1 built the
  loader but nothing calls it yet).
- Verify `config/universe.yaml` against the official 0050 list **[Q-2]**.

---

## M3 — Scheduling + ntfy delivery

**Goal:** the daily pipeline runs itself at 15:30 TST and Theo's phone gets the summary.

### New modules

| Module             | Responsibility                                                                                                                                                                                                               |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `notify/ntfy.py` | POST to`https://ntfy.sh/{NTFY_TOPIC}`: title, body, priority, tags; retry w/ backoff; never raises into the pipeline (failed push logged, report still written)                                                            |
| `jobs/daily.py`  | compose existing jobs: ingest (incremental) → report → mark`daily_reports.sent` → push summary (top movers + holdings P&L one-liner + report path). On ingest failure: push an error alert instead of silently skipping |
| `scheduler.py`   | APScheduler cron`Mon–Fri 15:30 Asia/Taipei`, guarded by `is_trading_day()` so holidays no-op **[Q-3]**                                                                                                            |

### CLI

- `declan run` — start the scheduler (foreground process; launchd/pm2 wrapper is
  Theo's choice).
- `declan notify --test` — send a test push to verify topic wiring.
- `declan daily` — run the whole daily job once, manually.

### Tests / acceptance

- Fake ntfy transport (assert payloads); daily-job orchestration test with fake
  sources; holiday no-op test.
- **Done when:** `declan daily` end-to-end on a real day pushes a summary to the
  phone; `declan run` left running fires it automatically the next trading day.

New dependency to approve: `apscheduler` **[Q-3]**.

---

## M4 — Backtest engine

**Goal:** `declan backtest trust_momentum_v1` evaluates a user-authored YAML strategy
over 5 years with TWSE-realistic costs and produces a run report + DB records.

### Prerequisite decision — adjusted prices (D-015 gap)

Backtest returns are wrong across ex-dividend dates while `adj_close == close`.
Options **[Q-4]**:
  a. Sponsor FinMind tier (paid) → real `TaiwanStockPriceAdj`.
  b. Build local adjustment factors from FinMind's dividend dataset (free tier) —
     one extra ingest dataset + a pure `adjust.py`; my recommendation.
  c. Accept the error for v1 (fine for short holding periods, bad for 60-day holds).

### New modules

| Module                      | Responsibility                                                                                                                                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strategy/loader.py`      | YAML → typed`Strategy` dataclasses; validate indicator names against the M2 registry, params against function signatures; reject unknown keys loudly                                                                            |
| `strategy/interpreter.py` | evaluate the`all/any/not` boolean tree per (ticker, date) over precomputed indicator frames → entry/exit signal masks                                                                                                           |
| `backtest/engine.py`      | daily event loop: signals at T close → execute at**T+1 open** **[Q-5]**; skip fills on limit-locked days (±10% rule, flagged in trade log); board-lot rounding; `max_positions`, `cash_buffer_pct`, equal weight |
| `backtest/portfolio.py`   | cash, positions, trade ledger, mark-to-market equity curve                                                                                                                                                                         |
| `backtest/metrics.py`     | CAGR, annualized Sharpe (√252, rf=0**[Q-6]**), max drawdown, win rate, turnover, total costs paid — each a pure function w/ hand-computed tests                                                                            |
| `backtest/report.py`      | per-run Markdown: config echo, equity curve stats, trade list, cost breakdown →`reports/backtests/`                                                                                                                             |

Costs per `config/costs.yaml`: brokerage both sides × discount, 0.3% tax on sells,
slippage bps. Every run writes `backtest_runs` + a `research_runs` row (hypothesis
prompted interactively or via `--hypothesis`, D-013).

### Config

- `config/strategies/trust_momentum_v1.yaml` — first real strategy, matching the
  DECLAN.md schema exactly (needs `sma_cross` + `trailing_stop` + `holding_days`
  added to the indicator/exit vocabulary).
- `config/analysis_spec.md` — skeleton for Theo to author: metric definitions +
  monthly review format **[Q-7]**.

### Tests / acceptance

- Golden 10-day hand-computed scenario (entries, exits, fees, equity curve to the
  cent); limit-day unfillable test; lot-rounding test; YAML validation errors.
- **Done when:** the example strategy backtests over 2021–2026 in < 30 s with a
  plausible, cost-inclusive result and a readable run report.

---

## M5 — LLM layer (news + daily analysis)

**Goal:** news is filtered/scored automatically; the daily report gains an
AI-written interpretation section; high-impact news pushes alerts.

### New modules

| Module                    | Responsibility                                                                                                                                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ingest/news_poller.py` | FinMind Taiwan news + RSS from`config/news_sources.yaml`; dedupe by URL hash; store raw → `news` table (unfiltered rows first)                                                                               |
| `llm/client.py`         | thin Anthropic API wrapper: model names from config, token/cost accounting per call logged to a`llm_calls` table (schema addition), hard daily budget cap **[Q-8]**                                       |
| `llm/news_filter.py`    | batch headlines → claude-haiku-4-5 +`prompts/news_rubric.md` → category/impact/rationale (strict JSON); score ≥ 7 → escalate                                                                                |
| `llm/daily_analyst.py`  | build context bundle (JSON: moves, flows, holdings P&L, escalated news) → claude-sonnet-4-6 +`prompts/daily_analyst.md` → report section. Bundle assembly is deterministic code; the LLM never touches the DB |
| `jobs/news_watch.py`    | every 30 min 08:00–22:00 TST; escalated items → ntfy alert with 2-line mechanism summary                                                                                                                        |

### Prompts (drafted by me, then user-authored per spec)

- `prompts/news_rubric.md` — taxonomy (earnings, tech-cycle, macro, geopolitics,
  regulation, company-specific …), 1–10 impact anchors, tracked-universe emphasis.
- `prompts/daily_analyst.md` — mechanism-not-magnitude rules, cite-the-data,
  explicit uncertainty, mandatory "what would change this view" line.

### Tests / acceptance

- Mocked API tests (no live calls in CI); JSON-parse robustness; budget-cap kill
  switch; escalation threshold config.
- **Done when:** a full day runs: news collected + scored all day, 15:30 report
  includes the AI section grounded in real data, phone gets alerts only for
  score ≥ 7 items, and total LLM spend for the day is visible in `llm_calls`.

New dependencies to approve: `anthropic`, `feedparser` **[Q-9]**.

---

## M6 — HTML reports + dashboard

**Goal:** the daily report becomes a self-contained HTML page with charts; optional
local browser dashboard.

- `report/html.py` — Jinja2 render of the same `DailyContext` (+ AI section) into
  `templates/daily.html`; candlesticks via lightweight-charts, flow/indicator charts
  via ECharts (CDN or vendored assets **[Q-10]**); one self-contained file per day.
- Template design pass in Stitch first (per spec), then encode.
- Backtest run reports get the same treatment (equity curve, drawdown chart).
- Optional: `declan dashboard` — Streamlit read-only browser over `reports/` +
  DuckDB (universe screener table, position charts) **[Q-11]**.
- ntfy messages link to the HTML file path.

**Done when:** daily HTML renders offline with interactive charts and the phone
notification opens into something pleasant to read.

New dependencies to approve: `jinja2`, optionally `streamlit`.

---

## Deliberately deferred (backlog, no milestone)

- `index_constituents` universe resolution + point-in-time membership (D-002) —
  needed before backtesting the full market without survivorship bias; not needed
  while the universe is the static 0050 list.
- Transaction journal replacing `holdings.yaml` (D-004 successor).
- Full-market expansion (~1,000 tickers) — checkpointed backfill already supports it.
- Odd-lot support; intraday anything; broker integration (never, per spec).

---

## Open questions for Theo

- **[Q-1]** M2 report: any sections to add/cut? (e.g. sector grouping? TAIEX index
  overview line? watchlist beyond holdings?)
- **[Q-2]** Should I fetch the official current 0050 constituent list and correct
  `universe.yaml` as part of M2?
- **[Q-3]** Scheduler: long-running APScheduler process (`declan run`, new dependency)
  or a plain macOS `launchd`/cron entry calling `declan daily`? Latter = zero new
  deps and survives reboots; former matches the spec.
- **[Q-4]** Adjusted prices: sponsor FinMind (a), build local dividend adjustment (b,
  recommended), or defer (c)?
- **[Q-5]** Backtest fill assumption: T+1 open (recommended, honest) or T close
  (common in naive backtests, flattering)?
- **[Q-6]** Sharpe risk-free rate: 0 (simple) or TW 1y deposit rate (configurable)?
- **[Q-7]** `config/analysis_spec.md` is user-authored per spec — want me to draft a
  skeleton for you to edit in M4, or will you write it from scratch?
- **[Q-8]** LLM daily budget cap: propose NT$30/day-ish (~US$1) default — acceptable?
- **[Q-9]** OK to add `anthropic` + `feedparser` deps in M5? (spec pre-approves the
  Anthropic API conceptually, flagging per the dependency convention anyway)
- **[Q-10]** M6 charts: CDN links (needs internet to view) or vendored JS assets
  (bigger repo, fully offline)?
- **[Q-11]** Dashboard: Streamlit yes/no/later?
