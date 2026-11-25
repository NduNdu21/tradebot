"""Three-candle swings for the ICT strategy (docs/strategy-ict.md §2).

A swing high is a candle whose high is strictly above the highs of the candles on
each side of it. A swing low is the mirror image, using lows. Closes are not used,
and equal highs (or equal lows) are not swings.

A swing is only usable once the candle to its right has closed. `confirmed_time`
records that moment, so the backtester never acts on a swing too early.
"""
from collections import namedtuple
from typing import List, Optional

import pandas as pd

# kind: "high" or "low". candle_time: open of the swing candle.
# confirmed_time: close of the candle to its right (its open + bar length).
Swing = namedtuple("Swing", "kind price candle_time confirmed_time")


def find_swings(df: pd.DataFrame, bar: pd.Timedelta) -> List[Swing]:
    """Return all swings in `df` (mid_h / mid_l), sorted by confirmed_time.

    The first and last candles can never be swings, because one side has no neighbour.
    """
    highs = df["mid_h"].tolist()
    lows = df["mid_l"].tolist()
    times = df.index
    swings = []
    for i in range(1, len(df) - 1):
        # Swing high: strictly above both neighbours (equal highs do not count).
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swings.append(Swing("high", highs[i], times[i], times[i + 1] + bar))
        # Swing low: strictly below both neighbours (equal lows do not count).
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swings.append(Swing("low", lows[i], times[i], times[i + 1] + bar))
    return sorted(swings, key=lambda s: s.confirmed_time)


def latest(
    swings: List[Swing], kind: str, before: pd.Timestamp, field: str = "confirmed_time"
) -> Optional[Swing]:
    """Return the most recent swing of `kind` whose `field` is strictly before `before`.

    Default `field` is confirmed_time, so a swing is only returned once it is usable.
    Returns None if no swing matches.
    """
    matches = [s for s in swings if s.kind == kind and getattr(s, field) < before]
    return max(matches, key=lambda s: getattr(s, field), default=None)
