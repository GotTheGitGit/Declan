from pathlib import Path

from declan.config import Paths
from declan.jobs import report as report_job
from tests.helpers_synth import build_frame, load_into_db


def _paths_with_data(tmp_path: Path, shapes) -> Paths:
    paths = Paths(root=tmp_path)
    # minimal configs
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    tickers = "\n".join(f'  - "{t}"' for t in shapes)
    (cfg_dir / "universe.yaml").write_text(
        f"type: static\nname: test_uni\ntickers:\n{tickers}\n", encoding="utf-8")
    held = list(shapes)[0]
    (cfg_dir / "holdings.yaml").write_text(
        f'positions:\n  "{held}":\n    qty: 2000\n    avg_cost: 100\n',
        encoding="utf-8")
    watched = list(shapes)[1]
    (cfg_dir / "watchlist.yaml").write_text(
        f'tickers:\n  - "{watched}"\n', encoding="utf-8")
    load_into_db(paths, build_frame(shapes))
    return paths


def test_build_context_and_render(tmp_path):
    shapes = {"1111": "up", "2222": "down", "3333": "dip", "4444": "flat"}
    paths = _paths_with_data(tmp_path, shapes)
    out = report_job.write_report(paths)
    assert out.exists()
    md = out.read_text(encoding="utf-8")
    for section in [
        "# Declan Daily Report", "## Market Regime", "## Market Overview",
        "## Institutional Flows", "## Momentum Ranking",
        "## Mean Reversion Candidates", "## Holdings", "## Watchlist",
        "## Data Health",
    ]:
        assert section in md
    # holding line present for the held ticker with lot qty
    assert "| 1111 | 2,000 |" in md
    # regime is one of the three labels
    assert any(f"Regime: **{lbl}**" in md for lbl in ("Bull", "Neutral", "Bear"))


def test_report_holding_pnl_math(tmp_path):
    # single flat ticker at 100, avg_cost 100 -> ~0 pnl; qty 2000
    shapes = {"1111": "flat", "2222": "up"}
    paths = _paths_with_data(tmp_path, shapes)
    ctx = report_job.build_context(paths)
    h = next(x for x in ctx.holdings if x.ticker == "1111")
    assert h.qty == 2000 and h.avg_cost == 100.0
    assert h.market_value == 2000 * h.close
    assert h.unrealized_pnl == (h.market_value - 2000 * 100.0)


def test_report_missing_date_errors_clearly(tmp_path):
    import pytest
    paths = Paths(root=tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "universe.yaml").write_text(
        'type: static\nname: t\ntickers: ["1111"]\n', encoding="utf-8")
    (tmp_path / "config" / "holdings.yaml").write_text("positions: {}\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        report_job.build_context(paths)
