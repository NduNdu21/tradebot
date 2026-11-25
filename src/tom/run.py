"""Turn-of-the-month trades on US index CFDs (docs/strategy-turn-of-month.md §3–§5).

Every trade, real or control, is "buy at the close of NYSE day d, sell at the close of
day d+4". `four_day_trades` prices that trade for every day d, so the strategy picks
the turn-of-month days and the random-timing control picks random ones, with
identical sizing and costs.
"""
import os

import numpy as np
import pandas as pd

from src.data import load_candles
from src.trend.bars import atr, daily_bars
from src.trend.rates import load_rates, rate_on
from src.tom.nyse import trading_days

INSTRUMENTS = ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD"]
DIVIDEND = {"SPX500_USD": 0.020, "NAS100_USD": 0.009, "US30_USD": 0.022, "US2000_USD": 0.014}
MARKUP = 0.025
RISK_GBP = 25.0
PRECISION = 2
HOLD = 4                          # trading days: day -2 close -> day +3 close
CLOSE_HOUR = 15                   # the H1 candle opening 15:00 New York closes at the 16:00 cash close
FIN_DAYS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 3}   # OANDA CFD schedule (Mon = 0), from the API 2026-11-25
DEV = (pd.Timestamp("2006-01-02"), pd.Timestamp("2024-11-13"))
HOLDOUT = (pd.Timestamp("2024-11-14"), pd.Timestamp("2026-11-13"))
OUT_DIR = os.path.join("data", "tom")
NY = "America/New_York"


def closes(h1):
    """Bid/ask 'close' per New York date: the 15:00 candle's close, else the last candle opening before 15:00."""
    ny = h1.index.tz_convert(NY)
    df = h1[["bid_c", "ask_c"]].copy()
    df["date"] = ny.tz_localize(None).normalize()
    df["hour"] = ny.hour
    df = df[df["hour"] <= CLOSE_HOUR]
    # Prefer hour 15; otherwise the latest earlier candle of the day.
    last = df.sort_values(["date", "hour"]).groupby("date").last()
    return last[["bid_c", "ask_c"]].rename(columns={"bid_c": "bid", "ask_c": "ask"})


def tom_entry_days(days):
    """Day -2 of every month: the trading day before the month's last trading day."""
    s = pd.Series(days, index=days)
    last = s.groupby([days.year, days.month]).max()
    pos = days.get_indexer(pd.DatetimeIndex(last.values)) - 1
    return days[pos[pos >= 0]]


def financing_days(entry, exit_):
    """Days charged for the 17:00 rollovers between a 16:00 entry and a 16:00 exit."""
    return sum(FIN_DAYS.get(d.weekday(), 0) for d in pd.date_range(entry, exit_ - pd.Timedelta(days=1)))


def latest_before(series, days):
    """Value of a daily-bar series from the last bar dated strictly before each day."""
    s = series.dropna()
    idx = s.index.searchsorted(days, side="left") - 1
    out = np.where(idx >= 0, s.values[np.maximum(idx, 0)], np.nan)
    return pd.Series(out, index=days)


def on_or_before(series, days):
    """Value from the last bar dated on or before each day (days may repeat)."""
    s = series.dropna()
    idx = s.index.searchsorted(days, side="right") - 1
    return pd.Series(np.where(idx >= 0, s.values[np.maximum(idx, 0)], np.nan), index=days)


def slippage(cl, days, span=DEV):
    d = cl.reindex(days)
    d = d[(d.index >= span[0]) & (d.index <= span[1])]
    return float(((d["ask"] - d["bid"]) / 2).median())


def four_day_trades(inst, h1, gbpusd_bars, rates, days, slip):
    """GBP P&L of buying at day d's close and selling at day d+4's close, for every trading day d.

    Returns a DataFrame indexed by entry day: exit day, units, price/cost/fin parts (GBP), total.
    Rows with a missing close at entry or exit have NaN total (skipped).
    """
    cl = closes(h1).reindex(days)
    bars = daily_bars(h1)
    a = latest_before(atr(bars, 20), days)               # last completed daily bar before the entry
    usd_to_gbp_entry = 1 / latest_before(gbpusd_bars["c"], days)
    units = (RISK_GBP / (a * usd_to_gbp_entry)).round(PRECISION)

    exit_days = pd.Series(days, index=days).shift(-HOLD)
    entry_bid, entry_ask = cl["bid"].values, cl["ask"].values
    ex = cl.shift(-HOLD)
    mid_in, mid_out = (entry_bid + entry_ask) / 2, (ex["bid"].values + ex["ask"].values) / 2
    fill_in = entry_ask + slip
    fill_out = ex["bid"].values - slip

    rate = DIVIDEND[inst] - rate_on(rates, "USD", days).values - MARKUP
    fdays = np.array([financing_days(d, x) if pd.notna(x) else 0 for d, x in zip(days, exit_days)])
    fx_exit = (1 / on_or_before(gbpusd_bars["c"], pd.DatetimeIndex(exit_days.fillna(days[-1])))).values

    out = pd.DataFrame(index=days)
    out["exit"] = exit_days.values
    out["units"] = units.values
    out["price"] = units.values * (mid_out - mid_in) * fx_exit
    out["cost"] = -units.values * ((fill_in - mid_in) + (mid_out - fill_out)) * fx_exit
    out["fin"] = units.values * mid_in * rate * fdays / 365 * fx_exit
    out["total"] = out["price"] + out["cost"] + out["fin"]
    out.loc[out["exit"].isna() | np.isnan(mid_in) | np.isnan(mid_out) | out["units"].isna(), "total"] = np.nan
    return out


def build(include_holdout=False):
    end = HOLDOUT[1] if include_holdout else pd.Timestamp("2024-11-13")
    days = trading_days("2005-01-03", end + pd.Timedelta(days=10))
    gbpusd = daily_bars(load_candles("GBP_USD", "H1", include_holdout))
    rates = load_rates()
    trades, slips = {}, {}
    for inst in INSTRUMENTS:
        h1 = load_candles(inst, "H1", include_holdout)
        slips[inst] = slippage(closes(h1), days)
        trades[inst] = four_day_trades(inst, h1, gbpusd, rates, days, slips[inst])
    return trades, slips, days
