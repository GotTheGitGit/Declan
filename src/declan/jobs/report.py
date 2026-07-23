"""Daily report job (M2): assemble a deterministic DailyContext from the DB and
render it to Markdown. No LLM. Read-only against DuckDB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from declan import config as cfg
from declan.features import regime as rg
from declan.features import snapshot as snap
from declan.indicators import calendar as cal
from declan.store import db as store_db

# History needed for the longest feature window (52w range = 252 bars) + slack.
_LOOKBACK_DAYS = 420


@dataclass
class HoldingLine:
    ticker: str
    qty: int
    avg_cost: float
    close: float | None
    day_move: float | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None
    rsi14: float | None
    above_sma20: bool | None
    foreign_streak: int | None


@dataclass
class DailyContext:
    as_of: date
    universe_name: str
    regime: rg.Regime
    snapshot: pl.DataFrame
    gainers: pl.DataFrame
    losers: pl.DataFrame
    volume_anomalies: pl.DataFrame
    near_high: pl.DataFrame
    near_low: pl.DataFrame
    top_foreign_buys: pl.DataFrame
    top_foreign_sells: pl.DataFrame
    both_bought: pl.DataFrame
    active_streaks: pl.DataFrame
    momentum_ranking: pl.DataFrame
    mean_reversion: pl.DataFrame
    holdings: list[HoldingLine]
    watchlist: pl.DataFrame
    data_health: list[dict] = field(default_factory=list)


def _load_price_flow(conn, tickers: list[str], start: date, end: date) -> pl.DataFrame:
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"""
        SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.adj_close, p.volume,
               f.foreign_net_shares, f.trust_net_shares, f.dealer_net_shares
        FROM prices p
        LEFT JOIN institutional_flows f USING (ticker, date)
        WHERE p.ticker IN ({placeholders}) AND p.date BETWEEN ? AND ?
        ORDER BY p.ticker, p.date
        """,
        [*tickers, start, end],
    ).arrow()
    return pl.from_arrow(rows)


def _holding_lines(
    holdings: list[cfg.Holding], snapshot: pl.DataFrame, price_flow: pl.DataFrame, as_of: date
) -> list[HoldingLine]:
    lines: list[HoldingLine] = []
    snap_by_ticker = {r["ticker"]: r for r in snapshot.to_dicts()}
    for h in holdings:
        s = snap_by_ticker.get(h.ticker)
        hist = price_flow.filter(pl.col("ticker") == h.ticker).sort("date")
        close = s["close"] if s else (hist.get_column("close")[-1] if hist.height else None)
        prev = hist.get_column("close")[-2] if hist.height >= 2 else None
        day_move = (close / prev - 1.0) if (close and prev) else None
        mv = h.qty * close if close else None
        pnl = (mv - h.qty * h.avg_cost) if mv is not None else None
        pct = (close / h.avg_cost - 1.0) if close else None
        lines.append(
            HoldingLine(
                ticker=h.ticker, qty=h.qty, avg_cost=h.avg_cost, close=close,
                day_move=day_move, market_value=mv, unrealized_pnl=pnl,
                unrealized_pct=pct,
                rsi14=s["rsi14"] if s else None,
                above_sma20=s["above_sma20"] if s else None,
                foreign_streak=s["foreign_streak"] if s else None,
            )
        )
    return lines


def _data_health(conn, as_of: date) -> list[dict]:
    rows = conn.execute(
        "SELECT dataset, max(end_date) AS last, sum(rows) AS rows "
        "FROM ingest_log WHERE status='ok' GROUP BY dataset ORDER BY dataset"
    ).fetchall()
    out = []
    for dataset, last, rows_n in rows:
        stale_days = (as_of - last).days if last else None
        out.append({"dataset": dataset, "last": last, "rows": rows_n, "stale_days": stale_days})
    return out


def build_context(paths: cfg.Paths, as_of: date | None = None) -> DailyContext:
    universe = cfg.load_universe(paths.config_dir / "universe.yaml")
    uni_tickers = universe.resolve()
    holdings = cfg.load_holdings(paths.config_dir / "holdings.yaml")
    watch = cfg.load_watchlist(paths.config_dir / "watchlist.yaml")
    all_tickers = sorted({*uni_tickers, *(h.ticker for h in holdings), *watch})

    if not paths.db_path.exists():
        raise RuntimeError(
            "no database found - run `declan ingest` before `declan report`"
        )
    with store_db.connect(paths.db_path, read_only=True) as conn:
        resolved = cal.last_trading_day(conn, as_of)
        if resolved is None:
            raise RuntimeError(
                "no price data for the requested date - run `declan ingest` first"
            )
        as_of = resolved
        start = as_of - timedelta(days=_LOOKBACK_DAYS)
        price_flow = _load_price_flow(conn, all_tickers, start, as_of)
        health = _data_health(conn, as_of)

    # Universe-only snapshot drives market/regime/rankings; holdings/watchlist
    # features are pulled from a snapshot over all loaded tickers.
    snap_all = snap.feature_snapshot(price_flow, as_of)
    snap_uni = snap_all.filter(pl.col("ticker").is_in(uni_tickers))

    regime = rg.classify_regime(snap_uni)
    gainers = snap_uni.sort("ret_1", descending=True, nulls_last=True).head(5).select(
        "ticker", "close", "ret_1", "volume_z")
    losers = snap_uni.sort("ret_1", nulls_last=True).head(5).select(
        "ticker", "close", "ret_1", "volume_z")
    vol_anom = snap_uni.filter(pl.col("volume_z") > 2).sort("volume_z", descending=True).select(
        "ticker", "close", "ret_1", "volume_z")
    near_high = snap_uni.filter(pl.col("range_pos") >= 0.98).sort(
        "range_pos", descending=True).select("ticker", "close", "range_pos")
    near_low = snap_uni.filter(pl.col("range_pos") <= 0.02).sort(
        "range_pos").select("ticker", "close", "range_pos")

    fb = snap_uni.filter(pl.col("foreign_net").is_not_null())
    top_fb = fb.sort("foreign_net", descending=True).head(5).select(
        "ticker", "foreign_net", "foreign_streak")
    top_fs = fb.sort("foreign_net").head(5).select("ticker", "foreign_net", "foreign_streak")
    both = snap_uni.filter(
        (pl.col("foreign_net") > 0) & (pl.col("trust_net") > 0)
    ).sort("foreign_net", descending=True).head(8).select(
        "ticker", "foreign_net", "trust_net")
    streaks = snap_uni.filter(
        (pl.col("foreign_streak").abs() >= 3) | (pl.col("trust_streak").abs() >= 3)
    ).sort("foreign_streak", descending=True).select(
        "ticker", "foreign_streak", "trust_streak")

    momentum = rg.momentum_ranking(snap_uni, top=10)
    mean_rev = rg.mean_reversion_candidates(snap_uni, top=10)

    holding_lines = _holding_lines(holdings, snap_all, price_flow, as_of)
    watch_snap = snap_all.filter(pl.col("ticker").is_in(watch)).select(
        "ticker", "close", "ret_1", "ret_20", "rsi14", "above_sma20",
        "momentum_score", "foreign_streak") if watch else snap_all.head(0)

    return DailyContext(
        as_of=as_of, universe_name=universe.name, regime=regime,
        snapshot=snap_uni, gainers=gainers, losers=losers, volume_anomalies=vol_anom,
        near_high=near_high, near_low=near_low, top_foreign_buys=top_fb,
        top_foreign_sells=top_fs, both_bought=both, active_streaks=streaks,
        momentum_ranking=momentum, mean_reversion=mean_rev, holdings=holding_lines,
        watchlist=watch_snap, data_health=health,
    )


def write_report(paths: cfg.Paths, as_of: date | None = None) -> Path:
    from declan.report import daily_md

    ctx = build_context(paths, as_of)
    md = daily_md.render(ctx)
    out_dir = paths.root / "reports" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ctx.as_of.isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
