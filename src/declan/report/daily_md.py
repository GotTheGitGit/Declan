"""Deterministic Markdown renderer for the daily report (M2, no LLM).

Takes a `DailyContext` and returns a Markdown string. Kept template-lite
(f-strings + a tiny table helper); Jinja/HTML arrives in M6.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

# ---- formatters -----------------------------------------------------------

def _pct(x, digits: int = 1) -> str:
    return "—" if x is None else f"{x*100:+.{digits}f}%"


def _num(x) -> str:
    return "—" if x is None else f"{x:,.2f}"


def _lots(shares) -> str:
    """Signed net shares -> 張 (1,000-share lots), thousands-separated."""
    if shares is None:
        return "—"
    return f"{shares/1000:+,.0f} 張"


def _int(x) -> str:
    return "—" if x is None else f"{int(x):+d}"


def _table(df: pl.DataFrame, headers: dict[str, tuple[str, Callable]]) -> str:
    """Render selected columns of `df`. `headers`: col -> (label, formatter)."""
    if df.is_empty():
        return "_none_\n"
    cols = list(headers)
    head = "| " + " | ".join(lbl for lbl, _ in headers.values()) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [head, sep]
    for row in df.to_dicts():
        cells = [fmt(row.get(c)) for c, (_, fmt) in headers.items()]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ---- sections -------------------------------------------------------------

def render(ctx) -> str:  # ctx: DailyContext
    r = ctx.regime
    p: list[str] = []
    p.append(f"# Declan Daily Report — {ctx.as_of.isoformat()}")
    p.append("")
    p.append(f"Universe: **{ctx.universe_name}** · Regime: **{r.label}**")
    p.append("")
    p.append("> Deterministic research summary. No forecasts; figures are computed "
             "from local market data. Not financial advice.")
    p.append("")

    # Market Regime
    p.append("## Market Regime")
    p.append("")
    p.append(f"**{r.label}** — {r.why()}. "
             f"Bull signals {r.bull_signals}/3, bear signals {r.bear_signals}/3.")
    p.append("")

    # Market Overview
    p.append("## Market Overview")
    p.append("")
    p.append(f"Breadth: {r.pct_above_sma20:.0%} above SMA20, "
             f"{r.pct_above_sma60:.0%} above SMA60 (n={r.breadth_n}).")
    p.append("")
    p.append("**Top gainers**")
    p.append(_table(ctx.gainers, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "ret_1": ("Day", _pct), "volume_z": ("Vol z", _num)}))
    p.append("**Top losers**")
    p.append(_table(ctx.losers, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "ret_1": ("Day", _pct), "volume_z": ("Vol z", _num)}))
    p.append("**Volume anomalies** (z > 2)")
    p.append(_table(ctx.volume_anomalies, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "ret_1": ("Day", _pct), "volume_z": ("Vol z", _num)}))
    p.append("**Near 52-week high**")
    p.append(_table(ctx.near_high, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "range_pos": ("Range pos", _num)}))
    p.append("**Near 52-week low**")
    p.append(_table(ctx.near_low, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "range_pos": ("Range pos", _num)}))

    # Institutional Flows
    p.append("## Institutional Flows (三大法人)")
    p.append("")
    p.append("**Top foreign (外資) net buys**")
    p.append(_table(ctx.top_foreign_buys, {
        "ticker": ("Ticker", str), "foreign_net": ("Foreign net", _lots),
        "foreign_streak": ("Streak", _int)}))
    p.append("**Top foreign (外資) net sells**")
    p.append(_table(ctx.top_foreign_sells, {
        "ticker": ("Ticker", str), "foreign_net": ("Foreign net", _lots),
        "foreign_streak": ("Streak", _int)}))
    p.append("**Foreign + trust (投信) both buying**")
    p.append(_table(ctx.both_bought, {
        "ticker": ("Ticker", str), "foreign_net": ("Foreign", _lots),
        "trust_net": ("Trust", _lots)}))
    p.append("**Active streaks (≥ 3 days)**")
    p.append(_table(ctx.active_streaks, {
        "ticker": ("Ticker", str), "foreign_streak": ("Foreign", _int),
        "trust_streak": ("Trust", _int)}))

    # Momentum ranking
    p.append("## Momentum Ranking")
    p.append("")
    p.append(_table(ctx.momentum_ranking, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "ret_20": ("20d", _pct), "ret_60": ("60d", _pct),
        "momentum_score": ("Score", _pct), "rsi14": ("RSI", _num)}))

    # Mean reversion candidates
    p.append("## Mean Reversion Candidates")
    p.append("")
    p.append("_Oversold: RSI < 35 and below SMA20, ranked by distance below SMA20._")
    p.append("")
    p.append(_table(ctx.mean_reversion, {
        "ticker": ("Ticker", str), "close": ("Close", _num),
        "rsi14": ("RSI", _num), "dist_sma20": ("vs SMA20", _pct),
        "mean_reversion_score": ("MR score", _pct)}))

    # Holdings
    p.append("## Holdings")
    p.append("")
    if not ctx.holdings:
        p.append("_No holdings configured (config/holdings.yaml)._")
        p.append("")
    else:
        p.append("| Ticker | Qty | Avg cost | Close | Day | Mkt value | "
                 "Unreal. P&L | Unreal. % | RSI | >SMA20 | Foreign streak |")
        p.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        total_mv = total_pnl = 0.0
        for h in ctx.holdings:
            total_mv += h.market_value or 0.0
            total_pnl += h.unrealized_pnl or 0.0
            p.append(
                f"| {h.ticker} | {h.qty:,} | {_num(h.avg_cost)} | {_num(h.close)} | "
                f"{_pct(h.day_move)} | {_num(h.market_value)} | {_num(h.unrealized_pnl)} | "
                f"{_pct(h.unrealized_pct)} | {_num(h.rsi14)} | "
                f"{'yes' if h.above_sma20 else 'no' if h.above_sma20 is not None else '—'} | "
                f"{_int(h.foreign_streak)} |"
            )
        p.append(f"| **Total** | | | | | **{_num(total_mv)}** | **{_num(total_pnl)}** | | | | |")
        p.append("")

    # Watchlist
    p.append("## Watchlist")
    p.append("")
    if ctx.watchlist.is_empty():
        p.append("_Watchlist empty (config/watchlist.yaml)._")
        p.append("")
    else:
        p.append(_table(ctx.watchlist, {
            "ticker": ("Ticker", str), "close": ("Close", _num),
            "ret_1": ("Day", _pct), "ret_20": ("20d", _pct),
            "rsi14": ("RSI", _num),
            "above_sma20": (">SMA20", lambda x: "yes" if x else "no" if x is not None else "—"),
            "momentum_score": ("Mom", _pct), "foreign_streak": ("Fgn streak", _int)}))

    # Data health
    p.append("## Data Health")
    p.append("")
    if not ctx.data_health:
        p.append("_No ingest log._")
    else:
        p.append("| Dataset | Last date | Rows | Stale (days) |")
        p.append("| --- | --- | --- | --- |")
        for d in ctx.data_health:
            stale = d["stale_days"]
            flag = " ⚠️" if (stale is not None and stale > 0) else ""
            p.append(f"| {d['dataset']} | {d['last']} | {d['rows']:,} | {stale}{flag} |")
    p.append("")
    p.append(f"_Generated deterministically from local data · as of {ctx.as_of.isoformat()}._")
    return "\n".join(p) + "\n"
