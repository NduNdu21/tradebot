"""Statistics, random-entry control and lookahead check for the ICT backtest (docs/strategy-ict.md §10).

summary             mean R, standard error and the "mean minus k SE" pass-bar numbers, overall and split
max_drawdown_pct    largest peak-to-trough fall of the additive equity curve, in percent points
random_control      same trades, but entered at a random time: is the setup better than chance?
truncation_check    do orders placed before a cut change when the data after the cut is removed?
"""
from collections import Counter
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.data import load_candles, to_timeframe
from src.ict.calendar import NY, is_skipped_market_day, missing_m5, session_times
from src.ict.fills import simulate
from src.ict.run import FIRST_DAY, LAST_DAY, _between, signal_window
from src.ict.setups import Order, find_orders, to_daily


# ------------------------------------------------------------------------------------ summary

def _basic(r: pd.Series) -> dict:
    """n, mean R, standard error of the mean and the mean minus 1 and 2 SE."""
    n = len(r)
    mean = float(r.mean()) if n else float("nan")
    se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    return {"n": n, "mean_r": mean, "se": se, "mean_minus_1se": mean - se, "mean_minus_2se": mean - 2 * se}


def _counts(trades: pd.DataFrame) -> dict:
    return {"stop_first": int(trades["stop_first"].sum()),      # bar touched both stop and target (§9)
            "rearmed": int((trades["cycle"] >= 2).sum())}       # trades from a re-armed cycle (§5)


def summary(trades: pd.DataFrame) -> dict:
    """Overall statistics plus the same per instrument, side and year (by_instrument, by_side, by_year)."""
    out = {**_basic(trades["r"]), **_counts(trades)}
    for key, col in (("by_instrument", "instrument"), ("by_side", "side"), ("by_year", "year")):
        out[key] = {k: {**_basic(g["r"]), **_counts(g)} for k, g in trades.groupby(col)}
    return out


def max_drawdown_pct(trades: pd.DataFrame, risk: float = 0.005) -> float:
    """Largest peak-to-trough drop, in percent points, of equity = running sum of R x risk.

    Trades are taken in fill_time order; equity starts at 0, which counts as the first peak."""
    r = trades.sort_values("fill_time", kind="stable")["r"].to_numpy(dtype=float)
    equity = np.concatenate([[0.0], np.cumsum(r) * risk * 100])
    peak = np.maximum.accumulate(equity)
    return float((peak - equity).max())


# ------------------------------------------------------------------------------- random control

def random_control_dropped(trades: pd.DataFrame, m1_by_instrument: Dict[str, pd.DataFrame],
                           runs: int = 1000, seed: int = 2026,
                           max_tries: int = 20) -> Tuple[List[float], int]:
    """Random-entry control. Returns (mean R of each run, total control trades dropped).

    For every real trade: pick a random M5 candle opening 08:30-10:55 New York that day, enter at market
    at its open on the fill side (short sells at the bid, long buys at the ask), with the real trade's
    stop and target distances measured from that entry, and simulate it. A run's mean is over its
    filled control trades. Dropped = control orders that were cancelled, plus trades for which no
    M1 bar was found in `max_tries` draws.
    """
    rng = np.random.default_rng(seed)
    # A control trade is fully determined by (trade, candle), so simulate each pair at most once.
    cache: Dict[Tuple[int, int], Optional[float]] = {}   # None = no M1 bar there / cancelled
    day_m1: Dict[Tuple[str, date], pd.DataFrame] = {}
    rows = list(trades.itertuples(index=False))
    days = [pd.Timestamp(t.day).date() for t in rows]

    def day_slice(instrument, day):
        # Pre-slice the day once (08:30 to 6 h after the 12:00 exit) so simulate searches a small frame.
        key = (instrument, day)
        if key not in day_m1:
            times = session_times(day)
            day_m1[key] = _between(m1_by_instrument[instrument], times["levels"],
                                   times["exit"] + pd.Timedelta(hours=6))
        return day_m1[key]

    def control_r(i: int, k: int) -> Optional[float]:
        """R of trade i entered at the k-th M5 candle (k=0 is 08:30), or None if it cannot be run."""
        t, day = rows[i], days[i]
        placed = session_times(day)["levels"] + pd.Timedelta(minutes=5 * k)
        bars = day_slice(t.instrument, day)
        if placed not in bars.index:
            return None
        bar = bars.loc[placed]
        entry = bar["bid_o"] if t.side == "short" else bar["ask_o"]
        stop_dist, target_dist = abs(t.entry - t.stop), abs(t.entry - t.target)
        sign = 1 if t.side == "short" else -1        # a short's stop is above entry, a long's below
        order = Order(t.instrument, day, t.side, placed, entry, entry + sign * stop_dist,
                      entry - sign * target_dist, entry, {}, 1)
        res = simulate(order, bars)
        return res.r if res.status == "filled" else None

    means, dropped = [], 0
    for _ in range(runs):
        rs = []
        for i in range(len(rows)):
            for _try in range(max_tries):
                k = int(rng.integers(0, 30))         # 30 candles: 08:30, 08:35, ... 10:55
                if (i, k) not in cache:
                    cache[(i, k)] = control_r(i, k)
                # A missing bar is redrawn; a cancelled order is dropped (both give None, so
                # tell them apart by the bar's existence).
                if cache[(i, k)] is not None:
                    rs.append(cache[(i, k)])
                    break
                if _bar_exists(day_slice(rows[i].instrument, days[i]), days[i], k):
                    dropped += 1                     # cancelled control order
                    break
            else:
                dropped += 1                         # no M1 bar in any of the tries
        means.append(float(np.mean(rs)) if rs else float("nan"))
    return means, dropped


def _bar_exists(bars: pd.DataFrame, day: date, k: int) -> bool:
    return (session_times(day)["levels"] + pd.Timedelta(minutes=5 * k)) in bars.index


def random_control(trades: pd.DataFrame, m1_by_instrument: Dict[str, pd.DataFrame],
                   runs: int = 1000, seed: int = 2026) -> List[float]:
    """Mean R of each random-entry run (see random_control_dropped for the rules and the dropped count)."""
    return random_control_dropped(trades, m1_by_instrument, runs, seed)[0]


# ------------------------------------------------------------------------------ truncation check

def _key(o: Order):
    return (o.placed_time, o.side, o.entry, o.stop, o.target)


def truncation_check(instrument: str, shift_rule: str, cuts: int = 25, seed: int = 2026,
                     m1: Optional[pd.DataFrame] = None, find_orders_fn: Callable = find_orders) -> bool:
    """True if no order placed before a random cut changes when the data after the cut is removed.

    Picks `cuts` random (day, time) pairs: a development weekday that is a normal, fully-covered market
    day, and a time between 08:30 and 11:00 New York. Orders found from the full data and
    placed at or before the cut must equal the orders found from data truncated at the cut. Only
    whole M5 candles that closed by the cut are kept (a candle is known only when it closes).
    `m1` and `find_orders_fn` can be injected (tests); by default the development M1 and the real
    find_orders are used.
    """
    if m1 is None:
        m1 = load_candles(instrument, "M1")          # development data only
    sig = to_timeframe(m1, "5min")
    daily = to_daily(m1)
    bar = pd.Timedelta("5min")

    # Candidate days: development weekdays that run() would trade.
    candidates, d = [], FIRST_DAY
    while d <= LAST_DAY:
        if d.weekday() < 5 and is_skipped_market_day(d) is None:
            midnight = pd.Timestamp(d, tz=NY)
            m5_day = _between(sig, midnight, midnight + pd.Timedelta("1 day"))
            if not m5_day.empty and not missing_m5(m5_day, d):
                candidates.append(d)
        d += timedelta(days=1)

    rng = np.random.default_rng(seed)
    for _ in range(cuts):
        day = candidates[int(rng.integers(len(candidates)))]
        times = session_times(day)
        cut = times["levels"] + pd.Timedelta(seconds=int(rng.integers(0, 150 * 60 + 1)))  # 08:30-11:00

        full, _ = find_orders_fn(signal_window(sig, day), daily, instrument, day, shift_rule, bar)
        expected = sorted(_key(o) for o in full if o.placed_time <= cut)

        m1_cut = m1[m1.index < cut]                   # no bar opening at or after the cut
        sig_cut = to_timeframe(m1_cut, "5min")
        sig_cut = sig_cut[sig_cut.index + bar <= cut]  # drop the candle still forming at the cut
        cut_orders, _ = find_orders_fn(signal_window(sig_cut, day), to_daily(m1_cut), instrument, day,
                                       shift_rule, bar)
        if sorted(_key(o) for o in cut_orders if o.placed_time <= cut) != expected:
            return False
    return True
