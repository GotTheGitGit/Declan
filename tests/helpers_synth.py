"""Deterministic synthetic market data for feature/report tests.

Generates ~300 trading days for a handful of tickers with distinct, predictable
shapes (uptrend, downtrend, oversold-dip) so regime/ranking/reversion logic has
something well-characterised to assert against. No randomness, no real data.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

START = date(2024, 1, 1)
N = 300


def _weekdays(n: int, start: date = START) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(kind: str) -> list[float]:
    base = 100.0
    out = []
    for i in range(N):
        if kind == "up":
            v = base + i * 0.5                      # steady uptrend
        elif kind == "down":
            v = base - i * 0.25                     # steady downtrend
        elif kind == "dip":
            v = base + i * 0.3                      # uptrend...
            if i >= N - 8:
                v -= (i - (N - 9)) * 4.0            # ...sharp recent dip (oversold)
        else:
            v = base
        out.append(round(v, 2))
    return out


def build_frame(shapes: dict[str, str]) -> pl.DataFrame:
    """shapes: ticker -> kind ('up'|'down'|'dip'|'flat'). Returns prices+flows long frame."""
    dates = _weekdays(N)
    frames = []
    for ticker, kind in shapes.items():
        closes = _series(kind)
        n = len(closes)
        frames.append(pl.DataFrame({
            "ticker": [ticker] * n, "date": dates,
            "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "adj_close": closes,
            "volume": [1_000_000 + (i % 5) * 10_000 for i in range(n)],
            "foreign_net_shares":
                [(1 if kind == "up" else -1) * 100_000 for _ in range(n)],
            "trust_net_shares": [(1 if kind in ("up", "dip") else -1) * 50_000 for _ in range(n)],
            "dealer_net_shares": [0] * n,
        }))
    return pl.concat(frames).sort(["ticker", "date"])


def load_into_db(paths, frame: pl.DataFrame) -> None:
    """Write the synthetic frame into a fresh Declan DB (prices + flows + ingest_log)."""
    from declan.store import db as store_db
    prices = frame.select("ticker", "date", "open", "high", "low", "close", "adj_close", "volume")
    flows = frame.select(
        "ticker", "date",
        "foreign_net_shares", "trust_net_shares", "dealer_net_shares")
    last = frame.get_column("date").max()
    first = frame.get_column("date").min()
    with store_db.connect(paths.db_path) as conn:
        store_db.upsert(conn, "prices", prices)
        store_db.upsert(conn, "institutional_flows", flows)
        for ds, n in (("prices", prices.height), ("flows", flows.height)):
            conn.execute(
                "INSERT OR REPLACE INTO ingest_log "
                "(source,dataset,ticker,start_date,end_date,rows,status) "
                "VALUES ('finmind',?,'ALL',?,?,?, 'ok')", [ds, first, last, n])
