"""Overnight financing (rollover) for EUR_USD and GBP_JPY positions.

OANDA charges or credits financing at each 17:00 New York rollover:
    financing (quote ccy) = units x price x annual rate x days charged / 365
A long position earns the base currency's interest rate and pays the quote
currency's; a short does the opposite. OANDA subtracts a markup on each side.

OANDA's API only gives *today's* rates, so history is rebuilt from central-bank
policy rates (tables below) minus a markup calibrated from today's OANDA rates.
See docs/strategy.md §8 and docs/decisions.md (2026-11-14 financing entries).
"""
import bisect
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
ROLLOVER_TIME = time(17, 0)  # New York local time, so it moves with US daylight saving

# Days of financing charged at each weekday's rollover (Monday = 0).
# "help": OANDA's help pages. Every weekday rolls one day, and Wednesday rolls
#   three because T+2 settlement jumps the weekend. 7 days a week (main run).
# "api": what the v20 API's financingDaysOfWeek returned on 2026-11-14 for both
#   pairs. Monday 0, so only 6 days a week; meaning undocumented (sensitivity run).
SCHEDULES = {
    "help": {0: 1, 1: 1, 2: 3, 3: 1, 4: 1},
    "api": {0: 0, 1: 1, 2: 3, 3: 1, 4: 1},
}

# Policy rates in percent, as (effective date, new rate), oldest first.
# The first entry is the rate in force when the data starts (2016-10-03).
POLICY_RATES = {
    # Federal Reserve: midpoint of the federal funds target range.
    # Source: https://www.federalreserve.gov/monetarypolicy/openmarket.htm
    # (dates on that page are the day after the FOMC meeting, when the range took effect).
    "USD": [
        (date(2015, 12, 17), 0.375),
        (date(2016, 12, 15), 0.625),
        (date(2017, 3, 16), 0.875),
        (date(2017, 6, 15), 1.125),
        (date(2017, 12, 14), 1.375),
        (date(2018, 3, 22), 1.625),
        (date(2018, 6, 14), 1.875),
        (date(2018, 9, 27), 2.125),
        (date(2018, 12, 20), 2.375),
        (date(2019, 8, 1), 2.125),
        (date(2019, 9, 19), 1.875),
        (date(2019, 10, 31), 1.625),
        (date(2020, 3, 4), 1.125),
        (date(2020, 3, 16), 0.125),
        (date(2022, 3, 17), 0.375),
        (date(2022, 5, 5), 0.875),
        (date(2022, 6, 16), 1.625),
        (date(2022, 7, 28), 2.375),
        (date(2022, 9, 22), 3.125),
        (date(2022, 11, 3), 3.875),
        (date(2022, 12, 15), 4.375),
        (date(2023, 2, 2), 4.625),
        (date(2023, 3, 23), 4.875),
        (date(2023, 5, 4), 5.125),
        (date(2023, 7, 27), 5.375),
        (date(2024, 9, 19), 4.875),
        (date(2024, 11, 8), 4.625),
        (date(2024, 12, 19), 4.375),
        (date(2025, 9, 18), 4.125),
        (date(2025, 10, 30), 3.875),
        (date(2025, 12, 11), 3.625),
        (date(2026, 9, 17), 3.875),
    ],
    # ECB: deposit facility rate, "with effect from" dates.
    # Source: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html
    "EUR": [
        (date(2016, 3, 16), -0.40),
        (date(2019, 9, 18), -0.50),
        (date(2022, 7, 27), 0.00),
        (date(2022, 9, 14), 0.75),
        (date(2022, 11, 2), 1.50),
        (date(2022, 12, 21), 2.00),
        (date(2023, 2, 8), 2.50),
        (date(2023, 3, 22), 3.00),
        (date(2023, 5, 10), 3.25),
        (date(2023, 6, 21), 3.50),
        (date(2023, 8, 2), 3.75),
        (date(2023, 9, 20), 4.00),
        (date(2024, 6, 12), 3.75),
        (date(2024, 9, 18), 3.50),
        (date(2024, 10, 23), 3.25),
        (date(2024, 12, 18), 3.00),
        (date(2025, 2, 5), 2.75),
        (date(2025, 3, 12), 2.50),
        (date(2025, 4, 23), 2.25),
        (date(2025, 6, 11), 2.00),
        (date(2026, 6, 17), 2.25),
        (date(2026, 9, 16), 2.50),
    ],
    # Bank of England: Bank Rate (effective on the announcement date).
    # Source: https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp
    "GBP": [
        (date(2016, 8, 4), 0.25),
        (date(2017, 11, 2), 0.50),
        (date(2018, 8, 2), 0.75),
        (date(2020, 3, 11), 0.25),
        (date(2020, 3, 19), 0.10),
        (date(2021, 12, 16), 0.25),
        (date(2022, 2, 3), 0.50),
        (date(2022, 3, 17), 0.75),
        (date(2022, 5, 5), 1.00),
        (date(2022, 6, 16), 1.25),
        (date(2022, 8, 4), 1.75),
        (date(2022, 9, 22), 2.25),
        (date(2022, 11, 3), 3.00),
        (date(2022, 12, 15), 3.50),
        (date(2023, 2, 2), 4.00),
        (date(2023, 3, 23), 4.25),
        (date(2023, 5, 11), 4.50),
        (date(2023, 6, 22), 5.00),
        (date(2023, 8, 3), 5.25),
        (date(2024, 8, 1), 5.00),
        (date(2024, 11, 7), 4.75),
        (date(2025, 2, 6), 4.50),
        (date(2025, 5, 8), 4.25),
        (date(2025, 8, 7), 4.00),
        (date(2025, 12, 18), 3.75),
        (date(2026, 11, 5), 4.00),
    ],
    # Bank of Japan: short-term policy rate (uncollateralized overnight call rate guideline).
    # Sources: https://www.boj.or.jp/en/mopo/mpmdeci/ statements (effective dates from
    # their footnotes). -0.1% applied from 2016-02-16. March 2024 target was
    # "around 0 to 0.1 percent", stored as 0.05. The two 2026 levels come from news
    # reports (CNBC, Al Jazeera) and their effective dates assume the next business day.
    "JPY": [
        (date(2016, 2, 16), -0.10),
        (date(2024, 3, 21), 0.05),
        (date(2024, 8, 1), 0.25),
        (date(2025, 1, 27), 0.50),
        (date(2025, 12, 22), 0.75),
        (date(2026, 6, 17), 1.00),
        (date(2026, 9, 21), 1.25),
    ],
}

# OANDA's annual financing rates (decimal) for this account, fetched from the
# v20 AccountInstruments endpoint on 2026-11-14. Positive = credit, negative = charge.
CALIBRATION_DATE = date(2026, 11, 14)
OANDA_RATES = {
    "EUR_USD": {"long": -0.0246, "short": 0.0044},
    "GBP_JPY": {"long": 0.0183, "short": -0.0391},
}


def policy_rate(currency, on_date):
    """Central-bank policy rate (decimal) for `currency` in force on `on_date`."""
    table = POLICY_RATES[currency]
    dates = [d for d, _ in table]
    i = bisect.bisect_right(dates, on_date) - 1
    if i < 0:
        raise ValueError(f"no {currency} policy rate before {table[0][0]}")
    return table[i][1] / 100


def theoretical_rate(pair, side, on_date):
    """Rate difference a position earns before OANDA's markup.

    Long EUR_USD = hold EUR, borrow USD, so it earns EUR rate minus USD rate.
    """
    base, quote = pair.split("_")
    diff = policy_rate(base, on_date) - policy_rate(quote, on_date)
    return diff if side == "long" else -diff


def markup(pair, side):
    """OANDA's markup: how far today's OANDA rate sits below the policy-rate difference."""
    return theoretical_rate(pair, side, CALIBRATION_DATE) - OANDA_RATES[pair][side]


def annual_rate(pair, side, on_date):
    """Estimated OANDA annual financing rate (decimal) on a past date."""
    return theoretical_rate(pair, side, on_date) - markup(pair, side)


def rollovers(start, end, schedule="help"):
    """List (UTC datetime, days charged) for every rollover with start < time <= end.

    `start`/`end` are timezone-aware datetimes. The rollover is 17:00 New York on
    each weekday, i.e. 21:00 UTC in US summer time and 22:00 UTC in winter.
    Market holidays are not modelled.
    """
    days_by_weekday = SCHEDULES[schedule]
    result = []
    day = start.astimezone(NEW_YORK).date()
    last_day = end.astimezone(NEW_YORK).date()
    while day <= last_day:
        days = days_by_weekday.get(day.weekday(), 0)
        if days:
            moment = datetime.combine(day, ROLLOVER_TIME, tzinfo=NEW_YORK).astimezone(UTC)
            if start < moment <= end:
                result.append((moment, days))
        day += timedelta(days=1)
    return result


def financing_amount(units, price, rate, days):
    """Financing for one rollover, in the quote currency.

    units: position size (positive number of base-currency units)
    price: closing price at the rollover
    rate:  annual rate for the position's side (decimal, from annual_rate)
    Positive = credited to the account, negative = charged.
    """
    return units * price * rate * days / 365


def apply_mode(amount, mode):
    """Financing reporting variants (docs/strategy.md §8)."""
    if mode == "on":
        return amount
    if mode == "off":
        return 0.0
    if mode == "cap":  # never let financing pay us
        return min(amount, 0.0)
    raise ValueError(f"unknown financing mode {mode!r}")
