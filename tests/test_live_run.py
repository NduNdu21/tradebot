import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from unittest import mock

import numpy as np
import pandas as pd

from src.live import run as L
from src.live.broker import TAG
from src.meanrev import run as R
from src.tom.nyse import trading_days
from src.trend.bars import daily_bars
from tests.test_live_broker import FILL, FakeClient, trade

TODAY = date(2026, 12, 1)                                    # Tuesday, New York on EST
NOW = datetime(2026, 12, 1, 21, 2, tzinfo=timezone.utc)      # 16:02 New York
DAYS = trading_days("2025-09-01", "2026-12-01")


def make_h1(prices, last_hour=15, spread=0.1, rng=1.0):
    """Hourly candles every weekday (built in New York time). One price per day; the last day stops at last_hour."""
    stamps, vals = [], []
    for d, p in zip(DAYS, prices):
        top = last_hour if d.date() == TODAY else 23
        for h in range(0, top + 1):
            stamps.append(pd.Timestamp(d).replace(hour=h).tz_localize("America/New_York"))
            vals.append(p)
    v = np.array(vals)
    df = pd.DataFrame({"bid_o": v - spread, "bid_h": v - spread + rng, "bid_l": v - spread - rng, "bid_c": v - spread,
                       "ask_o": v + spread, "ask_h": v + spread + rng, "ask_l": v + spread - rng, "ask_c": v + spread},
                      index=pd.DatetimeIndex(stamps).tz_convert("UTC"))
    df.index.name = "time"
    return df


N = len(DAYS)
RISING = 100 + 0.2 * np.arange(N)
DIP = RISING.copy()
DIP[-3:] = DIP[-4] - np.array([1.0, 2.0, 3.0])     # uptrend, then a new 5-day low today -> entry signal


def fake_fetch(overrides=None, calls=None):
    overrides = overrides or {}
    frames = {"GBP_USD": make_h1(1.25 + 0 * RISING, rng=0.01, spread=0.0001),
              "SPX500_USD": make_h1(DIP), "NAS100_USD": make_h1(RISING),
              "US30_USD": make_h1(RISING), "US2000_USD": make_h1(RISING)}
    frames.update(overrides)

    def fetch(client, instrument, today, days_back=430):
        if calls is not None:
            calls.append(instrument)
        f = frames[instrument]
        if isinstance(f, Exception):
            raise f
        return f
    return fetch


def decisions(records):
    return {r["instrument"]: r for r in records if r["kind"] == "decision"}


class WindowTests(unittest.TestCase):
    def check(self, now, expect):
        days = trading_days("2026-01-01", "2026-12-31")
        ok, why = L.in_window(now, days)
        self.assertEqual(ok, expect, why)
        self.assertIsInstance(why, str)

    def test_window_open_1600_to_1645_new_york_on_trading_days(self):
        utc = timezone.utc
        self.check(datetime(2026, 12, 1, 21, 2, tzinfo=utc), True)     # 16:02 EST
        self.check(datetime(2026, 12, 1, 20, 2, tzinfo=utc), False)    # 15:02 EST, before 16:00
        self.check(datetime(2026, 7, 1, 20, 2, tzinfo=utc), True)      # 16:02 EDT
        self.check(datetime(2026, 12, 1, 21, 45, tzinfo=utc), False)   # 16:45 is closed
        self.check(datetime(2026, 12, 5, 21, 2, tzinfo=utc), False)    # Saturday
        self.check(datetime(2026, 12, 25, 21, 2, tzinfo=utc), False)   # Christmas
        self.assertIn("before 16:00", L.in_window(datetime(2026, 12, 1, 20, 2, tzinfo=utc),
                                                  trading_days("2026-01-01", "2026-12-31"))[1])

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            L.in_window(datetime(2026, 12, 1, 21, 2), DAYS)


class UnitsTests(unittest.TestCase):
    def test_units_match_backtest_sizing(self):
        rng = np.random.default_rng(1)
        prices = 100 + np.cumsum(rng.normal(0.1, 1.0, N))
        h1 = make_h1(prices, rng=2.0)
        gbp = make_h1(1.25 + 0.01 * rng.standard_normal(N), rng=0.01, spread=0.0001)
        days = pd.DatetimeIndex([pd.Timestamp(TODAY)])
        expected = self.backtest_units(h1, gbp, days)
        self.assertGreater(expected, 0)
        self.assertEqual(L.units_for(h1, gbp, TODAY), expected)

    @staticmethod
    def backtest_units(h1, gbp, days):
        """Build a real Instrument on the same data (rates are not needed for sizing)."""
        with mock.patch.object(R, "rate_on", return_value=pd.Series(0.0, index=days)):
            return float(R.Instrument("SPX500_USD", h1, daily_bars(gbp), None, days).units[0])


class RunOnceTests(unittest.TestCase):
    def run_once(self, client=None, fetch=None, enabled=True, now=NOW):
        client = client or FakeClient({"OrderCreate": FILL})
        return client, L.run_once(now, client, "acc", enabled, fetch=fetch or fake_fetch())

    def test_outside_window_only_header(self):
        client, recs = self.run_once(now=datetime(2026, 12, 1, 20, 2, tzinfo=timezone.utc))
        self.assertEqual([r["kind"] for r in recs], ["header"])
        self.assertEqual(client.sent, [])

    def test_entry_signal_buys_with_gbp_notional(self):
        client, recs = self.run_once()
        d = decisions(recs)
        self.assertEqual(len(d), 4)
        spx = d["SPX500_USD"]
        self.assertEqual((spx["action"], spx["status"], spx["trade_id"]), ("buy", "sent", "77"))
        self.assertTrue(spx["entry_signal"])
        self.assertGreater(spx["units"], 0)
        self.assertEqual(client.names().count("OrderCreate"), 1)
        for other in ("NAS100_USD", "US30_USD", "US2000_USD"):
            self.assertEqual(d[other]["action"], "none")

    def test_dry_run_sends_no_order(self):
        client, recs = self.run_once(enabled=False)
        self.assertEqual(decisions(recs)["SPX500_USD"]["status"], "dry_run")
        self.assertNotIn("OrderCreate", client.names())

    def test_missing_todays_close_skips_index(self):
        fetch = fake_fetch({"NAS100_USD": make_h1(RISING, last_hour=14)})
        _, recs = self.run_once(fetch=fetch)
        d = decisions(recs)
        self.assertEqual((d["NAS100_USD"]["action"], d["NAS100_USD"]["reason"]), ("none", "no close yet"))
        self.assertEqual(d["SPX500_USD"]["action"], "buy")

    def test_second_run_same_day_is_noop(self):
        _, first = self.run_once()
        self.assertEqual(decisions(first)["SPX500_USD"]["action"], "buy")
        held = trade("77", "SPX500_USD", "1.5", tag=TAG, comment="entry 2026-12-01")
        client = FakeClient({"OrderCreate": FILL, "OpenTrades": {"trades": [held]}})
        _, second = self.run_once(client=client)
        d = decisions(second)["SPX500_USD"]
        self.assertEqual((d["action"], d["reason"]), ("none", "holding"))
        self.assertNotIn("OrderCreate", client.names())

    def test_blocked_index_is_skipped_even_with_tagged_trade(self):
        trades = [trade("1", "SPX500_USD"), trade("2", "SPX500_USD", tag=TAG, comment="entry 2026-11-20")]
        client = FakeClient({"OrderCreate": FILL, "OpenTrades": {"trades": trades}})
        _, recs = self.run_once(client=client)
        d = decisions(recs)["SPX500_USD"]
        self.assertEqual((d["action"], d["reason"]), ("none", "blocked: untagged or duplicate trade"))
        self.assertNotIn("OrderCreate", client.names())
        self.assertNotIn("TradeClose", client.names())

    def test_index_error_is_isolated(self):
        fetch = fake_fetch({"NAS100_USD": RuntimeError("boom")})
        _, recs = self.run_once(fetch=fetch)
        err = [r for r in recs if r["kind"] == "error"]
        self.assertEqual(len(err), 1)
        self.assertEqual((err[0]["instrument"], err[0]["status"]), ("NAS100_USD", "error"))
        self.assertIn("RuntimeError", err[0]["error"])
        self.assertEqual(decisions(recs)["SPX500_USD"]["action"], "buy")
        self.assertEqual(len(decisions(recs)), 3)

    def test_header_error_is_fatal_record(self):
        client = FakeClient({"OrderCreate": FILL}, raise_on="OpenTrades")
        _, recs = self.run_once(client=client)
        self.assertEqual(recs[-1]["kind"], "error")
        self.assertTrue(recs[-1]["fatal"])
        self.assertNotIn("OrderCreate", client.names())

    def test_gbpusd_failure_is_fatal(self):
        _, recs = self.run_once(fetch=fake_fetch({"GBP_USD": RuntimeError("down")}))
        self.assertTrue(recs[-1]["fatal"])
        self.assertEqual(decisions(recs), {})


class MainTests(unittest.TestCase):
    ENV = {"OANDA_TOKEN": "secret-xyz", "OANDA_ACCOUNT_ID": "acc", "OANDA_ENV": "practice", "TRADING_ENABLED": "no"}

    def test_log_never_contains_token(self):
        client = FakeClient({"OrderCreate": FILL})
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fake_fetch()), redirect_stdout(out):
            L.main(now_utc=NOW)
        text = out.getvalue()
        self.assertNotIn("secret-xyz", text)
        lines = [json.loads(x) for x in text.splitlines()]
        self.assertEqual(len(lines), 5)            # header + 4 indices
        self.assertEqual(lines[0]["kind"], "header")
        self.assertEqual(client.names().count("OrderCreate"), 0)   # TRADING_ENABLED=no

    def test_refuses_live_env(self):
        make = mock.Mock()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**self.ENV, "OANDA_ENV": "live"}), \
                mock.patch.object(L, "make_client", make), redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                L.main(now_utc=NOW)
        self.assertEqual(cm.exception.code, 1)
        make.assert_not_called()
        self.assertNotIn("secret-xyz", out.getvalue())

    def test_outside_window_exits_zero(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(L, "make_client", return_value=FakeClient()), \
                redirect_stdout(out):
            L.main(now_utc=datetime(2026, 12, 1, 20, 2, tzinfo=timezone.utc))   # returns normally = exit 0
        self.assertEqual(json.loads(out.getvalue())["kind"], "header")

    def test_header_failure_exits_one(self):
        client = FakeClient(raise_on="OpenTrades")
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fake_fetch()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                L.main(now_utc=NOW)
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
