"""Short-term interest rates for the carry signal and financing (strategy §2, §6).

OECD monthly "immediate rates: call money / interbank" from FRED, saved by hand to
data/rates/IRSTCI01<cc>M156N.csv. A month's average is only known once the month is
over, so the value for month m is used from the first day of month m+1 (no lookahead).
"""
import os
from datetime import date

import pandas as pd

RATES_DIR = os.path.join("data", "rates")
FRED_CODE = {"USD": "US", "EUR": "EZ", "GBP": "GB", "JPY": "JP",
             "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH"}
MARKUP = 0.01  # OANDA's financing markup per side, measured 2026-11-25 (§6)


def load_rates(rates_dir=RATES_DIR):
    """{currency: Series of decimal rates indexed by the first day each value may be used}."""
    out = {}
    for ccy, cc in FRED_CODE.items():
        df = pd.read_csv(os.path.join(rates_dir, f"IRSTCI01{cc}M156N.csv"))
        month = pd.to_datetime(df.iloc[:, 0])
        usable_from = month + pd.offsets.MonthBegin(1)   # month m's average is used from month m+1
        out[ccy] = pd.Series(df.iloc[:, 1].astype(float).values / 100, index=usable_from).sort_index()
    return out


def rate_on(rates, ccy, days):
    """Rate in force for each date in `days` (a DatetimeIndex of naive dates); NaN before the series starts."""
    s = rates[ccy]
    return s.reindex(s.index.union(days)).ffill().reindex(days)


def financing_rates(rates, pair, days):
    """(long, short) annual financing rates for `pair` on each day, after OANDA's markup.

    A long holds the base and borrows the quote, so it earns base - quote, minus the markup.
    """
    base, quote = pair.split("_")
    diff = rate_on(rates, base, days).values - rate_on(rates, quote, days).values
    return diff - MARKUP, -diff - MARKUP
