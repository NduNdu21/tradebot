"""Planted-edge test of the trend engines (strategy 2 and strategy 4).

Plants known trends into the real daily bars: each instrument gets random up/down phases
(geometric lengths, mean 126 bars, seed 0) with a steady drift mu per bar in the phase's
direction, applied multiplicatively to every price in the bar (open, high, low, close and the
08:00 bid/ask). FX conversion rates, spreads (scaled with price), financing rates and the
engines' code are unchanged.

Pass criteria (fixed before the first run, 2026-11-25), for both engines:
  1. mu = 0 reproduces the published results exactly
     (strategy 2: Sharpe -0.04, total -£1,394; strategy 4: Sharpe -0.10, total -£747);
  2. Sharpe rises with mu, while the random-sign control's median stays within ±0.1 of its mu = 0 value;
  3. the share of position-days on the side of the planted phase rises above 50% as mu grows;
  4. an independent cash ledger (cash paid and received at each fill, plus financing, plus the
     final position at the last close) equals the engine's summed daily P&L for every
     instrument, in the quote currency, to within 1e-6 of a unit.

    python -m src.checks.planted_trend
"""
import numpy as np
import pandas as pd

from src.trend import evaluate as E2
from src.trend import run as R2
from src.trend.bars import atr
from src.trend.signals import trend_score, unit_size
from src.multi import evaluate as E4
from src.multi import run as R4

MUS = [0.0, 0.0002, 0.0005, 0.0010]   # planted drift per daily bar
PRICE_COLS = ["o", "h", "l", "c", "exec_bid", "exec_ask"]
MEAN_PHASE = 126
SEED = 0


def phases(index, rng):
    """+1/-1 phase sign per bar, geometric phase lengths with mean MEAN_PHASE."""
    signs, s = np.empty(len(index)), rng.choice([-1.0, 1.0])
    for i in range(len(index)):
        if rng.random() < 1 / MEAN_PHASE:
            s = -s
        signs[i] = s
    return signs


def plant(b, signs, mu):
    """Scale bar t's prices by exp(mu * sum of signs up to t)."""
    out = b.copy()
    f = np.exp(mu * np.cumsum(signs))
    for col in PRICE_COLS:
        out[col] = b[col] * f
    return out


def ledger_check(b, sim, slip):
    """Max |cash ledger - summed daily P&L| over the run, in the quote currency."""
    held = sim["held"].values
    traded = np.diff(np.concatenate([[0.0], held]))
    buy = traded > 0
    fill = np.where(buy, b["exec_ask"].values + slip, b["exec_bid"].values - slip)
    fill = np.where(traded == 0, 0.0, fill)
    cash = -np.sum(traded * fill)
    fx = b["fx"].values
    fin_quote = np.sum(np.nan_to_num(sim["fin"].values / fx))
    ledger = cash + fin_quote + held[-1] * b["c"].values[-1]
    daily = np.nansum((sim["price"].values + sim["cost"].values + sim["fin"].values) / fx)
    return abs(ledger - daily)


def aligned_share(results, signs_by, span):
    on = tot = 0
    for inst, r in results.items():
        s = pd.Series(signs_by[inst], index=signs_by[inst + "_index"]).reindex(r.index).values
        h = np.sign(r["held"].values)
        on += np.sum((h != 0) & (h == s))
        tot += np.sum(h != 0)
    return on / tot


def strategy2(mu, base_inputs, signs_by):
    inputs = {}
    for p, b in base_inputs.items():
        x = plant(b, signs_by[p], mu)
        x["trend"] = trend_score(x["c"])
        x["atr"] = atr(x, 20)
        x["unit"] = unit_size(x["atr"], x["fx"])
        inputs[p] = x
    res = {p: E2.window(R2.simulate(b, p), R2.DEV) for p, b in inputs.items()}
    pnl = E2.portfolio(res) if hasattr(E2, "portfolio") else R2.portfolio(res)
    ctrl = E2.random_control(inputs, R2.DEV, pnl.index)
    ledger = max(ledger_check(inputs[p], R2.simulate(inputs[p], p), __import__("src.trend.sim", fromlist=["x"]).slippage(p))
                 for p in inputs)
    return E2.summary(pnl), float(np.median(ctrl)), aligned_share(res, signs_by, R2.DEV), ledger


def strategy4(mu, base_inputs, slips, signs_by):
    inputs = {}
    for i, b in base_inputs.items():
        x = plant(b, signs_by[i], mu)
        x["trend"] = trend_score(x["c"])
        x["atr"] = atr(x, 20)
        x["unit"] = R4.unit_risk_gbp(i) / (x["atr"] * x["fx"])
        inputs[i] = x
    # Slippage is half the median development spread; spreads scale with the planted price.
    sl = {i: R4.slippage_from_spreads(inputs[i]) for i in inputs}
    res = E4.run_set(inputs, sl, R4.DEV)
    pnl = E4.portfolio(res)
    ctrl = E4.random_control(inputs, sl, R4.DEV, pnl.index)
    ledger = max(ledger_check(inputs[i], R4.simulate(inputs[i], i, sl[i]), sl[i]) for i in inputs)
    return E2.summary(pnl), float(np.median(ctrl)), aligned_share(res, signs_by, R4.DEV), ledger


def report(name, rows, published):
    print(f"\n{name}")
    base_sharpe, base_ctrl = rows[0][1]["sharpe"], rows[0][2]
    c1 = round(base_sharpe, 2) == published[0] and abs(rows[0][1]["total_gbp"] - published[1]) < 1
    prev, c2, c3 = -np.inf, True, rows[-1][3] > 0.5
    for mu, s, ctrl, share, ledger in rows:
        print(f"  mu {mu * 100:.2f}%/bar: Sharpe {s['sharpe']:.2f}, total £{s['total_gbp']:,.0f}, control median "
              f"{ctrl:.2f}, position-days with the planted trend {share * 100:.1f}%, ledger gap {ledger:.2e}")
        c2 &= s["sharpe"] > prev and abs(ctrl - base_ctrl) <= 0.1
        prev = s["sharpe"]
    shares = [r[3] for r in rows]
    c3 &= all(b >= a for a, b in zip(shares[1:], shares[2:]))
    c4 = all(r[4] < 1e-6 for r in rows)
    for k, ok in enumerate([c1, c2, c3, c4], 1):
        print(f"  criterion {k}: {'PASS' if ok else 'FAIL'}")


def main():
    rng = np.random.default_rng(SEED)
    base2 = R2.build()
    base4, slips4, _ = R4.build()
    signs_by = {}
    for inst in sorted(set(base2) | set(base4)):
        idx = (base4.get(inst) if inst in base4 else base2[inst]).index
        signs_by[inst], signs_by[inst + "_index"] = phases(idx, rng), idx
    for p in base2:   # strategy 2's bars are the same daily bars as strategy 4's FX pairs
        assert base2[p].index.equals(signs_by[p + "_index"])
    rows2 = [(mu, *strategy2(mu, base2, signs_by)) for mu in MUS]
    report("Strategy 2 engine (trend + carry, 12 FX pairs)", rows2, (-0.04, -1394))
    rows4 = [(mu, *strategy4(mu, base4, slips4, signs_by)) for mu in MUS]
    report("Strategy 4 engine (multi-asset trend, 24 instruments)", rows4, (-0.10, -747))


if __name__ == "__main__":
    main()
