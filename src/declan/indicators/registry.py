"""Indicator registry (D-017).

Indicators are pure functions registered by name. The name space defined here
becomes the vocabulary the M4 strategy-YAML interpreter binds to, so names are a
committed contract. Registering = one decorator; the strategy engine and the
`declan indicators` debug command both read from this registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl

# An indicator takes a per-ticker price/flow frame (sorted by date, ascending)
# plus keyword params, and returns a polars Series aligned to that frame.
Indicator = Callable[..., pl.Series]

_REGISTRY: dict[str, Indicator] = {}


def indicator(name: str) -> Callable[[Indicator], Indicator]:
    def wrap(fn: Indicator) -> Indicator:
        if name in _REGISTRY:
            raise ValueError(f"indicator {name!r} already registered")
        _REGISTRY[name] = fn
        fn.indicator_name = name  # type: ignore[attr-defined]
        return fn

    return wrap


def get(name: str) -> Indicator:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown indicator {name!r}; registered: {sorted(_REGISTRY)}"
        ) from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def compute(name: str, df: pl.DataFrame, **params: Any) -> pl.Series:
    """Look up and run an indicator (thin convenience used by the debug CLI)."""
    return get(name)(df, **params)
