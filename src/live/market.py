"""Market data for the live job: H1 candles from the API in the backtests' shape."""
from __future__ import annotations

import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta

import pandas as pd

from src.download import COLUMNS, fetch_candles

NY = "America/New_York"
SIGNAL_HOUR = 15  # the 15:00 New York H1 candle is the signal candle
EARLY_CLOSE_LAST_OPEN_HOUR = 13  # on an early-close day nothing opens after 13:00
READY_HOUR = 16  # by 16:00 New York the 15:00 candle has closed


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Rows from fetch_candles -> frame like src.data.load_candles (UTC time index, float prices)."""
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    return df.astype(float)


def fetch_h1(client, instrument: str, today: date, days_back: int = 430) -> pd.DataFrame:
    """Completed H1 candles for the last `days_back` days.

    fetch_candles prints progress; send it to stderr so stdout stays clean for the JSON log.
    """
    start = (today - timedelta(days=days_back)).isoformat()
    with redirect_stdout(sys.stderr):
        rows = fetch_candles(client, instrument, start, "H1")
    return rows_to_frame(rows)


def todays_close_ready(h1: pd.DataFrame, today: date, now_utc: datetime) -> bool:
    """True when today's 15:00 New York candle exists, or it is an early-close day past 16:00.

    A day whose last candle is 14:00 (or later than 13:00) but has no 15:00 candle is a
    candle that has not arrived yet, not an early close, so we do nothing.
    """
    ny_index = h1.index.tz_convert(NY)
    opens = ny_index[[d == today for d in ny_index.date]]
    if any(t.hour == SIGNAL_HOUR for t in opens):
        return True
    if len(opens) == 0:
        return False
    now_ny = pd.Timestamp(now_utc).tz_convert(NY)
    return now_ny.hour >= READY_HOUR and opens.max().hour <= EARLY_CLOSE_LAST_OPEN_HOUR
