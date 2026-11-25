"""Tests for src.ict.stats: summary numbers, drawdown, random-entry control, truncation check."""
import unittest
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

from src.ict import stats
from src.ict.calendar import NY
from src.ict.setups import Order
from src.data import to_timeframe
from src.ict.setups import find_orders
from tests.test_ict_run import m1_day
from tests.test_ict_setups import DAILY, DAY, flat, frame, rows

DAYS = [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 12)]


def ts(day, hhmm):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY).tz_convert("UTC")


def trade_table(rs):
    n = len(rs)
    return pd.DataFrame({
        "instrument": ["NAS100_USD"] * n, "side": (["short", "long"] * n)[:n],
        "year": [2023] * (n // 2) + [2024] * (n - n // 2), "r": rs,
        "fill_time": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "stop_first": [True] + [False] * (n - 1), "cycle": [1, 2] + [1] * (n - 2),
    })


def synthetic_m1():
    return pd.concat([m1_day(d) for d in DAYS])


def synthetic_trades():
    """One short per day on the flat M1 data (bid 100, ask 101); target far below, stop far above."""
    rows = [{"instrument": "NAS100_USD", "day": d, "side": "short" if i % 2 else "long",
             "entry": 100.0, "stop": 110.0, "target": 90.0} for i, d in enumerate(DAYS)]
    return pd.DataFrame(rows)


class SummaryTests(unittest.TestCase):
    def test_summary_values(self):
        s = stats.summary(trade_table([1, -1, 2, -1]))
        self.assertEqual(s["n"], 4)
        self.assertAlmostEqual(s["mean_r"], 0.25)
        se = np.std([1, -1, 2, -1], ddof=1) / 2
        self.assertAlmostEqual(s["se"], se)
        self.assertAlmostEqual(s["mean_minus_1se"], 0.25 - se)
        self.assertAlmostEqual(s["mean_minus_2se"], 0.25 - 2 * se)
        self.assertEqual(s["stop_first"], 1)
        self.assertEqual(s["rearmed"], 1)
        self.assertEqual(s["by_side"]["short"]["n"], 2)
        self.assertEqual(s["by_year"][2024]["n"], 2)
        self.assertEqual(s["by_instrument"]["NAS100_USD"]["n"], 4)

    def test_drawdown(self):
        self.assertAlmostEqual(stats.max_drawdown_pct(trade_table([1, -1, -1, -1, 2])), 1.5)

    def test_drawdown_uses_fill_time_order(self):
        t = trade_table([1, -1, -1, -1, 2]).iloc[::-1]   # same trades, shuffled rows
        self.assertAlmostEqual(stats.max_drawdown_pct(t), 1.5)


class RandomControlTests(unittest.TestCase):
    def test_reproducible_and_length(self):
        m1, tr = synthetic_m1(), synthetic_trades()
        a = stats.random_control(tr, {"NAS100_USD": m1}, runs=20, seed=7)
        b = stats.random_control(tr, {"NAS100_USD": m1}, runs=20, seed=7)
        self.assertEqual(len(a), 20)
        self.assertEqual(a, b)

    def test_flat_market_ends_on_time_exit(self):
        # Flat prices: no stop or target is touched, so every control trade exits at 12:00 for a
        # loss of the spread plus slippage; all runs are identical and every trade is filled.
        m1, tr = synthetic_m1(), synthetic_trades()
        means, dropped = stats.random_control_dropped(tr, {"NAS100_USD": m1}, runs=5, seed=1)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(set(round(x, 9) for x in means)), 1)
        self.assertLess(means[0], 0)

    def _scripted(self, side, move):
        """One trade (stop 10 and target 20 from entry) on flat bars (bid 100, ask 101) that, from 11:05
        (after every control entry), swing `move` 'down' or 'up' inside one bar. Returns the control mean R."""
        m1 = m1_day(DAYS[0])
        bar = m1.index[m1.index >= ts(DAYS[0], "11:05")]
        m1.loc[bar[0], ["bid_l", "ask_l"] if move == "down" else ["bid_h", "ask_h"]] = \
            [70.0, 71.0] if move == "down" else [130.0, 131.0]
        m1.loc[bar[1:], ["bid_o", "bid_h", "bid_l", "bid_c"]] = 70.0 if move == "down" else 130.0
        m1.loc[bar[1:], ["ask_o", "ask_h", "ask_l", "ask_c"]] = 71.0 if move == "down" else 131.0
        tr = pd.DataFrame([{"instrument": "NAS100_USD", "day": DAYS[0], "side": side,
                            "entry": 100.0, "stop": 110.0 if side == "short" else 90.0,
                            "target": 80.0 if side == "short" else 120.0}])
        means, dropped = stats.random_control_dropped(tr, {"NAS100_USD": m1}, runs=4, seed=3)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(set(means)), 1)
        return means[0]

    def test_control_directions_and_distances(self):
        # Short enters at the bid (100): target 80, stop 110. Long enters at the ask (101): target 121,
        # stop 91. Stop exits pay 1.0 slippage; target exits are limit fills at the target.
        self.assertAlmostEqual(self._scripted("short", "down"), 2.0)     # target: (100-80)/10
        self.assertAlmostEqual(self._scripted("short", "up"), -1.1)      # stop 110 + slippage 1
        self.assertAlmostEqual(self._scripted("long", "up"), 2.0)        # target: (121-101)/10
        self.assertAlmostEqual(self._scripted("long", "down"), -1.1)     # stop 91 - slippage 1

    def test_no_m1_bars_are_dropped_and_counted(self):
        m1 = synthetic_m1()
        tr = synthetic_trades()
        tr.loc[0, "day"] = date(2024, 1, 15)     # a day with no data at all
        means, dropped = stats.random_control_dropped(tr, {"NAS100_USD": m1}, runs=3, seed=1)
        self.assertEqual(dropped, 3)             # that trade skipped in each run
        self.assertEqual(len(means), 3)


class TruncationTests(unittest.TestCase):
    def test_real_find_orders_passes(self):
        # The worked example as M1 bars (each M5 candle repeated 5 times), quiet until 11:55 so the
        # day is complete. The one-day M1 file has no daily history, so the daily frame of the setups
        # tests is injected; the real find_orders must still find the 09:00 order.
        m5 = frame(rows(flat("11:05", "11:55", 18060, 18045)))
        m1 = pd.concat([m5.set_axis(m5.index + pd.Timedelta(minutes=i)) for i in range(5)]).sort_index()
        m1["volume"] = 1
        m1.index.name = "time"
        with_daily = lambda m, d, *a: find_orders(m, DAILY, *a)
        full, _ = with_daily(to_timeframe(m1, "5min"), None, "NAS100_USD", DAY, "close")
        self.assertGreaterEqual(len(full), 1)
        self.assertTrue(stats.truncation_check("NAS100_USD", "close", cuts=25, m1=m1,
                                               find_orders_fn=with_daily))

    def test_truncation_catches_peek(self):
        def peeking(m5, daily, instrument, day, rule, bar):
            # Cheat: an order at 09:00 exists only if the 09:30 candle (the future) is in the data.
            if (m5.index >= ts(day, "09:30")).any():
                o = Order(instrument, day, "short", ts(day, "09:00"), 100.0, 110.0, 90.0, 105.0, {}, 1)
                return [o], Counter()
            return [], Counter()
        self.assertFalse(stats.truncation_check("NAS100_USD", "close", cuts=40, m1=synthetic_m1(),
                                                find_orders_fn=peeking))


if __name__ == "__main__":
    unittest.main()
