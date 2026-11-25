"""Evaluate strategy 4 against its pre-registered bar (docs/strategy-multi-asset-trend.md §8).

    python -m src.multi.evaluate             # development
    python -m src.multi.evaluate --holdout   # the single hold-out run, only after a development pass
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from src.multi.run import DEV, HOLDOUT, OUT_DIR, build, inputs_for, simulate
from src.multi.universe import CLASS_OF, CLASSES, INSTRUMENTS
from src.trend.evaluate import (SEED, CONTROL_RUNS, SUB_PERIODS, BASE_GBP, bars_per_year, checks, episodes,
                                holdout_checks, sharpe, summary, write_report)
from src.trend.run import MARGIN as FX_MARGIN
from src.trend.sim import daily_pnl

# OANDA margin rates, 2026-11-25.
MARGIN = {**{i: 0.05 for i in CLASSES["equity"]}, **{i: 0.2 for i in CLASSES["bond"]},
          "XAU_USD": 0.05, "XAG_USD": 0.10, **FX_MARGIN}


def window(df, span):
    return df[(df.index >= span[0]) & (df.index <= span[1])]


def portfolio(results, column="total"):
    return pd.concat({i: r[column] for i, r in results.items()}, axis=1).fillna(0.0).sum(axis=1)


def run_set(inputs, slips, span, **kw):
    return {i: window(simulate(b, i, slips[i], **kw), span) for i, b in inputs.items()}


def random_control(inputs, slips, span, dates, runs=CONTROL_RUNS, seed=SEED):
    """Random sign per holding episode, same sizes and trade dates (§8.3)."""
    rng = np.random.default_rng(seed)
    total = np.zeros((runs, len(dates)))
    for inst in INSTRUMENTS:
        b = inputs[inst]
        held = simulate(b, inst, slips[inst])["held"].values
        ep, n = episodes(held)
        signs = rng.choice([-1.0, 1.0], size=(runs, n))
        flip = np.where(ep >= 0, signs[:, np.maximum(ep, 0)], 0.0)
        parts = daily_pnl(flip * held, b, b["long_rate"].values, b["short_rate"].values, b["fx"].values, inst,
                          slip=slips[inst])
        pnl = parts["price"] + parts["cost"] + parts["fin"]
        keep = (b.index >= span[0]) & (b.index <= span[1])
        total[:, dates.get_indexer(b.index[keep])] += pnl[:, keep]
    return sharpe(total, bars_per_year(dates))


def evaluate(inputs, slips, extra, span=DEV):
    res = run_set(inputs, slips, span)
    pnl = portfolio(res)
    out = {"span": [str(span[0].date()), str(span[1].date())], "primary": summary(pnl)}
    out["sub_periods"] = {name: float(pnl[(pnl.index >= a) & (pnl.index <= z)].sum()) for name, a, z in SUB_PERIODS}
    per = {i: float(r["total"].sum()) for i, r in res.items()}
    out["per_instrument_gbp"] = per
    best = max(per, key=per.get)
    out["best_pair"] = best
    out["sharpe_without_best"] = summary(portfolio({i: r for i, r in res.items() if i != best}))["sharpe"]
    ctrl = random_control(inputs, slips, span, pnl.index)
    out["control"] = {"p95": float(np.percentile(ctrl, 95)), "median": float(np.median(ctrl)),
                      "share_beaten": float((ctrl < out["primary"]["sharpe"]).mean())}

    # Reported, not judged.
    out["classes"] = {c: summary(portfolio({i: res[i] for i in members}).reindex(pnl.index, fill_value=0.0))
                      for c, members in CLASSES.items()}
    out["unrounded"] = summary(portfolio(run_set(inputs, slips, span, rounded=False)))
    out["financing_off"] = summary(portfolio(run_set(inputs, slips, span, financing=False)))
    all_bars, rates, yields = extra
    for name, kw in (("no_dividends", {"dividends": False}), ("bond_carry_0", {"bond_carry": False})):
        alt = {i: inputs_for(i, all_bars, rates, yields, **kw) for i in INSTRUMENTS}
        out[name] = summary(portfolio(run_set(alt, slips, span)))
    out["parts_gbp"] = {k: float(portfolio(res, k).sum()) for k in ("price", "cost", "fin")}
    out["per_year_gbp"] = {str(y): float(v) for y, v in pnl.groupby(pnl.index.year).sum().items()}
    margin = sum((r["held"].abs() * inputs[i]["c"].reindex(r.index) * inputs[i]["fx"].reindex(r.index)
                  * MARGIN[i]).reindex(pnl.index).fillna(0) for i, r in res.items())
    out["peak_margin_pct"] = float(margin.max() / BASE_GBP * 100)
    lengths = []
    for r in res.values():
        ep, _ = episodes(r["held"].values)
        lengths += list(pd.Series(ep[ep >= 0]).value_counts().values)
    out["episodes"], out["mean_holding_bars"] = len(lengths), float(np.mean(lengths))
    out["flat_share"] = {i: float((r["held"] == 0).mean()) for i, r in res.items()}
    return out, pnl


def render(out, c, title):
    p = out["primary"]
    lines = [f"## {title}", "",
             f"**Verdict: {'PASS' if all(c.values()) else 'FAIL'}** (pre-registered bar, "
             "`docs/strategy-multi-asset-trend.md` §8).", "", "| Check | Result | Pass |", "|---|---|---|"]
    values = {
        "sharpe >= 0.4": f"Sharpe {p['sharpe']:.2f}",
        "positive in >= 2 of 3 sub-periods": ", ".join(f"{k} £{v:,.0f}" for k, v in out["sub_periods"].items()),
        "beats control 95th percentile": f"{p['sharpe']:.2f} vs control p95 {out['control']['p95']:.2f} "
                                         f"(median {out['control']['median']:.2f})",
        "sharpe > 0.2 without best pair": f"{out['sharpe_without_best']:.2f} without {out['best_pair']}",
        "sharpe > 0": f"Sharpe {p['sharpe']:.2f}", "max drawdown < 15%": f"{p['max_drawdown_pct']:.1f}%",
    }
    for k, ok in c.items():
        lines.append(f"| {k} | {values[k]} | {'✓' if ok else '✗'} |")
    lines += ["", f"Annual return {p['annual_return_pct']:.2f}% on a GBP 10,000 base (no compounding), max drawdown "
                  f"{p['max_drawdown_pct']:.1f}%, total £{p['total_gbp']:,.0f} over {p['bars']} daily bars.", "",
              "Reported, not judged:", "", "| Version | Sharpe | Annual % | Max DD % |", "|---|---|---|---|"]
    rows = [(f"{k} only", v) for k, v in out["classes"].items()] + [
        ("unrounded units", out["unrounded"]), ("no dividends", out["no_dividends"]),
        ("bond carry 0", out["bond_carry_0"]), ("financing off", out["financing_off"])]
    for name, s in rows:
        lines.append(f"| {name} | {s['sharpe']:.2f} | {s['annual_return_pct']:.2f} | {s['max_drawdown_pct']:.1f} |")
    parts = out["parts_gbp"]
    lines += ["", f"P&L parts: price £{parts['price']:,.0f}, spread + slippage £{parts['cost']:,.0f}, financing "
                  f"£{parts['fin']:,.0f}. Peak margin {out['peak_margin_pct']:.0f}% of the base. {out['episodes']} "
                  f"holding episodes, mean {out['mean_holding_bars']:.0f} daily bars.", "",
              "Per instrument (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in
                                                  sorted(out["per_instrument_gbp"].items(), key=lambda kv: -kv[1])),
              "", "Per year (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_year_gbp"].items()), ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    inputs, slips, extra = build(include_holdout=args.holdout)
    span = HOLDOUT if args.holdout else DEV
    out, pnl = evaluate(inputs, slips, extra, span)
    which = "holdout" if args.holdout else "development"
    title = f"Multi-asset trend, {which} ({out['span'][0]} → {out['span'][1]})"
    c = holdout_checks(out) if args.holdout else checks(out)
    out["checks"], out["verdict"], out["slippage"] = c, "PASS" if all(c.values()) else "FAIL", slips
    json.dump(out, open(os.path.join(OUT_DIR, f"evaluation_{which}.json"), "w"), indent=1)
    pnl.to_csv(os.path.join(OUT_DIR, f"daily_pnl_{which}.csv"))
    text = render(out, c, title)
    write_report(text, title)
    print(text)


if __name__ == "__main__":
    main()
