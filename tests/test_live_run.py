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

    def test_nan_close_today_skips_index(self):
        h1 = make_h1(RISING)
        today_1500 = pd.Timestamp(f"{TODAY} 15:00", tz="America/New_York").tz_convert("UTC")
        h1.loc[today_1500, ["bid_c", "ask_c"]] = np.nan   # the candle exists but its close is missing
        _, recs = self.run_once(fetch=fake_fetch({"NAS100_USD": h1}))
        d = decisions(recs)["NAS100_USD"]
        self.assertEqual((d["action"], d["reason"]), ("none", "no close yet"))
        self.assertEqual(decisions(recs)["SPX500_USD"]["action"], "buy")

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


class EarlyCloseAndSizingTests(unittest.TestCase):
    def test_early_close_flag_in_decision_records(self):
        _, recs = RunOnceTests().run_once()
        self.assertTrue(all(r["early_close"] is False for r in decisions(recs).values()))
        h1 = make_h1(RISING, last_hour=12)   # today stops at 12:00: no 15:00 candle, an early-close day
        now = datetime(2026, 12, 1, 21, 2, tzinfo=timezone.utc)
        recs = L.run_once(now, FakeClient({"OrderCreate": FILL}), "acc", False,
                          fetch=fake_fetch({"NAS100_USD": h1}))
        self.assertTrue(decisions(recs)["NAS100_USD"]["early_close"])
        self.assertFalse(decisions(recs)["US30_USD"]["early_close"])

    def test_open_trade_is_closed_even_if_units_for_raises(self):
        held = trade("77", "SPX500_USD", "1.5", tag=TAG, comment="entry 2026-11-10")   # held past MAX_HOLD: time stop
        client = FakeClient({"OrderCreate": FILL, "OpenTrades": {"trades": [held]},
                             "TradeClose": {"orderFillTransaction": {"id": "60", "price": "101.0"}}})
        with mock.patch.object(L, "units_for", side_effect=RuntimeError("no atr")):
            recs = L.run_once(NOW, client, "acc", True, fetch=fake_fetch())
        spx = decisions(recs)["SPX500_USD"]
        self.assertEqual((spx["action"], spx["reason"], spx["status"]), ("close", "time stop", "sent"))
        self.assertIn("TradeClose", client.names())
        # the other indices have no open trade, so their sizing failure is isolated as per-index errors
        self.assertEqual(len([r for r in recs if r["kind"] == "error"]), 3)


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

    def orders_with(self, value):
        env = {k: v for k, v in self.ENV.items() if k != "TRADING_ENABLED"}
        if value is not None:
            env["TRADING_ENABLED"] = value
        client = FakeClient({"OrderCreate": FILL})
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fake_fetch()), \
                redirect_stdout(io.StringIO()):
            L.main(now_utc=NOW)
        return client.names().count("OrderCreate")

    def test_only_exactly_yes_enables_trading(self):
        self.assertEqual(self.orders_with("yes"), 1)
        for value in ("true", "1", "YES", " yes ", "", None):
            self.assertEqual(self.orders_with(value), 0, repr(value))

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

    def run_main(self, fetch):
        client = FakeClient({"OrderCreate": FILL})
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fetch), redirect_stdout(out):
            try:
                L.main(now_utc=NOW)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, [json.loads(x) for x in out.getvalue().splitlines()]

    def test_normal_run_exits_zero(self):
        code, _ = self.run_main(fake_fetch())
        self.assertEqual(code, 0)

    def test_index_error_exits_one_but_others_processed(self):
        code, lines = self.run_main(fake_fetch({"NAS100_USD": RuntimeError("boom")}))
        self.assertEqual(code, 1)
        self.assertEqual(len([r for r in lines if r["kind"] == "decision"]), 3)

    def test_failed_broker_status_exits_one(self):
        client = FakeClient(raise_on="OrderCreate")
        with mock.patch.dict(os.environ, {**self.ENV, "TRADING_ENABLED": "yes"}), \
                mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fake_fetch()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                L.main(now_utc=NOW)
        self.assertEqual(cm.exception.code, 1)

    def test_client_has_timeout(self):
        with mock.patch.object(L.oandapyV20, "API") as api:
            L.make_client({"token": "t", "env": "practice"})
        self.assertEqual(api.call_args.kwargs["request_params"], {"timeout": 30})

    def test_header_failure_exits_one(self):
        client = FakeClient(raise_on="OpenTrades")
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(L, "make_client", return_value=client), \
                mock.patch.object(L, "fetch_h1", fake_fetch()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                L.main(now_utc=NOW)
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
