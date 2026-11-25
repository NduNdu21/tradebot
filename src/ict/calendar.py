"""New York session calendar for the ICT strategy.

All session rules are defined in New York local time, so DST is handled by
converting from New York to UTC per day (US and Europe change clocks on
different dates, so a fixed UTC offset would be wrong for some weeks).
"""
from datetime import date, datetime, time
from typing import Optional

import pandas as pd

NY = "America/New_York"

# Source: NYSE holiday and early-close rules, written out by hand and checked against an independent rule-derived list (2026-11-25), 2016-10-03
# to 2026-11-13. Weekend holidays use the NYSE observance rule (Sat -> Fri,
# Sun -> Mon; New Year's on a Saturday is not observed).
NYSE_HOLIDAYS = {
    date(2016, 11, 24),  # Thanksgiving
    date(2016, 12, 26),  # Christmas (observed)
    date(2017, 1, 2),    # New Year's Day (observed)
    date(2017, 1, 16),   # Martin Luther King Jr. Day
    date(2017, 2, 20),   # Presidents' Day
    date(2017, 4, 14),   # Good Friday
    date(2017, 5, 29),   # Memorial Day
    date(2017, 7, 4),    # Independence Day
    date(2017, 9, 4),    # Labor Day
    date(2017, 11, 23),  # Thanksgiving
    date(2017, 12, 25),  # Christmas
    date(2018, 1, 1),    # New Year's Day
    date(2018, 1, 15),   # Martin Luther King Jr. Day
    date(2018, 2, 19),   # Presidents' Day
    date(2018, 3, 30),   # Good Friday
    date(2018, 5, 28),   # Memorial Day
    date(2018, 7, 4),    # Independence Day
    date(2018, 9, 3),    # Labor Day
    date(2018, 11, 22),  # Thanksgiving
    date(2018, 12, 5),   # National day of mourning, President G.H.W. Bush
    date(2018, 12, 25),  # Christmas
    date(2019, 1, 1),    # New Year's Day
    date(2019, 1, 21),   # Martin Luther King Jr. Day
    date(2019, 2, 18),   # Presidents' Day
    date(2019, 4, 19),   # Good Friday
    date(2019, 5, 27),   # Memorial Day
    date(2019, 7, 4),    # Independence Day
    date(2019, 9, 2),    # Labor Day
    date(2019, 11, 28),  # Thanksgiving
    date(2019, 12, 25),  # Christmas
    date(2020, 1, 1),    # New Year's Day
    date(2020, 1, 20),   # Martin Luther King Jr. Day
    date(2020, 2, 17),   # Presidents' Day
    date(2020, 4, 10),   # Good Friday
    date(2020, 5, 25),   # Memorial Day
    date(2020, 7, 3),    # Independence Day (observed)
    date(2020, 9, 7),    # Labor Day
    date(2020, 11, 26),  # Thanksgiving
    date(2020, 12, 25),  # Christmas
    date(2021, 1, 1),    # New Year's Day
    date(2021, 1, 18),   # Martin Luther King Jr. Day
    date(2021, 2, 15),   # Presidents' Day
    date(2021, 4, 2),    # Good Friday
    date(2021, 5, 31),   # Memorial Day
    date(2021, 7, 5),    # Independence Day (observed)
    date(2021, 9, 6),    # Labor Day
    date(2021, 11, 25),  # Thanksgiving
    date(2021, 12, 24),  # Christmas (observed)
    date(2022, 1, 17),   # Martin Luther King Jr. Day
    date(2022, 2, 21),   # Presidents' Day
    date(2022, 4, 15),   # Good Friday
    date(2022, 5, 30),   # Memorial Day
    date(2022, 6, 20),   # Juneteenth (observed)
    date(2022, 7, 4),    # Independence Day
    date(2022, 9, 5),    # Labor Day
    date(2022, 11, 24),  # Thanksgiving
    date(2022, 12, 26),  # Christmas (observed)
    date(2023, 1, 2),    # New Year's Day (observed)
    date(2023, 1, 16),   # Martin Luther King Jr. Day
    date(2023, 2, 20),   # Presidents' Day
    date(2023, 4, 7),    # Good Friday
    date(2023, 5, 29),   # Memorial Day
    date(2023, 6, 19),   # Juneteenth
    date(2023, 7, 4),    # Independence Day
    date(2023, 9, 4),    # Labor Day
    date(2023, 11, 23),  # Thanksgiving
    date(2023, 12, 25),  # Christmas
    date(2024, 1, 1),    # New Year's Day
    date(2024, 1, 15),   # Martin Luther King Jr. Day
    date(2024, 2, 19),   # Presidents' Day
    date(2024, 3, 29),   # Good Friday
    date(2024, 5, 27),   # Memorial Day
    date(2024, 6, 19),   # Juneteenth
    date(2024, 7, 4),    # Independence Day
    date(2024, 9, 2),    # Labor Day
    date(2024, 11, 28),  # Thanksgiving
    date(2024, 12, 25),  # Christmas
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 9),    # National day of mourning, President Carter
    date(2025, 1, 20),   # Martin Luther King Jr. Day
    date(2025, 2, 17),   # Presidents' Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
}

# Same source. These days close at 13:00 New York, so the cash session is cut short.
NYSE_EARLY_CLOSES = {
    date(2016, 11, 25),  # day after Thanksgiving
    date(2017, 7, 3),    # day before Independence Day
    date(2017, 11, 24),  # day after Thanksgiving
    date(2018, 7, 3),    # day before Independence Day
    date(2018, 11, 23),  # day after Thanksgiving
    date(2018, 12, 24),  # Christmas Eve
    date(2019, 7, 3),    # day before Independence Day
    date(2019, 11, 29),  # day after Thanksgiving
    date(2019, 12, 24),  # Christmas Eve
    date(2020, 11, 27),  # day after Thanksgiving
    date(2020, 12, 24),  # Christmas Eve
    date(2021, 11, 26),  # day after Thanksgiving
    date(2022, 11, 25),  # day after Thanksgiving
    date(2023, 7, 3),    # day before Independence Day
    date(2023, 11, 24),  # day after Thanksgiving
    date(2024, 7, 3),    # day before Independence Day
    date(2024, 11, 29),  # day after Thanksgiving
    date(2024, 12, 24),  # Christmas Eve
    date(2025, 7, 3),    # day before Independence Day
    date(2025, 11, 28),  # day after Thanksgiving
    date(2025, 12, 24),  # Christmas Eve
    date(2026, 11, 27),  # day after Thanksgiving
}


def ny_time(utc_index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Convert a UTC index to New York local time."""
    return utc_index.tz_convert(NY)


def _utc(day: date, clock: time) -> pd.Timestamp:
    """The UTC instant of `clock` New York time on `day` (DST-aware)."""
    return pd.Timestamp(datetime.combine(day, clock), tz=NY).tz_convert("UTC")


def session_times(day: date) -> dict:
    """Key instants of the trading day, as UTC Timestamps.

    check_start 08:00 - first candle of the pre-entry data check
    levels      08:30 - liquidity levels are fixed from the data before this
    entry_end   11:00 - no new entries after this
    exit        12:00 - any open trade is closed
    """
    return {
        "check_start": _utc(day, time(8, 0)),
        "levels": _utc(day, time(8, 30)),
        "entry_end": _utc(day, time(11, 0)),
        "exit": _utc(day, time(12, 0)),
    }


def is_skipped_market_day(day: date) -> Optional[str]:
    """"holiday" (NYSE closed), "early_close" (13:00 close) or None (normal day)."""
    if day in NYSE_HOLIDAYS:
        return "holiday"
    if day in NYSE_EARLY_CLOSES:
        return "early_close"
    return None


def missing_m5(m5: pd.DataFrame, day: date) -> bool:
    """True if any of the 48 M5 slots 08:00-11:55 New York on `day` has no candle."""
    start = pd.Timestamp(datetime.combine(day, time(8, 0)), tz=NY)
    expected = pd.date_range(start, periods=48, freq="5min").tz_convert("UTC")
    return not expected.isin(m5.index).all()
