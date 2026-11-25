"""Inputs, simulation and portfolio P&L for strategy 4 (strategy §2–§7)."""
import os

import numpy as np
import pandas as pd

from src.data import load_candles
from src.trend.bars import atr, daily_bars
from src.trend.rates import RATES_DIR, load_rates, rate_on
from src.trend.signals import trend_score
from src.trend.sim import daily_pnl, positions
from src.multi.universe import (BOND_YIELD, CLASS_OF, CLASS_RISK_GBP, CLASSES, CONVERTERS, DIVIDEND, INSTRUMENTS,
                                MARKUP, PRECISION, quote_currency)

DEV = (pd.Timestamp("2006-01-02"), pd.Timestamp("2024-11-13"))
HOLDOUT = (pd.Timestamp("2024-11-14"), pd.Timestamp("2026-11-13"))
OUT_DIR = os.path.join("data", "multi")


def load_yields(rates_dir=RATES_DIR):
    """{bond instrument: decimal yield Series indexed by the first day it may be used (month after)}."""
    out = {}
    for inst, code in BOND_YIELD.items():
        df = pd.read_csv(os.path.join(rates_dir, f"{code}.csv"))
        usable_from = pd.to_datetime(df.iloc[:, 0]) + pd.offsets.MonthBegin(1)
        out[inst] = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values / 100, index=usable_from).dropna()
    return out


def asset_and_funding(inst, days, rates, yields, dividends=True, bond_carry=True):
    """(asset yield, funding rate) per day, as arrays (§6)."""
    cls = CLASS_OF[inst]
    if cls == "fx":
        base, quote = inst.split("_")
        return rate_on(rates, base, days).values, rate_on(rates, quote, days).values
    funding = rate_on(rates, quote_currency(inst), days).values
    if cls == "equity":
        asset = np.full(len(days), DIVIDEND[inst] if dividends else 0.0)
    elif cls == "bond":
        y = yields[inst]
        asset = y.reindex(y.index.union(days)).ffill().reindex(days).values if bond_carry else funding
    else:  # metal
        asset = np.zeros(len(days))
    return asset, funding


def financing_rates(inst, days, rates, yields, **kw):
    """(long, short) annual rates: +/-(asset yield - funding) - markup."""
    asset, funding = asset_and_funding(inst, days, rates, yields, **kw)
    carry = asset - funding
    return carry - MARKUP[inst], -carry - MARKUP[inst]


def quote_to_gbp(inst, index, all_bars):
    quote = quote_currency(inst)
    if quote == "GBP":
        return pd.Series(1.0, index=index)
    pair, invert = CONVERTERS[quote]
    c = all_bars[pair]["c"]
    c = 1 / c if invert else c
    return c.reindex(c.index.union(index)).ffill().reindex(index)


def unit_risk_gbp(inst):
    """GBP per ATR for one instrument: its class's budget split equally (§3)."""
    return CLASS_RISK_GBP / len(CLASSES[CLASS_OF[inst]])


def slippage_from_spreads(bars, span=DEV):
    """Half the median 08:00 spread over the development period (§5), in price units."""
    d = bars[(bars.index >= span[0]) & (bars.index <= span[1])]
    return float(((d["exec_ask"] - d["exec_bid"]) / 2).median())


def inputs_for(inst, all_bars, rates, yields, **fin_kw):
    b = all_bars[inst].copy()
    b["fx"] = quote_to_gbp(inst, b.index, all_bars)
    b["long_rate"], b["short_rate"] = financing_rates(inst, b.index, rates, yields, **fin_kw)
    b["trend"] = trend_score(b["c"])
    b["atr"] = atr(b, 20)
    b["unit"] = unit_risk_gbp(inst) / (b["atr"] * b["fx"])
    return b


def target(b, inst, rounded=True):
    """Trend score x U, rounded to OANDA's unit precision (NaN in warm-up -> flat)."""
    t = (b["trend"] * b["unit"]).fillna(0.0)
    return t.round(PRECISION[inst]) if rounded else t


def simulate(b, inst, slip, rounded=True, financing=True):
    tgt = target(b, inst, rounded)
    held = positions(tgt.values, b["exec_bid"].notna().values)
    zero = np.zeros(len(b))
    parts = daily_pnl(held, b, b["long_rate"].values if financing else zero,
                      b["short_rate"].values if financing else zero, b["fx"].values, inst, slip=slip)
    out = pd.DataFrame(parts, index=b.index)
    out["held"], out["target"] = held, tgt.values
    out["total"] = out["price"] + out["cost"] + out["fin"]
    return out


def load_all(include_holdout=False):
    needed = set(INSTRUMENTS) | {p for p, _ in CONVERTERS.values()}
    return {i: daily_bars(load_candles(i, "H1", include_holdout)) for i in sorted(needed)}


def build(include_holdout=False, **fin_kw):
    all_bars = load_all(include_holdout)
    rates, yields = load_rates(), load_yields()
    inputs = {i: inputs_for(i, all_bars, rates, yields, **fin_kw) for i in INSTRUMENTS}
    slips = {i: slippage_from_spreads(all_bars[i]) for i in INSTRUMENTS}
    return inputs, slips, (all_bars, rates, yields)
