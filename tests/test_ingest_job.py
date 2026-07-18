from datetime import date

from declan.config import Paths
from declan.jobs import ingest as job
from declan.store import db as store_db
from tests.helpers import FakeCloseSource, FakeFlowSource, FakePriceSource


def _paths(tmp_path) -> Paths:
    return Paths(root=tmp_path)


def _counts(paths):
    with store_db.connect(paths.db_path, read_only=True) as conn:
        p = conn.execute("SELECT count(*) FROM prices").fetchone()[0]
        f = conn.execute("SELECT count(*) FROM institutional_flows").fetchone()[0]
    return p, f


def test_ingest_end_to_end(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    ps, fs = FakePriceSource(prices_df), FakeFlowSource(flows_df)
    summary = job.run_ingest(
        paths, ["2330", "2317"], date(2024, 1, 2), date(2024, 1, 8), ps, fs
    )
    assert not summary.errors
    assert summary.prices_rows == 10 and summary.flows_rows == 10
    assert _counts(paths) == (10, 10)
    # raw parquet written
    assert (paths.raw_dir / "finmind" / "2024" / "prices" / "2330.parquet").exists()


def test_rerun_is_idempotent_and_skips_up_to_date(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    args = (paths, ["2330"], date(2024, 1, 2), date(2024, 1, 8))
    job.run_ingest(*args, FakePriceSource(prices_df), FakeFlowSource(flows_df))
    ps2 = FakePriceSource(prices_df)
    s2 = job.run_ingest(*args, ps2, FakeFlowSource(flows_df))
    # checkpoint says range fully covered -> no fetch at all (D-008)
    assert ps2.calls == []
    assert "2330/prices" in s2.skipped
    assert _counts(paths)[0] == 5  # unchanged


def test_checkpoint_fetches_only_missing_range(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    job.run_ingest(
        paths, ["2330"], date(2024, 1, 2), date(2024, 1, 4),
        FakePriceSource(prices_df), FakeFlowSource(flows_df),
    )
    ps2 = FakePriceSource(prices_df)
    job.run_ingest(
        paths, ["2330"], date(2024, 1, 2), date(2024, 1, 8),
        ps2, FakeFlowSource(flows_df),
    )
    # resumes the day after the checkpoint, not from the original start
    assert ps2.calls == [("2330", date(2024, 1, 5), date(2024, 1, 8))]
    assert _counts(paths)[0] == 5


def test_force_ignores_checkpoint(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    args = (paths, ["2330"], date(2024, 1, 2), date(2024, 1, 8))
    job.run_ingest(*args, FakePriceSource(prices_df), FakeFlowSource(flows_df))
    ps2 = FakePriceSource(prices_df)
    job.run_ingest(*args, ps2, FakeFlowSource(flows_df), force=True)
    assert ps2.calls == [("2330", date(2024, 1, 2), date(2024, 1, 8))]


def test_validation_passes_on_matching_source(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    summary = job.run_ingest(
        paths, ["2330", "2317"], date(2024, 1, 2), date(2024, 1, 8),
        FakePriceSource(prices_df), FakeFlowSource(flows_df),
        validate_source=FakeCloseSource(prices_df, shift=0.0),
    )
    assert summary.validation is not None
    assert summary.validation.checked > 0 and summary.validation.ok


def test_validation_flags_mismatch(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    summary = job.run_ingest(
        paths, ["2330"], date(2024, 1, 2), date(2024, 1, 8),
        FakePriceSource(prices_df), FakeFlowSource(flows_df),
        validate_source=FakeCloseSource(prices_df, shift=1.5),
    )
    assert not summary.validation.ok
    m = summary.validation.mismatches[0]
    assert abs(m["secondary"] - m["primary"] - 1.5) < 1e-9


def test_source_error_is_recorded_not_fatal(tmp_path, prices_df, flows_df):
    class BrokenPriceSource:
        name = "finmind"

        def fetch_prices(self, ticker, start, end):
            raise RuntimeError("boom")

    paths = _paths(tmp_path)
    summary = job.run_ingest(
        paths, ["2330"], date(2024, 1, 2), date(2024, 1, 8),
        BrokenPriceSource(), FakeFlowSource(flows_df),
    )
    assert summary.errors and "boom" in summary.errors[0]
    assert summary.flows_rows == 5  # flows still ingested
    with store_db.connect(paths.db_path, read_only=True) as conn:
        status = conn.execute(
            "SELECT status FROM ingest_log WHERE dataset='prices'"
        ).fetchone()[0]
    assert status == "error"


def test_rebuild_from_parquet(tmp_path, prices_df, flows_df):
    paths = _paths(tmp_path)
    job.run_ingest(
        paths, ["2330", "2317"], date(2024, 1, 2), date(2024, 1, 8),
        FakePriceSource(prices_df), FakeFlowSource(flows_df),
    )
    before = _counts(paths)
    paths.db_path.unlink()  # nuke the DB - raw parquet is the source of truth
    counts = job.rebuild(paths)
    assert counts == {"prices": 10, "institutional_flows": 10}
    assert _counts(paths) == before
