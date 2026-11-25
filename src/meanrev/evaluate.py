"""Evaluate mean reversion against its pre-registered bar (docs/strategy-mean-reversion.md §5).

    python -m src.meanrev.evaluate             # development
    python -m src.meanrev.evaluate --holdout   # the single hold-out run, only after a development pass
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from src.meanrev.run import DEV, HOLDOUT, MAX_HOLD, build, signals, walk
from src.tom.evaluate import sharpe, summary
from src.tom.run import INSTRUMENTS
from src.trend.evaluate import SUB_PERIODS, checks, holdout_checks, write_report

OUT_DIR = os.path.join("data", "meanrev")
CONTROL_RUNS, SEED = 1000, 0


def trades_for(m, span, use_filter=True):
    """DataFrame of one instrument's trades with entry inside the span and exit inside it."""
    entry, exit_, _ = signals(m.close, use_filter)
    pairs = [(e, x) for e, x in walk(entry, exit_, m.can_trade)
             if m.days[e] >= span[0] and m.days[x] <= span[1]]
    if not pairs:
        return pd.DataFrame(columns=["entry", "exit", "e", "x", "hold", "price", "cost", "fin", "total"])
    e, x = np.array(pairs).T
    price, cost, fin = m.trade_pnl(e, x)
    return pd.DataFrame({"entry": m.days[e], "exit": m.days[x], "e": e, "x": x, "hold": x - e,
                         "price": price, "cost": cost, "fin": fin, "total": price + cost + fin})


def book(t, span_days, column="total"):
    return t.groupby("exit")[column].sum().reindex(span_days, fill_value=0.0)


def random_control(insts, real, span_days, span, runs=CONTROL_RUNS, seed=SEED):
    """Each real trade -> same instrument, year and hold, random entry on an uptrend-filter day (§5.3)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((runs, len(span_days)))
    for inst, t in real.items():
        m = insts[inst]
        _, _, up = signals(m.close)
        n = len(m.days)
        for (year, hold), grp in t.groupby([t["entry"].dt.year, "hold"]):
            idx = np.arange(n - hold)
            ok = (up[idx] & m.can_trade[idx] & m.can_trade[idx + hold] & (m.days[idx].year == year)
                  & (m.days[idx] >= span[0]) & (m.days[idx + hold] <= span[1]))
            cand = idx[ok]
            if len(cand) == 0:
                continue
            e = cand[rng.integers(0, len(cand), size=(runs, len(grp)))]
            x = e + hold
            p, c, f = m.trade_pnl(e, x)
            col = span_days.get_indexer(m.days[x.ravel()]).reshape(x.shape)
            np.add.at(out, (np.repeat(np.arange(runs), len(grp)), col.ravel()), (p + c + f).ravel())
    return sharpe(out)


def evaluate(insts, days, span=DEV):
    span_days = days[(days >= span[0]) & (days <= span[1])]
    real = {i: trades_for(insts[i], span) for i in INSTRUMENTS}
    pnl = sum(book(real[i], span_days) for i in INSTRUMENTS)
    out = {"span": [str(span[0].date()), str(span[1].date())], "primary": summary(pnl)}
    out["sub_periods"] = {n: float(pnl[(pnl.index >= a) & (pnl.index <= z)].sum()) for n, a, z in SUB_PERIODS}
    per = {i: float(t["total"].sum()) for i, t in real.items()}
    out["per_instrument_gbp"] = per
    best = max(per, key=per.get)
    out["best_pair"] = best
    out["sharpe_without_best"] = summary(sum(book(real[i], span_days) for i in INSTRUMENTS if i != best))["sharpe"]
    ctrl = random_control(insts, real, span_days, span)
    out["control"] = {"p95": float(np.percentile(ctrl, 95)), "median": float(np.median(ctrl)),
                      "share_beaten": float((ctrl < out["primary"]["sharpe"]).mean())}

    allt = pd.concat(real.values())
    out["trades"] = int(len(allt))
    out["mean_trade_gbp"] = float(allt["total"].mean())
    out["trade_t_stat"] = float(allt["total"].mean() / allt["total"].std(ddof=1) * np.sqrt(len(allt)))
    out["win_rate"] = float((allt["total"] > 0).mean())
    out["mean_hold_days"] = float(allt["hold"].mean())
    out["time_stop_exits"] = int((allt["hold"] >= MAX_HOLD).sum())
    out["parts_gbp"] = {k: float(allt[k].sum()) for k in ("price", "cost", "fin")}
    out["financing_off"] = summary(sum(book(real[i].assign(total=real[i]["price"] + real[i]["cost"]), span_days)
                                       for i in INSTRUMENTS))
    nof = {i: trades_for(insts[i], span, use_filter=False) for i in INSTRUMENTS}
    out["no_filter"] = summary(sum(book(nof[i], span_days) for i in INSTRUMENTS))
    out["no_filter_trades"] = int(sum(len(t) for t in nof.values()))
    out["per_year_gbp"] = {str(y): float(v) for y, v in pnl.groupby(pnl.index.year).sum().items()}
    return out, pnl, allt


def render(out, c, title):
    p = out["primary"]
    lines = [f"## {title}", "",
             f"**Verdict: {'PASS' if all(c.values()) else 'FAIL'}** (pre-registered bar, "
             "`docs/strategy-mean-reversion.md` §5).", "", "| Check | Result | Pass |", "|---|---|---|"]
    values = {
        "sharpe >= 0.4": f"Sharpe {p['sharpe']:.2f}",
        "positive in >= 2 of 3 sub-periods": ", ".join(f"{k} £{v:,.0f}" for k, v in out["sub_periods"].items()),
        "beats control 95th percentile": f"{p['sharpe']:.2f} vs random-entry p95 {out['control']['p95']:.2f} "
                                         f"(median {out['control']['median']:.2f})",
        "sharpe > 0.2 without best pair": f"{out['sharpe_without_best']:.2f} without {out['best_pair']}",
        "sharpe > 0": f"Sharpe {p['sharpe']:.2f}", "max drawdown < 15%": f"{p['max_drawdown_pct']:.1f}%",
    }
    for k, ok in c.items():
        lines.append(f"| {k} | {values[k]} | {'✓' if ok else '✗'} |")
    parts = out["parts_gbp"]
    lines += ["", f"Annual return {p['annual_return_pct']:.2f}% on a GBP 10,000 base (no compounding), max drawdown "
                  f"{p['max_drawdown_pct']:.1f}%, total £{p['total_gbp']:,.0f}.", "", "Reported, not judged:", "",
              f"- {out['trades']} trades, mean £{out['mean_trade_gbp']:.2f}, t-statistic {out['trade_t_stat']:.2f}, "
              f"win rate {out['win_rate'] * 100:.0f}%, mean hold {out['mean_hold_days']:.1f} trading days, "
              f"{out['time_stop_exits']} time-stop exits.",
              f"- P&L parts: price £{parts['price']:,.0f}, spread + slippage £{parts['cost']:,.0f}, financing "
              f"£{parts['fin']:,.0f}. Financing off: Sharpe {out['financing_off']['sharpe']:.2f}.",
              f"- Without the uptrend filter: Sharpe {out['no_filter']['sharpe']:.2f} ({out['no_filter_trades']} trades).",
              "- Per instrument (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_instrument_gbp"].items()),
              "- Per year (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_year_gbp"].items()), ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    insts, days = build(include_holdout=args.holdout)
    span = HOLDOUT if args.holdout else DEV
    out, pnl, allt = evaluate(insts, days, span)
    which = "holdout" if args.holdout else "development"
    title = f"Mean reversion, {which} ({out['span'][0]} → {out['span'][1]})"
    c = holdout_checks(out) if args.holdout else checks(out)
    out["checks"], out["verdict"] = c, "PASS" if all(c.values()) else "FAIL"
    json.dump(out, open(os.path.join(OUT_DIR, f"evaluation_{which}.json"), "w"), indent=1, default=str)
    pnl.to_csv(os.path.join(OUT_DIR, f"daily_pnl_{which}.csv"))
    allt.to_csv(os.path.join(OUT_DIR, f"trades_{which}.csv"), index=False)
    text = render(out, c, title)
    write_report(text, title)
    print(text)


if __name__ == "__main__":
    main()
