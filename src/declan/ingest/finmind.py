"""FinMind adapter (primary source).

Datasets used:
- ``TaiwanStockPrice``     raw OHLCV (close = ground truth, D-001)
- ``TaiwanStockPriceAdj``  dividend/split-adjusted prices -> adj_close (D-001)
- ``TaiwanStockInstitutionalInvestorsBuySell``  三大法人 buy/sell in shares (D-003)

Parsing is split into pure functions so tests run offline against fixture
responses. The HTTP client handles auth, retry with backoff, and a simple
minimum-interval rate limiter (FinMind free tier is request-budgeted).
"""

from __future__ import annotations

import logging
import time
from datetime import date

import httpx
import polars as pl
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from declan.ingest.base import FLOWS_SCHEMA, PRICES_SCHEMA
from declan.ingest.normalize import finalize

log = logging.getLogger(__name__)

BASE_URL = "https://api.finmindtrade.com/api/v4/data"

# FinMind investor-name -> canonical bucket (D-003 aggregation).
_FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}
_TRUST_NAMES = {"Investment_Trust"}
_DEALER_NAMES = {"Dealer_self", "Dealer_Hedging"}


class FinMindError(RuntimeError):
    pass


class FinMindRateLimitError(FinMindError):
    pass


class FinMindPermissionError(FinMindError):
    """Dataset not available on this token's tier (e.g. TaiwanStockPriceAdj
    requires the FinMind sponsor tier)."""


def parse_prices(
    raw: list[dict], adj: list[dict], ticker: str
) -> pl.DataFrame:
    """Combine TaiwanStockPrice + TaiwanStockPriceAdj records into canonical prices.

    FinMind columns: date, stock_id, Trading_Volume (shares), open, max, min, close.
    If an adjusted close is missing for a date, fall back to the raw close.
    """
    if not raw:
        return pl.DataFrame(schema=PRICES_SCHEMA)
    base = pl.DataFrame(raw).select(
        pl.col("date").str.to_date(),
        pl.lit(ticker).alias("ticker"),
        pl.col("open").cast(pl.Float64),
        pl.col("max").cast(pl.Float64).alias("high"),
        pl.col("min").cast(pl.Float64).alias("low"),
        pl.col("close").cast(pl.Float64),
        pl.col("Trading_Volume").cast(pl.Int64).alias("volume"),
    )
    if adj:
        adj_df = pl.DataFrame(adj).select(
            pl.col("date").str.to_date(),
            pl.col("close").cast(pl.Float64).alias("adj_close"),
        )
        base = base.join(adj_df, on="date", how="left")
        base = base.with_columns(pl.col("adj_close").fill_null(pl.col("close")))
    else:
        base = base.with_columns(pl.col("close").alias("adj_close"))
    return finalize(base, PRICES_SCHEMA, f"finmind_prices[{ticker}]")


def parse_flows(raw: list[dict], ticker: str) -> pl.DataFrame:
    """Aggregate 三大法人 buy/sell records into signed net shares per bucket.

    FinMind reports units of shares; net = buy - sell.
    foreign = Foreign_Investor + Foreign_Dealer_Self;
    dealer  = Dealer_self + Dealer_Hedging (D-003).
    """
    if not raw:
        return pl.DataFrame(schema=FLOWS_SCHEMA)
    df = pl.DataFrame(raw).select(
        pl.col("date").str.to_date(),
        pl.col("name"),
        (pl.col("buy").cast(pl.Int64) - pl.col("sell").cast(pl.Int64)).alias("net"),
    )

    def bucket(names: set[str], alias: str) -> pl.Expr:
        return (
            pl.when(pl.col("name").is_in(list(names))).then(pl.col("net")).otherwise(0)
            .sum()
            .alias(alias)
        )

    out = (
        df.group_by("date")
        .agg(
            bucket(_FOREIGN_NAMES, "foreign_net_shares"),
            bucket(_TRUST_NAMES, "trust_net_shares"),
            bucket(_DEALER_NAMES, "dealer_net_shares"),
        )
        .with_columns(pl.lit(ticker).alias("ticker"))
    )
    return finalize(out, FLOWS_SCHEMA, f"finmind_flows[{ticker}]")


class FinMindClient:
    """HTTP client for FinMind v4. Satisfies PriceSource and FlowSource."""

    name = "finmind"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = BASE_URL,
        min_interval_s: float = 0.35,
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._min_interval_s = min_interval_s
        self._last_request_at = 0.0
        self._adj_available = True  # flips off after a permission-denied response
        self._http = httpx.Client(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, FinMindRateLimitError)),
        wait=wait_exponential(multiplier=2, min=2, max=120),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _get(self, dataset: str, ticker: str, start: date, end: date) -> list[dict]:
        self._throttle()
        resp = self._http.get(
            self._base_url,
            params={
                "dataset": dataset,
                "data_id": ticker,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        if resp.status_code == 429:
            raise FinMindRateLimitError(f"rate limited on {dataset}/{ticker}")
        if 400 <= resp.status_code < 500:
            detail = resp.text[:300]
            raise FinMindPermissionError(
                f"{dataset}/{ticker}: HTTP {resp.status_code} - {detail}"
            )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") not in (200, "200", None) and payload.get("msg") != "success":
            raise FinMindError(f"{dataset}/{ticker}: {payload.get('msg')}")
        return payload.get("data", [])

    def fetch_prices(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        raw = self._get("TaiwanStockPrice", ticker, start, end)
        adj: list[dict] = []
        if self._adj_available:
            try:
                adj = self._get("TaiwanStockPriceAdj", ticker, start, end)
            except FinMindPermissionError as exc:
                # TaiwanStockPriceAdj requires the FinMind sponsor tier (D-015).
                # Degrade: adj_close falls back to the raw close for the whole run.
                self._adj_available = False
                log.warning(
                    "TaiwanStockPriceAdj unavailable on this token tier; "
                    "adj_close will fall back to raw close for this run. (%s)", exc
                )
        return parse_prices(raw, adj, ticker)

    def fetch_flows(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        raw = self._get("TaiwanStockInstitutionalInvestorsBuySell", ticker, start, end)
        return parse_flows(raw, ticker)
