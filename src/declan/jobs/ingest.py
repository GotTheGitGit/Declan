"""Reusable ingest job (D-014): CLI and future scheduled jobs both call this.

Pipeline per (ticker, dataset):
  checkpoint lookup (ingest_log, D-008) -> fetch missing range -> canonical frame
  -> raw Parquet partitions (atomic, D-007) -> DuckDB upsert (idempotent)
  -> ingest_log record.
Then cross-source validation on a sample of closes (D-009).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import polars as pl

from declan.config import Paths
from declan.ingest.base import (
    FLOWS_SCHEMA,
    PRICES_SCHEMA,
    CloseSource,
    FlowSource,
    PriceSource,
    assert_schema,
)
from declan.store import db as store_db
from declan.store import parquet_io

log = logging.getLogger(__name__)

_DATASETS = {
    "prices": (PRICES_SCHEMA, "prices"),
    "flows": (FLOWS_SCHEMA, "institutional_flows"),
}


@dataclass
class ValidationResult:
    checked: int = 0
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


@dataclass
class IngestSummary:
    prices_rows: int = 0
    flows_rows: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validation: ValidationResult | None = None


def _checkpoint(
    conn: duckdb.DuckDBPyConnection, source: str, dataset: str, ticker: str
) -> date | None:
    """Latest successfully ingested end_date for (source, dataset, ticker)."""
    row = conn.execute(
        "SELECT max(end_date) FROM ingest_log "
        "WHERE source = ? AND dataset = ? AND ticker = ? AND status = 'ok'",
        [source, dataset, ticker],
    ).fetchone()
    return row[0] if row and row[0] else None


def _log_fetch(
    conn: duckdb.DuckDBPyConnection,
    source: str, dataset: str, ticker: str,
    start: date, end: date, rows: int, status: str, message: str = "",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_log "
        "(source, dataset, ticker, start_date, end_date, rows, status, message, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)",
        [source, dataset, ticker, start, end, rows, status, message],
    )


def run_ingest(
    paths: Paths,
    tickers: list[str],
    start: date,
    end: date,
    price_source: PriceSource,
    flow_source: FlowSource,
    *,
    validate_source: CloseSource | None = None,
    validate_sample: int = 5,
    force: bool = False,
) -> IngestSummary:
    """Ingest prices + flows for ``tickers`` over [start, end]. Idempotent."""
    summary = IngestSummary()
    with store_db.connect(paths.db_path) as conn:
        for ticker in tickers:
            for dataset, fetch in (("prices", price_source.fetch_prices),
                                   ("flows", flow_source.fetch_flows)):
                schema, table = _DATASETS[dataset]
                source_name = price_source.name if dataset == "prices" else flow_source.name
                fetch_start = start
                if not force:
                    cp = _checkpoint(conn, source_name, dataset, ticker)
                    if cp is not None:
                        if cp >= end:
                            summary.skipped.append(f"{ticker}/{dataset}")
                            continue
                        fetch_start = max(start, cp + timedelta(days=1))
                try:
                    df = fetch(ticker, fetch_start, end)
                    assert_schema(df, schema, f"{source_name}:{dataset}[{ticker}]")
                    if not df.is_empty():
                        parquet_io.write_partitions(df, paths.raw_dir, source_name, dataset)
                        store_db.upsert(conn, table, df)
                    _log_fetch(conn, source_name, dataset, ticker, fetch_start, end,
                               df.height, "ok")
                    if dataset == "prices":
                        summary.prices_rows += df.height
                    else:
                        summary.flows_rows += df.height
                except Exception as exc:  # noqa: BLE001 - keep going, record the failure
                    log.exception("ingest failed for %s/%s", ticker, dataset)
                    _log_fetch(conn, source_name, dataset, ticker, fetch_start, end, 0,
                               "error", str(exc))
                    summary.errors.append(f"{ticker}/{dataset}: {exc}")

        if validate_source is not None:
            summary.validation = _validate(
                conn, price_source.name, validate_source, sample=validate_sample
            )
    return summary


def _validate(
    conn: duckdb.DuckDBPyConnection,
    primary_name: str,
    secondary: CloseSource,
    *,
    sample: int,
    tolerance: float = 1e-6,
) -> ValidationResult:
    """Cross-check a random sample of stored closes against the secondary source."""
    result = ValidationResult()
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices").fetchall()]
    if not tickers:
        return result
    for ticker in random.sample(tickers, min(sample, len(tickers))):
        row = conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if row is None:
            continue
        d, close = row
        try:
            ref = secondary.fetch_closes(ticker, d, d)
        except Exception as exc:  # noqa: BLE001
            log.warning("validation fetch failed for %s: %s", ticker, exc)
            continue
        result.checked += 1
        if ref.is_empty():
            result.mismatches.append(
                {"ticker": ticker, "date": str(d), "primary": close, "secondary": None,
                 "note": f"{secondary.name} has no row"}
            )
            continue
        ref_close = ref.get_column("close")[0]
        if abs(ref_close - close) > tolerance:
            result.mismatches.append(
                {"ticker": ticker, "date": str(d), "primary": close, "secondary": ref_close,
                 "note": f"{primary_name} vs {secondary.name} close differs"}
            )
    return result


def rebuild(paths: Paths) -> dict[str, int]:
    """Rebuild the DuckDB file from the raw Parquet tree (D-007 guarantee)."""
    counts: dict[str, int] = {}
    with store_db.connect(paths.db_path) as conn:
        for source, dataset in parquet_io.list_sources(paths.raw_dir):
            if dataset not in _DATASETS:
                continue
            schema, table = _DATASETS[dataset]
            df = parquet_io.read_dataset(paths.raw_dir, source, dataset)
            if df is None:
                continue
            assert_schema(df, schema, f"rebuild:{source}/{dataset}")
            counts[table] = counts.get(table, 0) + store_db.upsert(conn, table, df)
    return counts


def load_holdings_positions(paths: Paths, holdings: list) -> int:
    """Snapshot config/holdings.yaml into the positions table (D-004)."""
    if not holdings:
        return 0
    df = pl.DataFrame(
        {
            "ticker": [h.ticker for h in holdings],
            "qty": [h.qty for h in holdings],
            "avg_cost": [h.avg_cost for h in holdings],
        }
    )
    with store_db.connect(paths.db_path) as conn:
        conn.execute("DELETE FROM positions")
        conn.register("_pos", df.to_arrow())
        conn.execute(
            "INSERT INTO positions (ticker, qty, avg_cost) "
            "SELECT ticker, qty, avg_cost FROM _pos"
        )
        conn.unregister("_pos")
    return df.height
