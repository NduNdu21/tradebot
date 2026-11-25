"""Tests for src.ict.swings: three-candle swings (docs/strategy-ict.md §2)."""
import unittest

import pandas as pd

from src.ict.swings import Swing, find_swings, latest

T0 = pd.Timestamp("2024-01-09 13:30", tz="UTC")
M5 = pd.Timedelta("5min")


def frame(highs, lows, closes=None, start=T0, freq="5min"):
    """Candles indexed by UTC open time, with mid_h / mid_l / mid_c columns."""
    idx = pd.date_range(start, periods=len(highs), freq=freq, tz="UTC")
    if closes is None:
        closes = [1.0] * len(highs)
    return pd.DataFrame({"mid_h": highs, "mid_l": lows, "mid_c": closes}, index=idx)


class TestFindSwings(unittest.TestCase):
    def test_swing_high_three_candles(self):
        # Candle 2 (high 12) is above both neighbours, so it is a swing high.
        # Lows 9, 11, 10 give no swing low (the middle low is not below both neighbours).
        df = frame(highs=[10, 12, 11], lows=[9, 11, 10])
        swings = find_swings(df, M5)
        # Swing candle opens 13:35. Candle 3 opens 13:40 and closes 13:45, so confirmed = 13:45.
        self.assertEqual(swings, [Swing("high", 12, T0 + M5, T0 + 3 * M5)])

    def test_swing_low_three_candles(self):
        # Mirror image: candle 2 (low 8) is below both neighbours.
        df = frame(highs=[10, 9, 9.5], lows=[9, 8, 9.2])
        swings = find_swings(df, M5)
        self.assertEqual(swings, [Swing("low", 8, T0 + M5, T0 + 3 * M5)])

    def test_equal_highs_not_swing(self):
        # Candles 1 and 2 both have high 12: neither is strictly above its neighbour.
        df = frame(highs=[10, 12, 12, 11], lows=[9, 11, 11, 10])
        swings = find_swings(df, M5)
        self.assertEqual([s for s in swings if s.kind == "high"], [])

    def test_equal_lows_not_swing(self):
        # Candles 1 and 2 both have low 8: neither is strictly below its neighbour.
        df = frame(highs=[12, 11, 11, 12], lows=[10, 8, 8, 10])
        swings = find_swings(df, M5)
        self.assertEqual([s for s in swings if s.kind == "low"], [])

    def test_closes_ignored(self):
        highs = [10, 12, 11]
        lows = [9, 11, 10]
        rising = find_swings(frame(highs, lows, closes=[9.5, 9.6, 9.7]), M5)
        falling = find_swings(frame(highs, lows, closes=[11.5, 11.0, 10.5]), M5)
        self.assertEqual(rising, falling)
        self.assertEqual(len(rising), 1)
        self.assertEqual(rising[0].price, 12)

    def test_daily_swing_low(self):
        # Daily candles, bar = 1 day. Candle 2 (low 8) is a swing low, confirmed at
        # the close of candle 3: candle 3 opens 2 days after candle 1, closes 1 day later.
        start = pd.Timestamp("2024-01-08 22:00", tz="UTC")
        # Highs 10, 9, 9.5: no swing high, so only the swing low is expected.
        df = frame(highs=[10, 9, 9.5], lows=[9, 8, 9.5], start=start, freq="1D")
        swings = find_swings(df, pd.Timedelta("1 day"))
        self.assertEqual(
            swings,
            [Swing("low", 8, start + pd.Timedelta("1 day"), start + pd.Timedelta("3 days"))],
        )

    def test_results_sorted_by_confirmed_time(self):
        # Swing highs at candles 1 and 3. Confirmed times must come out in order.
        df = frame(highs=[10, 12, 9, 13, 9], lows=[9, 11, 8, 12, 8])
        swings = find_swings(df, M5)
        confirmed = [s.confirmed_time for s in swings]
        self.assertEqual(confirmed, sorted(confirmed))
        self.assertEqual([s.price for s in swings if s.kind == "high"], [12, 13])


class TestLatest(unittest.TestCase):
    def setUp(self):
        # Candles: c0 13:30, c1 13:35 (high 12), c2 13:40, c3 13:45 (high 13), c4 13:50.
        # Swing high 12 (candle 1): confirmed 13:45 (c2 close).
        # Swing high 13 (candle 3): confirmed 13:55 (c4 close).
        # Swing low 8 (candle 2): confirmed 13:50 (c3 close).
        df = frame(highs=[10, 12, 9, 13, 9], lows=[9, 11, 8, 12, 8])
        self.swings = find_swings(df, M5)

    def test_not_usable_before_confirmed(self):
        # Review Focus 2: a swing high at candle 1 is only usable once candle 2 has closed.
        df = frame(highs=[10, 12, 11], lows=[9, 11, 10])
        swings = find_swings(df, M5)
        candle3_open = T0 + 2 * M5            # 13:40
        candle3_close = candle3_open + M5     # 13:45
        self.assertIsNone(latest(swings, "high", before=candle3_open))
        self.assertEqual(
            latest(swings, "high", before=candle3_close + pd.Timedelta("1min")),
            swings[0],
        )

    def test_latest_picks_most_recent_of_kind(self):
        # Both swing highs are confirmed by 14:00; the later one (13) wins.
        found = latest(self.swings, "high", before=T0 + 30 * M5)
        self.assertEqual(found.price, 13)

    def test_latest_ignores_other_kind(self):
        found = latest(self.swings, "low", before=T0 + 30 * M5)
        self.assertEqual(found.price, 8)
        self.assertIsNone(latest(self.swings, "low", before=T0))

    def test_latest_field_candle_time(self):
        # Before 13:46, the swing at candle 1 is confirmed (13:45) but the swing at
        # candle 3 (open 13:45) is not yet usable by confirmed_time.
        before = T0 + pd.Timedelta("16min")  # 13:46
        default = latest(self.swings, "high", before=before)
        self.assertEqual(default.price, 12)
        by_candle = latest(self.swings, "high", before=before, field="candle_time")
        self.assertEqual(by_candle.price, 13)


if __name__ == "__main__":
    unittest.main()
