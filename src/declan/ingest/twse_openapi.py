"""TWSE official endpoints: fallback + cross-check source (D-009).

Used in M1 only for validation of FinMind closes. Handles TWSE quirks:
ROC (Minguo) date strings and comma-formatted numbers.

Endpoint: monthly per-stock daily quotes
  https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=NNNN&response=json
"""

from __future__ import annotations

from datetime import date

import httpx
import polars as pl
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from declan.ingest.normalize import parse_number, roc_to_gregorian

STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"


class TwseError(RuntimeError):
    pass


def parse_stock_day(payload: dict, ticker: str) -> pl.DataFrame:
    """Parse a STOCK_DAY JSON payload into (ticker, date, close, volume).

    Row layout: [日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數]
    Dates are ROC-era ('113/01/02'); numbers use comma separators; '--' = missing.
    """
    rows = payload.get("data") or []
    records = []
    for row in rows:
        close = parse_number(row[6])
        if close is None:
            continue
        records.append(
            {
                "ticker": ticker,
                "date": roc_to_gregorian(row[0]),
                "close": close,
                "volume": int(parse_number(row[1]) or 0),
            }
        )
    if not records:
        return pl.DataFrame(
            schema={"ticker": pl.Utf8, "date": pl.Date, "close": pl.Float64, "volume": pl.Int64}
        )
    return pl.DataFrame(records).sort("date")


def _month_starts(start: date, end: date) -> list[date]:
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


class TwseOpenApi:
    """Cross-check source. Satisfies CloseSource."""

    name = "twse"

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._http = httpx.Client(timeout=timeout_s)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_month(self, ticker: str, month_start: date) -> dict:
        resp = self._http.get(
            STOCK_DAY_URL,
            params={
                "date": month_start.strftime("%Y%m01"),
                "stockNo": ticker,
                "response": "json",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("stat") not in ("OK", None):
            raise TwseError(f"STOCK_DAY {ticker} {month_start:%Y-%m}: {payload.get('stat')}")
        return payload

    def fetch_closes(self, ticker: str, start: date, end: date) -> pl.DataFrame:
        frames = [
            parse_stock_day(self._get_month(ticker, m), ticker)
            for m in _month_starts(start, end)
        ]
        df = pl.concat(frames) if frames else parse_stock_day({}, ticker)
        return df.filter(pl.col("date").is_between(start, end)).select(
            "ticker", "date", "close"
        )
