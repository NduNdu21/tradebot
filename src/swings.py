"""ATR(14) and the ATR zigzag that finds swing highs and lows (docs/strategy.md §1-§2).

Both are online: they're fed one completed H1 bar at a time and never look
ahead. Prices are H1 mid prices.
"""
from collections import namedtuple
from dataclasses import dataclass

import pandas as pd

Point = namedtuple("Point", "price index time")   # a price at an H1 bar number


class WilderATR:
    """ATR with Wilder smoothing (§1).

    True range = max(high - low, |high - previous close|, |low - previous close|),
    so weekend gaps count. The first bar has no previous close: its TR is high - low.
    The first ATR is the simple average of the first `period` TRs; after that
    ATR = (previous ATR x (period - 1) + TR) / period.
    """

    def __init__(self, period=14):
        self.period = period
        self.prev_close = None
        self.seed = []
        self.value = None

    def update(self, high, low, close):
        """Add one bar; return the ATR (None until `period` bars have been seen)."""
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.prev_close = close
        if self.value is None:
            self.seed.append(tr)
            if len(self.seed) == self.period:
                self.value = sum(self.seed) / self.period
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


@dataclass(frozen=True)
class Pivot:
    kind: str            # "high" or "low"
    price: float
    index: int           # H1 bar number
    time: pd.Timestamp


class ZigZag:
    """Swing highs and lows confirmed by a K x ATR reversal (§2).

    In an up-leg the zigzag tracks the highest high since the last swing low.
    The high is confirmed as a swing high when price has fallen at least K x ATR
    below it (ATR read at the bar where that happens). The down-leg then starts
    from the lowest low since the high. Down-legs mirror this.

    A bar that makes a new extreme can't also confirm it: on H1 we can't tell
    whether its high or its low came first.

    min_bars is not applied here; it's an arming rule (src/setups.py).
    """

    def __init__(self, k):
        self.k = k
        self.direction = None   # "up" (tracking a high) or "down"; None before the first pivot
        self.extreme = None     # running extreme of the current leg
        self.since = None       # best opposite price after the extreme (the reversal so far)
        self.since2 = None      # best same-side price after `since`, to seed the next leg's reversal
        # Before the first pivot we track both sides.
        self.hi = self.lo = None
        self.lo_after_hi = self.hi_after_lo = None
        self.pivots = []

    def update(self, index, time, high, low, atr):
        """Feed one completed H1 bar. Returns the Pivot this bar confirmed, or None."""
        if self.direction is None:
            return self._first_pivot(index, time, high, low, atr)
        up = self.direction == "up"
        # Write everything as an up-leg: a down-leg is the same with prices negated.
        sign = 1 if up else -1
        ahead, behind = (high, low) if up else (low, high)
        if sign * ahead > sign * self.extreme.price:
            self.extreme = Point(ahead, index, time)
            self.since = self.since2 = None
            return None
        if self.since is None or sign * behind < sign * self.since.price:
            self.since = Point(behind, index, time)
            self.since2 = None   # this bar's other end may have come before its new low
        elif self.since2 is None or sign * ahead > sign * self.since2.price:
            self.since2 = Point(ahead, index, time)
        if sign * (self.extreme.price - self.since.price) >= self.k * atr:
            return self._confirm("high" if up else "low", self.since, self.since2)
        return None

    def _confirm(self, kind, next_extreme, next_since):
        pivot = Pivot(kind, *self.extreme)
        self.pivots.append(pivot)
        self.direction = "down" if kind == "high" else "up"
        self.extreme, self.since, self.since2 = next_extreme, next_since, None
        return pivot

    def _first_pivot(self, index, time, high, low, atr):
        """Before any pivot: confirm whichever side first sees a K x ATR reversal."""
        point_h, point_l = Point(high, index, time), Point(low, index, time)
        if self.hi is None or high > self.hi.price:
            self.hi, self.lo_after_hi = point_h, None
        elif self.lo_after_hi is None or low < self.lo_after_hi.price:
            self.lo_after_hi = point_l
        if self.lo is None or low < self.lo.price:
            self.lo, self.hi_after_lo = point_l, None
        elif self.hi_after_lo is None or high > self.hi_after_lo.price:
            self.hi_after_lo = point_h

        if self.lo_after_hi and self.hi.price - self.lo_after_hi.price >= self.k * atr:
            self.extreme = self.hi
            return self._confirm("high", self.lo_after_hi, None)
        if self.hi_after_lo and self.hi_after_lo.price - self.lo.price >= self.k * atr:
            self.extreme = self.lo
            return self._confirm("low", self.hi_after_lo, None)
        return None
