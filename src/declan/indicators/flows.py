"""Institutional-flow indicators (D-003, D-017). Pure per-ticker frame -> Series.

`df` must carry the canonical FLOWS_SCHEMA columns joined onto a date-sorted
frame (typically prices left-joined with institutional_flows). Values are signed
net shares.
"""

from __future__ import annotations

import polars as pl

from declan.indicators.registry import indicator

_FLOW_COLS = {
    "foreign": "foreign_net_shares",
    "trust": "trust_net_shares",
    "dealer": "dealer_net_shares",
}


def _col(investor: str) -> str:
    try:
        return _FLOW_COLS[investor]
    except KeyError:
        raise KeyError(
            f"unknown investor {investor!r}; use one of {sorted(_FLOW_COLS)}"
        ) from None


@indicator("flow_sum")
def flow_sum(df: pl.DataFrame, *, investor: str = "foreign", window: int = 5) -> pl.Series:
    """Rolling sum of signed net shares over `window` days."""
    col = _col(investor)
    return df.get_column(col).rolling_sum(window_size=window).alias(f"{investor}_sum_{window}")


@indicator("flow_streak")
def flow_streak(df: pl.DataFrame, *, investor: str = "foreign") -> pl.Series:
    """Signed consecutive-day streak of net buying/selling.

    +N = N straight net-buy days, -N = N straight net-sell days, 0 = flat/first.
    Computed in Python for clarity (streaks are inherently sequential).
    """
    col = _col(investor)
    out: list[int] = []
    streak = 0
    for v in df.get_column(col).to_list():
        if v is None or v == 0:
            streak = 0
        elif v > 0:
            streak = streak + 1 if streak > 0 else 1
        else:
            streak = streak - 1 if streak < 0 else -1
        out.append(streak)
    return pl.Series(f"{investor}_streak", out, dtype=pl.Int64)


@indicator("flow_vs_volume")
def flow_vs_volume(df: pl.DataFrame, *, investor: str = "foreign") -> pl.Series:
    """Net shares as a fraction of that day's total volume — conviction proxy."""
    col = _col(investor)
    vol = df.get_column("volume").cast(pl.Float64)
    net = df.get_column(col).cast(pl.Float64)
    return (net / vol).alias(f"{investor}_vs_vol")
