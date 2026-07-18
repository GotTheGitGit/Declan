from datetime import date

import polars as pl
import pytest

from declan.config import ConfigError
from declan.ingest.base import PRICES_SCHEMA, SchemaError, assert_schema
from declan.ingest.normalize import (
    finalize,
    parse_number,
    roc_to_gregorian,
    thousand_shares_to_shares,
)


def test_roc_to_gregorian():
    assert roc_to_gregorian("113/01/02") == date(2024, 1, 2)
    assert roc_to_gregorian("99/12/31") == date(2010, 12, 31)


def test_parse_number():
    assert parse_number("25,000,000") == 25_000_000.0
    assert parse_number("593.00") == 593.0
    assert parse_number("--") is None
    assert parse_number(None) is None
    assert parse_number(42) == 42.0


def test_thousand_shares_to_shares():
    assert thousand_shares_to_shares(1.5) == 1500


def test_finalize_dedupes_sorts_and_casts(prices_df):
    dup = pl.concat([prices_df, prices_df.head(3)])
    out = finalize(dup, PRICES_SCHEMA, "t")
    assert out.height == prices_df.height
    assert out.get_column("date").is_sorted() or out.get_column("ticker").n_unique() > 1
    # sorted within ticker
    for t in ("2317", "2330"):
        assert out.filter(pl.col("ticker") == t).get_column("date").is_sorted()


def test_finalize_rejects_bad_ticker(prices_df):
    bad = prices_df.with_columns(pl.lit("2330.TW").alias("ticker"))
    with pytest.raises(ConfigError):
        finalize(bad, PRICES_SCHEMA, "t")


def test_assert_schema_catches_drift(prices_df):
    with pytest.raises(SchemaError):
        assert_schema(prices_df.drop("adj_close"), PRICES_SCHEMA, "t")
    with pytest.raises(SchemaError):
        assert_schema(
            prices_df.with_columns(pl.col("volume").cast(pl.Float64)), PRICES_SCHEMA, "t"
        )
