"""One test per live decision rule; the live bot must act exactly like the backtest walk."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from src.live.decide import OpenTrade, decide
from src.meanrev import run as R


def make(values):
    """Closes on consecutive weekdays standing in for NYSE days; returns (closes, days, today)."""
    days = pd.bdate_range("2025-01-01", periods=len(values))
    return pd.Series(values, index=days, dtype=float), days, days[-1].date()


def trade(days, ago):
    """An open trade entered `ago` days before the last day."""
    return OpenTrade("1", "NAS100_USD", 5.0, days[-1 - ago].date())


def rising_then(tail):
    return np.r_[np.linspace(100, 200, 250), tail]


def run(values, open_trade=None, closed_today=False):
    closes, days, today = make(values)
    return decide("NAS100_USD", closes, days, today, open_trade, closed_today, 7.0)


class TestDecide(unittest.TestCase):
    def test_enter_on_signal_when_flat(self):
        d = run(rising_then([199, 198, 197, 196, 195.5]))
        self.assertEqual(d.action, "buy")
        self.assertEqual(d.units, 7.0)
        self.assertTrue(d.entry_signal)

    def test_no_entry_without_signal(self):
        d = run(rising_then([199, 198, 197, 196, 197]))
        self.assertEqual(d.action, "none")

    def test_no_entry_below_200_day_average(self):
        d = run(np.r_[np.linspace(200, 100, 250), [99, 98, 97, 96, 95]])
        self.assertEqual(d.action, "none")

    def test_exit_on_exit_signal(self):
        v = rising_then([199, 198, 197, 196, 195, 199])   # last close above 5-day average
        closes, days, _ = make(v)
        d = run(v, trade(days, 2))
        self.assertEqual(d.action, "close")
        self.assertTrue(d.exit_signal)

    def test_exit_signal_on_entry_day_does_not_count(self):
        v = rising_then([199, 198, 197, 196, 195, 199])
        closes, days, _ = make(v)
        d = run(v, trade(days, 0))
        self.assertTrue(d.exit_signal)
        self.assertEqual(d.action, "none")

    def test_missed_exit_is_caught_up(self):
        # Exit signal two days ago (jump), none today (falling again), trade still open.
        v = rising_then([199, 198, 197, 200, 190, 189])
        closes, days, _ = make(v)
        d = run(v, trade(days, 4))
        self.assertFalse(d.exit_signal)
        self.assertEqual(d.action, "close")
        self.assertIn("caught up", d.reason)

    def test_time_stop_on_10th_trading_day(self):
        v = rising_then(list(np.linspace(199, 150, 12)))   # keeps falling: no exit signals
        closes, days, _ = make(v)
        self.assertEqual(run(v, trade(days, R.MAX_HOLD)).action, "close")
        self.assertEqual(run(v, trade(days, R.MAX_HOLD - 1)).action, "none")

    def test_no_reentry_same_day_after_close(self):
        d = run(rising_then([199, 198, 197, 196, 195.5]), closed_today=True)
        self.assertTrue(d.entry_signal)
        self.assertEqual(d.action, "none")


class TestMatchesWalk(unittest.TestCase):
    def test_matches_backtest_walk(self):
        rng = np.random.RandomState(3)
        v = 100 + np.cumsum(rng.normal(0.15, 1.0, 400))
        closes, days, _ = make(v)
        entry, exit_, _ = R.signals(closes)
        expected = [t for t in R.walk(entry, exit_, np.ones(len(v), bool))]

        got, open_t, entered = [], None, None
        for i in range(len(v)):
            d = decide("X", closes.iloc[:i + 1], days[:i + 1], days[i].date(), open_t, False, 1.0)
            if d.action == "buy":
                open_t, entered = OpenTrade("1", "X", 1.0, days[i].date()), i
            elif d.action == "close":
                got.append((entered, i))
                open_t = None
        self.assertGreater(len(expected), 3)
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
