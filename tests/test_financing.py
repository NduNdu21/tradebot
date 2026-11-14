"""Tests for src.financing: rate tables, markup calibration, rollover times and charges."""
import unittest
from datetime import date, datetime

from src.financing import (
    OANDA_RATES, POLICY_RATES, UTC, annual_rate, apply_mode, financing_amount,
    markup, policy_rate, rollovers,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


class PolicyRateTest(unittest.TestCase):
    def test_tables_are_sorted_and_cover_the_data(self):
        for currency, table in POLICY_RATES.items():
            dates = [d for d, _ in table]
            self.assertEqual(dates, sorted(dates), currency)
            self.assertLessEqual(dates[0], date(2016, 10, 3), currency)

    def test_rate_changes_on_its_effective_date(self):
        # BoE cut Bank Rate from 0.75% to 0.25% on 2020-03-11.
        self.assertAlmostEqual(policy_rate("GBP", date(2020, 3, 10)), 0.0075)
        self.assertAlmostEqual(policy_rate("GBP", date(2020, 3, 11)), 0.0025)

    def test_fed_uses_range_midpoint(self):
        # Target range 5.25-5.50% from 2023-07-27.
        self.assertAlmostEqual(policy_rate("USD", date(2024, 1, 1)), 0.05375)

    def test_negative_rates(self):
        self.assertAlmostEqual(policy_rate("EUR", date(2020, 1, 1)), -0.005)
        self.assertAlmostEqual(policy_rate("JPY", date(2020, 1, 1)), -0.001)

    def test_before_table_raises(self):
        with self.assertRaises(ValueError):
            policy_rate("JPY", date(2010, 1, 1))


class AnnualRateTest(unittest.TestCase):
    def test_calibration_reproduces_todays_oanda_rates(self):
        for pair, sides in OANDA_RATES.items():
            for side, rate in sides.items():
                self.assertAlmostEqual(annual_rate(pair, side, date(2026, 11, 14)), rate)

    def test_markups_are_plausible(self):
        # A markup is a cost to the trader on both sides: positive and around 1% a year.
        for pair in OANDA_RATES:
            for side in ("long", "short"):
                self.assertGreater(markup(pair, side), 0)
                self.assertLess(markup(pair, side), 0.02)

    def test_long_earns_base_minus_quote(self):
        # Early 2020: GBP 0.75%, JPY -0.10%, so long GBP_JPY earns 0.85% before markup.
        on = date(2020, 1, 15)
        self.assertAlmostEqual(annual_rate("GBP_JPY", "long", on), 0.0085 - markup("GBP_JPY", "long"))
        self.assertAlmostEqual(annual_rate("GBP_JPY", "short", on), -0.0085 - markup("GBP_JPY", "short"))


class RolloverTimeTest(unittest.TestCase):
    def test_wednesday_charges_three_days_and_weekend_none(self):
        # Mon 2024-01-08 00:00 UTC to Mon 2024-01-15 00:00 UTC, US winter time.
        rolls = rollovers(utc(2024, 1, 8), utc(2024, 1, 15))
        self.assertEqual([(t.strftime("%a %H:%M"), d) for t, d in rolls],
                         [("Mon 22:00", 1), ("Tue 22:00", 1), ("Wed 22:00", 3),
                          ("Thu 22:00", 1), ("Fri 22:00", 1)])
        self.assertEqual(sum(d for _, d in rolls), 7)

    def test_api_schedule_skips_monday(self):
        rolls = rollovers(utc(2024, 1, 8), utc(2024, 1, 15), schedule="api")
        self.assertEqual([(t.strftime("%a"), d) for t, d in rolls],
                         [("Tue", 1), ("Wed", 3), ("Thu", 1), ("Fri", 1)])

    def test_spring_dst_change_moves_rollover_an_hour_earlier_in_utc(self):
        # US clocks go forward on Sunday 2024-03-10.
        rolls = dict((t.strftime("%m-%d"), t.strftime("%H:%M"))
                     for t, _ in rollovers(utc(2024, 3, 8), utc(2024, 3, 12)))
        self.assertEqual(rolls, {"03-08": "22:00", "03-11": "21:00"})

    def test_autumn_dst_change_moves_rollover_an_hour_later_in_utc(self):
        # US clocks go back on Sunday 2024-11-03.
        rolls = dict((t.strftime("%m-%d"), t.strftime("%H:%M"))
                     for t, _ in rollovers(utc(2024, 11, 1), utc(2024, 11, 5)))
        self.assertEqual(rolls, {"11-01": "21:00", "11-04": "22:00"})

    def test_uk_us_dst_mismatch_week_follows_new_york(self):
        # 2024-03-11..29: US on summer time, UK not yet. Rollover is still 21:00 UTC.
        times = {t.strftime("%H:%M") for t, _ in rollovers(utc(2024, 3, 11), utc(2024, 3, 23))}
        self.assertEqual(times, {"21:00"})

    def test_window_is_open_start_closed_end(self):
        # Position opened exactly at the rollover isn't charged; one closed exactly at it is.
        roll = utc(2024, 1, 9, 22)
        self.assertEqual(rollovers(roll, utc(2024, 1, 9, 23)), [])
        self.assertEqual(len(rollovers(utc(2024, 1, 9, 12), roll)), 1)


class FinancingAmountTest(unittest.TestCase):
    def test_one_night_charge(self):
        # 100,000 EUR_USD at 1.10, -2.46% a year, one day: 110,000 x -0.0246 / 365 USD.
        self.assertAlmostEqual(financing_amount(100_000, 1.10, -0.0246, 1), -7.4137, places=4)

    def test_triple_day_is_three_times_one_day(self):
        one = financing_amount(50_000, 150.0, 0.0183, 1)
        self.assertAlmostEqual(financing_amount(50_000, 150.0, 0.0183, 3), 3 * one)

    def test_modes(self):
        self.assertEqual(apply_mode(5.0, "on"), 5.0)
        self.assertEqual(apply_mode(5.0, "off"), 0.0)
        self.assertEqual(apply_mode(5.0, "cap"), 0.0)
        self.assertEqual(apply_mode(-5.0, "cap"), -5.0)
        with self.assertRaises(ValueError):
            apply_mode(1.0, "bogus")


if __name__ == "__main__":
    unittest.main()
