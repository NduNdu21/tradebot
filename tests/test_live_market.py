import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from unittest import mock

import pandas as pd

from src.live.market import fetch_h1, rows_to_frame, todays_close_ready

NY = "America/New_York"
TODAY = date(2026, 11, 25)


def row(time, price=100.0):
    r = {"time": time, "volume": 10}
    for side in ("bid", "ask"):
        for f in ("o", "h", "l", "c"):
            r[f"{side}_{f}"] = str(price)
    return r


def frame_with_ny_opens(*hours):
    """H1 frame with candles opening at the given New York hours on TODAY."""
    idx = [pd.Timestamp(f"2026-11-25 {h:02d}:00", tz=NY).tz_convert("UTC") for h in hours]
    return rows_to_frame([row(t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")) for t in idx])


def utc(h, m):
    # 2026-11-25 is winter, New York = UTC-5
    return datetime(2026, 11, 25, h, m, tzinfo=timezone.utc)


class TestRowsToFrame(unittest.TestCase):
    def test_rows_to_frame_matches_load_candles_shape(self):
        df = rows_to_frame([row("2026-11-25T14:00:00.000000000Z"), row("2026-11-25T15:00:00.000000000Z", 101.5)])
        self.assertEqual(df.index.name, "time")
        self.assertEqual(str(df.index.tz), "UTC")
        self.assertIn("bid_c", df.columns)
        self.assertIn("ask_o", df.columns)
        self.assertEqual(df["ask_o"].dtype, float)
        self.assertEqual(df["bid_c"].iloc[1], 101.5)


class TestTodaysCloseReady(unittest.TestCase):
    def test_incomplete_signal_candle_means_no_action(self):
        h1 = frame_with_ny_opens(13, 14)
        self.assertFalse(todays_close_ready(h1, TODAY, utc(21, 2)))  # 16:02 NY
        h1 = frame_with_ny_opens(13, 14, 15)
        self.assertTrue(todays_close_ready(h1, TODAY, utc(21, 2)))

    def test_early_close_day_is_ready_after_1600(self):
        h1 = frame_with_ny_opens(11, 12)
        self.assertFalse(todays_close_ready(h1, TODAY, utc(20, 30)))  # 15:30 NY
        self.assertTrue(todays_close_ready(h1, TODAY, utc(21, 2)))  # 16:02 NY


class TestFetchH1(unittest.TestCase):
    def test_fetch_h1_passes_start_and_keeps_stdout_clean(self):
        def fake(client, instrument, start, granularity):
            print("progress")
            return [row("2026-11-25T14:00:00.000000000Z")]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch("src.live.market.fetch_candles", side_effect=fake) as m, \
                redirect_stdout(out), mock.patch.object(sys, "stderr", err):
            df = fetch_h1("client", "NAS100_USD", TODAY, days_back=10)
        m.assert_called_once_with("client", "NAS100_USD", "2026-11-15", "H1")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("progress", err.getvalue())
        self.assertEqual(len(df), 1)


if __name__ == "__main__":
    unittest.main()
