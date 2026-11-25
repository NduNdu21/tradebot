"""Daily runner: find each day's orders, simulate them, and keep at most one trade per day (§7).

For every weekday it asks find_orders (setups.py) for the day's orders and simulate (fills.py) what
happened to each, then applies the one-trade-per-day rules the order finder leaves to the caller:
  busy         an order placed while an earlier order is still pending is dropped
  one_per_day  after a fill, later orders are dropped
Days that cannot be traded are counted, not run: holiday, early_close, no_data, missing_m5.

Run `python -m src.ict.run` to write the trade tables for every variant to data/ict/.
"""
import json
import os
import time
from collections import Counter
from datetime import date, timedelta
from typing import Optional, Tuple

import pandas as pd

from src.data import load_candles, to_timeframe
from src.ict.calendar import NY, is_skipped_market_day, missing_m5, session_times
from src.ict.fills import simulate
from src.ict.setups import LOOKBACK, find_orders, to_daily

INSTRUMENTS = ("NAS100_USD", "SPX500_USD")
# Development period only (§1): the hold-out starts after this and is never touched here.
FIRST_DAY = date(2016, 11, 14)
LAST_DAY = date(2024, 11, 13)
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ict")

TRADE_COLUMNS = ["instrument", "day", "side", "placed_time", "entry", "stop", "target", "fill_time",
                 "fill_price", "exit_time", "exit_price", "exit_reason", "r", "stop_first", "cycle", "year"]


def _between(df: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """Rows with lo <= index <= hi. searchsorted slices the sorted index; no mask over the history."""
    return df.iloc[df.index.searchsorted(lo, side="left"):df.index.searchsorted(hi, side="right")]


def signal_window(sig: pd.DataFrame, day: date) -> pd.DataFrame:
    """The part of the signal candles find_orders reads for `day`.

    find_orders only looks at candles from 5 days (LOOKBACK) before 08:30 up to 11:00; the 24 h
    target window is inside that. Passing just this slice gives the same orders as the full frame
    (tested) while keeping each day's work small."""
    times = session_times(day)
    return _between(sig, times["levels"] - LOOKBACK, times["entry_end"])


def run(instrument: str, shift_rule: str, exec_tf: str = "5min", m1: Optional[pd.DataFrame] = None,
        start: date = FIRST_DAY, end: date = LAST_DAY) -> Tuple[pd.DataFrame, Counter]:
    """Backtest one instrument and shift rule: (trade table, Counter of skipped days and setups).

    `exec_tf` is the signal chart ("5min" primary; "1min" and "2min" for the §10 sensitivity).
    Fills are always simulated on M1. `m1` can be passed in (tests); otherwise it is loaded.
    """
    if m1 is None:
        m1 = load_candles(instrument, "M1")  # development data only: the hold-out is cut at load
    sig = to_timeframe(m1, exec_tf)
    # The data-gap check is always on M5 candles, whatever the signal chart is.
    m5 = sig if exec_tf == "5min" else to_timeframe(m1, "5min")
    daily = to_daily(m1)
    bar = pd.Timedelta(exec_tf)

    trades, counts = [], Counter()
    day = start
    while day <= end:
        this_day, day = day, day + timedelta(days=1)
        if this_day.weekday() >= 5:
            continue
        reason = is_skipped_market_day(this_day)
        if reason is None:
            midnight = pd.Timestamp(this_day, tz=NY)
            m5_day = _between(m5, midnight, midnight + pd.Timedelta("1 day"))
            if m5_day.empty:
                reason = "no_data"
            elif missing_m5(m5_day, this_day):
                reason = "missing_m5"
        if reason:
            counts[reason] += 1
            continue

        orders, skips = find_orders(signal_window(sig, this_day), daily, instrument, this_day, shift_rule, bar)
        counts.update(skips)
        _simulate_day(orders, m1, trades, counts)

    table = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    return table, counts


def _simulate_day(orders, m1, trades, counts):
    """Apply the busy / one-per-day rules to one day's orders (in placed_time order)."""
    last = None  # result of the most recent order that was actually simulated
    for o in orders:
        if last is not None:
            if last.fill_time is not None and last.fill_time <= o.placed_time:
                counts["one_per_day"] += 1   # an earlier order has already filled
                continue
            # Pending until it fills (fill_time) or is cancelled (resolved_time).
            until = last.fill_time if last.fill_time is not None else last.resolved_time
            if until > o.placed_time:
                counts["busy"] += 1          # the earlier order is still waiting for a fill
                continue
        res = simulate(o, m1)
        last = res
        if res.status == "no_exit_data":
            counts["no_exit_data"] += 1      # filled, but no bar to exit on: not a trade row
        elif res.status == "filled":
            counts["stop_first"] += int(res.stop_first)
            counts["rearmed"] += int(o.cycle >= 2)
            trades.append({
                "instrument": o.instrument, "day": o.day, "side": o.side, "placed_time": o.placed_time,
                "entry": o.entry, "stop": o.stop, "target": o.target, "fill_time": res.fill_time,
                "fill_price": res.fill_price, "exit_time": res.exit_time, "exit_price": res.exit_price,
                "exit_reason": res.exit_reason, "r": res.r,
                "stop_first": res.stop_first, "cycle": o.cycle, "year": o.day.year,
            })
        # A cancelled order leaves `last` with resolved_time set; later orders may then be placed.


# (shift rule, signal chart): both rules on M5, plus the §10 sensitivities for the close rule.
VARIANTS = [("close", "5min"), ("wick", "5min"), ("close", "1min"), ("close", "2min")]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    began = time.time()
    for instrument in INSTRUMENTS:
        m1 = load_candles(instrument, "M1")
        for rule, tf in VARIANTS:
            t0 = time.time()
            table, counts = run(instrument, rule, tf, m1=m1)
            name = f"{instrument}_{rule}_{tf}"
            table.to_csv(os.path.join(OUT_DIR, f"trades_{name}.csv"), index=False)
            with open(os.path.join(OUT_DIR, f"skips_{name}.json"), "w") as f:
                json.dump(dict(counts), f, indent=2, sort_keys=True)
            print(f"{name}: {len(table)} trades ({time.time() - t0:.0f}s)", flush=True)
    print(f"total run time {time.time() - began:.0f}s")


if __name__ == "__main__":
    main()
