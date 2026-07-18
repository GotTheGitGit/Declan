"""DuckDB schema: full DDL created in M1 (D-010).

Only ``prices``, ``institutional_flows`` and ``ingest_log`` receive data in M1;
the rest are created now to avoid migration machinery later.
"""

from __future__ import annotations

import duckdb

SCHEMA_VERSION = 1

DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prices (
        ticker TEXT, date DATE,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        adj_close DOUBLE, volume BIGINT,
        PRIMARY KEY (ticker, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS institutional_flows (
        ticker TEXT, date DATE,
        foreign_net_shares BIGINT,   -- signed shares (D-003)
        trust_net_shares BIGINT,
        dealer_net_shares BIGINT,
        PRIMARY KEY (ticker, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_log (
        source TEXT, dataset TEXT, ticker TEXT,
        start_date DATE, end_date DATE,
        rows INTEGER, status TEXT, message TEXT,
        fetched_at TIMESTAMP DEFAULT current_timestamp,
        PRIMARY KEY (source, dataset, ticker, start_date, end_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news (
        id TEXT PRIMARY KEY, published_at TIMESTAMP, source TEXT,
        headline TEXT, url TEXT,
        tickers TEXT[], category TEXT, impact_score TINYINT,
        filter_rationale TEXT, escalated BOOLEAN, full_analysis TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        ticker TEXT, qty BIGINT, avg_cost DOUBLE,
        opened_at DATE, closed_at DATE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        run_id TEXT PRIMARY KEY, strategy TEXT, params JSON,
        start DATE, "end" DATE,
        cagr DOUBLE, sharpe DOUBLE, max_drawdown DOUBLE, win_rate DOUBLE,
        trades INTEGER, report_path TEXT, created_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_runs (
        run_id TEXT PRIMARY KEY,
        hypothesis TEXT, objective TEXT,
        strategy_name TEXT, strategy_version TEXT,
        universe TEXT, data_version TEXT,
        start_date DATE, end_date DATE,
        notes TEXT, created_at TIMESTAMP DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_reports (
        date DATE PRIMARY KEY, md_path TEXT, html_path TEXT, sent BOOLEAN
    )
    """,
]


def create_all(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every table (idempotent) and record the schema version."""
    for stmt in DDL:
        conn.execute(stmt)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION]
    )
