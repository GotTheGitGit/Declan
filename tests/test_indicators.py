"""Indicator unit tests with independently hand-computed / reproduced expected
values (money-math convention). Reference numbers derived outside polars."""

from datetime import date, timedelta

import polars as pl
import pytest

from declan.indicators import flows as fl
from declan.indicators import price as pr
from declan.indicators import registry

SERIES = [10, 11, 12, 11, 10, 12, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19]


def _price_df(closes, volumes=None):
    n = len(closes)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({
        "ticker": ["2330"] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "adj_close": closes, "volume": volumes or [100] * n,
    })


def test_sma_window3():
    s = pr.sma(_price_df([1, 2, 3, 4, 5]), window=3).to_list()
    assert s[:2] == [None, None]
    assert s[2:] == [2.0, 3.0, 4.0]  # (1+2+3)/3, (2+3+4)/3, (3+4+5)/3


def test_ema_span3_alpha_half():
    # alpha = 2/(3+1) = 0.5, adjust=False, seed = x0; min_periods=3
    s = pr.ema(_price_df([1, 2, 3, 4, 5]), window=3).to_list()
    assert s[0] is None and s[1] is None
    assert s[2] == pytest.approx(2.25)     # 0.5*3 + 0.5*1.5
    assert s[4] == pytest.approx(4.0625)   # hand-computed recursion


def test_rsi_wilder_reference():
    # Reproduced independently (EWM Wilder smoothing, alpha=1/14): 74.652262
    v = pr.rsi(_price_df(SERIES), window=14).to_list()[-1]
    assert v == pytest.approx(74.652262, abs=1e-5)


def test_rsi_all_gains_is_100_all_losses_is_0():
    up = list(range(1, 20))
    assert pr.rsi(_price_df(up), window=14).to_list()[-1] == pytest.approx(100.0)
    down = list(range(20, 1, -1))
    assert pr.rsi(_price_df(down), window=14).to_list()[-1] == pytest.approx(0.0)


def test_returns_window5():
    v = pr.returns(_price_df(SERIES), window=5).to_list()[-1]
    assert v == pytest.approx(0.1875)  # 19/16 - 1


def test_volume_zscore_sample_std():
    df = _price_df([1] * 5, volumes=[100, 100, 100, 100, 400])
    # window 3: mean=200, sample std=173.205; z=(400-200)/173.205
    v = pr.volume_zscore(df, window=3).to_list()[-1]
    assert v == pytest.approx(1.1547005, abs=1e-6)


def test_drawdown_from_peak():
    s = pr.drawdown_from_peak(_price_df([10, 12, 9, 15, 12])).to_list()
    assert s[0] == pytest.approx(0.0)
    assert s[2] == pytest.approx(9 / 12 - 1)     # -0.25 from peak 12
    assert s[4] == pytest.approx(12 / 15 - 1)    # -0.2 from peak 15


def test_high_low_52w_position():
    s = pr.high_low_52w(_price_df([10, 20, 30, 25, 15]), window=5).to_list()
    # last row over window: hi=30, lo=10, close=15 -> (15-10)/(30-10)=0.25
    assert s[-1] == pytest.approx(0.25)


def _flow_df(foreign):
    n = len(foreign)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({
        "ticker": ["2330"] * n, "date": dates,
        "foreign_net_shares": foreign,
        "trust_net_shares": [0] * n, "dealer_net_shares": [0] * n,
        "volume": [1000] * n,
    })


def test_flow_streak_signs():
    s = fl.flow_streak(_flow_df([100, 200, -50, -10, 0, 300]), investor="foreign").to_list()
    assert s == [1, 2, -1, -2, 0, 1]


def test_flow_sum_window2():
    s = fl.flow_sum(_flow_df([100, 200, -50, -10]), investor="foreign", window=2).to_list()
    assert s == [None, 300, 150, -60]


def test_flow_vs_volume():
    df = _flow_df([250, -500, 0])
    s = fl.flow_vs_volume(df, investor="foreign").to_list()
    assert s[0] == pytest.approx(0.25)   # 250/1000
    assert s[1] == pytest.approx(-0.5)


def test_registry_lookup_and_unknown():
    assert "sma" in registry.names() and "rsi" in registry.names()
    assert "flow_streak" in registry.names()
    with pytest.raises(KeyError):
        registry.get("does_not_exist")


def test_registry_rejects_duplicate():
    with pytest.raises(ValueError):
        @registry.indicator("sma")
        def _dupe(df):
            return df.get_column("close")


def test_rsi_flat_series_is_null_not_100():
    # constant series: no gains, no losses -> RSI undefined (null), not 100
    v = pr.rsi(_price_df([50] * 20), window=14).to_list()[-1]
    assert v is None


def test_high_low_52w_constant_window_is_null():
    # hi == lo -> range position undefined (null), never NaN
    v = pr.high_low_52w(_price_df([100] * 6), window=5).to_list()[-1]
    assert v is None
