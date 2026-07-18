"""Canonical dataframe contracts and source protocols (D-011).

Every adapter must return frames matching these schemas exactly; the contract is
asserted again before Parquet writes and DuckDB loads so silent column drift
from upstream API changes fails loudly.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import polars as pl

PRICES_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,      # raw market close - ground truth, never modified (D-001)
    "adj_close": pl.Float64,  # dividend/split adjusted - default for return math (D-001)
    "volume": pl.Int64,       # shares
}

FLOWS_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "foreign_net_shares": pl.Int64,  # signed shares (D-003)
    "trust_net_shares": pl.Int64,
    "dealer_net_shares": pl.Int64,
}


class SchemaError(ValueError):
    pass


def assert_schema(df: pl.DataFrame, schema: dict[str, pl.DataType], name: str) -> pl.DataFrame:
    """Fail loudly if ``df`` deviates from the canonical contract."""
    expected_cols = list(schema.keys())
    if df.columns != expected_cols:
        raise SchemaError(
            f"{name}: columns {df.columns} != canonical {expected_cols}"
        )
    for col, dtype in schema.items():
        if df.schema[col] != dtype:
            raise SchemaError(
                f"{name}: column {col!r} has dtype {df.schema[col]}, expected {dtype}"
            )
    return df


class PriceSource(Protocol):
    """Adapter that yields canonical PRICES_SCHEMA frames."""

    name: str

    def fetch_prices(self, ticker: str, start: date, end: date) -> pl.DataFrame: ...


class FlowSource(Protocol):
    """Adapter that yields canonical FLOWS_SCHEMA frames."""

    name: str

    def fetch_flows(self, ticker: str, start: date, end: date) -> pl.DataFrame: ...


class CloseSource(Protocol):
    """Minimal interface used for cross-source validation (D-009)."""

    name: str

    def fetch_closes(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        """Return frame with columns: ticker (str), date (Date), close (f64)."""
        ...
