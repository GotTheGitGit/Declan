"""Generate canonical fixture Parquet files. All values synthetic + hand-chosen.

Run from repo root:  PYTHONPATH=src python tests/fixtures/make_fixtures.py
"""

from datetime import date
from pathlib import Path

import polars as pl

from declan.ingest.base import FLOWS_SCHEMA, PRICES_SCHEMA
from declan.ingest.normalize import finalize

HERE = Path(__file__).parent

DAYS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4),
        date(2024, 1, 5), date(2024, 1, 8)]  # 01-06/07 = weekend


def main() -> None:
    prices = pl.DataFrame(
        {
            "ticker": ["2330"] * 5 + ["2317"] * 5,
            "date": DAYS * 2,
            "open":  [590.0, 592.0, 587.0, 582.0, 585.0, 104.0, 104.5, 105.0, 104.0, 106.0],
            "high":  [594.0, 593.0, 588.0, 586.0, 589.0, 105.0, 105.5, 105.5, 106.0, 107.0],
            "low":   [588.0, 586.0, 578.0, 580.0, 584.0, 103.5, 104.0, 104.0, 103.5, 105.5],
            "close": [593.0, 589.0, 580.0, 585.0, 588.0, 104.5, 105.0, 104.5, 105.5, 106.5],
            "adj_close": [583.0, 579.0, 570.0, 575.0, 578.0,
                          100.5, 101.0, 100.5, 101.5, 102.5],
            "volume": [25_000_000, 30_000_000, 28_000_000, 22_000_000, 26_000_000,
                       40_000_000, 38_000_000, 41_000_000, 39_000_000, 42_000_000],
        }
    )
    flows = pl.DataFrame(
        {
            "ticker": ["2330"] * 5 + ["2317"] * 5,
            "date": DAYS * 2,
            "foreign_net_shares": [5_060_000, -1_000_000, 2_000_000, 500_000, -250_000,
                                   1_000_000, 2_000_000, -500_000, 0, 750_000],
            "trust_net_shares": [2_000_000, 0, 300_000, -100_000, 50_000,
                                 500_000, -200_000, 0, 100_000, 250_000],
            "dealer_net_shares": [-100_000, 50_000, 0, 25_000, -75_000,
                                  10_000, 0, -20_000, 5_000, 15_000],
        }
    )
    finalize(prices, PRICES_SCHEMA, "fixture_prices").write_parquet(HERE / "prices_fixture.parquet")
    finalize(flows, FLOWS_SCHEMA, "fixture_flows").write_parquet(HERE / "flows_fixture.parquet")
    print("fixtures written")


if __name__ == "__main__":
    main()
