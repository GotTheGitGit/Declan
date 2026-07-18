import polars as pl
import pytest

from declan.store import db as store_db
from declan.store import parquet_io, schema


def test_create_all_creates_every_table(tmp_path):
    with store_db.connect(tmp_path / "t.duckdb") as conn:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert {
        "prices", "institutional_flows", "ingest_log", "news", "positions",
        "backtest_runs", "research_runs", "daily_reports", "schema_version",
    } <= tables


def test_upsert_is_idempotent(tmp_path, prices_df):
    db = tmp_path / "t.duckdb"
    with store_db.connect(db) as conn:
        n1 = store_db.upsert(conn, "prices", prices_df)
        n2 = store_db.upsert(conn, "prices", prices_df)  # rerun: replaces, no dupes
        count = conn.execute("SELECT count(*) FROM prices").fetchone()[0]
    assert n1 == n2 == prices_df.height
    assert count == prices_df.height


def test_upsert_replaces_restated_rows(tmp_path, prices_df):
    db = tmp_path / "t.duckdb"
    restated = prices_df.with_columns((pl.col("close") + 1.0).alias("close"))
    with store_db.connect(db) as conn:
        store_db.upsert(conn, "prices", prices_df)
        store_db.upsert(conn, "prices", restated)
        close = conn.execute(
            "SELECT close FROM prices WHERE ticker='2330' AND date=DATE '2024-01-02'"
        ).fetchone()[0]
    assert close == 594.0  # 593 + 1, hand-computed


def test_write_lock_blocks_second_writer(tmp_path):
    db = tmp_path / "t.duckdb"
    with (
        store_db.connect(db),
        pytest.raises(store_db.DatabaseLockedError),
        store_db.connect(db),
    ):
        pass
    # lock released after exit
    with store_db.connect(db):
        pass


def test_parquet_partition_roundtrip_and_atomic_overwrite(tmp_path, prices_df):
    raw = tmp_path / "raw"
    paths = parquet_io.write_partitions(prices_df, raw, "finmind", "prices")
    assert all(p.exists() for p in paths)
    back = parquet_io.read_dataset(raw, "finmind", "prices")
    assert back.sort(["ticker", "date"]).equals(prices_df.sort(["ticker", "date"]))
    # restatement overwrites, no duplicates, keeps merged year partition
    restated = prices_df.with_columns((pl.col("close") + 1.0).alias("close"))
    parquet_io.write_partitions(restated, raw, "finmind", "prices")
    back2 = parquet_io.read_dataset(raw, "finmind", "prices")
    assert back2.height == prices_df.height
    assert (back2.get_column("close") - back.get_column("close")).abs().sum() == pytest.approx(
        back.height * 1.0
    )


def test_partition_merge_keeps_existing_rows_of_same_year(tmp_path, prices_df):
    raw = tmp_path / "raw"
    early = prices_df.filter(pl.col("date") <= pl.date(2024, 1, 3))
    late = prices_df.filter(pl.col("date") > pl.date(2024, 1, 3))
    parquet_io.write_partitions(early, raw, "finmind", "prices")
    parquet_io.write_partitions(late, raw, "finmind", "prices")
    back = parquet_io.read_dataset(raw, "finmind", "prices")
    assert back.height == prices_df.height


def test_schema_version_recorded(tmp_path):
    with store_db.connect(tmp_path / "t.duckdb") as conn:
        v = conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
    assert v == schema.SCHEMA_VERSION
