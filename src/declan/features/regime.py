"""Deterministic market-regime classification (D-019).

Classifies the universe as Bull / Neutral / Bear from breadth and median
momentum — no LLM, fully reproducible. The thresholds are module constants so
the rule is auditable and tunable. Regime is itself a reusable feature that later
strategies can gate on.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# Tunable thresholds.
BULL_BREADTH = 0.60   # >= 60% of names above the MA is bullish
BEAR_BREADTH = 0.40   # <= 40% is bearish


@dataclass(frozen=True)
class Regime:
    label: str                 # "Bull" | "Neutral" | "Bear"
    pct_above_sma20: float
    pct_above_sma60: float
    median_ret_20: float
    bull_signals: int
    bear_signals: int
    breadth_n: int             # tickers with enough history to score

    def why(self) -> str:
        return (
            f"{self.pct_above_sma20:.0%} above SMA20, "
            f"{self.pct_above_sma60:.0%} above SMA60, "
            f"median 20d return {self.median_ret_20:+.1%} "
            f"(n={self.breadth_n})"
        )


def classify_regime(snapshot: pl.DataFrame) -> Regime:
    """Classify from a `feature_snapshot` frame.

    Rule: count bullish vs bearish conditions among {breadth>SMA20, breadth>SMA60,
    median 20d return sign}. >=2 bullish -> Bull, >=2 bearish -> Bear, else Neutral.
    """
    scored = snapshot.filter(
        pl.col("above_sma20").is_not_null() & pl.col("above_sma60").is_not_null()
    )
    n = scored.height
    if n == 0:
        return Regime("Neutral", 0.0, 0.0, 0.0, 0, 0, 0)

    pct20 = scored.get_column("above_sma20").mean()
    pct60 = scored.get_column("above_sma60").mean()
    med20 = snapshot.get_column("ret_20").drop_nulls().median() or 0.0

    bull = sum([pct20 >= BULL_BREADTH, pct60 >= BULL_BREADTH, med20 > 0])
    bear = sum([pct20 <= BEAR_BREADTH, pct60 <= BEAR_BREADTH, med20 < 0])
    label = "Bull" if bull >= 2 else "Bear" if bear >= 2 else "Neutral"
    return Regime(label, float(pct20), float(pct60), float(med20), bull, bear, n)


def momentum_ranking(snapshot: pl.DataFrame, top: int = 10) -> pl.DataFrame:
    """Top names by momentum_score (blended 20/60/120d return)."""
    return (
        snapshot.filter(pl.col("momentum_score").is_not_null())
        .sort("momentum_score", descending=True)
        .head(top)
        .select("ticker", "close", "ret_20", "ret_60", "momentum_score", "rsi14")
    )


def mean_reversion_candidates(
    snapshot: pl.DataFrame, top: int = 10, rsi_max: float = 35.0
) -> pl.DataFrame:
    """Oversold names: RSI below `rsi_max` and trading below SMA20,
    ranked by how far below SMA20 (mean_reversion_score)."""
    return (
        snapshot.filter(
            (pl.col("rsi14") < rsi_max)
            & (pl.col("above_sma20") == False)  # noqa: E712 - polars needs ==
            & pl.col("mean_reversion_score").is_not_null()
        )
        .sort("mean_reversion_score", descending=True)
        .head(top)
        .select("ticker", "close", "rsi14", "dist_sma20", "mean_reversion_score")
    )
