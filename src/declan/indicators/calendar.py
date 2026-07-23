"""Trading calendar derived from market data, not a maintained holiday list (D-006).

A date is a trading day iff the DB has price rows for it. These helpers read the
`prices` table so gap detection can distinguish "market closed" from "missing data".
"""

from __future__ import annotations

from datetime import date

import duckdb


def trading_days(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> list[date]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM prices WHERE date BETWEEN ? AND ? ORDER BY date",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def last_trading_day(
    conn: duckdb.DuckDBPyConnection, on_or_before: date | None = None
) -> date | None:
    if on_or_before is None:
        row = conn.execute("SELECT max(date) FROM prices").fetchone()
    else:
        row = conn.execute(
            "SELECT max(date) FROM prices WHERE date <= ?", [on_or_before]
        ).fetchone()
    return row[0] if row and row[0] else None


def is_trading_day(conn: duckdb.DuckDBPyConnection, d: date) -> bool:
    row = conn.execute("SELECT 1 FROM prices WHERE date = ? LIMIT 1", [d]).fetchone()
    return row is not None


def previous_trading_day(conn: duckdb.DuckDBPyConnection, d: date) -> date | None:
    row = conn.execute("SELECT max(date) FROM prices WHERE date < ?", [d]).fetchone()
    return row[0] if row and row[0] else None
