"""Trend score, carry signal and target position (strategy §3)."""
import numpy as np
import pandas as pd

LOOKBACKS = (63, 126, 252)   # about 3, 6 and 12 months of daily bars
ATR_BARS = 20
RISK_GBP = 25.0              # 0.25% of the GBP 10,000 base per ATR, per sleeve
WARMUP_BARS = max(LOOKBACKS) + ATR_BARS   # 272: a pair joins once all inputs exist


def trend_score(close):
    """Mean of the signs of the close change over each lookback: -1, -1/3, +1/3 or +1 (NaN in warm-up)."""
    signs = [np.sign(close - close.shift(n)) for n in LOOKBACKS]
    return sum(signs) / len(signs)


def carry_signal(long_rate, short_rate):
    """+1 if a long earns financing after markup, -1 if a short does, 0 if neither (NaN if unknown)."""
    long_rate = pd.Series(long_rate)
    short_rate = pd.Series(short_rate, index=long_rate.index)
    out = np.where(long_rate > 0, 1.0, np.where(short_rate > 0, -1.0, 0.0))
    out = pd.Series(out, index=long_rate.index)
    out[long_rate.isna() | short_rate.isna()] = np.nan
    return out


def unit_size(atr_quote, quote_to_gbp):
    """U: units whose one-ATR move is worth GBP 25."""
    return RISK_GBP / (atr_quote * quote_to_gbp)


def target_units(trend, carry, unit, w_trend=1.0, w_carry=1.0):
    """Target position in whole units: (trend score + carry signal) x U. NaN inputs -> 0 (not trading yet).

    w_trend / w_carry = 0 switch a sleeve off for the sleeve-only reports.
    """
    t = (w_trend * trend + w_carry * carry) * unit
    return t.fillna(0.0).round()
