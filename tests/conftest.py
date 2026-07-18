"""Pytest fixtures only - reusable helpers live in tests/helpers.py."""

import polars as pl
import pytest

from tests.helpers import FIXTURES


@pytest.fixture
def prices_df() -> pl.DataFrame:
    return pl.read_parquet(FIXTURES / "prices_fixture.parquet")


@pytest.fixture
def flows_df() -> pl.DataFrame:
    return pl.read_parquet(FIXTURES / "flows_fixture.parquet")
