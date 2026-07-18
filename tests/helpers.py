"""Reusable test utilities: fixture loading + offline fake sources.

Plain module (not conftest) so tests import it explicitly:
    from tests.helpers import load_json, FakePriceSource
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakePriceSource:
    """Serves canonical fixture rows filtered to the requested range; records calls."""

    name = "finmind"

    def __init__(self, df: pl.DataFrame):
        self.df = df
        self.calls: list[tuple[str, date, date]] = []

    def fetch_prices(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        self.calls.append((ticker, start, end))
        return self.df.filter(
            (pl.col("ticker") == ticker) & pl.col("date").is_between(start, end)
        )


class FakeFlowSource:
    name = "finmind"

    def __init__(self, df: pl.DataFrame):
        self.df = df
        self.calls: list[tuple[str, date, date]] = []

    def fetch_flows(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        self.calls.append((ticker, start, end))
        return self.df.filter(
            (pl.col("ticker") == ticker) & pl.col("date").is_between(start, end)
        )


class FakeCloseSource:
    """Cross-check source; optionally shifts closes to force mismatches."""

    name = "twse"

    def __init__(self, df: pl.DataFrame, shift: float = 0.0):
        self.df = df
        self.shift = shift

    def fetch_closes(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        return self.df.filter(
            (pl.col("ticker") == ticker) & pl.col("date").is_between(start, end)
        ).select("ticker", "date", (pl.col("close") + self.shift).alias("close"))
