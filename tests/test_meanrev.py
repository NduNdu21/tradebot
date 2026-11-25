"""One test per mean-reversion rule (docs/strategy-mean-reversion.md)."""
import unittest

import numpy as np
import pandas as pd

from src.meanrev import run as R


class TestSignals(unittest.TestCase):
    def test_entry_needs_5_day_low_and_uptrend(self):
        close = pd.Series(np.r_[np.linspace(100, 200, 250), [199, 198, 197, 196, 195.5]])
        entry, exit_, up = R.signals(close)
        self.assertTrue(entry[-1])                  # lowest of last 5, above 200-day average
        self.assertFalse(entry[200])                # rising: not a 5-day low

    def test_no_entry_below_200_day_average(self):
        close = pd.Series(np.r_[np.linspace(200, 100, 250), [99, 98, 97, 96, 95]])
        entry, _, up = R.signals(close)
        self.assertFalse(entry[-1])
        self.assertFalse(up[-1])
        entry_nf, _, _ = R.signals(close, use_filter=False)
        self.assertTrue(entry_nf[-1])

    def test_tie_with_earlier_close_counts_as_low(self):
        close = pd.Series(np.r_[np.linspace(100, 200, 250), [190, 195, 196, 197, 190]])
        self.assertTrue(R.signals(close)[0][-1])

    def test_exit_when_close_above_5_day_average(self):
        close = pd.Series(np.r_[np.ones(250) * 100, [100, 100, 100, 100, 101]])
        self.assertTrue(R.signals(close)[1][-1])
        self.assertFalse(R.signals(close)[1][-2])   # equal to the average is not above it


class TestWalk(unittest.TestCase):
    def test_enter_then_exit_on_a_later_signal_day(self):
        entry = np.array([1, 0, 0, 0, 0], bool)
        exit_ = np.array([1, 0, 1, 0, 0], bool)    # exit on the entry day itself doesn't count
        self.assertEqual(R.walk(entry, exit_, np.ones(5, bool)), [(0, 2)])

    def test_time_stop_after_10_days(self):
        n = 15
        entry = np.zeros(n, bool); entry[0] = True
        self.assertEqual(R.walk(entry, np.zeros(n, bool), np.ones(n, bool)), [(0, 10)])

    def test_exit_waits_for_an_execution_price(self):
        entry = np.array([1, 0, 0, 0], bool)
        exit_ = np.array([0, 1, 0, 0], bool)
        can = np.array([1, 0, 0, 1], bool)
        self.assertEqual(R.walk(entry, exit_, can), [(0, 3)])

    def test_new_entry_only_after_exit_day(self):
        entry = np.array([1, 1, 1, 1], bool)
        exit_ = np.array([0, 1, 0, 1], bool)
        self.assertEqual(R.walk(entry, exit_, np.ones(4, bool)), [(0, 1), (2, 3)])

    def test_trade_open_at_end_is_not_counted(self):
        entry = np.array([1, 0, 0], bool)
        self.assertEqual(R.walk(entry, np.zeros(3, bool), np.ones(3, bool)), [])


class TestExecution(unittest.TestCase):
    def test_signal_on_15_00_close_execution_at_next_candle_open(self):
        ny = pd.DatetimeIndex(["2024-06-03 14:00", "2024-06-03 15:00", "2024-06-03 16:00"]).tz_localize(R.NY)
        h1 = pd.DataFrame({"bid_c": [1.0, 2.0, 3.0], "ask_c": [1.0, 2.2, 3.0], "bid_o": [0, 0, 5.0],
                           "ask_o": [0, 0, 5.2]}, index=ny.tz_convert("UTC"))
        se = R.signal_and_exec(h1)
        self.assertAlmostEqual(se.loc["2024-06-03", "close"], 2.1)
        self.assertEqual(se.loc["2024-06-03", "exec_bid"], 5.0)
        self.assertEqual(se.loc["2024-06-03", "exec_ask"], 5.2)

    def test_trade_pnl_parts(self):
        m = R.Instrument.__new__(R.Instrument)
        m.units = np.array([2.0, 2.0]); m.fx_exit = np.array([0.8, 0.8])
        m.bid, m.ask = np.array([99.0, 109.0]), np.array([101.0, 111.0])
        m.mid = (m.bid + m.ask) / 2; m.slip = 0.5
        m.rate = np.array([-0.05, -0.05]); m.cum_fin = np.array([0, 4])
        price, cost, fin = m.trade_pnl(np.array([0]), np.array([1]))
        self.assertAlmostEqual(price[0], 2 * 10 * 0.8)
        self.assertAlmostEqual(cost[0], -2 * (1.5 + 1.5) * 0.8)
        self.assertAlmostEqual(fin[0], 2 * 100 * -0.05 * 4 / 365 * 0.8)


if __name__ == "__main__":
    unittest.main()
