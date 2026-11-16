"""Tests for src.diagnostics: zero-cost data and config, development-year windows."""
import unittest

import pandas as pd

from src.data import DEV_START, HOLDOUT_START
from src.diagnostics import dev_years, with_year_windows, zero_cost_config, zero_spread
from tests.test_experiments import setups, ts


class ZeroCostTest(unittest.TestCase):
    def test_bid_and_ask_become_the_mid(self):
        df = pd.DataFrame({"bid_o": [1.0998], "bid_h": [1.1008], "bid_l": [1.0990], "bid_c": [1.1000],
                           "ask_o": [1.1002], "ask_h": [1.1012], "ask_l": [1.0996], "ask_c": [1.1004],
                           "volume": [7]})
        out = zero_spread(df)
        for f, mid in zip("ohlc", (1.1000, 1.1010, 1.0993, 1.1002)):
            self.assertAlmostEqual(out[f"bid_{f}"][0], mid)
            self.assertAlmostEqual(out[f"ask_{f}"][0], mid)
        self.assertEqual(out.volume[0], 7)
        self.assertAlmostEqual(df.bid_o[0], 1.0998)   # input untouched

    def test_no_slippage_but_otherwise_primary(self):
        cfg = zero_cost_config()
        self.assertEqual(set(cfg.slippage_pips.values()), {0.0})
        self.assertTrue(cfg.friday_close)


class DevYearsTest(unittest.TestCase):
    def test_eight_years_cover_development(self):
        years = dev_years()
        self.assertEqual(len(years), 8)
        self.assertEqual(years[0], (DEV_START, ts("2017-11-14")))
        self.assertEqual(years[-1][1], HOLDOUT_START)
        for (_, end), (start, _) in zip(years, years[1:]):
            self.assertEqual(end, start)

    def test_setup_labelled_with_year_of_first_fill(self):
        s = with_year_windows(setups([1.0, 1.0], start="2017-11-13"))
        self.assertEqual(list(s.test_start), [DEV_START, ts("2017-11-14")])
        self.assertEqual(list(s.test_end), [ts("2017-11-14"), ts("2018-11-14")])
        self.assertEqual(list(s.cell), [(3, 5), (3, 5)])


if __name__ == "__main__":
    unittest.main()
