from pathlib import Path

import pytest

from declan import config as cfg


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "u.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_static_universe(tmp_path):
    p = _write(tmp_path, "type: static\nname: t\ntickers: ['2330', '2317']\n")
    u = cfg.load_universe(p)
    assert u.resolve() == ["2330", "2317"]


def test_index_universe_schema_supported_but_not_resolvable(tmp_path):
    p = _write(tmp_path, "type: index_constituents\nindex: '0050'\nhistorical: true\n")
    u = cfg.load_universe(p)
    assert u.index == "0050" and u.historical is True
    with pytest.raises(NotImplementedError):
        u.resolve()


def test_invalid_ticker_rejected(tmp_path):
    p = _write(tmp_path, "type: static\ntickers: ['2330.TW']\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_universe(p)


def test_unknown_type_rejected(tmp_path):
    with pytest.raises(cfg.ConfigError):
        cfg.load_universe(_write(tmp_path, "type: magic\n"))


def test_holdings(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(
        "positions:\n  '2330': {qty: 2000, avg_cost: 980}\n  '2317': {qty: 1000, avg_cost: 620}\n",
        encoding="utf-8",
    )
    hs = cfg.load_holdings(p)
    assert {h.ticker: (h.qty, h.avg_cost) for h in hs} == {
        "2330": (2000, 980.0), "2317": (1000, 620.0)
    }


def test_costs(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "brokerage: {base_rate: 0.001425, discount_multiplier: 0.5}\n"
        "tax: {sell_tax_rate: 0.003}\nslippage: {bps: 5}\n",
        encoding="utf-8",
    )
    c = cfg.load_costs(p)
    # hand-computed: 0.001425 * 0.5 = 0.0007125
    assert c.effective_brokerage_rate == pytest.approx(0.0007125)
    assert c.sell_tax_rate == 0.003
