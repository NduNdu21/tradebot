"""Tests for src.data: H1 resampling, data checks, and the hold-out cut-off."""
import os
import tempfile
import unittest

import pandas as pd

from src.data import HOLDOUT_START, check_candles, find_gaps, load_m5, to_h1


def make_m5(times, bid_prices, spread=0.0002):
    """Build an M5 frame where each candle's bid o/h/l/c come from one tuple."""
    rows = []
    for o, h, l, c in bid_prices:
        rows.append({"volume": 10,
                     "bid_o": o, "bid_h": h, "bid_l": l, "bid_c": c,
                     "ask_o": o + spread, "ask_h": h + spread,
                     "ask_l": l + spread, "ask_c": c + spread})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="time"))


class ToH1Test(unittest.TestCase):
    def test_combines_m5_into_hour_candles(self):
        # 10:00-10:55 (12 candles) then two candles in the 11:00 hour.
        times = list(pd.date_range("2024-01-02 10:00", periods=12, freq="5min")) + \
            ["2024-01-02 11:00", "2024-01-02 11:05"]
        prices = [(1.10, 1.11, 1.09, 1.10)] * 12 + [(1.20, 1.25, 1.18, 1.22), (1.22, 1.30, 1.21, 1.28)]
        prices[0] = (1.05, 1.11, 1.09, 1.10)    # first open of the hour
        prices[5] = (1.10, 1.15, 1.09, 1.10)    # hour high
        prices[7] = (1.10, 1.11, 1.01, 1.10)    # hour low
        prices[11] = (1.10, 1.11, 1.09, 1.07)   # last close of the hour
        h1 = to_h1(make_m5(times, prices))

        self.assertEqual(len(h1), 2)
        first = h1.iloc[0]
        self.assertEqual(h1.index[0], pd.Timestamp("2024-01-02 10:00", tz="UTC"))
        self.assertEqual((first.bid_o, first.bid_h, first.bid_l, first.bid_c), (1.05, 1.15, 1.01, 1.07))
        self.assertEqual(first.volume, 120)
        second = h1.iloc[1]
        self.assertEqual((second.bid_o, second.bid_h, second.bid_l, second.bid_c), (1.20, 1.30, 1.18, 1.28))

    def test_mid_is_average_of_bid_and_ask(self):
        h1 = to_h1(make_m5(["2024-01-02 10:00"], [(1.1000, 1.1010, 1.0990, 1.1005)], spread=0.0002))
        self.assertAlmostEqual(h1.iloc[0].mid_c, 1.1006)
        self.assertAlmostEqual(h1.iloc[0].mid_h, 1.1011)

    def test_empty_hours_are_dropped(self):
        h1 = to_h1(make_m5(["2024-01-02 10:00", "2024-01-02 13:00"], [(1.1, 1.1, 1.1, 1.1)] * 2))
        self.assertEqual(len(h1), 2)


class CheckCandlesTest(unittest.TestCase):
    def test_clean_data_passes(self):
        df = make_m5(["2024-01-02 10:00", "2024-01-02 10:05"], [(1.1, 1.2, 1.0, 1.1)] * 2)
        self.assertEqual(check_candles(df), [])

    def test_flags_duplicates_and_crossed_prices(self):
        df = make_m5(["2024-01-02 10:00", "2024-01-02 10:00"], [(1.1, 1.2, 1.0, 1.1)] * 2)
        df.iloc[1, df.columns.get_loc("ask_c")] = 1.05  # ask below bid close
        problems = check_candles(df)
        self.assertTrue(any("duplicate" in p for p in problems))
        self.assertTrue(any("ask is below bid" in p for p in problems))

    def test_flags_high_below_close(self):
        df = make_m5(["2024-01-02 10:00"], [(1.1, 1.1, 1.0, 1.15)])
        self.assertTrue(any("high/low" in p for p in check_candles(df)))


class FindGapsTest(unittest.TestCase):
    def test_weekend_close_is_not_a_gap_but_midweek_hole_is(self):
        times = ["2024-01-05 21:55",   # Friday, last candle before the weekend
                 "2024-01-07 22:00",   # Sunday reopen
                 "2024-01-08 10:00",   # Monday
                 "2024-01-08 14:00"]   # 4-hour hole midweek
        gaps = find_gaps(make_m5(times, [(1.1, 1.1, 1.1, 1.1)] * 4), min_gap="1h")
        lengths = list(gaps["length"])
        self.assertIn(pd.Timedelta("4h"), lengths)
        self.assertNotIn(pd.Timedelta("2 days 00:05:00"), lengths)


class HoldoutCutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        times = [HOLDOUT_START - pd.Timedelta("5min"), HOLDOUT_START, HOLDOUT_START + pd.Timedelta("5min")]
        df = make_m5(times, [(1.1, 1.1, 1.1, 1.1)] * 3)
        df.index = df.index.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        df.to_csv(os.path.join(self.tmp.name, "EURUSD_M5.csv"), index_label="time")

    def tearDown(self):
        self.tmp.cleanup()

    def test_holdout_is_hidden_by_default(self):
        df = load_m5("EURUSD", data_dir=self.tmp.name)
        self.assertEqual(len(df), 1)
        self.assertTrue((df.index < HOLDOUT_START).all())

    def test_holdout_only_with_explicit_flag(self):
        df = load_m5("EURUSD", include_holdout=True, data_dir=self.tmp.name)
        self.assertEqual(len(df), 3)


if __name__ == "__main__":
    unittest.main()
