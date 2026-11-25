"""M1 order simulator for ICT limit orders (docs/strategy-ict.md §6-§9).

Shorts and longs share one code path. For a long we flip every price (p -> -p) and swap bid with
ask: a long's ask low becomes a short's bid high, and so on. The short rules then run unchanged and
the prices in the result are flipped back. R does not change sign under the flip.

Short rules, on M1 bars that open at or after the order was placed (§9: nothing fills inside the
M5 candle that created the order):
  before a fill: fills when bid_h >= entry; cancelled by 11:00, by ask_h >= stop, by ask_l <= target
  after a fill:  stop on ask_h >= stop; target on ask_l <= target; time exit at 12:00
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.ict.calendar import session_times
from src.ict.setups import Order

# §8: points added against the trader on stop exits and the 12:00 exit.
SLIPPAGE = {"NAS100_USD": 1.0, "SPX500_USD": 0.5}


@dataclass
class Result:
    order: Order
    status: str                         # "filled", "cancelled_time", "cancelled_stop", "cancelled_target"
    fill_time: Optional[pd.Timestamp] = None
    fill_price: Optional[float] = None
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # "stop", "target", "time"
    r: Optional[float] = None
    stop_first: bool = False            # a bar touched both stop and target; the stop was counted (§9)


def simulate(order: Order, m1: pd.DataFrame) -> Result:
    """Walk the M1 bars from the order's placement and report what happened to it."""
    times = session_times(order.day)
    entry_end, exit_time = times["entry_end"], times["exit"]
    slip = SLIPPAGE[order.instrument]

    bars = m1[m1.index >= order.placed_time]
    # Short-side view of the bars: for a long, prices change sign and bid/ask swap.
    if order.side == "long":
        sign = -1.0
        view = pd.DataFrame({
            "bid_o": -bars["ask_o"], "bid_h": -bars["ask_l"],
            "ask_o": -bars["bid_o"], "ask_h": -bars["bid_l"], "ask_l": -bars["bid_h"],
        })
    else:
        sign = 1.0
        view = bars[["bid_o", "bid_h", "ask_o", "ask_h", "ask_l"]]
    entry, stop, target = sign * order.entry, sign * order.stop, sign * order.target
    risk = abs(entry - stop)

    result = Result(order=order, status="cancelled_time")
    filled = False
    for t, b in view.iterrows():
        if not filled:
            if t >= entry_end:                       # §6: 11:00 cancels an unfilled order
                return result
            hit_entry = b.bid_h >= entry
            hit_stop = b.ask_h >= stop
            hit_target = b.ask_l <= target
            if hit_entry and hit_stop:
                pass                                 # fills and is stopped out below (conservative)
            elif hit_target:                         # §6: target before a fill cancels, even in the fill bar
                result.status = "cancelled_target"
                return result
            elif hit_stop:
                result.status = "cancelled_stop"
                return result
            elif not hit_entry:
                continue
            # Fill: a limit order gets the open when the bar gaps past the entry, else the entry.
            filled = True
            result.status = "filled"
            result.fill_time = t
            fill = max(entry, b.bid_o)
            result.fill_price = sign * fill
            # The fill bar can stop us out, but its target is only reachable from the next bar.
            if hit_stop:
                return _exit(result, sign, t, max(stop, b.ask_o) + slip, "stop", entry, risk)
            continue

        if t >= exit_time:                           # §7: still open at 12:00 -> market exit
            return _exit(result, sign, t, b.ask_o + slip, "time", entry, risk)
        hit_stop = b.ask_h >= stop
        hit_target = b.ask_l <= target
        if hit_stop:                                 # §9: stop and target in one bar -> stop first
            result.stop_first = hit_target
            return _exit(result, sign, t, max(stop, b.ask_o) + slip, "stop", entry, risk)
        if hit_target:                               # limit target: the open if it gaps past, else target
            return _exit(result, sign, t, min(target, b.ask_o), "target", entry, risk)

    if filled:
        raise ValueError("no M1 bar at or after 12:00 New York to close the position")
    return result


def _exit(result, sign, t, price, reason, entry, risk):
    """Record the exit (price given in short-side view) and R = P&L / planned risk (entry to stop)."""
    result.exit_time, result.exit_reason = t, reason
    result.exit_price = sign * price
    result.r = (entry - price) / risk                # short-side view: profit when price falls
    return result
