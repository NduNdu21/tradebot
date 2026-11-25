"""Build each pair's inputs, run the strategy, and collect daily P&L (strategy §2–§7).

    python -m src.trend.run          # development run -> data/trend/
"""
import json
import os

import numpy as np
import pandas as pd

from src.data import load_candles
from src.trend.bars import atr, daily_bars
from src.trend.rates import financing_rates, load_rates
from src.trend.signals import ATR_BARS, carry_signal, target_units, trend_score, unit_size
from src.trend.sim import daily_pnl, positions

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "USD_CAD", "AUD_USD", "NZD_USD",
         "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_CHF"]
# Quote currency -> the GBP pair whose mid close converts it (GBP per quote unit = 1 / close).
CONVERTERS = {"USD": "GBP_USD", "JPY": "GBP_JPY", "CHF": "GBP_CHF", "CAD": "GBP_CAD"}
# OANDA margin rates measured 2026-11-25 (data/rates/oanda_financing_2026-11-25.json).
MARGIN = {p: (0.05 if p[:3] in ("AUD", "NZD") else 1 / 30) for p in PAIRS}
DEV = (pd.Timestamp("2006-01-02"), pd.Timestamp("2024-11-13"))
HOLDOUT = (pd.Timestamp("2024-11-14"), pd.Timestamp("2026-11-13"))
OUT_DIR = os.path.join("data", "trend")


def load_bars(instruments, include_holdout=False):
    return {p: daily_bars(load_candles(p, "H1", include_holdout)) for p in instruments}


def quote_to_gbp(pair, index, all_bars):
    """GBP value of one unit of the pair's quote currency, at each bar's close."""
    quote = pair.split("_")[1]
    if quote == "GBP":
        return pd.Series(1.0, index=index)
    conv = all_bars[CONVERTERS[quote]]["c"]
    return (1 / conv).reindex(conv.index.union(index)).ffill().reindex(index)


def pair_inputs(pair, all_bars, rates):
    """Everything the simulation needs for one pair, one row per daily bar."""
    b = all_bars[pair].copy()
    b["fx"] = quote_to_gbp(pair, b.index, all_bars)
    b["long_rate"], b["short_rate"] = financing_rates(rates, pair, b.index)
    b["trend"] = trend_score(b["c"])
    b["carry"] = carry_signal(b["long_rate"], b["short_rate"])
    b["atr"] = atr(b, ATR_BARS)
    b["unit"] = unit_size(b["atr"], b["fx"])
    return b


def simulate(b, pair, w_trend=1.0, w_carry=1.0, financing=True):
    """Position path and daily GBP P&L parts for one pair."""
    target = target_units(b["trend"], b["carry"], b["unit"], w_trend, w_carry)
    held = positions(target.values, b["exec_bid"].notna().values)
    zero = np.zeros(len(b))
    pnl = daily_pnl(held, b, b["long_rate"].values if financing else zero,
                    b["short_rate"].values if financing else zero, b["fx"].values, pair)
    out = pd.DataFrame(pnl, index=b.index)
    out["held"] = held
    out["target"] = target.values
    out["total"] = out["price"] + out["cost"] + out["fin"]
    return out


def window(df, span):
    return df[(df.index >= span[0]) & (df.index <= span[1])]


def run_all(inputs, span, **kw):
    """{pair: per-bar result inside span}."""
    return {p: window(simulate(b, p, **kw), span) for p, b in inputs.items()}


def portfolio(results, column="total"):
    """Daily portfolio P&L in GBP over the union of dates (a pair with no bar that day adds 0)."""
    return pd.concat({p: r[column] for p, r in results.items()}, axis=1).fillna(0.0).sum(axis=1)


def build(include_holdout=False):
    instruments = sorted(set(PAIRS) | set(CONVERTERS.values()))
    all_bars = load_bars(instruments, include_holdout)
    rates = load_rates()
    return {p: pair_inputs(p, all_bars, rates) for p in PAIRS}
