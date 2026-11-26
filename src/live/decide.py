"""Pure entry/exit decisions for the live bot. No network, no state: the same rules as the backtest."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.meanrev.run import MAX_HOLD, signals   # the frozen rules are imported, never copied


@dataclass
class OpenTrade:
    trade_id: str
    instrument: str
    units: float
    entry_day: date


@dataclass
class Decision:
    instrument: str
    action: str          # "buy", "close" or "none"
    reason: str
    close: float
    entry_signal: bool
    exit_signal: bool
    units: float = 0.0


def decide(instrument: str, closes: pd.Series, nyse_days: pd.DatetimeIndex, today: date,
           open_trade: Optional[OpenTrade], closed_today: bool, units_if_entering: float) -> Decision:
    """Buy, close or do nothing today. `closes` is indexed by NYSE days up to and including today."""
    entry, exit_, _ = signals(closes)
    entry, exit_ = np.asarray(entry), np.asarray(exit_)
    days = pd.DatetimeIndex(closes.index)

    def result(action, reason, units=0.0):
        return Decision(instrument, action, reason, float(closes.iloc[-1]),
                        bool(entry[-1]), bool(exit_[-1]), units)

    if open_trade is None:
        if closed_today:
            return result("none", "closed today")       # the backtest re-enters the day after an exit at the earliest
        if entry[-1]:
            return result("buy", "entry signal", units_if_entering)
        return result("none", "no signal")

    # Days after the entry day, up to today. An exit signal on the entry day itself does not count.
    after = days > pd.Timestamp(open_trade.entry_day)
    hold = int(after.sum())
    if hold >= MAX_HOLD:
        return result("close", "time stop")
    hits = days[after & exit_]
    if len(hits):
        if exit_[-1]:
            return result("close", "exit signal")
        # A missed exit stays wanted until it is carried out, as in the backtest walk.
        return result("close", "exit caught up (signal on %s)" % hits[0].date())
    return result("none", "holding")
