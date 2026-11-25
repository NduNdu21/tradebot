"""One test per trend + carry rule (docs/strategy-trend-carry.md)."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from src.trend import bars as B
from src.trend import rates as R
from src.trend import signals as S
from src.trend import sim as M
from src.trend import evaluate as E


def h1(start_utc, n, price=1.0, spread=0.0002):
    idx = pd.date_range(start_utc, periods=n, freq="1h", tz="UTC")
    p = pd.Series(price, index=idx, dtype=float)
    return pd.DataFrame({"bid_o": p, "bid_h": p + 0.001, "bid_l": p - 0.001, "bid_c": p,
                         "ask_o": p + spread, "ask_h": p + 0.001 + spread, "ask_l": p - 0.001 + spread,
                         "ask_c": p + spread, "volume": 1}, index=idx)


class TestBars(unittest.TestCase):
    def test_daily_bar_runs_17_to_17_new_york_named_after_close(self):
        # 2024-01-08 (Mon) 16:00 NY = 21:00 UTC belongs to Monday's bar; 17:00 NY belongs to Tuesday's.
        labels = B.bar_labels(pd.DatetimeIndex(["2024-01-08 21:00", "2024-01-08 22:00"], tz="UTC"))
        self.assertEqual(list(labels.strftime("%Y-%m-%d")), ["2024-01-08", "2024-01-09"])

    def test_sunday_open_joins_monday_bar(self):
        labels = B.bar_labels(pd.DatetimeIndex(["2024-01-07 22:00"], tz="UTC"))  # Sun 17:00 NY
        self.assertEqual(labels[0].strftime("%Y-%m-%d"), "2024-01-08")

    def test_weekend_candles_are_dropped(self):
        # Fri 16:00 NY kept; Fri 17:00 NY, Sat, Sun 16:00 NY dropped; Sun 17:00 NY kept.
        idx = pd.DatetimeIndex(["2024-01-05 21:00", "2024-01-05 22:00", "2024-01-06 12:00",
                                "2024-01-07 21:00", "2024-01-07 22:00"], tz="UTC")
        self.assertEqual(list(B.in_trading_week(idx)), [True, False, False, False, True])

    def test_holiday_stub_merges_into_next_bar(self):
        idx = pd.DatetimeIndex(list(pd.date_range("2024-01-08 22:00", periods=3, freq="1h", tz="UTC"))
                               + list(pd.date_range("2024-01-09 22:00", periods=24, freq="1h", tz="UTC")))
        labels = B.merged_labels(idx)
        self.assertEqual(set(labels.dt.strftime("%Y-%m-%d")), {"2024-01-10"})

    def test_execution_is_the_08_00_new_york_open_of_the_bar(self):
        df = h1("2024-01-07 22:00", 48)                  # Sun 17:00 NY onwards
        df.loc[pd.Timestamp("2024-01-08 13:00", tz="UTC"), ["bid_o", "ask_o"]] = [1.5, 1.5002]  # 08:00 NY
        bars = B.daily_bars(df)
        self.assertAlmostEqual(bars.loc["2024-01-08", "exec_bid"], 1.5)
        self.assertAlmostEqual(bars.loc["2024-01-08", "exec_ask"], 1.5002)

    def test_missing_08_00_candle_uses_next_candle_same_day(self):
        df = h1("2024-01-07 22:00", 48).drop(pd.Timestamp("2024-01-08 13:00", tz="UTC"))
        df.loc[pd.Timestamp("2024-01-08 14:00", tz="UTC"), "bid_o"] = 1.7
        self.assertAlmostEqual(B.daily_bars(df).loc["2024-01-08", "exec_bid"], 1.7)

    def test_atr_is_simple_mean_of_true_range(self):
        bars = pd.DataFrame({"h": [2, 3, 4.0], "l": [1, 1, 3.0], "c": [1.5, 2, 3.5]})
        # TR: 1, 2, max(1, 2, 1) = 2 -> mean of last 2 = 2
        self.assertAlmostEqual(B.atr(bars, 2).iloc[-1], 2.0)


class TestRates(unittest.TestCase):
    def test_month_is_used_only_from_next_month_and_markup_applies(self):
        with tempfile.TemporaryDirectory() as d:
            for ccy, cc in R.FRED_CODE.items():
                val = 5.0 if ccy == "AUD" else 1.0
                pd.DataFrame({"observation_date": ["2024-01-01", "2024-02-01"], "v": [val, val + 1]}).to_csv(
                    os.path.join(d, f"IRSTCI01{cc}M156N.csv"), index=False)
            rates = R.load_rates(d)
            days = pd.DatetimeIndex(["2024-01-31", "2024-02-01", "2024-03-05"])
            self.assertTrue(np.isnan(R.rate_on(rates, "AUD", days).iloc[0]))   # January's value not known yet
            self.assertAlmostEqual(R.rate_on(rates, "AUD", days).iloc[1], 0.05)  # January's, from Feb 1
            self.assertAlmostEqual(R.rate_on(rates, "AUD", days).iloc[2], 0.06)  # February's, from Mar 1
            long, short = R.financing_rates(rates, "AUD_USD", days[1:])
            self.assertAlmostEqual(long[0], 0.04 - 0.01)
            self.assertAlmostEqual(short[0], -0.04 - 0.01)


class TestSignals(unittest.TestCase):
    def test_trend_score_is_mean_of_three_signs(self):
        close = pd.Series(np.arange(300, dtype=float))
        self.assertTrue(np.isnan(S.trend_score(close).iloc[251]))
        self.assertAlmostEqual(S.trend_score(close).iloc[252], 1.0)
        close.iloc[-1] = close.iloc[-64] - 1        # down over 63 bars only
        self.assertAlmostEqual(S.trend_score(close).iloc[-1], 1 / 3)

    def test_carry_needs_to_beat_the_markup(self):
        sig = S.carry_signal([0.005, -0.02, -0.01], [-0.025, 0.0, 0.003])
        self.assertEqual(list(sig), [1.0, 0.0, -1.0])

    def test_unit_risks_25_gbp_per_atr(self):
        self.assertAlmostEqual(S.unit_size(0.01, 0.8) * 0.01 * 0.8, 25.0)

    def test_target_is_sum_of_sleeves_times_unit(self):
        t = S.target_units(pd.Series([1 / 3, 1.0, np.nan]), pd.Series([1.0, -1.0, 1.0]), pd.Series([300.0] * 3))
        self.assertEqual(list(t), [400.0, 0.0, 0.0])

    def test_sleeve_switch(self):
        t = S.target_units(pd.Series([1.0]), pd.Series([1.0]), pd.Series([100.0]), w_carry=0.0)
        self.assertEqual(t.iloc[0], 100.0)


class TestSim(unittest.TestCase):
    def test_trade_on_sign_change_or_more_than_25_percent_drift(self):
        self.assertTrue(M.needs_trade(100, -100))
        self.assertTrue(M.needs_trade(0, 100))
        self.assertTrue(M.needs_trade(100, 0))
        self.assertFalse(M.needs_trade(100, 125))
        self.assertTrue(M.needs_trade(100, 134))
        self.assertFalse(M.needs_trade(100, 80))

    def test_target_acted_on_next_bar_and_waits_without_exec_candle(self):
        held = M.positions([100, 100, 100, 0], [True, False, True, True])
        self.assertEqual(list(held), [0, 0, 100, 100])

    def test_no_stop_position_only_changes_with_target(self):
        held = M.positions([100] * 5, [True] * 5)
        self.assertEqual(list(held), [0, 100, 100, 100, 100])

    def test_slippage(self):
        self.assertAlmostEqual(M.slippage("EUR_USD"), 0.00005)
        self.assertAlmostEqual(M.slippage("GBP_JPY"), 0.01)
        self.assertAlmostEqual(M.slippage("USD_JPY"), 0.005)

    def test_pnl_buy_at_ask_plus_slip_mark_at_mid_close_with_wednesday_triple_financing(self):
        idx = pd.DatetimeIndex(["2024-01-09", "2024-01-10"])   # Tue, Wed
        bars = pd.DataFrame({"c": [1.1000, 1.1010], "exec_bid": [np.nan, 1.1000], "exec_ask": [np.nan, 1.1002]},
                            index=idx)
        out = M.daily_pnl(np.array([0.0, 1000.0]), bars, np.array([0.0365, 0.0365]), np.array([-0.05, -0.05]),
                          np.array([0.8, 0.8]), "EUR_USD")
        fill = 1.1002 + 0.00005
        self.assertAlmostEqual(out["price"][1] + out["cost"][1], 1000 * (1.1010 - fill) * 0.8)
        self.assertAlmostEqual(out["fin"][1], 1000 * 1.1010 * 0.0365 * 3 / 365 * 0.8)

    def test_short_pays_short_rate(self):
        idx = pd.DatetimeIndex(["2024-01-08", "2024-01-09"])   # Mon, Tue
        bars = pd.DataFrame({"c": [1.0, 1.0], "exec_bid": [1.0, 1.0], "exec_ask": [1.0, 1.0]}, index=idx)
        out = M.daily_pnl(np.array([-100.0, -100.0]), bars, np.array([0.1, 0.1]), np.array([-0.0365, -0.0365]),
                          np.ones(2), "EUR_USD")
        self.assertAlmostEqual(out["fin"][1], -100 * 0.0365 / 365)


class TestEvaluate(unittest.TestCase):
    def test_episodes(self):
        ep, n = E.episodes(np.array([0, 5, 5, -3, -3, 0, 2]))
        self.assertEqual(list(ep), [-1, 1, 1, 2, 2, -1, 4])
        self.assertEqual(n, 5)

    def test_max_drawdown(self):
        self.assertAlmostEqual(E.max_drawdown([100, -300, 50, 500]), 0.03)

    def test_checks(self):
        out = {"primary": {"sharpe": 0.5}, "sub_periods": {"a": 1, "b": -1, "c": 2},
               "control": {"p95": 0.3}, "sharpe_without_best": 0.25}
        self.assertTrue(all(E.checks(out).values()))
        out["sub_periods"] = {"a": 1, "b": -1, "c": -2}
        self.assertFalse(E.checks(out)["positive in >= 2 of 3 sub-periods"])
        self.assertFalse(E.holdout_checks({"primary": {"sharpe": 0.1, "max_drawdown_pct": 15.0}})["max drawdown < 15%"])


if __name__ == "__main__":
    unittest.main()
