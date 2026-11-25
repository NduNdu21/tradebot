"""Evaluate turn of the month against its pre-registered bar (docs/strategy-turn-of-month.md §6).

    python -m src.tom.evaluate             # development
    python -m src.tom.evaluate --holdout   # the single hold-out run, only after a development pass
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from src.tom.run import DEV, HOLDOUT, INSTRUMENTS, OUT_DIR, build, tom_entry_days
from src.trend.evaluate import BASE_GBP, SUB_PERIODS, checks, holdout_checks, max_drawdown, write_report

CONTROL_RUNS = 1000
SEED = 0


def sharpe(pnl):
    r = np.asarray(pnl, dtype=float) / BASE_GBP
    sd = r.std(ddof=1, axis=-1)
    return np.where(sd > 0, r.mean(axis=-1) / sd * np.sqrt(252), 0.0)


def summary(pnl):
    years = len(pnl) / 252
    return {"sharpe": float(sharpe(pnl.values)), "annual_return_pct": float(pnl.sum() / years / BASE_GBP * 100),
            "max_drawdown_pct": max_drawdown(pnl.values) * 100, "total_gbp": float(pnl.sum()), "bars": len(pnl)}


def in_span(trades, span):
    """Trades that enter on or after the start and exit on or before the end."""
    return trades[(trades.index >= span[0]) & (trades["exit"] <= span[1])]


def book(trade_rows, days, column="total"):
    """Daily P&L: each trade's P&L on its exit day, 0 elsewhere."""
    s = trade_rows.dropna(subset=["total"]).groupby("exit")[column].sum()
    return s.reindex(days, fill_value=0.0)


def selected(trades, entries, span):
    t = in_span(trades, span)
    return t[t.index.isin(entries)]


def random_control(trades, days, span, runs=CONTROL_RUNS, seed=SEED):
    """Each month: buy at a random trading day's close (same for all instruments), hold 4 days (§6.3)."""
    rng = np.random.default_rng(seed)
    valid = in_span(trades[INSTRUMENTS[0]], span).index
    months = pd.Series(valid, index=valid).groupby([valid.year, valid.month])
    groups = [valid.get_indexer(pd.DatetimeIndex(g.values)) for _, g in months]
    span_days = days[(days >= span[0]) & (days <= span[1])]
    exit_pos = {inst: span_days.get_indexer(pd.DatetimeIndex(trades[inst]["exit"].reindex(valid)))
                for inst in INSTRUMENTS}
    totals = {inst: np.nan_to_num(trades[inst]["total"].reindex(valid).values) for inst in INSTRUMENTS}
    out = np.zeros((runs, len(span_days)))
    for g in groups:
        picks = rng.integers(0, len(g), size=runs)
        rows = g[picks]
        for inst in INSTRUMENTS:
            np.add.at(out, (np.arange(runs), exit_pos[inst][rows]), totals[inst][rows])
    return sharpe(out)


def evaluate(trades, days, span=DEV):
    span_days = days[(days >= span[0]) & (days <= span[1])]
    entries = tom_entry_days(days)
    sel = {i: selected(trades[i], entries, span) for i in INSTRUMENTS}
    pnl = sum(book(sel[i], span_days) for i in INSTRUMENTS)
    out = {"span": [str(span[0].date()), str(span[1].date())], "primary": summary(pnl)}
    out["sub_periods"] = {n: float(pnl[(pnl.index >= a) & (pnl.index <= z)].sum()) for n, a, z in SUB_PERIODS}
    per = {i: float(sel[i]["total"].sum()) for i in INSTRUMENTS}
    out["per_instrument_gbp"] = per
    best = max(per, key=per.get)
    out["best_pair"] = best
    out["sharpe_without_best"] = summary(sum(book(sel[i], span_days) for i in INSTRUMENTS if i != best))["sharpe"]
    ctrl = random_control(trades, days, span)
    out["control"] = {"p95": float(np.percentile(ctrl, 95)), "median": float(np.median(ctrl)),
                      "share_beaten": float((ctrl < out["primary"]["sharpe"]).mean())}

    # Reported, not judged.
    allt = pd.concat(sel.values())
    done = allt.dropna(subset=["total"])
    out["trades"] = int(len(done))
    out["skipped"] = int(allt["total"].isna().sum())
    out["mean_trade_gbp"] = float(done["total"].mean())
    out["trade_t_stat"] = float(done["total"].mean() / done["total"].std(ddof=1) * np.sqrt(len(done)))
    out["win_rate"] = float((done["total"] > 0).mean())
    out["parts_gbp"] = {k: float(done[k].sum()) for k in ("price", "cost", "fin")}
    out["financing_off"] = summary(sum(book(sel[i].assign(total=sel[i]["price"] + sel[i]["cost"]), span_days)
                                       for i in INSTRUMENTS))
    out["per_year_gbp"] = {str(y): float(v) for y, v in pnl.groupby(pnl.index.year).sum().items()}
    out["per_month_gbp"] = {str(m): float(v) for m, v in done.groupby(done.index.month)["total"].sum().items()}
    # For scale: every 4-day trade in the span, i.e. long stocks on every day (overlapping, price only).
    every = pd.concat([in_span(trades[i], span).dropna(subset=["total"]) for i in INSTRUMENTS])
    out["mean_price_per_4day_any_gbp"] = float(every["price"].mean())
    out["mean_price_per_4day_tom_gbp"] = float(done["price"].mean())
    return out, pnl


def render(out, c, title):
    p = out["primary"]
    lines = [f"## {title}", "",
             f"**Verdict: {'PASS' if all(c.values()) else 'FAIL'}** (pre-registered bar, "
             "`docs/strategy-turn-of-month.md` §6).", "", "| Check | Result | Pass |", "|---|---|---|"]
    values = {
        "sharpe >= 0.4": f"Sharpe {p['sharpe']:.2f}",
        "positive in >= 2 of 3 sub-periods": ", ".join(f"{k} £{v:,.0f}" for k, v in out["sub_periods"].items()),
        "beats control 95th percentile": f"{p['sharpe']:.2f} vs random-timing p95 {out['control']['p95']:.2f} "
                                         f"(median {out['control']['median']:.2f})",
        "sharpe > 0.2 without best pair": f"{out['sharpe_without_best']:.2f} without {out['best_pair']}",
        "sharpe > 0": f"Sharpe {p['sharpe']:.2f}", "max drawdown < 15%": f"{p['max_drawdown_pct']:.1f}%",
    }
    for k, ok in c.items():
        lines.append(f"| {k} | {values[k]} | {'✓' if ok else '✗'} |")
    parts, fo = out["parts_gbp"], out["financing_off"]
    lines += ["", f"Annual return {p['annual_return_pct']:.2f}% on a GBP 10,000 base (no compounding), max drawdown "
                  f"{p['max_drawdown_pct']:.1f}%, total £{p['total_gbp']:,.0f}.", "",
              "Reported, not judged:", "",
              f"- {out['trades']} trades ({out['skipped']} skipped), mean £{out['mean_trade_gbp']:.2f}, "
              f"t-statistic {out['trade_t_stat']:.2f}, win rate {out['win_rate'] * 100:.0f}%.",
              f"- P&L parts: price £{parts['price']:,.0f}, spread + slippage £{parts['cost']:,.0f}, financing "
              f"£{parts['fin']:,.0f}. Financing off: Sharpe {fo['sharpe']:.2f}.",
              f"- Mean price move per trade: turn of month £{out['mean_price_per_4day_tom_gbp']:.2f} vs any 4-day "
              f"hold £{out['mean_price_per_4day_any_gbp']:.2f}.",
              "- Per instrument (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_instrument_gbp"].items()),
              "- Per year (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_year_gbp"].items()),
              "- Per entry month (GBP): " + ", ".join(f"{k} {v:,.0f}" for k, v in out["per_month_gbp"].items()), ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    trades, slips, days = build(include_holdout=args.holdout)
    span = HOLDOUT if args.holdout else DEV
    out, pnl = evaluate(trades, days, span)
    which = "holdout" if args.holdout else "development"
    title = f"Turn of the month, {which} ({out['span'][0]} → {out['span'][1]})"
    c = holdout_checks(out) if args.holdout else checks(out)
    out["checks"], out["verdict"], out["slippage"] = c, "PASS" if all(c.values()) else "FAIL", slips
    json.dump(out, open(os.path.join(OUT_DIR, f"evaluation_{which}.json"), "w"), indent=1, default=str)
    pnl.to_csv(os.path.join(OUT_DIR, f"daily_pnl_{which}.csv"))
    text = render(out, c, title)
    write_report(text, title)
    print(text)


if __name__ == "__main__":
    main()
