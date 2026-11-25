import unittest
from datetime import date

import pandas as pd

from src.ict.calendar import is_skipped_market_day, missing_m5, session_times


def full_m5_frame(day):
    """M5 candles 08:00-11:55 New York on `day`, indexed by UTC open time."""
    idx = pd.date_range(f"{day} 08:00", f"{day} 11:55", freq="5min", tz="America/New_York")
    return pd.DataFrame({"mid_c": 1.0}, index=idx.tz_convert("UTC"))


class TestSessionTimes(unittest.TestCase):
    def test_session_winter(self):
        t = session_times(date(2024, 1, 9))
        self.assertEqual(t["levels"], pd.Timestamp("2024-01-09 13:30", tz="UTC"))
        self.assertEqual(t["exit"], pd.Timestamp("2024-01-09 17:00", tz="UTC"))

    def test_session_us_dst_before_europe(self):
        # US clocks changed on 2024-03-10, Europe not yet: 08:30 NY is 12:30 UTC.
        t = session_times(date(2024, 3, 12))
        self.assertEqual(t["levels"], pd.Timestamp("2024-03-12 12:30", tz="UTC"))
        self.assertEqual(t["check_start"], pd.Timestamp("2024-03-12 12:00", tz="UTC"))
        self.assertEqual(t["entry_end"], pd.Timestamp("2024-03-12 15:00", tz="UTC"))


class TestHolidays(unittest.TestCase):
    def test_holidays(self):
        self.assertEqual(is_skipped_market_day(date(2023, 7, 4)), "holiday")
        self.assertEqual(is_skipped_market_day(date(2024, 3, 29)), "holiday")
        self.assertEqual(is_skipped_market_day(date(2023, 11, 24)), "early_close")
        self.assertIsNone(is_skipped_market_day(date(2024, 1, 9)))

    def test_special_closures(self):
        self.assertEqual(is_skipped_market_day(date(2018, 12, 5)), "holiday")
        self.assertEqual(is_skipped_market_day(date(2025, 1, 9)), "holiday")


class TestMissingM5(unittest.TestCase):
    def test_missing_m5(self):
        day = date(2024, 1, 9)
        m5 = full_m5_frame(day)
        self.assertEqual(len(m5), 48)
        self.assertFalse(missing_m5(m5, day))
        gap = pd.Timestamp("2024-01-09 10:15", tz="America/New_York").tz_convert("UTC")
        self.assertTrue(missing_m5(m5.drop(gap), day))


if __name__ == "__main__":
    unittest.main()
