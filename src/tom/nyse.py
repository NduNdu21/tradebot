"""NYSE trading days from the exchange's holiday rules, 2005 onward.

Rules: New Year's Day (Sunday -> Monday; a Saturday New Year is not observed), Martin
Luther King Jr. Day (3rd Monday of January), Presidents' Day (3rd Monday of February),
Good Friday, Memorial Day (last Monday of May), Juneteenth (from 2022), Independence
Day, Labor Day (1st Monday of September), Thanksgiving (4th Thursday of November),
Christmas. Fixed-date holidays on a Saturday move to Friday, on a Sunday to Monday.
Plus the one-off closures listed in SPECIAL_CLOSURES.
"""
from datetime import date, timedelta

import pandas as pd
from dateutil.easter import easter

SPECIAL_CLOSURES = {
    date(2007, 1, 2),    # national day of mourning, President Ford
    date(2012, 10, 29),  # Hurricane Sandy
    date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),   # national day of mourning, President G.H.W. Bush
    date(2025, 1, 9),    # national day of mourning, President Carter
}


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year):
    out = set()
    ny = date(year, 1, 1)
    if ny.weekday() != 5:                       # Saturday New Year is not observed
        out.add(_observed(ny))
    out.add(_nth_weekday(year, 1, 0, 3))        # MLK
    out.add(_nth_weekday(year, 2, 0, 3))        # Presidents'
    out.add(easter(year) - timedelta(days=2))   # Good Friday
    out.add(_last_weekday(year, 5, 0))          # Memorial
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))   # Juneteenth
    out.add(_observed(date(year, 7, 4)))
    out.add(_nth_weekday(year, 9, 0, 1))        # Labor
    out.add(_nth_weekday(year, 11, 3, 4))       # Thanksgiving
    out.add(_observed(date(year, 12, 25)))
    return out | {d for d in SPECIAL_CLOSURES if d.year == year}


def trading_days(start, end):
    """NYSE trading days from start to end inclusive, as a DatetimeIndex of naive dates."""
    days = pd.bdate_range(start, end)
    closed = set().union(*(holidays(y) for y in range(days[0].year, days[-1].year + 1)))
    return days[[d.date() not in closed for d in days]]
