"""Development evaluation of trend + carry against the pre-registered bar (strategy §8).

    python -m src.trend.evaluate             # development: writes data/trend/ and docs/backtests.md
    python -m src.trend.evaluate --holdout   # the single hold-out run (only after a development pass)
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from src.trend.run import DEV, HOLDOUT, MARGIN, OUT_DIR, PAIRS, build, portfolio, simulate, window
from src.trend.sim import daily_pnl

BASE_GBP = 10_000.0
SUB_PERIODS = [("2006–2011", "2006-01-01", "2011-12-31"),
               ("2012–2017", "2012-01-01", "2017-12-31"),
               ("2018–2024", "2018-01-01", "2024-11-13")]
CONTROL_RUNS = 1000
SEED = 0


def sharpe(pnl, bars_per_year):
    r = np.asarray(pnl, dtype=float) / BASE_GBP
    sd = r.std(ddof=1, axis=-1)
    return np.where(sd > 0, r.mean(axis=-1) / sd * np.sqrt(bars_per_year), 0.0)


def bars_per_year(index):
    years = (index[-1] - index[0]).days / 365.25
    return len(index) / years


def max_drawdown(pnl):
    """Largest peak-to-trough fall of cumulative P&L, as a share of the base."""
    cum = np.cumsum(np.asarray(pnl, dtype=float))
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float((peak - cum).max() / BASE_GBP)


def summary(pnl):
    bpy = bars_per_year(pnl.index)
    years = len(pnl) / bpy
    return {"sharpe": float(sharpe(pnl.values, bpy)), "annual_return_pct": float(pnl.sum() / years / BASE_GBP * 100),
            "max_drawdown_pct": max_drawdown(pnl.values) * 100, "total_gbp": float(pnl.sum()), "bars": len(pnl)}


def episodes(held):
    """Episode id per bar (runs of the same nonzero position sign); -1 when flat."""
    sign = np.sign(held)
    new = np.concatenate([[True], sign[1:] != sign[:-1]])
    ids = np.cumsum(new) - 1
    return np.where(sign != 0, ids, -1), ids.max() + 1


def random_control(inputs, span, dates, runs=CONTROL_RUNS, seed=SEED):
    """Sharpe of `runs` portfolios where every holding episode gets a random sign (§8.3)."""
    rng = np.random.default_rng(seed)
    total = np.zeros((runs, len(dates)))
    for pair in PAIRS:
        b = inputs[pair]
        held = simulate(b, pair)["held"].values
        ep, n = episodes(held)
        signs = rng.choice([-1.0, 1.0], size=(runs, n))
        flip = np.where(ep >= 0, signs[:, np.maximum(ep, 0)], 0.0)
        parts = daily_pnl(flip * held, b, b["long_rate"].values, b["short_rate"].values, b["fx"].values, pair)
        pnl = parts["price"] + parts["cost"] + parts["fin"]
        keep = (b.index >= span[0]) & (b.index <= span[1])
        pos = dates.get_indexer(b.index[keep])
        total[:, pos] += pnl[:, keep]
    return sharpe(total, bars_per_year(dates))


def evaluate(inputs, span=DEV):
    res = {p: window(simulate(b, p), span) for p, b in inputs.items()}
    pnl = portfolio(res)
    out = {"span": [str(span[0].date()), str(span[1].date())], "primary": summary(pnl)}

    out["sub_periods"] = {name: float(pnl[(pnl.index >= a) & (pnl.index <= z)].sum())
                          for name, a, z in SUB_PERIODS}
    per_pair = {p: float(r["total"].sum()) for p, r in res.items()}
    out["per_pair_gbp"] = per_pair
    best = max(per_pair, key=per_pair.get)
    out["best_pair"] = best
    out["sharpe_without_best"] = summary(portfolio({p: r for p, r in res.items() if p != best}))["sharpe"]

    ctrl = random_control(inputs, span, pnl.index)
    out["control"] = {"p95": float(np.percentile(ctrl, 95)), "median": float(np.median(ctrl)),
                      "share_beaten": float((ctrl < out["primary"]["sharpe"]).mean())}

    # Reported, not judged.
    out["parts_gbp"] = {k: float(portfolio(res, k).sum()) for k in ("price", "cost", "fin")}
    out["per_year_gbp"] = {str(y): float(v) for y, v in pnl.groupby(pnl.index.year).sum().items()}
    for name, kw in (("trend_only", {"w_carry": 0.0}), ("carry_only", {"w_trend": 0.0}),
                     ("financing_off", {"financing": False})):
        out[name] = summary(portfolio({p: window(simulate(b, p, **kw), span) for p, b in inputs.items()}))
    margin = sum((r["held"].abs() * inputs[p]["c"].reindex(r.index) * inputs[p]["fx"].reindex(r.index)
                  * MARGIN[p]).reindex(pnl.index).fillna(0) for p, r in res.items())
    out["peak_margin_pct"] = float(margin.max() / BASE_GBP * 100)
    lengths = []
    for r in res.values():
        ep, _ = episodes(r["held"].values)
        lengths += list(pd.Series(ep[ep >= 0]).value_counts().values)
    out["mean_holding_bars"] = float(np.mean(lengths))
    out["episodes"] = len(lengths)
    return out, pnl


def checks(out):
    p = out["primary"]
    positive = sum(v > 0 for v in out["sub_periods"].values())
    return {
        "sharpe >= 0.4": p["sharpe"] >= 0.4,
        "positive in >= 2 of 3 sub-periods": positive >= 2,
        "beats control 95th percentile": p["sharpe"] > out["control"]["p95"],
        "sharpe > 0.2 without best pair": out["sharpe_without_best"] > 0.2,
    }


def holdout_checks(out):
    p = out["primary"]
    return {"sharpe > 0": p["sharpe"] > 0, "max drawdown < 15%": p["max_drawdown_pct"] < 15}


def render(out, results_checks, title):
    p = out["primary"]
    passed = all(results_checks.values())
    lines = [f"## {title}", "",
             f"**Verdict: {'PASS' if passed else 'FAIL'}** (pre-registered bar, `docs/strategy-trend-carry.md` §8).", "",
             "| Check | Result | Pass |", "|---|---|---|"]
    values = {
        "sharpe >= 0.4": f"Sharpe {p['sharpe']:.2f}",
        "positive in >= 2 of 3 sub-periods": ", ".join(f"{k} £{v:,.0f}" for k, v in out["sub_periods"].items()),
        "beats control 95th percentile": f"{p['sharpe']:.2f} vs control p95 {out['control']['p95']:.2f} "
                                         f"(median {out['control']['median']:.2f})",
        "sharpe > 0.2 without best pair": f"{out['sharpe_without_best']:.2f} without {out['best_pair']}",
        "sharpe > 0": f"Sharpe {p['sharpe']:.2f}",
        "max drawdown < 15%": f"{p['max_drawdown_pct']:.1f}%",
    }
    for k, ok in results_checks.items():
        lines.append(f"| {k} | {values[k]} | {'✓' if ok else '✗'} |")
    lines += ["", f"Annual return {p['annual_return_pct']:.2f}% on a GBP 10,000 base (no compounding), max drawdown "
                  f"{p['max_drawdown_pct']:.1f}%, total £{p['total_gbp']:,.0f} over {p['bars']} daily bars.", "",
              "Reported, not judged:", "",
              "| Version | Sharpe | Annual % | Max DD % |", "|---|---|---|---|"]
    for name in ("trend_only", "carry_only", "financing_off"):
        s = out[name]
        lines.append(f"| {name.replace('_', ' ')} | {s['sharpe']:.2f} | {s['annual_return_pct']:.2f} | "
                     f"{s['max_drawdown_pct']:.1f} |")
    parts = out["parts_gbp"]
    lines += ["", f"P&L parts: price £{parts['price']:,.0f}, spread + slippage £{parts['cost']:,.0f}, "
                  f"financing £{parts['fin']:,.0f}. Peak margin {out['peak_margin_pct']:.0f}% of the base. "
                  f"{out['episodes']} holding episodes, mean {out['mean_holding_bars']:.0f} daily bars.", "",
              "Per pair (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in sorted(out["per_pair_gbp"].items(),
                                                                              key=lambda kv: -kv[1])), "",
              "Per year (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_year_gbp"].items()), ""]
    return "\n".join(lines)


def write_report(text, title, path="docs/backtests.md"):
    """Replace this section of docs/backtests.md (or append it)."""
    doc = open(path).read() if os.path.exists(path) else "# Backtests\n"
    head = f"## {title}"
    if head in doc:
        start = doc.index(head)
        nxt = doc.find("\n## ", start + len(head))
        doc = doc[:start] + text + ("\n" + doc[nxt + 1:] if nxt != -1 else "")
    else:
        doc = doc.rstrip("\n") + "\n\n" + text
    open(path, "w").write(doc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    inputs = build(include_holdout=args.holdout)
    span = HOLDOUT if args.holdout else DEV
    out, pnl = evaluate(inputs, span)
    which = "holdout" if args.holdout else "development"
    title = f"Trend + carry, {which} ({out['span'][0]} → {out['span'][1]})"
    c = holdout_checks(out) if args.holdout else checks(out)
    out["checks"] = c
    out["verdict"] = "PASS" if all(c.values()) else "FAIL"
    json.dump(out, open(os.path.join(OUT_DIR, f"evaluation_{which}.json"), "w"), indent=1)
    pnl.to_csv(os.path.join(OUT_DIR, f"daily_pnl_{which}.csv"))
    text = render(out, c, title)
    write_report(text, title)
    print(text)


if __name__ == "__main__":
    main()
