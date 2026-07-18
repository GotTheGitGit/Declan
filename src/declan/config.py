"""Typed loaders for Declan's user-authored configuration (D-002, D-004)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

TICKER_RE = re.compile(r"^\d{4}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "declan.duckdb"


def project_paths(root: Path | None = None) -> Paths:
    """Resolve the project root: explicit arg > $DECLAN_ROOT > cwd."""
    load_dotenv()
    if root is None:
        root = Path(os.environ.get("DECLAN_ROOT", Path.cwd()))
    return Paths(root=Path(root))


def validate_ticker(ticker: str) -> str:
    t = str(ticker)
    if not TICKER_RE.match(t):
        raise ConfigError(f"invalid TWSE ticker {ticker!r}: must be a 4-digit code like '2330'")
    return t


@dataclass(frozen=True)
class Universe:
    """Typed universe (D-002). ``static`` resolves now; ``index_constituents``
    is schema-supported but resolved in a later milestone."""

    type: str
    name: str
    tickers: list[str] = field(default_factory=list)
    index: str | None = None
    historical: bool = False

    def resolve(self) -> list[str]:
        if self.type == "static":
            return self.tickers
        raise NotImplementedError(
            f"universe type {self.type!r} is not resolvable yet "
            "(index_constituents lands in a later milestone)"
        )


def load_universe(path: Path) -> Universe:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    utype = raw.get("type")
    if utype == "static":
        tickers = [validate_ticker(t) for t in raw.get("tickers", [])]
        if not tickers:
            raise ConfigError(f"{path}: static universe has no tickers")
        return Universe(type="static", name=raw.get("name", "static"), tickers=tickers)
    if utype == "index_constituents":
        index = raw.get("index")
        if not index:
            raise ConfigError(f"{path}: index_constituents universe requires 'index'")
        return Universe(
            type="index_constituents",
            name=raw.get("name", f"index_{index}"),
            index=str(index),
            historical=bool(raw.get("historical", False)),
        )
    raise ConfigError(f"{path}: unknown universe type {utype!r}")


@dataclass(frozen=True)
class Holding:
    ticker: str
    qty: int
    avg_cost: float


def load_holdings(path: Path) -> list[Holding]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    holdings = []
    for ticker, pos in (raw.get("positions") or {}).items():
        holdings.append(
            Holding(
                ticker=validate_ticker(ticker),
                qty=int(pos["qty"]),
                avg_cost=float(pos["avg_cost"]),
            )
        )
    return holdings


@dataclass(frozen=True)
class Costs:
    brokerage_base_rate: float
    brokerage_discount_multiplier: float
    sell_tax_rate: float
    slippage_bps: float

    @property
    def effective_brokerage_rate(self) -> float:
        return self.brokerage_base_rate * self.brokerage_discount_multiplier


def load_costs(path: Path) -> Costs:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Costs(
        brokerage_base_rate=float(raw["brokerage"]["base_rate"]),
        brokerage_discount_multiplier=float(raw["brokerage"]["discount_multiplier"]),
        sell_tax_rate=float(raw["tax"]["sell_tax_rate"]),
        slippage_bps=float(raw["slippage"]["bps"]),
    )
