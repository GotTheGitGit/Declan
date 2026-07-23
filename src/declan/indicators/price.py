"""Primitive price indicators (D-017). Pure: per-ticker frame in, Series out.

Every function assumes `df` is a single ticker's rows sorted by date ascending,
with the canonical PRICES_SCHEMA columns. Return-based indicators use `adj_close`
(D-001); price-posture ones use raw `close`.
"""

from __future__ import annotations

import polars as pl

from declan.indicators.registry import indicator


@indicator("sma")
def sma(df: pl.DataFrame, *, window: int, column: str = "close") -> pl.Series:
    """Simple moving average over `window` bars."""
    return df.get_column(column).rolling_mean(window_size=window).alias(f"sma_{window}")


@indicator("ema")
def ema(df: pl.DataFrame, *, window: int, column: str = "close") -> pl.Series:
    """Exponential moving average (span = window, adjust=False)."""
    return (
        df.get_column(column)
        .ewm_mean(span=window, adjust=False, min_samples=window)
        .alias(f"ema_{window}")
    )


@indicator("rsi")
def rsi(df: pl.DataFrame, *, window: int = 14, column: str = "close") -> pl.Series:
    """Wilder's RSI. Uses Wilder smoothing (alpha = 1/window) of gains/losses."""
    delta = df.get_column(column).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    frame = df.select(gain.alias("g"), loss.alias("l"))
    avg_gain = frame.get_column("g").ewm_mean(alpha=1 / window, adjust=False, min_samples=window)
    avg_loss = frame.get_column("l").ewm_mean(alpha=1 / window, adjust=False, min_samples=window)
    rs = avg_gain / avg_loss
    formula = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss==0 & avg_gain>0 -> 100 (all gains); both 0 (no movement) -> null
    out = pl.select(
        pl.when((avg_loss == 0) & (avg_gain > 0)).then(100.0)
        .when((avg_loss == 0) & (avg_gain == 0)).then(None)
        .otherwise(formula)
        .alias("rsi")
    ).get_column("rsi")
    return out.alias(f"rsi_{window}")


@indicator("returns")
def returns(df: pl.DataFrame, *, window: int = 1, column: str = "adj_close") -> pl.Series:
    """Simple return over `window` bars (adjusted by default, D-001)."""
    s = df.get_column(column)
    return (s / s.shift(window) - 1.0).alias(f"ret_{window}")


@indicator("volume_zscore")
def volume_zscore(df: pl.DataFrame, *, window: int = 60) -> pl.Series:
    """(volume - rolling_mean) / rolling_std over `window` — volume anomaly."""
    v = df.get_column("volume").cast(pl.Float64)
    mean = v.rolling_mean(window_size=window)
    std = v.rolling_std(window_size=window)
    return ((v - mean) / std).alias("volume_z")


@indicator("high_low_52w")
def high_low_52w(df: pl.DataFrame, *, window: int = 252) -> pl.Series:
    """Position within the trailing `window`-bar range: 0 = at low, 1 = at high."""
    c = df.get_column("close")
    hi = c.rolling_max(window_size=window)
    lo = c.rolling_min(window_size=window)
    span = hi - lo
    return pl.select(
        pl.when(span > 0).then((c - lo) / span).otherwise(None).alias("range_pos")
    ).get_column("range_pos")


@indicator("drawdown_from_peak")
def drawdown_from_peak(df: pl.DataFrame, *, column: str = "adj_close") -> pl.Series:
    """Drawdown from the running peak (<= 0), e.g. -0.12 = 12% below peak."""
    s = df.get_column(column)
    peak = s.cum_max()
    return (s / peak - 1.0).alias("drawdown")
