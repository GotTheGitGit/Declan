
import polars as pl

from declan.features import regime as rg
from declan.features import snapshot as snap
from tests.helpers_synth import build_frame


def _snap():
    frame = build_frame({"1111": "up", "2222": "down", "3333": "dip", "4444": "flat"})
    as_of = frame.get_column("date").max()
    return snap.feature_snapshot(frame, as_of), as_of


def test_snapshot_shape_and_columns():
    s, as_of = _snap()
    assert s.height == 4
    assert s.columns == snap.SNAPSHOT_COLS
    assert set(s.get_column("ticker").to_list()) == {"1111", "2222", "3333", "4444"}
    assert (s.get_column("date") == as_of).all()


def test_uptrend_features_positive_momentum_above_smas():
    s, _ = _snap()
    up = s.filter(pl.col("ticker") == "1111").to_dicts()[0]
    assert up["momentum_score"] > 0
    assert up["above_sma20"] is True and up["above_sma60"] is True
    assert up["dist_sma20"] > 0
    assert up["foreign_streak"] > 0        # steady net buying


def test_downtrend_features_negative():
    s, _ = _snap()
    dn = s.filter(pl.col("ticker") == "2222").to_dicts()[0]
    assert dn["momentum_score"] < 0
    assert dn["above_sma20"] is False
    assert dn["foreign_streak"] < 0


def test_no_lookahead_snapshot_ignores_future_rows():
    frame = build_frame({"1111": "up"})
    mid = frame.get_column("date").to_list()[200]
    s = snap.feature_snapshot(frame, mid)
    assert s.height == 1
    assert s.get_column("date")[0] == mid
    # close at mid equals the raw series value at index 200, not the final one
    assert s.get_column("close")[0] == frame.filter(pl.col("date") == mid).get_column("close")[0]


def test_momentum_ranking_orders_uptrend_first():
    s, _ = _snap()
    rank = rg.momentum_ranking(s, top=4)
    assert rank.get_column("ticker")[0] == "1111"   # strongest momentum on top


def test_mean_reversion_flags_the_dip():
    s, _ = _snap()
    cand = rg.mean_reversion_candidates(s, top=5, rsi_max=45)
    # the 'dip' ticker is the oversold one below SMA20
    assert "3333" in cand.get_column("ticker").to_list()


def test_regime_bull_when_breadth_strong():
    # three up, one flat -> majority above SMAs, positive median momentum
    frame = build_frame({"a111": "up", "b222": "up", "c333": "up", "d444": "flat"})
    s = snap.feature_snapshot(frame, frame.get_column("date").max())
    reg = rg.classify_regime(s)
    assert reg.label == "Bull"
    assert reg.pct_above_sma60 >= 0.6


def test_regime_bear_when_breadth_weak():
    frame = build_frame({"a111": "down", "b222": "down", "c333": "down"})
    s = snap.feature_snapshot(frame, frame.get_column("date").max())
    reg = rg.classify_regime(s)
    assert reg.label == "Bear"
    assert reg.bear_signals >= 2
