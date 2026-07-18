# Declan — Research Log

> Human-readable companion to the `research_runs` table. One entry per experiment.
> Newest entries on top. Entries reference `run_id` so results in DuckDB and
> reports under `reports/` can be traced back to the hypothesis that produced them.

## Entry template

```
## [run_id] — <short title> (YYYY-MM-DD)
- **Hypothesis:**   what do I believe and why?
- **Objective:**    what would confirm/refute it?
- **Strategy:**     name @ version (config/strategies/…)
- **Universe:**     universe name / file
- **Data version:** ingest snapshot or date range used
- **Period:**       start → end
- **Result:**       link to backtest_runs / report; key metrics
- **Conclusion:**   keep / discard / iterate — and why
- **Notes:**        anything surprising
```

---

*(no experiments yet — Milestone 1 is data infrastructure)*
