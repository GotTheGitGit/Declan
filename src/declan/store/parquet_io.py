"""Raw Parquet storage: immutable-ish audit trail under ``data/raw`` (D-007).

Layout: ``data/raw/{source}/{year}/{dataset}/{ticker}.parquet``.
Partitions are overwritten atomically (tmp file + ``os.replace``) when a source
restates history. The DuckDB file can always be rebuilt from this tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl


def partition_path(raw_root: Path, source: str, year: int, dataset: str, ticker: str) -> Path:
    return Path(raw_root) / source / str(year) / dataset / f"{ticker}.parquet"


def write_partition(
    df: pl.DataFrame, raw_root: Path, source: str, year: int, dataset: str, ticker: str
) -> Path:
    """Atomically (over)write one partition. ``df`` must already be canonical."""
    path = partition_path(raw_root, source, year, dataset, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)
    return path


def write_partitions(
    df: pl.DataFrame, raw_root: Path, source: str, dataset: str
) -> list[Path]:
    """Split a canonical frame by (year, ticker) and write each partition.

    New data for a (source, year, dataset, ticker) partition replaces the whole
    partition, merged with any existing rows outside the new date range being
    unnecessary: FinMind-style fetches always cover contiguous ranges, so the
    incoming frame is merged with the existing partition before writing to avoid
    losing earlier rows of the same year.
    """
    paths: list[Path] = []
    with_year = df.with_columns(pl.col("date").dt.year().alias("_year"))
    for (year, ticker), part in with_year.group_by(["_year", "ticker"], maintain_order=True):
        part = part.drop("_year")
        existing_path = partition_path(raw_root, source, int(year), dataset, str(ticker))
        if existing_path.exists():
            existing = pl.read_parquet(existing_path)
            part = (
                pl.concat([existing, part])
                .unique(subset=["ticker", "date"], keep="last")
                .sort(["ticker", "date"])
            )
        paths.append(
            write_partition(part, raw_root, source, int(year), dataset, str(ticker))
        )
    return paths


def read_dataset(raw_root: Path, source: str, dataset: str) -> pl.DataFrame | None:
    """Read every partition of a dataset back into one frame (for rebuilds)."""
    files = sorted(Path(raw_root).glob(f"{source}/*/{dataset}/*.parquet"))
    if not files:
        return None
    return (
        pl.concat([pl.read_parquet(f) for f in files])
        .unique(subset=["ticker", "date"], keep="last")
        .sort(["ticker", "date"])
    )


def list_sources(raw_root: Path) -> list[tuple[str, str]]:
    """Return distinct (source, dataset) pairs present in the raw tree."""
    pairs: set[tuple[str, str]] = set()
    root = Path(raw_root)
    if not root.exists():
        return []
    for f in root.glob("*/*/*/*.parquet"):
        source = f.parts[len(root.parts)]
        dataset = f.parts[len(root.parts) + 2]
        pairs.add((source, dataset))
    return sorted(pairs)
