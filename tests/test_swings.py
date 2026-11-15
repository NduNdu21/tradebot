"""Tests for src.swings: Wilder ATR and the K x ATR zigzag (docs/strategy.md §1-§2)."""
import unittest

import pandas as pd

from src.swings import WilderATR, ZigZag


class WilderATRTest(unittest.TestCase):
    def test_none_until_period_bars(self):
        atr = WilderATR(3)
        self.assertIsNone(atr.update(1.0, 0.9, 0.95))
        self.assertIsNone(atr.update(1.0, 0.9, 0.95))
        self.assertIsNotNone(atr.update(1.0, 0.9, 0.95))

    def test_seed_is_simple_average_of_first_true_ranges(self):
        atr = WilderATR(3)
        atr.update(1.10, 1.00, 1.05)          # first bar: TR = high - low = 0.10
        atr.update(1.08, 1.04, 1.06)          # TR = 0.04
        value = atr.update(1.12, 1.06, 1.10)  # TR = max(0.06, |1.12-1.06|, |1.06-1.06|) = 0.06
        self.assertAlmostEqual(value, (0.10 + 0.04 + 0.06) / 3)

    def test_wilder_smoothing_after_seed(self):
        atr = WilderATR(3)
        for bar in [(1.10, 1.00, 1.05), (1.08, 1.04, 1.06), (1.12, 1.06, 1.10)]:
            seed = atr.update(*bar)
        value = atr.update(1.11, 1.09, 1.10)  # TR = 0.02
        self.assertAlmostEqual(value, (seed * 2 + 0.02) / 3)

    def test_gap_counts_in_true_range(self):
        # Weekend gap: previous close 1.10, bar trades 1.15-1.16. TR = 1.16 - 1.10.
        atr = WilderATR(2)
        atr.update(1.11, 1.09, 1.10)          # TR 0.02
        value = atr.update(1.16, 1.15, 1.155)
        self.assertAlmostEqual(value, (0.02 + 0.06) / 2)

    def test_default_period_is_14(self):
        self.assertEqual(WilderATR().period, 14)


def feed(zz, bars, atr=0.0010, start="2024-01-08"):
    """Feed (high, low) bars; return the pivots confirmed, as (bar number confirmed at, pivot)."""
    times = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=len(bars), freq="h")
    out = []
    for i, ((high, low), t) in enumerate(zip(bars, times)):
        pivot = zz.update(i, t, high, low, atr)
        if pivot:
            out.append((i, pivot))
    return out


class ZigZagTest(unittest.TestCase):
    # K = 3, ATR 0.0010: a reversal needs 0.0030.
    def test_confirms_swing_high_after_k_atr_drop(self):
        bars = [(1.1010, 1.1000), (1.1040, 1.1030), (1.1060, 1.1045),   # up from 1.1000 (low not yet a pivot)
                (1.1055, 1.1035), (1.1050, 1.1029)]                       # 1.1060 - 1.1029 = 0.0031
        found = feed(ZigZag(3), bars)
        kinds = [(i, p.kind, p.price) for i, p in found]
        self.assertIn((4, "high", 1.1060), kinds)

    def test_reversal_just_short_of_k_atr_does_not_confirm(self):
        bars = [(1.1010, 1.1000), (1.1040, 1.1030), (1.1060, 1.1045), (1.1050, 1.1031)]
        self.assertNotIn("high", [p.kind for _, p in feed(ZigZag(3), bars)])

    def test_bar_making_new_high_cannot_confirm_it(self):
        # Up-leg high 1.1060; next bar makes 1.1070 and also trades down to 1.1035 (3.5 ATR).
        # We can't tell which came first, so the high is only confirmed by a later bar.
        zz = ZigZag(3)
        bars = [(1.1010, 1.1000), (1.1040, 1.1030), (1.1060, 1.1045), (1.1070, 1.1035)]
        found = feed(zz, bars)
        self.assertNotIn("high", [p.kind for _, p in found])
        self.assertEqual(zz.extreme.price, 1.1070)
        later = feed(zz, [(1.1045, 1.1039)], start="2024-01-08 04:00")   # 1.1070 - 1.1039 = 0.0031
        self.assertEqual(later[0][1].price, 1.1070)

    def test_confirms_swing_low_after_k_atr_rally(self):
        bars = [(1.1100, 1.1090), (1.1080, 1.1060), (1.1050, 1.1040), (1.1060, 1.1045), (1.1072, 1.1050)]
        found = feed(ZigZag(3), bars)
        lows = [p for _, p in found if p.kind == "low"]
        self.assertEqual(lows[0].price, 1.1040)

    def test_next_leg_starts_from_lowest_low_after_the_high(self):
        zz = ZigZag(3)
        bars = [(1.1010, 1.1000), (1.1040, 1.1030), (1.1060, 1.1045), (1.1050, 1.1025)]
        feed(zz, bars)
        self.assertEqual(zz.direction, "down")
        self.assertEqual(zz.extreme.price, 1.1025)

    def test_min_bars_is_not_a_zigzag_rule(self):
        # A 1-bar spike of 4 ATR still confirms (min_bars only matters when arming a setup).
        bars = [(1.1010, 1.1000), (1.1045, 1.1005), (1.1015, 1.1010)]
        found = [(i, p.kind, p.price) for i, p in feed(ZigZag(3), bars)]
        self.assertIn((2, "high", 1.1045), found)

    def test_pivots_alternate(self):
        bars = [(1.1010, 1.1000), (1.1050, 1.1040), (1.1015, 1.1005), (1.1055, 1.1045),
                (1.1015, 1.1005), (1.1060, 1.1050)]
        kinds = [p.kind for _, p in feed(ZigZag(3), bars)]
        self.assertTrue(all(a != b for a, b in zip(kinds, kinds[1:])))


if __name__ == "__main__":
    unittest.main()
