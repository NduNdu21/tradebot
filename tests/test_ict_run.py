"""Tests for src.ict.run: the daily runner (docs/strategy-ict.md §7).

The one-trade-per-day and pending rules are tested with stand-ins for find_orders and simulate, so
each test says exactly which orders exist and what happened to them. The slicing test uses the real
find_orders.
"""
import unittest
from collections import Counter
from datetime import date
from unittest import mock

import pandas as pd

from src.ict import run as runner
from src.ict.calendar import NY
from src.ict.fills import Result
from src.ict.setups import Order, find_orders
from tests.test_ict_setups import DAILY, DAY, flat, frame, rows

D = date(2024, 1, 11)


def ts(hhmm, day=D):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY).tz_convert("UTC")


def m1_day(day, first="07:00", last="12:30"):
    """Flat M1 bid/ask candles for one New York day (every slot present, so the day is not skipped)."""
    idx = pd.date_range(pd.Timestamp(f"{day} {first}", tz=NY), pd.Timestamp(f"{day} {last}", tz=NY),
                        freq="1min").tz_convert("UTC")
    n = len(idx)
    data = {"volume": [1] * n}
    for side, off in (("bid", 0.0), ("ask", 1.0)):
        for f in "ohlc":
            data[f"{side}_{f}"] = [100.0 + off] * n
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="time"))


def order(placed, cycle=1, day=D):
    return Order("NAS100_USD", day, "short", ts(placed, day), 18078.0, 18112.0, 17990.0, 18100.0, {}, cycle)


def filled(o, fill, r=1.0):
    return Result(o, "filled", fill_time=ts(fill), fill_price=o.entry, exit_time=ts("10:30"),
                  exit_price=o.target, exit_reason="target", r=r)


def cancelled(o, when, status="cancelled_stop"):
    return Result(o, status, resolved_time=ts(when))


def run_with(orders, results, m1, **kw):
    """Run one day with find_orders returning `orders` and simulate returning results[order]."""
    by_id = {id(o): r for o, r in zip(orders, results)}
    with mock.patch.object(runner, "find_orders", return_value=(list(orders), Counter({"no_shift": 2}))), \
            mock.patch.object(runner, "simulate", side_effect=lambda o, m: by_id[id(o)]):
        return runner.run("NAS100_USD", "close", m1=m1, start=D, end=D, **kw)


class TestOnePerDayRules(unittest.TestCase):
    def test_one_trade_per_day(self):
        a, b = order("09:00"), order("09:30", cycle=2)
        trades, skips = run_with([a, b], [filled(a, "09:05"), filled(b, "09:35")], m1_day(D))
        self.assertEqual(len(trades), 1)
        self.assertEqual(skips["one_per_day"], 1)
        self.assertEqual(skips["no_shift"], 2)  # find_orders' own skips are carried over
        self.assertEqual(skips["rearmed"], 0)

    def test_pending_blocks_second_order(self):
        # The first order is still unfilled at 09:30 (it is cancelled at 10:00): the second is busy.
        a, b = order("09:00"), order("09:30", cycle=2)
        trades, skips = run_with([a, b], [cancelled(a, "10:00"), filled(b, "09:35")], m1_day(D))
        self.assertEqual(len(trades), 0)
        self.assertEqual(skips["busy"], 1)

    def test_rearmed_order_after_cancel(self):
        # Cycle 1's order is cancelled at 09:20 by the new high; the cycle 2 order (09:30) then fills.
        a, b = order("09:00"), order("09:30", cycle=2)
        trades, skips = run_with([a, b], [cancelled(a, "09:20"), filled(b, "09:35")], m1_day(D))
        self.assertEqual(len(trades), 1)
        row = trades.iloc[0]
        self.assertEqual(row["cycle"], 2)
        self.assertEqual(skips["busy"], 0)
        self.assertEqual(skips["rearmed"], 1)

    def test_order_placed_before_fill_is_busy(self):
        a, b = order("09:00"), order("09:30", cycle=2)
        trades, skips = run_with([a, b], [filled(a, "09:45"), filled(b, "09:35")], m1_day(D))
        self.assertEqual(len(trades), 1)
        self.assertEqual(skips["busy"], 1)
        self.assertEqual(skips["one_per_day"], 0)

    def test_no_exit_data_counts_as_fill_but_not_a_trade(self):
        a, b = order("09:00"), order("09:30", cycle=2)
        broken = Result(a, "no_exit_data", fill_time=ts("09:05"), fill_price=a.entry)
        trades, skips = run_with([a, b], [broken, filled(b, "09:35")], m1_day(D))
        self.assertEqual(len(trades), 0)
        self.assertEqual(skips["no_exit_data"], 1)
        self.assertEqual(skips["one_per_day"], 1)

    def test_trade_row_columns_and_stop_first(self):
        a = order("09:00")
        res = filled(a, "09:05", r=-1.1)
        res.stop_first = True
        trades, skips = run_with([a], [res], m1_day(D))
        self.assertEqual(list(trades.columns), runner.TRADE_COLUMNS)
        row = trades.iloc[0]
        self.assertEqual((row["instrument"], row["day"], row["side"], row["year"]), ("NAS100_USD", D, "short", 2024))
        self.assertEqual((row["entry"], row["stop"], row["target"], row["r"]), (18078.0, 18112.0, 17990.0, -1.1))
        self.assertEqual(skips["stop_first"], 1)


class TestSkippedDays(unittest.TestCase):
    def test_skips_holiday_and_missing_day(self):
        holiday, bad, ok = date(2023, 7, 4), date(2023, 7, 5), date(2023, 7, 6)
        m1 = pd.concat([m1_day(holiday), m1_day(bad), m1_day(ok)])
        # One M5 slot (10:15-10:19) loses all its M1 bars.
        m1 = m1[~((m1.index >= ts("10:15", bad)) & (m1.index < ts("10:20", bad)))]
        with mock.patch.object(runner, "find_orders", return_value=([], Counter())) as fo:
            trades, skips = runner.run("NAS100_USD", "close", m1=m1, start=holiday, end=date(2023, 7, 7))
        self.assertEqual(len(trades), 0)
        self.assertEqual((skips["holiday"], skips["missing_m5"], skips["no_data"]), (1, 1, 1))  # 07-07 has no data
        self.assertEqual(fo.call_count, 1)  # only the good day reached find_orders

    def test_missing_m5_is_checked_on_m5_even_for_1min(self):
        # Every M1 minute is present except 10:15-10:19: gone on M5 and M1 alike.
        day = date(2023, 7, 6)
        m1 = m1_day(day)
        m1 = m1[~((m1.index >= ts("10:15", day)) & (m1.index < ts("10:20", day)))]
        trades, skips = runner.run("NAS100_USD", "close", exec_tf="1min", m1=m1, start=day, end=day)
        self.assertEqual(skips["missing_m5"], 1)

    def test_early_close(self):
        day = date(2023, 7, 3)  # NYSE 13:00 early close
        trades, skips = runner.run("NAS100_USD", "close", m1=m1_day(day), start=day, end=day)
        self.assertEqual(skips["early_close"], 1)


class TestSlicing(unittest.TestCase):
    def test_sliced_frame_gives_same_orders_as_full_frame(self):
        # Eight quiet days before the worked-example day and a quiet tail after it.
        parts = [frame(flat("00:00", "23:55", 17950, 17940), day=date(2024, 1, d)) for d in range(2, 11)]
        before = frame(flat("00:00", "06:45", 17950, 17940), day=D)
        worked = frame(rows(), day=D)
        after = frame(flat("11:05", "23:55", 18060, 18045), day=D)
        full = pd.concat(parts + [before, worked, after, frame(flat("00:00", "23:55", 18060, 18045), day=date(2024, 1, 12))])
        self.assertTrue(full.index.is_monotonic_increasing)
        want, want_skips = find_orders(full, DAILY, "NAS100_USD", D, "close")
        window = runner.signal_window(full, D)
        self.assertLess(len(window), len(full))
        got, got_skips = find_orders(window, DAILY, "NAS100_USD", D, "close")
        self.assertEqual(len(want), 1)
        self.assertEqual(got, want)
        self.assertEqual(got_skips, want_skips)


if __name__ == "__main__":
    unittest.main()
