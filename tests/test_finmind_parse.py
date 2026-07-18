from datetime import date

import polars as pl

from declan.ingest.base import FLOWS_SCHEMA, PRICES_SCHEMA, assert_schema
from declan.ingest.finmind import parse_flows, parse_prices
from tests.helpers import load_json


def test_parse_prices_joins_adjusted_series():
    df = parse_prices(
        load_json("finmind_price_2330.json"),
        load_json("finmind_price_adj_2330.json"),
        "2330",
    )
    assert_schema(df, PRICES_SCHEMA, "parsed")
    assert df.height == 3
    d2 = df.row(by_predicate=pl.col("date") == date(2024, 1, 2), named=True)
    # raw close is ground truth; adj_close comes from the Adj dataset (D-001)
    assert d2["close"] == 593.0 and d2["adj_close"] == 583.0
    assert d2["high"] == 594.0 and d2["low"] == 588.0 and d2["volume"] == 25_000_000


def test_parse_prices_adj_fallback_to_raw_close():
    # fixture has no Adj row for 2024-01-04 -> falls back to raw close
    df = parse_prices(
        load_json("finmind_price_2330.json"),
        load_json("finmind_price_adj_2330.json"),
        "2330",
    )
    d4 = df.row(by_predicate=pl.col("date") == date(2024, 1, 4), named=True)
    assert d4["adj_close"] == d4["close"] == 580.0


def test_parse_prices_empty():
    df = parse_prices([], [], "2330")
    assert df.is_empty() and df.columns == list(PRICES_SCHEMA)


def test_parse_flows_hand_computed_nets():
    df = parse_flows(load_json("finmind_flows_2330.json"), "2330")
    assert_schema(df, FLOWS_SCHEMA, "parsed")
    d2 = df.row(by_predicate=pl.col("date") == date(2024, 1, 2), named=True)
    # hand-computed (D-003 aggregation):
    # foreign = (20,000,000-15,000,000) + (100,000-40,000) = 5,060,000
    # trust   = 3,000,000-1,000,000 = 2,000,000
    # dealer  = (500,000-700,000) + (250,000-150,000) = -100,000
    assert d2["foreign_net_shares"] == 5_060_000
    assert d2["trust_net_shares"] == 2_000_000
    assert d2["dealer_net_shares"] == -100_000
    d3 = df.row(by_predicate=pl.col("date") == date(2024, 1, 3), named=True)
    # foreign = 1,000,000-2,000,000 = -1,000,000; trust = 0; dealer = 50,000
    assert d3["foreign_net_shares"] == -1_000_000
    assert d3["trust_net_shares"] == 0
    assert d3["dealer_net_shares"] == 50_000


def test_fetch_prices_degrades_when_adj_dataset_forbidden(monkeypatch):
    """Sponsor-tier-only Adj dataset (D-015): 4xx -> raw-close fallback, once per run."""
    from declan.ingest import finmind as fm

    client = fm.FinMindClient(token="t")
    calls = []

    def fake_get(dataset, ticker, start, end):
        calls.append(dataset)
        if dataset == "TaiwanStockPrice":
            return load_json("finmind_price_2330.json")
        raise fm.FinMindPermissionError("TaiwanStockPriceAdj/2330: HTTP 400 - tier")

    monkeypatch.setattr(client, "_get", fake_get)

    df = client.fetch_prices("2330", date(2024, 1, 2), date(2024, 1, 4))
    assert df.height == 3
    # every adj_close fell back to the raw close
    assert (df.get_column("adj_close") == df.get_column("close")).all()

    # second ticker in the same run: Adj is not even attempted anymore
    calls.clear()
    client.fetch_prices("2330", date(2024, 1, 2), date(2024, 1, 4))
    assert calls == ["TaiwanStockPrice"]
