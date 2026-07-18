"""Pure normalization helpers used by every adapter.

Unit policy (D-003): flows are converted to shares here, once, and nowhere else.
Date policy: TWSE ROC-era dates are converted to Gregorian here.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from declan.config import validate_ticker
from declan.ingest.base import assert_schema


def roc_to_gregorian(roc: str) -> date:
    """Convert a Minguo (ROC) date string like '113/01/02' to date(2024, 1, 2)."""
    y, m, d = roc.strip().split("/")
    return date(int(y) + 1911, int(m), int(d))


def parse_number(value: str | float | int | None) -> float | None:
    """Parse TWSE-style numbers: comma thousands separators, '--' for missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = value.strip().replace(",", "")
    if s in ("", "--", "-"):
        return None
    return float(s)


def thousand_shares_to_shares(n: float) -> int:
    return int(round(n * 1000))


def finalize(df: pl.DataFrame, schema: dict[str, pl.DataType], name: str) -> pl.DataFrame:
    """Cast to canonical dtypes, dedupe on (ticker, date), sort, assert."""
    for t in df.get_column("ticker").unique().to_list():
        validate_ticker(t)
    out = (
        df.select([pl.col(c).cast(dtype) for c, dtype in schema.items()])
        .unique(subset=["ticker", "date"], keep="last")
        .sort(["ticker", "date"])
    )
    return assert_schema(out, schema, name)
