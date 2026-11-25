"""Pre-registered development evaluation of the ICT strategy (docs/strategy-ict.md §10).

Order of work, fixed in advance:
  1. truncation check (no lookahead) for both instruments, primary rule first; abort if it fails
  2. primary (shift by close, M5): pass test with mean R - 1 SE > 0
  3. rescue (shift by wick, M5): only if the primary failed, with mean R - 2 SE > 0
Whatever passes would go to the hold-out. This module never runs the hold-out
(load_candles is never called with include_holdout=True).

Run `python -m src.ict.evaluate`; it writes data/ict/evaluation.json and prints the report section.
"""
import json
import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from src.data import load_candles
from src.ict.run import INSTRUMENTS, OUT_DIR
from src.ict.stats import max_drawdown_pct, random_control_dropped, summary, truncation_check

MIN_TRADES = 200
MIN_MEAN_R = 0.1
RUNS = 1000
SEED = 2026


def passes(stats: dict, control_p95: float, se_multiple: int) -> Tuple[bool, List[str]]:
    """The development pass bar. Returns (all passed, names of the failed criteria)."""
    failed = []
    if not stats["n"] >= MIN_TRADES:
        failed.append("n >= 200")
    if not stats["mean_r"] >= MIN_MEAN_R:
        failed.append("mean R >= 0.1")
    if not stats["mean_r"] - se_multiple * stats["se"] > 0:
        failed.append(f"mean R - {se_multiple} SE > 0")
    if not all(v["mean_r"] > 0 for v in stats["by_instrument"].values()):
        failed.append("mean R > 0 on each instrument")
    if not stats["mean_r"] > control_p95:
        failed.append("mean R > control 95th percentile")
    return not failed, failed


def verdict(primary_ok: bool, rescue_ok: Optional[bool]) -> str:
    """The rescue is only looked at when the primary failed (rescue_ok is None otherwise)."""
    if primary_ok:
        return "primary to hold-out"
    if rescue_ok:
        return "rescue to hold-out"
    return "fail"


# ------------------------------------------------------------------------------------ the run

def load_trades(rule: str, tf: str = "5min") -> Tuple[pd.DataFrame, dict]:
    """Both instruments' trade tables stacked, and each instrument's skip counts."""
    tables, skips = [], {}
    for inst in INSTRUMENTS:
        name = f"{inst}_{rule}_{tf}"
        tables.append(pd.read_csv(os.path.join(OUT_DIR, f"trades_{name}.csv"),
                                  parse_dates=["fill_time", "placed_time", "exit_time"]))
        with open(os.path.join(OUT_DIR, f"skips_{name}.json")) as f:
            skips[inst] = json.load(f)
    return pd.concat(tables, ignore_index=True), skips


def evaluate_variant(rule: str, se_multiple: int, m1) -> dict:
    """Summary, control and pass test for one shift rule on M5, both instruments pooled."""
    trades, skips = load_trades(rule)
    stats = summary(trades)
    runs, dropped = random_control_dropped(trades, m1, runs=RUNS, seed=SEED)
    p95 = float(np.percentile(runs, 95))
    ok, failed = passes(stats, p95, se_multiple)
    return {"rule": rule, "se_multiple": se_multiple, "stats": stats, "control_p95": p95,
            "control_mean": float(np.mean(runs)), "control_dropped": dropped,
            "max_drawdown_pct": max_drawdown_pct(trades), "passed": ok, "failed": failed, "skips": skips}

# ----------------------------------------------------------------------------------- report

HEADING = "## ICT, development (2016-11-14 → 2024-11-13)"
DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "backtests.md")
NOT_REASONS = ("stop_first", "rearmed")      # counters stored beside the skip reasons


def _f(x, d=3):
    return f"{x:+.{d}f}"


def _group_table(title, groups):
    rows = [f"| {title} | n | mean R | SE |", "|---|---|---|---|"]
    rows += [f"| {k} | {g['n']} | {_f(g['mean_r'])} | {g['se']:.3f} |" for k, g in groups.items()]
    return "\n".join(rows)


def _criteria(v):
    s, m = v["stats"], v["se_multiple"]
    inst = ", ".join(f"{k} {_f(g['mean_r'])}" for k, g in s["by_instrument"].items())
    rows = [("n", str(s["n"]), ">= 200", s["n"] >= MIN_TRADES),
            ("mean R", _f(s["mean_r"]), ">= +0.1", s["mean_r"] >= MIN_MEAN_R),
            (f"mean R - {m} SE", _f(s["mean_r"] - m * s["se"]), "> 0", s["mean_r"] - m * s["se"] > 0),
            ("mean R on each instrument", inst, "> 0 on both",
             all(g["mean_r"] > 0 for g in s["by_instrument"].values())),
            ("mean R vs control 95th percentile", f"{_f(s['mean_r'])} vs {_f(v['control_p95'])}",
             "mean R > p95", s["mean_r"] > v["control_p95"])]
    out = ["| Criterion | Value | Threshold | Result |", "|---|---|---|---|"]
    return "\n".join(out + [f"| {a} | {b} | {c} | {'pass' if d else 'FAIL'} |" for a, b, c, d in rows])


def _detail(v):
    s = v["stats"]
    skips = v["skips"]
    reasons = sorted({r for c in skips.values() for r in c if r not in NOT_REASONS})
    sk = ["| Skip reason | " + " | ".join(skips) + " |", "|---|" + "---|" * len(skips)]
    sk += [f"| {r} | " + " | ".join(str(skips[i].get(r, 0)) for i in skips) + " |" for r in reasons]
    return "\n\n".join([
        _group_table("Instrument", s["by_instrument"]), _group_table("Side", s["by_side"]),
        _group_table("Year", s["by_year"]),
        f"Max drawdown at 0.5% risk per trade: {v['max_drawdown_pct']:.1f} percentage points. "
        f"Stop-first trades: {s['stop_first']}. Trades from re-armed cycles: {s['rearmed']}. "
        f"Random control: mean of runs {_f(v['control_mean'])}, 95th percentile {_f(v['control_p95'])}, "
        f"control trades dropped over {RUNS} runs: {v['control_dropped']}.",
        "\n".join(sk)])


def render(out: dict) -> str:
    p, r = out["primary"], out["rescue"]
    lines = [HEADING, "", f"**Verdict: {out['verdict']}.** Truncation check passed for both instruments "
             f"and both shift rules (no lookahead). The hold-out has not been run. The random-entry control "
             f"loses heavily on average (market entries pay the spread against tight stops), so beating "
             f"its 95th percentile is the only criterion the strategy meets besides trade count.", "",
             f"### Primary: shift by close, M5, both instruments pooled "
             f"({'passed' if p['passed'] else 'failed: ' + '; '.join(p['failed'])})", "",
             _criteria(p), "", _detail(p), "",
             "SPX500_USD lost 206 development days to missing M5 data (148 of them in 2017), "
             "against 9 for NAS100_USD.", ""]
    if r:
        lines += [f"### Rescue: shift by wick, M5 (stricter bar, mean R - 2 SE > 0; evaluated because the "
                  f"primary failed; {'passed' if r['passed'] else 'failed: ' + '; '.join(r['failed'])})", "",
                  _criteria(r), "", _detail(r), ""]
    else:
        lines += ["The rescue was not evaluated because the primary passed.", ""]
    lines += ["### Reported, not judged", "",
              "| Variant | n | mean R | SE |", "|---|---|---|---|"]
    for key, label in (("wick_5min", "Shift by wick, M5"), ("close_1min", "Shift by close, 1-minute chart"),
                       ("close_2min", "Shift by close, 2-minute chart")):
        x = out["sensitivity"][key]
        lines.append(f"| {label} | {x['n']} | {_f(x['mean_r'])} | {x['se']:.3f} |")
    return "\n".join(lines) + "\n"


def write_report(out: dict, path: str = DOCS):
    """Put the section in docs/backtests.md, replacing an earlier run's section if there is one."""
    text = open(path).read()
    if HEADING in text:
        head, rest = text.split(HEADING, 1)
        nxt = rest.find("\n## ")
        text = head.rstrip("\n") + "\n\n" + (rest[nxt + 1:] if nxt >= 0 else "")
    with open(path, "w") as f:
        f.write(text.rstrip("\n") + "\n\n" + render(out))


def main():
    m1 = {i: load_candles(i, "M1") for i in INSTRUMENTS}   # development data only
    checks = {}
    for rule in ("close", "wick"):                         # primary rule first
        for inst in INSTRUMENTS:
            checks[f"{inst}_{rule}"] = truncation_check(inst, rule, m1=m1[inst])
            print(f"truncation_check {inst} {rule}: {checks[f'{inst}_{rule}']}", flush=True)
            if not checks[f"{inst}_{rule}"]:
                raise SystemExit(f"ABORT: truncation check failed for {inst} ({rule}); fix lookahead first")
    primary = evaluate_variant("close", 1, m1)
    print("primary", primary["passed"], primary["failed"], flush=True)
    rescue = evaluate_variant("wick", 2, m1) if not primary["passed"] else None
    out = {"truncation_checks": checks, "primary": primary, "rescue": rescue,
           "verdict": verdict(primary["passed"], rescue["passed"] if rescue else None), "sensitivity": {}}
    # Reported, not judged: the other shift rule (no control) and the close rule on M1 and M2.
    for key, (rule, tf) in {"wick_5min": ("wick", "5min"), "close_1min": ("close", "1min"),
                            "close_2min": ("close", "2min")}.items():
        t, _ = load_trades(rule, tf)
        s = summary(t)
        out["sensitivity"][key] = {k: s[k] for k in ("n", "mean_r", "se")}
    with open(os.path.join(OUT_DIR, "evaluation.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    write_report(out)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
