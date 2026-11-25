"""Planted-edge test of the turn-of-the-month backtester.

Takes the real H1 prices and adds a known extra return delta on every turn-of-month day
(days -1, +1, +2, +3), by scaling every price from 00:00 New York on that day onward by
(1 + delta). Spreads, financing and sizing stay real. The unchanged backtester is then run,
and its extra P&L is compared with the extra P&L worked out by hand from the trades:

    extra per trade = units x exit price x ((1 + delta)^4 - 1) x USD->GBP

Pass criteria (fixed before the first run, 2026-11-25):
  1. delta = 0 reproduces the published result exactly;
  2. measured extra P&L within 3% of the hand-worked figure for every delta;
  3. Sharpe rises with delta, and the random-timing control rises much less.

    python -m src.checks.planted_edge
"""
import numpy as np
import pandas as pd

from src.data import load_candles
from src.tom import run as R
from src.tom.evaluate import evaluate, selected
from src.tom.nyse import trading_days
from src.trend.bars import daily_bars
from src.trend.rates import load_rates

DELTAS = [0.0, 0.0005, 0.0010, 0.0020]   # extra return per turn-of-month day
PRICE_COLS = [f"{s}_{f}" for s in ("bid", "ask") for f in ("o", "h", "l", "c")]


def tom_days(days):
    """Days -1, +1, +2, +3 of every month."""
    entries = R.tom_entry_days(days)
    pos = days.get_indexer(entries)
    out = [days[p + k] for p in pos for k in (1, 2, 3, 4) if p + k < len(days)]
    return pd.DatetimeIndex(sorted(set(out)))


def plant(h1, boost_days, delta):
    """Scale prices by (1 + delta) for every boost day whose 00:00 New York has passed."""
    if delta == 0:
        return h1
    ny_date = h1.index.tz_convert(R.NY).tz_localize(None).normalize()
    count = np.searchsorted(boost_days.values, ny_date.values, side="right")   # boost days on or before
    out = h1.copy()
    out[PRICE_COLS] = out[PRICE_COLS].mul((1 + delta) ** count, axis=0)
    return out


def run(delta, raw, gbpusd, rates, days, boost):
    trades, slips = {}, {}
    for inst in R.INSTRUMENTS:
        h1 = plant(raw[inst], boost, delta)
        slips[inst] = R.slippage(R.closes(h1), days)
        trades[inst] = R.four_day_trades(inst, h1, gbpusd, rates, days, slips[inst])
    return trades


def main():
    days = trading_days("2005-01-03", pd.Timestamp("2024-11-23"))
    raw = {i: load_candles(i, "H1") for i in R.INSTRUMENTS}
    gbpusd = daily_bars(load_candles("GBP_USD", "H1"))
    rates = load_rates()
    boost = tom_days(days)
    entries = R.tom_entry_days(days)

    base_trades = run(0.0, raw, gbpusd, rates, days, boost)
    base, _ = evaluate(base_trades, days)
    base_sel = {i: selected(base_trades[i], entries, R.DEV) for i in R.INSTRUMENTS}
    print(f"delta 0: Sharpe {base['primary']['sharpe']:.3f}, total £{base['primary']['total_gbp']:,.0f}, "
          f"control median {base['control']['median']:.2f}")

    ok = abs(base["primary"]["total_gbp"] - (-795)) < 1 and round(base["primary"]["sharpe"], 2) == -0.09
    print(f"  criterion 1 (reproduces −0.09 / −£795): {'PASS' if ok else 'FAIL'}")
    prev = base["primary"]["sharpe"]
    for delta in DELTAS[1:]:
        tr = run(delta, raw, gbpusd, rates, days, boost)
        out, _ = evaluate(tr, days)
        measured = out["primary"]["total_gbp"] - base["primary"]["total_gbp"]
        # Hand-worked: unplanted units and exit prices, the 4-day boost, USD->GBP at exit.
        expected = 0.0
        for inst in R.INSTRUMENTS:
            s = base_sel[inst].dropna(subset=["total"])
            cl = R.closes(raw[inst]).reindex(days)
            mid_out = ((cl["bid"] + cl["ask"]) / 2).reindex(pd.DatetimeIndex(s["exit"])).values
            fx = (1 / R.on_or_before(gbpusd["c"], pd.DatetimeIndex(s["exit"]))).values
            expected += float(np.sum(s["units"].values * mid_out * ((1 + delta) ** 4 - 1) * fx))
        err = measured / expected - 1
        p, c = out["primary"], out["control"]
        print(f"delta {delta * 100:.2f}%/day ({((1 + delta) ** 4 - 1) * 100:.2f}% per trade): Sharpe {p['sharpe']:.2f} "
              f"(+{p['sharpe'] - base['primary']['sharpe']:.2f}), verdict {out['verdict'] if 'verdict' in out else ''}"
              f"{'PASS' if all(__import__('src.trend.evaluate', fromlist=['checks']).checks(out).values()) else 'FAIL'}, "
              f"control median {c['median']:.2f} (+{c['median'] - base['control']['median']:.2f}), p95 {c['p95']:.2f}")
        print(f"   extra P&L measured £{measured:,.0f} vs hand-worked £{expected:,.0f} ({err * 100:+.1f}%), "
              f"criterion 2 {'PASS' if abs(err) < 0.03 else 'FAIL'}, criterion 3 "
              f"{'PASS' if p['sharpe'] > prev and (p['sharpe'] - base['primary']['sharpe']) > 2 * (c['median'] - base['control']['median']) else 'FAIL'}")
        prev = p["sharpe"]


if __name__ == "__main__":
    main()
