"""Declan CLI: `declan ingest`, `declan rebuild`, `declan status`."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import typer

from declan import config as cfg
from declan.jobs import ingest as ingest_job

app = typer.Typer(help="Declan - TWSE research assistant", no_args_is_help=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines can leak query params


def _paths(root: Path | None) -> cfg.Paths:
    return cfg.project_paths(root)


@app.command()
def ingest(
    start: str | None = typer.Option(None, help="Start date YYYY-MM-DD"),
    end: str | None = typer.Option(None, help="End date YYYY-MM-DD (default today)"),
    backfill_years: int = typer.Option(5, help="Used when --start is omitted"),
    tickers: str | None = typer.Option(None, help="Comma-separated override, e.g. 2330,2317"),
    no_validate: bool = typer.Option(False, "--no-validate", help="Skip cross-source check"),
    force: bool = typer.Option(False, help="Ignore ingest_log checkpoints and refetch"),
    root: Path | None = typer.Option(None, help="Project root (default: cwd or $DECLAN_ROOT)"),
) -> None:
    """Backfill/refresh prices and institutional flows for the universe."""
    from declan.ingest.finmind import FinMindClient
    from declan.ingest.twse_openapi import TwseOpenApi

    paths = _paths(root)
    end_d = date.fromisoformat(end) if end else date.today()
    start_d = date.fromisoformat(start) if start else end_d - timedelta(days=365 * backfill_years)

    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        typer.secho("FINMIND_TOKEN is not set (.env)", fg="red", err=True)
        raise typer.Exit(1)

    if tickers:
        universe = [cfg.validate_ticker(t.strip()) for t in tickers.split(",")]
    else:
        universe = cfg.load_universe(paths.config_dir / "universe.yaml").resolve()

    client = FinMindClient(token)
    validator = None if no_validate else TwseOpenApi()

    typer.echo(f"Ingesting {len(universe)} tickers, {start_d} -> {end_d}")
    summary = ingest_job.run_ingest(
        paths, universe, start_d, end_d,
        price_source=client, flow_source=client,
        validate_source=validator, force=force,
    )
    typer.echo(
        f"prices rows: {summary.prices_rows}  flows rows: {summary.flows_rows}  "
        f"skipped (up-to-date): {len(summary.skipped)}  errors: {len(summary.errors)}"
    )
    for e in summary.errors:
        typer.secho(f"  ERROR {e}", fg="red")
    if summary.validation is not None:
        v = summary.validation
        if v.ok:
            typer.secho(f"validation: {v.checked} sampled closes match TWSE", fg="green")
        else:
            typer.secho(f"validation: {len(v.mismatches)} mismatch(es)!", fg="red")
            for m in v.mismatches:
                typer.echo(f"  {m}")
    raise typer.Exit(1 if summary.errors else 0)


@app.command()
def rebuild(
    root: Path | None = typer.Option(None, help="Project root"),
) -> None:
    """Rebuild data/declan.duckdb from the raw Parquet tree."""
    paths = _paths(root)
    counts = ingest_job.rebuild(paths)
    if not counts:
        typer.echo("no raw parquet found - nothing to rebuild")
        return
    for table, n in counts.items():
        typer.echo(f"{table}: {n} rows")


@app.command()
def status(
    root: Path | None = typer.Option(None, help="Project root"),
) -> None:
    """Show ingest coverage per (source, dataset, ticker)."""
    from declan.store import db as store_db

    paths = _paths(root)
    if not paths.db_path.exists():
        typer.echo("no database yet - run `declan ingest`")
        return
    with store_db.connect(paths.db_path, read_only=True) as conn:
        rows = conn.execute(
            "SELECT source, dataset, ticker, min(start_date), max(end_date), sum(rows) "
            "FROM ingest_log WHERE status = 'ok' "
            "GROUP BY source, dataset, ticker ORDER BY source, dataset, ticker"
        ).fetchall()
    if not rows:
        typer.echo("ingest_log is empty")
        return
    for source, dataset, ticker, s, e, n in rows:
        typer.echo(f"{source:8s} {dataset:7s} {ticker}  {s} -> {e}  ({n} rows)")


if __name__ == "__main__":
    app()
