import unittest

import pandas as pd

from src.live.compare import match, report


def live(rows):
    return pd.DataFrame(rows, columns=["instrument", "entry", "exit", "entry_price", "exit_price"]).astype(
        {"entry": "datetime64[ns]", "exit": "datetime64[ns]"})


def bt(rows):
    return pd.DataFrame(rows, columns=["instrument", "entry", "exit", "entry_mid", "exit_mid", "entry_fill", "exit_fill"]).astype(
        {"entry": "datetime64[ns]", "exit": "datetime64[ns]"})


# Backtest: mid 100 -> 102, fills 100.1 (ask+slip) and 101.9 (bid-slip): assumed cost 0.2 price units = 20 bp.
BT = ("NAS100_USD", "2026-09-01", "2026-09-04", 100.0, 102.0, 100.1, 101.9)


class TestCompare(unittest.TestCase):
    def test_match_pairs_by_instrument_and_entry_day(self):
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-04", 100.1, 101.9)])
        out = match(lv, bt([BT]))
        self.assertEqual(list(out["status"]), ["matched"])
        self.assertEqual(pd.Timestamp(out.iloc[0]["bt_exit"]), pd.Timestamp("2026-09-04"))
        # same entry day on another instrument is not a match
        lv2 = live([("SPX500_USD", "2026-09-01", "2026-09-04", 100.1, 101.9)])
        self.assertEqual(sorted(match(lv2, bt([BT]))["status"]), ["extra_live", "missed_live"])

    def test_missed_and_extra_trades_flagged(self):
        lv = live([("NAS100_USD", "2026-10-01", "2026-10-05", 100.0, 101.0)])
        out = match(lv, bt([BT]))
        by = dict(zip(out["status"], out["instrument"]))
        self.assertIn("missed_live", by)
        self.assertIn("extra_live", by)
        self.assertTrue(pd.isna(out[out.status == "missed_live"].iloc[0]["live_entry"]))
        self.assertTrue(pd.isna(out[out.status == "extra_live"].iloc[0]["bt_entry"]))

    def test_fill_diff_in_bp(self):
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-04", 100.2, 101.8)])
        r = match(lv, bt([BT])).iloc[0]
        self.assertAlmostEqual(r["entry_fill_diff_bp"], (100.2 - 100.1) / 100.1 * 1e4)   # live paid more
        self.assertAlmostEqual(r["exit_fill_diff_bp"], (101.8 - 101.9) / 101.9 * 1e4)    # live sold lower

    def test_exit_mismatch(self):
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-05", 100.1, 101.9)])
        self.assertEqual(list(match(lv, bt([BT]))["status"]), ["exit_mismatch"])

    def test_still_open_live_is_matched_open_not_missed(self):
        lv = live([("NAS100_USD", "2026-11-20", pd.NaT, 100.0, float("nan"))])
        out = match(lv, bt([]))
        self.assertEqual(list(out["status"]), ["matched_open"])

    def test_report_states_three_criteria(self):
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-04", 100.1, 101.9)])
        text = report(match(lv, bt([BT])))
        for n in ("1", "2", "3"):
            self.assertIn(f"Criterion {n}", text)
        self.assertIn("PASS", text)
        self.assertIn("refused/failed/error", text)
        # live cost 0.2 on mid 100 = 20 bp, same as assumed: within 2x
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-04", 100.5, 101.5)])   # cost 1.0 = 100 bp vs 20 bp
        self.assertIn("FAIL", report(match(lv, bt([BT]))))
        self.assertIn("FAIL", report(match(live([]), bt([BT]))))   # missed trade fails criterion 1

    def test_early_close_column_flags_backtest_or_live_day(self):
        lv = live([("NAS100_USD", "2026-09-01", "2026-09-04", 100.1, 101.9)])
        self.assertEqual(list(match(lv, bt([BT]))["early_close"]), [False])
        early = {("NAS100_USD", pd.Timestamp("2026-09-04"))}          # the exit day
        self.assertEqual(list(match(lv, bt([BT]), early)["early_close"]), [True])
        other = {("SPX500_USD", pd.Timestamp("2026-09-04"))}          # another instrument: not flagged
        self.assertEqual(list(match(lv, bt([BT]), other)["early_close"]), [False])
        lv_only = live([("NAS100_USD", "2026-09-01", None, 100.1, None)])   # open live trade, entry on early day
        self.assertEqual(list(match(lv_only, bt([]), {("NAS100_USD", pd.Timestamp("2026-09-01"))})["early_close"]), [True])

    def test_report_leaves_early_close_rows_out_of_criterion_1(self):
        early = {("NAS100_USD", pd.Timestamp("2026-09-04"))}
        out = match(live([]), bt([BT]), early)        # a missed trade, but on an early-close day
        text = report(out)
        self.assertIn("Criterion 1 (signal agreement): PASS", text)
        self.assertIn("1 trades on early-close days, reported separately", text)
        self.assertIn("0 trades on early-close days", report(match(live([]), bt([BT]))))


if __name__ == "__main__":
    unittest.main()
