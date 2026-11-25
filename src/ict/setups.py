"""ICT setup detection: liquidity sweep -> market structure shift -> FVG -> limit order.

Every rule is from docs/strategy-ict.md (section numbers §n below). Episode references such as
"Ep6 36:00" are the 2022 ICT Mentorship captions the spec cites.

Shorts and longs share one code path. For a long we flip every price (p -> -p, so highs become
lows and lows become highs), run the short rules, and flip the answer back. In flipped prices a
sweep of the low level is a sweep of a high, a swing high is a swing low, a bullish FVG is a
bearish one, and "below the 50%" means "above" it, so the two sides mirror each other exactly.
In the scanner below, "high" / "low" / "above" always mean the short side's view.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

import pandas as pd

from src.data import add_mid
from src.ict.calendar import NY, session_times
from src.ict.swings import Swing, find_swings, latest

DAY = pd.Timedelta("1 day")
TARGET_WINDOW = pd.Timedelta("24h")  # §7: the target swing formed in the 24 h before the order
# Swings for levels and targets are searched in this much history before 08:30. Targets need only
# 24 h (§7). Levels have no lookback limit (§4), so if no level is found here the full history is
# searched; with M5 candles round the clock that fallback should never be needed in practice.
LOOKBACK = pd.Timedelta("5 days")


@dataclass
class Order:
    instrument: str
    day: date
    side: str                 # "short" or "long"
    placed_time: pd.Timestamp  # close of the candle that completed shift + FVG
    entry: float              # FVG near edge (candle 3's high for a short)
    stop: float               # candle 1's high for a short
    target: float
    sweep_level: float
    notes: dict               # sweep extreme, short-term swing, FVG bounds, dealing-range 50%, daily 50%
    cycle: int                # 1 for the first cycle on a level that day, 2+ for re-armed cycles (§5)


# ---------------------------------------------------------------- daily candles and bias (§1, §3)

def to_daily(candles: pd.DataFrame) -> pd.DataFrame:
    """Daily bid/ask candles running 17:00 -> 17:00 New York, built from M1 or M5 (§1).

    Each candle is named after the day it closes but indexed by its open: the UTC instant of
    17:00 New York on the day before (DST-aware). Monday's candle opens Sunday 17:00.
    """
    # Shift New York wall-clock time by 7 hours so 17:00 lands on midnight: the date is then the
    # day the candle closes.
    wall = candles.index.tz_convert(NY).tz_localize(None)
    close_day = (wall + pd.Timedelta(hours=7)).normalize()
    agg = {}
    for side in ("bid", "ask"):
        agg.update({f"{side}_o": "first", f"{side}_h": "max", f"{side}_l": "min", f"{side}_c": "last"})
    if "volume" in candles.columns:
        agg["volume"] = "sum"
    daily = candles[list(agg)].groupby(close_day).agg(agg)
    # 17:00 always exists exactly once in New York (clocks change at 02:00).
    opens = (daily.index - DAY + pd.Timedelta(hours=17)).tz_localize(NY).tz_convert("UTC")
    daily.index = pd.DatetimeIndex(opens, name="time")
    return add_mid(daily)


def daily_bias_mid(daily: pd.DataFrame, at: pd.Timestamp) -> Optional[float]:
    """Midpoint of the most recent confirmed daily swing high and swing low at `at` (§3).

    A daily swing counts only once the candle to its right has closed at 17:00 New York (§2, §3),
    so only swings with confirmed_time <= `at` are used; the candle still forming never counts.
    Returns None if there is no confirmed swing high or low yet.
    """
    # Candles opening after `at` can't matter; dropping them just keeps the search short.
    swings = [s for s in find_swings(daily[daily.index < at], DAY) if s.confirmed_time <= at]
    high = _most_recent(swings, "high")
    low = _most_recent(swings, "low")
    if high is None or low is None:
        return None
    return (high.price + low.price) / 2


def _most_recent(swings: List[Swing], kind: str) -> Optional[Swing]:
    """The swing of `kind` with the latest confirmed_time (swings are already filtered)."""
    return max((s for s in swings if s.kind == kind), key=lambda s: s.confirmed_time, default=None)


# ---------------------------------------------------------------- order finding (§4-§7)

def find_orders(m5: pd.DataFrame, daily: pd.DataFrame, instrument: str, day: date, shift_rule: str,
                bar: pd.Timedelta = pd.Timedelta("5min")) -> Tuple[List[Order], Counter]:
    """Every order the rules produce on `day`, in time order, plus a Counter of skipped setups.

    `m5` is the signal chart (M5 normally; `bar` is its candle length, so M1/M2 frames work for
    the §10 sensitivity). `shift_rule` is "close" (primary) or "wick" (rescue variant), §5.
    Skip reasons: no_shift, no_fvg, fvg_not_premium, bias, no_target, too_late. The
    one-trade-per-day rule (§7) is applied by the caller.
    """
    if shift_rule not in ("close", "wick"):
        raise ValueError(f"shift_rule must be 'close' or 'wick', not {shift_rule!r}")
    times = session_times(day)
    start, end = times["levels"], times["entry_end"]
    # Nothing after the 11:00 candle is needed: it can only be FVG candle 3 for a 10:55 shift.
    window = m5[(m5.index >= start - LOOKBACK) & (m5.index <= end)]

    orders, skips = [], Counter()
    # Shorts come from sweeps of the high level, longs from sweeps of the low level (§4).
    for side, sign in (("short", 1), ("long", -1)):
        frame = _oriented(window, sign)
        swings = find_swings(frame, bar)
        level = _level(swings, start)
        if level is None:  # §4: no lookback limit on the level
            level = _level(find_swings(_oriented(m5[m5.index < start], sign), bar), start)
        if level is None:
            continue
        found, skipped = _scan_level(frame, swings, level, side, sign, daily, instrument, day,
                                     shift_rule, bar, start, end)
        orders += found
        skips.update(skipped)
    orders.sort(key=lambda o: o.placed_time)
    return orders, skips


def _oriented(df: pd.DataFrame, sign: int) -> pd.DataFrame:
    """mid_h / mid_l / mid_c seen from the short side: as-is for shorts, flipped for longs."""
    if sign == 1:
        return df[["mid_h", "mid_l", "mid_c"]]
    return pd.DataFrame({"mid_h": -df["mid_l"], "mid_l": -df["mid_h"], "mid_c": -df["mid_c"]},
                        index=df.index)


def _level(swings: List[Swing], levels_time: pd.Timestamp) -> Optional[float]:
    """§4: the most recent swing high confirmed by 08:30 ("the first one, not going back days", Ep5 32:00)."""
    confirmed = [s for s in swings if s.confirmed_time <= levels_time]
    swing = _most_recent(confirmed, "high")
    return None if swing is None else swing.price


def _scan_level(frame, swings, level, side, sign, daily, instrument, day, shift_rule, bar, start, end):
    """Walk the candles from 08:30 to 11:00 for one level and return (orders, skip Counter).

    State is one "cycle" at a time: a sweep extreme, its short-term low, and whether it has
    shifted yet. A candle above the extreme starts the cycle again (§5 re-arm).
    """
    times = frame.index
    hi = frame["mid_h"].to_numpy()
    lo = frame["mid_l"].to_numpy()
    close = frame["mid_c"].to_numpy()
    position = {t: i for i, t in enumerate(times)}
    swing_lows = [s for s in swings if s.kind == "low"]

    orders, skips = [], Counter()
    swept = False     # §4: each level can be swept only once a day
    cycle = 0
    ext_i = None      # index of the sweep-extreme candle (§4)
    stl = None        # this cycle's short-term low (§5)
    shift_i = None    # index of this cycle's shift candle; None until it shifts
    pending = False   # shifted, nothing placed yet: candle 3 may still be the next candle (§6)
    saw_fvg = False   # a window FVG was found at the shift but none was in premium

    def place(fvgs: List[Tuple[int, int]], place_i: int, final: bool) -> bool:
        """Apply the placement checks to FVGs completed by candle `place_i` (nearest first).

        Returns True if the cycle is decided (order placed or skip counted). With final=False and
        no FVG in premium, nothing is counted: an FVG ending on the next candle may still qualify.

        Skip reasons are checked in this order (only which reason is counted depends on it):
        too_late -> fvg_not_premium -> bias -> no_target. (no_fvg is decided by the caller.)
        """
        placed = times[place_i] + bar
        # §6: no order is placed at or after 11:00.
        if placed >= end:
            skips["too_late"] += 1
            return True
        # §6 dealing range: sweep extreme -> lowest low from the extreme candle to the placement
        # candle, fixed now (Ep4 01:20, Ep6).
        extreme = hi[ext_i]
        dealing_mid = (extreme + lo[ext_i:place_i + 1].min()) / 2
        # §6 (controller ruling): an FVG "qualifies" if it is in the window AND its near edge
        # (candle 3's high, the entry, Ep6 35:40-36:20) is above the 50% (premium). The nearest
        # qualifying FVG to the sweep extreme is used; bias and target apply to it alone.
        premium = [f for f in fvgs if hi[f[1]] > dealing_mid]
        if not premium:
            if final:
                skips["fvg_not_premium"] += 1
            return final
        c1, c3 = premium[0]
        near = hi[c3]
        # §3: shorts only above the daily 50%, longs only below; exactly at 50% allows neither.
        # With no confirmed daily swing high and low yet, the bias can't be read: counted as bias.
        daily_mid = daily_bias_mid(daily, placed)
        if daily_mid is None or not near > sign * daily_mid:
            skips["bias"] += 1
            return True
        target = _target(swing_lows, lo, position, dealing_mid, placed, place_i)
        if target is None:
            skips["no_target"] += 1
            return True
        orders.append(Order(
            instrument=instrument, day=day, side=side, placed_time=placed,
            entry=sign * near,
            stop=sign * hi[c1],  # §7: candle 1's high, no buffer (owner's ruling, Ep6 36:00)
            target=sign * target.price,
            sweep_level=sign * level,
            notes={
                "sweep_extreme": sign * extreme, "sweep_extreme_time": times[ext_i],
                "short_term_swing": sign * stl.price, "short_term_swing_time": stl.candle_time,
                "shift_time": times[shift_i],
                "fvg_far": sign * lo[c1], "fvg_near": sign * near, "fvg_candle1_time": times[c1],
                "dealing_range_mid": sign * dealing_mid, "daily_mid": daily_mid,
                "target_time": target.candle_time,
            },
            cycle=cycle,
        ))
        return True

    for i in range(times.searchsorted(start), len(times)):
        t = times[i]
        if t > end:
            break

        # §6: a shift with nothing placed yet can still use an FVG whose candle 3 is this candle
        # (the candle after the shift), judged against the range through this candle. Decided
        # here, before this candle can re-arm. A candle above the extreme can't be such a candle 3
        # anyway (its high is above candle 1's low).
        if pending:
            pending = False
            fvgs = _window_fvgs(hi, lo, ext_i, i, i)
            if fvgs:
                place(fvgs, i, final=True)
            elif saw_fvg:
                skips["fvg_not_premium"] += 1
            else:
                skips["no_fvg"] += 1  # §6: no FVG, no trade (Ep6 22:00, Ep4 06:40)

        # Sweeps, re-arms and shifts happen only on candles opening before 11:00 (§4, §5).
        if t >= end:
            continue

        new_extreme = False
        if not swept:
            # §4 sweep: a candle trading above the level; a wick is enough (Ep4 03:00).
            if hi[i] > level:
                swept, cycle, new_extreme = True, 1, True
        elif hi[i] > hi[ext_i]:
            # §5 re-arm (owner's ruling): a candle above the current extreme becomes the new
            # extreme and the cycle starts again. Ruling D: it counts as a new cycle only if the
            # current cycle has already shifted; before a shift it just moves the extreme.
            if shift_i is not None:
                cycle += 1
            new_extreme = True
        if new_extreme:
            ext_i, shift_i = i, None
            # §5: short-term low = the most recent swing low that formed before the extreme's
            # candle (Ep2 43:20, Ep4 07:20, Ep6 20:40), confirmed by this candle's close. Any swing
            # before this candle is confirmed by then at the latest, so it stays fixed this cycle.
            usable = [s for s in swing_lows if s.confirmed_time <= t + bar]
            stl = latest(usable, "low", before=t, field="candle_time")

        # §5 shift: close below the short-term low (primary, Ep6 19:40-21:00) or trade below it
        # (rescue variant, Ep4 07:20). The first such candle is the cycle's shift.
        if swept and shift_i is None and stl is not None:
            price = close[i] if shift_rule == "close" else lo[i]
            if price < stl.price:
                shift_i = i
                # §6: candle 1 at or after the extreme candle, candle 3 no later than the candle
                # after the shift. FVGs complete now are placed at this close; any in premium is
                # nearer the extreme than one ending on the next candle, so it wins without waiting.
                fvgs = _window_fvgs(hi, lo, ext_i, ext_i + 2, i)
                saw_fvg = bool(fvgs)
                if not (fvgs and place(fvgs, i, final=False)):
                    pending = True

    # §5: cancel if no shift by 11:00. Counted once per swept level whose last cycle never shifted.
    if swept and shift_i is None:
        skips["no_shift"] += 1
    return orders, skips


def _window_fvgs(hi, lo, ext_i: int, first_c3: int, last_c3: int) -> List[Tuple[int, int]]:
    """Bearish FVGs with candle 3 in [first_c3, last_c3], nearest the sweep extreme first (§6).

    Bearish FVG: candle 1's low above candle 3's high, wicks included (Ep6 12:00-12:20). Candle 1
    must be at or after the extreme candle (displacement leg, Ep6 20:00-24:20). Nearest the
    extreme = earliest candle 1. Returns a list of (c1, c3).
    """
    return [(c3 - 2, c3) for c3 in range(max(first_c3, ext_i + 2), last_c3 + 1) if lo[c3 - 2] > hi[c3]]


def _target(swing_lows, lo, position, dealing_mid, placed, place_i) -> Optional[Swing]:
    """§7 target: the nearest swing low below the dealing-range 50% (Ep4 02:00-02:40).

    It must have formed in the 24 h before the order (candle_time >= placed - 24 h), be confirmed
    by the placement, and not be traded through: no mid low at or below its price after the swing
    candle up to and including the placement candle (a touch counts). Nearest = highest price.
    """
    best = None
    for s in swing_lows:
        if not (s.price < dealing_mid and s.confirmed_time <= placed
                and s.candle_time >= placed - TARGET_WINDOW):
            continue
        j = position[s.candle_time]
        if lo[j + 1:place_i + 1].min() <= s.price:
            continue  # traded through
        if best is None or s.price > best.price:
            best = s
    return best
