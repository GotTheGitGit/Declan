"""Feature layer (D-017): compose primitive indicators into reusable,
strategy-facing features, evaluated as a point-in-time snapshot per ticker.

A snapshot is one row per ticker holding the feature values as of a chosen date.
Reports and (later) factor models consume these features rather than recomputing.
Everything here is deterministic and pure (frames in, frame out).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from declan.indicators import flows as fl
from declan.indicators import price as pr

# Feature column order of the snapshot frame (one row per ticker).
SNAPSHOT_COLS = [
    "ticker", "date", "close", "adj_close", "volume",
    "ret_1", "ret_20", "ret_60", "ret_120",
    "sma20", "sma60", "dist_sma20", "rsi14",
    "vol_ratio", "volume_z", "range_pos", "drawdown",
    "foreign_net", "trust_net", "dealer_net",
    "foreign_streak", "trust_streak", "dealer_streak",
    "foreign_sum_5", "trust_sum_5", "inst_flow_strength",
    "momentum_score", "mean_reversion_score",
    "above_sma20", "above_sma60",
]


def _last_value(s: pl.Series):
    v = s.drop_nulls()
    return v[-1] if len(v) else None


def _last_int(g: pl.DataFrame, col: str):
    if col not in g.columns:
        return None
    v = _last_value(g.get_column(col))
    return int(v) if v is not None else None


def _ticker_features(g: pl.DataFrame) -> dict:
    """Compute one ticker's latest features. `g` is that ticker's history,
    sorted by date ascending, prices left-joined with flows."""
    close = g.get_column("close")
    sma20 = _last_value(pr.sma(g, window=20))
    sma60 = _last_value(pr.sma(g, window=60))
    last_close = _last_value(close)
    ret_20 = _last_value(pr.returns(g, window=20))
    ret_60 = _last_value(pr.returns(g, window=60))
    ret_120 = _last_value(pr.returns(g, window=120))

    mom_parts = [r for r in (ret_20, ret_60, ret_120) if r is not None]
    momentum_score = sum(mom_parts) / len(mom_parts) if mom_parts else None

    dist_sma20 = (last_close / sma20 - 1.0) if (sma20 and last_close) else None
    # mean reversion: positive when trading below SMA20 (oversold room)
    mean_reversion_score = (sma20 / last_close - 1.0) if (sma20 and last_close) else None

    vol = g.get_column("volume").cast(pl.Float64)
    vol_sma20 = _last_value(vol.rolling_mean(window_size=20))
    last_vol = _last_value(vol)
    vol_ratio = (last_vol / vol_sma20) if (vol_sma20 and last_vol) else None

    foreign_sum_5 = _last_value(fl.flow_sum(g, investor="foreign", window=5))
    trust_sum_5 = _last_value(fl.flow_sum(g, investor="trust", window=5))
    vol_sum_5 = _last_value(vol.rolling_sum(window_size=5))
    inst_5 = (foreign_sum_5 or 0) + (trust_sum_5 or 0)
    inst_flow_strength = (inst_5 / vol_sum_5) if vol_sum_5 else None

    return {
        "ticker": g.get_column("ticker")[0],
        "date": _last_value(g.get_column("date")),
        "close": last_close,
        "adj_close": _last_value(g.get_column("adj_close")),
        "volume": int(last_vol) if last_vol is not None else None,
        "ret_1": _last_value(pr.returns(g, window=1)),
        "ret_20": ret_20, "ret_60": ret_60, "ret_120": ret_120,
        "sma20": sma20, "sma60": sma60,
        "dist_sma20": dist_sma20,
        "rsi14": _last_value(pr.rsi(g, window=14)),
        "vol_ratio": vol_ratio,
        "volume_z": _last_value(pr.volume_zscore(g, window=60)),
        "range_pos": _last_value(pr.high_low_52w(g, window=252)),
        "drawdown": _last_value(pr.drawdown_from_peak(g)),
        "foreign_net": _last_int(g, "foreign_net_shares"),
        "trust_net": _last_int(g, "trust_net_shares"),
        "dealer_net": _last_int(g, "dealer_net_shares"),
        "foreign_streak": _last_value(fl.flow_streak(g, investor="foreign")),
        "trust_streak": _last_value(fl.flow_streak(g, investor="trust")),
        "dealer_streak": _last_value(fl.flow_streak(g, investor="dealer")),
        "foreign_sum_5": foreign_sum_5,
        "trust_sum_5": trust_sum_5,
        "inst_flow_strength": inst_flow_strength,
        "momentum_score": momentum_score,
        "mean_reversion_score": mean_reversion_score,
        "above_sma20": bool(last_close > sma20) if (sma20 and last_close) else None,
        "above_sma60": bool(last_close > sma60) if (sma60 and last_close) else None,
    }


def feature_snapshot(price_flow: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """One row of features per ticker as of `as_of`.

    `price_flow`: long frame of prices left-joined with institutional_flows for
    the whole universe, any date range ending >= as_of. Rows after `as_of` are
    dropped so the snapshot never sees the future (no look-ahead).
    """
    hist = price_flow.filter(pl.col("date") <= as_of).sort(["ticker", "date"])
    records = [
        _ticker_features(g)
        for _, g in hist.group_by("ticker", maintain_order=True)
        if g.filter(pl.col("date") == as_of).height > 0  # ticker traded on as_of
    ]
    if not records:
        return pl.DataFrame({c: [] for c in SNAPSHOT_COLS})
    return pl.DataFrame(records).select(SNAPSHOT_COLS).sort("ticker")
