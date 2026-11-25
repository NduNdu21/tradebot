"""One test per turn-of-the-month rule (docs/strategy-turn-of-month.md)."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from src.tom import nyse as N
from src.tom import run as R
from src.tom import evaluate as E


class TestCalendar(unittest.TestCase):
    def test_holiday_rules(self):
        h = N.holidays(2012)
        self.assertIn(date(2012, 1, 2), h)       # New Year on Sunday -> Monday
        self.assertIn(date(2012, 4, 6), h)       # Good Friday
        self.assertIn(date(2012, 10, 29), h)     # Sandy
        self.assertNotIn(date(2010, 12, 31), N.holidays(2010))  # 2011 New Year on Saturday: not observed
        self.assertIn(date(2010, 12, 24), N.holidays(2010))     # Christmas on Saturday -> Friday
        self.assertNotIn(date(2021, 6, 18), N.holidays(2021))   # Juneteenth only from 2022
        self.assertIn(date(2022, 6, 20), N.holidays(2022))

    def test_entry_is_day_minus_2(self):
        days = N.trading_days("2024-05-01", "2024-07-15")
        e = R.tom_entry_days(days)
        # May 2024 ends Fri 31 -> entry Thu 30; June ends Fri 28 -> entry Thu 27.
        self.assertIn(pd.Timestamp("2024-05-30"), e)
        self.assertIn(pd.Timestamp("2024-06-27"), e)

    def test_exit_is_day_plus_3_four_trading_days_later(self):
        days = N.trading_days("2024-05-01", "2024-07-15")
        i = days.get_loc(pd.Timestamp("2024-06-27"))
        # -1 = Jun 28, +1 Jul 1, +2 Jul 2, +3 Jul 3 (Jul 4 holiday irrelevant here).
        self.assertEqual(days[i + R.HOLD], pd.Timestamp("2024-07-03"))


class TestPrices(unittest.TestCase):
    def _h1(self, ny_times, bid):
        idx = pd.DatetimeIndex(ny_times).tz_localize("America/New_York").tz_convert("UTC")
        return pd.DataFrame({"bid_c": bid, "ask_c": np.array(bid) + 1}, index=idx)

    def test_close_is_the_15_00_candle(self):
        h = self._h1(["2024-06-03 14:00", "2024-06-03 15:00", "2024-06-03 16:00"], [1.0, 2.0, 3.0])
        self.assertEqual(R.closes(h).loc["2024-06-03", "bid"], 2.0)

    def test_early_close_uses_last_candle_before_15_00(self):
        h = self._h1(["2024-07-03 12:00", "2024-07-03 13:00"], [5.0, 6.0])
        self.assertEqual(R.closes(h).loc["2024-07-03", "bid"], 6.0)

    def test_financing_days_cfd_schedule(self):
        # Thu entry -> next Wed exit: rollovers Thu 1, Fri 3, Mon 1, Tue 1 = 6
        self.assertEqual(R.financing_days(pd.Timestamp("2024-06-27"), pd.Timestamp("2024-07-03")), 6)

    def test_sizing_uses_last_completed_bar(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(["2024-06-03", "2024-06-04", "2024-06-05"]))
        self.assertEqual(R.latest_before(s, pd.DatetimeIndex(["2024-06-05"])).iloc[0], 2.0)


class TestTrades(unittest.TestCase):
    def test_four_day_trade_pnl_buy_ask_sell_bid_with_slippage_and_financing(self):
        days = N.trading_days("2024-01-02", "2024-03-01")
        ny = []
        for d in pd.date_range("2023-11-01", "2024-03-05"):
            if d.weekday() < 5:
                ny += [d + pd.Timedelta(hours=h) for h in range(0, 17)]
        idx = pd.DatetimeIndex(ny).tz_localize("America/New_York").tz_convert("UTC")
        price = pd.Series(100.0, index=idx)
        price[idx.tz_convert("America/New_York").normalize().tz_localize(None) >= pd.Timestamp("2024-01-08")] = 110.0
        h1 = pd.DataFrame({"bid_o": price, "bid_h": price + 1, "bid_l": price - 1, "bid_c": price,
                           "ask_o": price + 0.2, "ask_h": price + 1.2, "ask_l": price - 0.8, "ask_c": price + 0.2})
        gbp = pd.DataFrame({"c": 1.25}, index=pd.date_range("2023-10-01", "2024-03-05"))
        rates = {"USD": pd.Series([0.05], index=pd.DatetimeIndex(["2000-01-01"]))}
        t = R.four_day_trades("SPX500_USD", h1, gbp, rates, days, slip=0.1)
        row = t.loc["2024-01-02"]            # exit Jan 8 at 110
        u = row["units"]
        self.assertAlmostEqual(row["price"], u * (110.1 - 100.1) * 0.8, places=6)
        self.assertAlmostEqual(row["cost"], -u * (0.1 + 0.1 + 0.1 + 0.1) * 0.8, places=6)
        fdays = R.financing_days(pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-08"))
        self.assertAlmostEqual(row["fin"], u * 100.1 * (0.02 - 0.05 - 0.025) * fdays / 365 * 0.8, places=6)
        self.assertEqual(u, round(u, 2))


class TestEvaluate(unittest.TestCase):
    def test_trades_booked_on_exit_day_and_span_requires_exit_inside(self):
        t = pd.DataFrame({"exit": pd.to_datetime(["2024-01-08", "2024-11-20"]), "total": [10.0, 5.0]},
                         index=pd.to_datetime(["2024-01-02", "2024-11-12"]))
        self.assertEqual(len(E.in_span(t, (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-11-13")))), 1)
        days = pd.DatetimeIndex(["2024-01-05", "2024-01-08"])
        self.assertEqual(list(E.book(t.iloc[:1], days)), [0.0, 10.0])


if __name__ == "__main__":
    unittest.main()
