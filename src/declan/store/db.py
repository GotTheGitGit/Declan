"""DuckDB connection management: single-writer lock (D-012) + idempotent upserts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import polars as pl

from declan.store import schema


class DatabaseLockedError(RuntimeError):
    """Another process holds the write lock on the DuckDB file."""


def _lock_path(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".lock")


@contextmanager
def connect(db_path: Path, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the Declan database.

    Writers acquire an exclusive lock file next to the DB (DuckDB is
    single-writer); readers open read-only without the lock.
    """
    db_path = Path(db_path)
    if read_only:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(db_path)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DatabaseLockedError(
            f"{lock} exists - another Declan process is writing. "
            "Remove the lock file only if you are sure no process is running."
        ) from exc
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        conn = duckdb.connect(str(db_path))
        try:
            schema.create_all(conn)
            yield conn
        finally:
            conn.close()
    finally:
        lock.unlink(missing_ok=True)


def upsert(conn: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame) -> int:
    """INSERT OR REPLACE ``df`` into ``table`` (idempotent on the table's PK).

    Column names must match table columns; order is taken from the dataframe.
    Returns the number of rows written.
    """
    if df.is_empty():
        return 0
    arrow_tbl = df.to_arrow()
    cols = ", ".join(f'"{c}"' for c in df.columns)
    conn.register("_upsert_df", arrow_tbl)
    try:
        conn.execute(f'INSERT OR REPLACE INTO "{table}" ({cols}) SELECT {cols} FROM _upsert_df')
    finally:
        conn.unregister("_upsert_df")
    return df.height
