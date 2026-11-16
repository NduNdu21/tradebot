"""Step 5 experiments: walk-forward, sensitivities and controls (docs/strategy.md §10).

Development data only (2016-11-14 -> 2024-11-13); the hold-out stays locked in src.data.

    python -m src.experiments                # run what isn't cached, rewrite the report
    python -m src.experiments --fresh        # rerun everything

Engine runs are cached in data/experiments/ (gitignored). The report goes into
docs/backtests.md between the "experiments" markers.

Primary configuration (owner ruling 2026-11-16): mode a, TP2, 96-bar time limit,
running-extreme anchoring, financing on, Friday close on, with K x min_bars chosen
in each training window by the selection rule (select_cell). The pass bar, the
random-entry control and the zone-shift control apply to the primary only. Every
other variant is a sensitivity with its own walk-forward on the same nine cells;
a sensitivity can't replace a failed primary.

Unit of observation: a setup with at least one filled leg. Setup R = sum of its
legs' P&L / £50 (one leg's planned risk: 0.5% of the fixed £10,000), so leg 1
losing £50 and leg 2 winning £80 is +0.6R, and a two-leg loss is -2R.
"""
import argparse
import os
import pickle
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.backtest import PULL_HOUR, Config, Engine, is_pull_day, load_data, trade_table
from src.data import DEV_START, HOLDOUT_START
from src.financing import NEW_YORK
from src.setups import FibStrategy, Params, build_h1

CACHE_DIR = os.path.join("data", "experiments")
REPORT_PATH = os.path.join("docs", "backtests.md")
MARKERS = ("<!-- experiments:start -->", "<!-- experiments:end -->")

GRID_K = (2, 3, 4)
GRID_MIN_BARS = (3, 5, 8)
CELLS = [(k, m) for k in GRID_K for m in GRID_MIN_BARS]
MIN_SETUPS = 30                                    # a cell needs this many setups to be chosen
FIRST_TEST = pd.Timestamp("2019-11-14", tz="UTC")  # walk-forward: 3-year train, 1-year test, 5 test years
TRAIN_YEARS, TEST_YEARS = 3, 5
UNIT_GBP = Config().starting_balance * Config().risk_per_leg   # £50 = 1R
RANDOM_RUNS = 1000
PASS_EXPECTANCY = 0.1
PASS_SETUPS = 200


@dataclass(frozen=True)
class Variant:
    name: str
    mode: str = "a"
    target: str = "tp2"
    time_limit: int = 96
    anchoring: str = "running"
    zone_shift: float = 0.0
    friday_close: bool = True
    financing_schedule: str = "help"
    score_column: str = "r_on"   # financing column the selection rule scores on

    def params(self, k, min_bars):
        return Params(k=k, min_bars=min_bars, mode=self.mode, target=self.target, time_limit=self.time_limit,
                      anchoring=self.anchoring, zone_shift=self.zone_shift)

    def config(self):
        return Config(friday_close=self.friday_close, financing_schedule=self.financing_schedule)

    @property
    def run_key(self):
        """Everything that changes an engine run (variants differing only in scoring share runs)."""
        return (f"{self.mode}_{self.target}_t{self.time_limit}_{self.anchoring}_z{self.zone_shift:+.2f}"
                f"_fri{int(self.friday_close)}_{self.financing_schedule}")


PRIMARY = Variant("primary")
SENSITIVITIES = [
    Variant("mode b (confirmation candle)", mode="b"),
    Variant("mode c (leg 1 only)", mode="c"),
    Variant("mode d (leg 2 only)", mode="d"),
    Variant("target TP1", target="tp1"),
    Variant("target TP3", target="tp3"),
    Variant("target 1R", target="1r"),
    Variant("target 2R", target="2r"),
    Variant("target 3R", target="3r"),
    Variant("time limit 48", time_limit=48),
    Variant("time limit 200", time_limit=200),
    Variant("Friday close off (hold weekends)", friday_close=False),
    Variant("rollover schedule from API", financing_schedule="api"),
    Variant("fully confirmed swings", anchoring="confirmed"),
    Variant("selection on financing off", score_column="r_off"),
    Variant("selection on financing capped", score_column="r_capped"),
]
# Zone-shift control: equal-width zones in 5-point retracement steps; the primary is 61.8-76.4.
ZONES = {shift: Variant(f"zone {61.8 + 100 * shift:.1f}-{76.4 + 100 * shift:.1f}", zone_shift=shift)
         for shift in (-0.10, -0.05, 0.05, 0.10)}
ZONES[0.0] = PRIMARY
ALL_VARIANTS = [PRIMARY] + SENSITIVITIES + [ZONES[s] for s in (-0.10, -0.05, 0.05, 0.10)]


# ---- turning engine output into setups ----

SETUP_COLUMNS = ["pair", "setup_id", "side", "first_fill", "last_exit", "legs", "r_on", "r_off", "r_capped",
                 "fallbacks", "friday_legs"]


def cell_result(engine):
    """Closed legs and dated events from one engine run.

    Setups with a leg still open when the data ends are dropped: their result isn't known.
    """
    legs = trade_table(engine.closed)
    still_open = {(t.pair, t.setup_id) for p in engine.pairs for t in engine.open[p]}
    if still_open and len(legs):
        legs = legs[[(p, s) not in still_open for p, s in zip(legs.pair, legs.setup_id)]]
    events = [(t, order.pair, reason) for t, order, reason in engine.skipped]
    events += [(reopen, pair, "weekend_hold") for pair, _, _, _, reopen in engine.weekend_holds]
    events += [(t, pair, "target_before_fill") for t, pair in engine.target_before_fill]
    return {"legs": legs, "events": pd.DataFrame(events, columns=["time", "pair", "kind"])}


def setup_table(legs, unit=UNIT_GBP):
    """One row per setup with at least one filled leg. R columns are summed leg P&L / unit."""
    if legs.empty:
        return pd.DataFrame(columns=SETUP_COLUMNS)
    legs = legs.assign(key=legs.setup_id.map(repr), friday=legs.exit_reason.eq("friday"))
    out = legs.groupby(["pair", "key"], sort=False).agg(
        setup_id=("setup_id", "first"), side=("side", "first"), first_fill=("entry_time", "min"),
        last_exit=("exit_time", "max"), legs=("leg", "size"), pnl_on=("pnl_on", "sum"),
        pnl_off=("pnl_off", "sum"), pnl_capped=("pnl_capped", "sum"), fallbacks=("fallback", "sum"),
        friday_legs=("friday", "sum")).reset_index()
    for mode in ("on", "off", "capped"):
        out[f"r_{mode}"] = out[f"pnl_{mode}"] / unit
    return out[SETUP_COLUMNS].sort_values("first_fill").reset_index(drop=True)


def in_window(df, start, end, column="first_fill"):
    """Rows whose `column` falls in [start, end). A setup belongs to the window of its first fill."""
    return df[(df[column] >= start) & (df[column] < end)]


# ---- the selection rule and walk-forward ----

def wf_windows(first_test=FIRST_TEST, train_years=TRAIN_YEARS, test_years=TEST_YEARS):
    """(train start, test start, test end) for each rolling year: 2016-11-14 .. 2024-11-14."""
    out = []
    for i in range(test_years):
        test_start = first_test + pd.DateOffset(years=i)
        out.append((test_start - pd.DateOffset(years=train_years), test_start, test_start + pd.DateOffset(years=1)))
    return out


def summarize(r):
    """n, mean planned R, its standard error, and the selection score (mean minus one SE)."""
    r = pd.Series(r, dtype=float)
    n = len(r)
    mean = r.mean() if n else float("nan")
    se = r.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "mean": mean, "se": se, "score": mean - se}


def neighbours(cell):
    """Orthogonal neighbours of a cell in the K x min_bars grid (edge cells have fewer)."""
    i, j = GRID_K.index(cell[0]), GRID_MIN_BARS.index(cell[1])
    steps = [(i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)]
    return [(GRID_K[a], GRID_MIN_BARS[b]) for a, b in steps if 0 <= a < len(GRID_K) and 0 <= b < len(GRID_MIN_BARS)]


def select_cell(train, column="r_on"):
    """The selection rule (§10), applied to one training window.

    train: {(k, min_bars): setups whose first fill is in the window}.
    A cell is eligible if it has at least 30 setups, a positive mean R, and every
    orthogonal neighbour has a positive mean R (a neighbour with no setups isn't
    positive). The highest score (mean R minus one standard error) wins; ties go
    to more setups. Returns (chosen cell or None if nothing qualifies, stats per cell).
    """
    stats = {cell: summarize(df[column]) for cell, df in train.items()}

    def positive(cell):
        return cell in stats and stats[cell]["n"] > 0 and stats[cell]["mean"] > 0

    eligible = [c for c, s in stats.items()
                if s["n"] >= MIN_SETUPS and positive(c) and all(positive(nb) for nb in neighbours(c))]
    chosen = max(eligible, key=lambda c: (stats[c]["score"], stats[c]["n"]), default=None)
    return chosen, stats


def walk_forward(cells, column="r_on"):
    """Pick a cell on each training window, apply it blind to the next year.

    cells: {(k, min_bars): setup table over all of development}.
    Returns (one dict per test year, out-of-sample setups stitched together).
    A year with no eligible cell is sat out: no trades.
    """
    years, oos = [], []
    for train_start, test_start, test_end in wf_windows():
        chosen, train_stats = select_cell({c: in_window(df, train_start, test_start) for c, df in cells.items()},
                                          column)
        test = {c: in_window(df, test_start, test_end) for c, df in cells.items()}
        years.append({"train_start": train_start, "test_start": test_start, "test_end": test_end,
                      "chosen": chosen, "train": train_stats,
                      "test": {c: summarize(df[column]) for c, df in test.items()}})
        if chosen:
            oos.append(test[chosen].assign(cell=[chosen] * len(test[chosen]), test_start=test_start,
                                           test_end=test_end))
    if not oos:   # keep the columns' types so the report works on an empty result
        empty = next(iter(cells.values())).iloc[0:0]
        oos = [empty.assign(cell=[], test_start=pd.Series(dtype="datetime64[ns, UTC]"),
                            test_end=pd.Series(dtype="datetime64[ns, UTC]"))]
    return years, pd.concat(oos, ignore_index=True)


# ---- random-entry control ----

def entry_hours(setups):
    """UTC hour of each real setup's first fill, per pair: the hour-of-day distribution to draw from."""
    return {pair: df.first_fill.dt.hour.to_numpy() for pair, df in setups.groupby("pair")}


def random_plan(oos, legs_by_cell):
    """One random setup per real out-of-sample setup: same pair, side, test year and
    legs, each leg with its real stop and target distances in price."""
    plan = []
    for row in oos.itertuples():
        legs = legs_by_cell[row.cell]
        mine = legs[(legs.pair == row.pair) & (legs.setup_id.map(repr) == repr(row.setup_id))]
        plan.append({"pair": row.pair, "side": row.side, "window": (row.test_start, row.test_end),
                     "legs": [(int(l.leg), abs(l.entry_price - l.stop), abs(l.target - l.entry_price))
                              for l in mine.sort_values("leg").itertuples()]})
    return plan


def entry_bars(h1_index):
    """H1 bars a random entry may open at: not the first bar, and not in the pulled
    window from 16:00 New York on a Friday or holiday eve until the reopen."""
    ny = h1_index.tz_convert(NEW_YORK)
    pulled = is_pull_day(ny.tz_localize(None).normalize()) & (ny.hour >= PULL_HOUR)
    return h1_index[1:][~pulled[1:]]


class RandomEntryStrategy:
    """Random-entry control (§10). Each planned setup enters at market at the open of
    a random H1 bar in its test year, the hour drawn from the real hour-of-day
    distribution. The decision is made at the previous H1 close, like a mode-b entry.

    Stop and target sit at the real distances from the decision bar's mid close.
    If the pair is busy with another random setup when one is due, it is redrawn
    later in the same year; if no bar at the drawn hour is left, it is dropped.
    """

    def __init__(self, h1, plan, hours, rng):
        self.plan, self.hours, self.rng = plan, hours, rng
        self.close = {pair: df.mid_c for pair, df in h1.items()}
        self.bars = {pair: entry_bars(df.index) for pair, df in h1.items()}
        self.previous = {pair: pd.Series(df.index[:-1], index=df.index[1:]) for pair, df in h1.items()}
        self.due = defaultdict(list)     # (pair, decision hour) -> plan numbers
        self.redraws = self.dropped = 0
        for i, item in enumerate(plan):
            self._schedule(i, after=item["window"][0] - pd.Timedelta(1))

    def _schedule(self, i, after):
        item = self.plan[i]
        pair, (start, end) = item["pair"], item["window"]
        bars = self.bars[pair]
        hour = self.rng.choice(self.hours[pair])
        pool = bars[(bars > after) & (bars >= start) & (bars < end) & (bars.hour == hour)]
        if not len(pool):
            self.dropped += 1
            return
        bar = pool[self.rng.integers(len(pool))]
        self.due[(pair, self.previous[pair][bar])].append(i)

    def on_h1_close(self, engine, pair, hour):
        for i in self.due.pop((pair, hour), []):
            if engine.active[pair] is not None:
                self.redraws += 1
                self._schedule(i, after=hour + pd.Timedelta(hours=1))
                continue
            item = self.plan[i]
            sign = 1 if item["side"] == "long" else -1
            mid = self.close[pair][hour]
            for leg, stop_dist, target_dist in item["legs"]:
                engine.place_market(pair, i, leg, item["side"], mid - sign * stop_dist, target=mid + sign * target_dist)


# ---- running (in worker processes) ----

_STATE = {}


def _init_worker():
    _STATE["data"] = load_data()
    _STATE["h1"] = build_h1(_STATE["data"])


def _run_cell(job):
    variant, k, min_bars = job
    engine = Engine(_STATE["data"], FibStrategy(_STATE["h1"], variant.params(k, min_bars)), variant.config())
    return job, cell_result(engine.run())


def _run_random(job):
    seed, plan, hours = job
    if "oos_data" not in _STATE:
        # Only the out-of-sample years plus a short lead-in for prices.
        start = FIRST_TEST - pd.Timedelta(days=14)
        _STATE["oos_data"] = {k: v[v.index >= start] for k, v in _STATE["data"].items()}
        _STATE["oos_h1"] = {k: v[v.index >= start] for k, v in _STATE["h1"].items()}
    strategy = RandomEntryStrategy(_STATE["oos_h1"], plan, hours, np.random.default_rng(seed))
    engine = Engine(_STATE["oos_data"], strategy, PRIMARY.config()).run()
    setups = setup_table(cell_result(engine)["legs"])
    out = {f"mean_{m}": setups[f"r_{m}"].mean() for m in ("on", "off", "capped")}
    out.update(n=len(setups), redraws=strategy.redraws, dropped=strategy.dropped)
    return out


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name + ".pkl")


def run_grid(variants, workers, fresh=False):
    """{run_key: {cell: cell_result}} for every variant, running only what isn't cached."""
    keys = {v.run_key: v for v in variants}
    jobs = [(v, k, m) for v in keys.values() for k, m in CELLS
            if fresh or not os.path.exists(_cache_path(f"{v.run_key}_k{k}_m{m}"))]
    if jobs:
        print(f"{len(jobs)} engine runs on {workers} workers")
        with ProcessPoolExecutor(workers, initializer=_init_worker) as pool:
            for done, ((v, k, m), result) in enumerate(pool.map(_run_cell, jobs), 1):
                with open(_cache_path(f"{v.run_key}_k{k}_m{m}"), "wb") as f:
                    pickle.dump(result, f)
                print(f"  {done}/{len(jobs)} {v.run_key} K={k} min_bars={m}")
    out = {}
    for key in keys:
        out[key] = {}
        for k, m in CELLS:
            with open(_cache_path(f"{key}_k{k}_m{m}"), "rb") as f:
                out[key][(k, m)] = pickle.load(f)
    return out


def run_random(plan, hours, workers, runs=RANDOM_RUNS, fresh=False):
    path = _cache_path(f"random_{runs}")
    if not fresh and os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    print(f"{runs} random-entry runs on {workers} workers")
    jobs = [(seed, plan, hours) for seed in range(runs)]
    with ProcessPoolExecutor(workers, initializer=_init_worker) as pool:
        results = pd.DataFrame(list(pool.map(_run_random, jobs, chunksize=4)))
    with open(path, "wb") as f:
        pickle.dump(results, f)
    return results


# ---- analysis ----

def analyse(variant, grid):
    """Development setups per cell, event counts per cell, and the walk-forward."""
    runs = grid[variant.run_key]
    setups = {c: in_window(setup_table(r["legs"]), DEV_START, HOLDOUT_START) for c, r in runs.items()}
    events = {c: in_window(r["events"], DEV_START, HOLDOUT_START, "time") for c, r in runs.items()}
    legs = {c: in_window(r["legs"], DEV_START, HOLDOUT_START, "entry_time") for c, r in runs.items()}
    years, oos = walk_forward(setups, variant.score_column)
    return {"variant": variant, "setups": setups, "events": events, "legs": legs, "years": years, "oos": oos}


def max_drawdown_pct(setups, column="r_on"):
    """Largest peak-to-trough fall in closed P&L, ordered by exit, as % of the £10,000 start."""
    if setups.empty:
        return 0.0
    gbp = setups.sort_values("last_exit")[column].to_numpy() * UNIT_GBP
    equity = np.concatenate([[0.0], np.cumsum(gbp)])
    return float(np.max(np.maximum.accumulate(equity) - equity)) / Config().starting_balance * 100


def weekday_table(setups):
    """Setups and mean R by the New York weekday of the first fill (Sunday = the reopen evening)."""
    if setups.empty:
        return []
    days = setups.first_fill.dt.tz_convert(NEW_YORK).dt.day_name()
    order = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    return [(d, summarize(setups.r_on[days == d])) for d in order if (days == d).any()]


# ---- report ----

def _r(x):
    return "—" if pd.isna(x) else f"{x:+.3f}"


def _cell(c):
    return "none" if c is None else f"K={c[0]}, mb={c[1]}"


def _counts(setups, events):
    kinds = events.kind
    return {"long": int((setups.side == "long").sum()), "short": int((setups.side == "short").sum()),
            "fallback": int(setups.fallbacks.sum()), "friday": int(setups.friday_legs.sum()),
            "tbf": int((kinds == "target_before_fill").sum()),
            "notional": int(kinds.str.contains("notional_cap").sum()),
            "margin": int(kinds.str.contains("margin_cap").sum()),
            "thr_stop": int((kinds == "through_stop").sum()), "thr_target": int((kinds == "through_target").sum()),
            "weekend": int((kinds == "weekend_hold").sum())}


def _oos_summary_row(a):
    oos = a["oos"]
    by_pair = {p: oos.r_on[oos.pair == p].mean() for p in ("EUR_USD", "GBP_JPY")}
    on = summarize(oos.r_on)
    return (f"| {a['variant'].name} | {len(oos)} | {_r(on['mean'])} | {_r(on['se'])} | {_r(oos.r_off.mean())} "
            f"| {_r(oos.r_capped.mean())} | {_r(by_pair['EUR_USD'])} | {_r(by_pair['GBP_JPY'])} "
            f"| {max_drawdown_pct(oos):.1f}% | {', '.join(_cell(y['chosen']) for y in a['years'])} "
            f"| {_dev_range(a)} |")


def _dev_range(a):
    """Lowest and highest whole-development mean R (on) across the nine cells."""
    means = [a["setups"][c].r_on.mean() for c in CELLS]
    return f"{_r(min(means))} to {_r(max(means))}"


def _cell_table(a):
    lines = ["| Cell | Setups | Long | Short | Mean R on | off | capped | Fallback | Target-before-fill "
             "| Notional cap | Margin cap | Skip thr. stop | Skip thr. target | Weekend holds | Friday exits |",
             "|" + "---|" * 15]
    for c in CELLS:
        s, n = a["setups"][c], _counts(a["setups"][c], a["events"][c])
        lines.append(f"| {_cell(c)} | {len(s)} | {n['long']} | {n['short']} | {_r(s.r_on.mean())} "
                     f"| {_r(s.r_off.mean())} | {_r(s.r_capped.mean())} | {n['fallback']} | {n['tbf']} "
                     f"| {n['notional']} | {n['margin']} | {n['thr_stop']} | {n['thr_target']} | {n['weekend']} "
                     f"| {n['friday']} |")
    return lines


def _wf_table(a):
    col = a["variant"].score_column
    lines = [f"Selection scored on `{col}`.", "",
             "| Test year | Chosen | Train setups | Train mean R | Train score | OOS setups | OOS mean R "
             "| OOS mean R of the chosen cell's neighbours | Best train mean R (any cell) |", "|" + "---|" * 9]
    for y in a["years"]:
        c = y["chosen"]
        tr = y["train"].get(c, {}) if c else {}
        te = y["test"].get(c, {}) if c else {}
        nbs = ", ".join(f"{_cell(nb)}: {_r(y['test'][nb]['mean'])}" for nb in neighbours(c)) if c else "—"
        lines.append(f"| {y['test_start']:%Y-%m-%d} → {y['test_end']:%Y-%m-%d} | {_cell(c)} | {tr.get('n', '—')} "
                     f"| {_r(tr.get('mean'))} | {_r(tr.get('score'))} | {te.get('n', 0)} | {_r(te.get('mean'))} "
                     f"| {nbs} | {_best_train(y)} |")
    return lines


def _best_train(y):
    cell = max(y["train"], key=lambda c: y["train"][c]["mean"])
    return f"{_cell(cell)}: {_r(y['train'][cell]['mean'])}"


def build_report(analyses, random_results):
    p = analyses[PRIMARY.name]
    oos = p["oos"]
    on = summarize(oos.r_on)
    zone = {s: analyses[ZONES[s].name]["oos"].r_on.mean() for s in sorted(ZONES)}
    p95 = random_results.mean_on.quantile(0.95) if random_results is not None else float("nan")
    by_pair = {pair: oos.r_on[oos.pair == pair].mean() for pair in ("EUR_USD", "GBP_JPY")}
    stable = [(y["chosen"], [y["test"][nb]["mean"] > 0 for nb in neighbours(y["chosen"])])
              for y in p["years"] if y["chosen"]]

    def verdict(ok):
        return "PASS" if ok else "FAIL"

    L = [MARKERS[0], "",
         "## Step 5: development walk-forward (generated by `python -m src.experiments`)", "",
         f"Generated {pd.Timestamp.now(tz=NEW_YORK):%Y-%m-%d %H:%M} New York. Development data only "
         f"({DEV_START:%Y-%m-%d} → {HOLDOUT_START - pd.Timedelta(days=1):%Y-%m-%d}); the hold-out is untouched.",
         "Unit: one setup with at least one filled leg; setup R = sum of leg P&L / £50 (0.5% of the fixed £10,000).",
         "Out-of-sample (OOS) = the five walk-forward test years 2019-11-14 → 2024-11-13, each traded with the "
         "cell chosen on the three years before it.", "",
         "### Primary: pass criteria (development)", "",
         f"Primary: mode a, TP2, 96-bar time limit, running-extreme anchoring, financing on, Friday close on.", "",
         "| Criterion | Result | Verdict |", "|---|---|---|",
         f"| OOS setups ≥ {PASS_SETUPS} | {len(oos)} | {verdict(len(oos) >= PASS_SETUPS)} |",
         f"| Expectancy ≥ +{PASS_EXPECTANCY}R per setup (financing on) | {_r(on['mean'])} (SE {on['se']:.3f}) "
         f"| {verdict(on['mean'] >= PASS_EXPECTANCY)} |",
         f"| Beats the random-entry 95th percentile | {_r(on['mean'])} vs {_r(p95)} "
         f"| {verdict(on['mean'] > p95) if random_results is not None else 'FAIL (no OOS setups; not run)'} |",
         f"| 61.8–76.4 beats both zone neighbours | {_r(zone[0.0])} vs {_r(zone[-0.05])} (56.8–71.4) and "
         f"{_r(zone[0.05])} (66.8–81.4) | {verdict(zone[0.0] > zone[-0.05] and zone[0.0] > zone[0.05])} |",
         f"| Positive on both pairs | EUR_USD {_r(by_pair['EUR_USD'])}, GBP_JPY {_r(by_pair['GBP_JPY'])} "
         f"| {verdict(all(v > 0 for v in by_pair.values()))} |",
         "| Stable across neighbouring cells | " + "; ".join(
             f"{_cell(c)}: {sum(ok)}/{len(ok)} neighbours positive OOS" for c, ok in stable) +
         " | judgement: see the walk-forward table |", "",
         f"Financing off: {_r(oos.r_off.mean())}; credits capped at zero: {_r(oos.r_capped.mean())}. "
         f"Max drawdown on OOS closed P&L: {max_drawdown_pct(oos):.1f}% of £10,000 (hold-out bar: < 15%). "
         f"Fallback-resolved setups: {int((oos.fallbacks > 0).sum())} of {len(oos)}.", "",
         "### Primary: walk-forward", ""]
    L += _wf_table(p)
    L += ["", "### Primary: OOS by entry day (New York weekday of the first fill)", "",
          "| Day | Setups | Mean R on |", "|---|---|---|"]
    L += [f"| {d} | {s['n']} | {_r(s['mean'])} |" for d, s in weekday_table(oos)]
    oos_legs = primary_oos_legs(p)
    fri = oos_legs[oos_legs.exit_reason == "friday"]
    hit = oos[oos.friday_legs > 0]
    L += ["", "### Primary: Friday exits (OOS)", "",
          f"Legs closed by the Friday/holiday-eve exit: {len(fri)} of {len(oos_legs)}, mean leg R "
          f"(financing on, each leg's own planned risk) {_r(fri.r_on.mean())}. Setups with at least one such leg: "
          f"{len(hit)}, mean setup R {_r(hit.r_on.mean())}."]
    L += ["", "### Random-entry control", ""]
    if random_results is None:
        L += ["Not run: the primary has no out-of-sample setups to re-enter at random.", ""]
    else:
        L += [f"{len(random_results)} runs. Each run re-enters every primary OOS setup ({len(oos)}) at market at a "
              "random H1 open in the same test year, the hour drawn from that pair's real first-fill hours (UTC), "
              "with the same side, legs and stop/target distances, through the same engine (primary config).", "",
              "| | Mean R on | off | capped |", "|---|---|---|---|"]
        for q in (0.05, 0.5, 0.95):
            L.append(f"| {int(q * 100)}th percentile | {_r(random_results.mean_on.quantile(q))} "
                     f"| {_r(random_results.mean_off.quantile(q))} | {_r(random_results.mean_capped.quantile(q))} |")
        L.append(f"| Primary OOS | {_r(on['mean'])} | {_r(oos.r_off.mean())} | {_r(oos.r_capped.mean())} |")
        L += ["", f"Random setups per run: mean {random_results.n.mean():.0f}; redrawn because the pair was busy: "
              f"{random_results.redraws.mean():.0f} per run; dropped (no bar left at the drawn hour): "
              f"{random_results.dropped.mean():.1f} per run.", ""]
    L += [
          "### Zone-shift control (each zone has its own walk-forward)", "",
          "| Zone (retracement) | OOS setups | OOS mean R on | Chosen cells |", "|---|---|---|---|"]
    for s in sorted(ZONES):
        a = analyses[ZONES[s].name]
        L.append(f"| {61.8 + 100 * s:.1f}–{76.4 + 100 * s:.1f}{' (primary)' if s == 0 else ''} | {len(a['oos'])} "
                 f"| {_r(a['oos'].r_on.mean())} | {', '.join(_cell(y['chosen']) for y in a['years'])} |")
    L += ["", "### All variants: walk-forward OOS", "",
          "Sensitivities only. None of these can replace a failed primary; adopting one needs fresh data.", "",
          "| Variant | OOS setups | Mean R on | SE | off | capped | EUR_USD | GBP_JPY | Max DD | Chosen cells "
          "| Whole-dev mean R across cells |",
          "|" + "---|" * 11]
    L += [_oos_summary_row(analyses[v.name]) for v in ALL_VARIANTS]
    L += ["", "### Per-cell development results (whole development period, every cell)", "",
          "Counts are over 2016-11-14 → 2024-11-13. Cap columns count legs each cap would have broken.", ""]
    for v in ALL_VARIANTS:
        if v.score_column != "r_on":
            continue   # same runs as the primary
        L += [f"#### {v.name}", ""] + _cell_table(analyses[v.name]) + [""]
        if v is not PRIMARY:
            L += ["Walk-forward:", ""] + _wf_table(analyses[v.name]) + [""]
    L.append(MARKERS[1])
    return "\n".join(L)


def primary_oos_legs(a):
    """The legs behind a walk-forward's OOS setups (each from its year's chosen cell)."""
    oos = a["oos"]
    if oos.empty:
        return pd.DataFrame(columns=["exit_reason", "r_on"])
    wanted = set(zip(oos.cell, oos.pair, oos.setup_id.map(repr)))
    legs = pd.concat([a["legs"][c].assign(cell=[c] * len(a["legs"][c])) for c in set(oos.cell)])
    keep = [(c, p, repr(s)) in wanted for c, p, s in zip(legs.cell, legs.pair, legs.setup_id)]
    return legs[keep]


def write_report(text, path=REPORT_PATH):
    old = open(path).read() if os.path.exists(path) else "# Backtest results\n"
    if MARKERS[0] in old:
        head, rest = old.split(MARKERS[0], 1)
        tail = rest.split(MARKERS[1], 1)[1] if MARKERS[1] in rest else ""
        new = head + text + tail
    else:
        new = old.rstrip("\n") + "\n\n" + text + "\n"
    with open(path, "w") as f:
        f.write(new)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--random-runs", type=int, default=RANDOM_RUNS)
    parser.add_argument("--fresh", action="store_true", help="ignore cached runs")
    args = parser.parse_args()

    grid = run_grid(ALL_VARIANTS, args.workers, args.fresh)
    analyses = {v.name: analyse(v, grid) for v in ALL_VARIANTS}
    p = analyses[PRIMARY.name]
    if p["oos"].empty:
        print("primary has no out-of-sample setups: random-entry control not run")
        random_results = None
    else:
        plan = random_plan(p["oos"], {c: grid[PRIMARY.run_key][c]["legs"] for c in CELLS})
        random_results = run_random(plan, entry_hours(p["oos"]), args.workers, args.random_runs, args.fresh)
    write_report(build_report(analyses, random_results))
    print(f"report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
