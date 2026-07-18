from datetime import date

import polars as pl

from declan.ingest.twse_openapi import parse_stock_day
from tests.helpers import load_json


def test_parse_stock_day_roc_dates_and_commas():
    df = parse_stock_day(load_json("twse_stock_day_2330.json"), "2330")
    # third row has close '--' and is dropped
    assert df.height == 2
    d2 = df.row(by_predicate=pl.col("date") == date(2024, 1, 2), named=True)
    assert d2["close"] == 593.0 and d2["volume"] == 25_000_000


def test_parse_stock_day_empty():
    df = parse_stock_day({}, "2330")
    assert df.is_empty()
