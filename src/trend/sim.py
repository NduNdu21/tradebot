"""Position path and daily P&L for one pair (strategy §4–§7).

`positions` walks the bars in order and decides when to trade; `daily_pnl` then
turns any position path into daily P&L, vectorised, so the random-sign control
(evaluate.py) can reuse it with flipped positions.
"""
import numpy as np
import pandas as pd

DRIFT = 0.25   # trade when the position is more than 25% away from the target
SLIP_PIPS_WIDE = {"EUR_JPY", "GBP_JPY", "AUD_JPY"}


def pip(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001


def slippage(pair):
    """0.5 pip, or 1.0 pip on the JPY crosses (§5)."""
    return (1.0 if pair in SLIP_PIPS_WIDE else 0.5) * pip(pair)


def needs_trade(position, target):
    """§4: trade if the sign changes (incl. to/from flat) or |position - target| > 25% of |target|."""
    if np.sign(position) != np.sign(target):
        return True
    return abs(position - target) > DRIFT * abs(target)


def positions(target, can_trade):
    """Units held at each bar's close.

    target[i] is set at bar i's close and acted on in bar i+1, at that bar's 08:00
    candle, if it has one (can_trade[i+1]). Otherwise the bar passes with no trade.
    """
    target = np.asarray(target, dtype=float)
    can_trade = np.asarray(can_trade, dtype=bool)
    held = np.zeros(len(target))
    pos = 0.0
    for i in range(1, len(target)):
        if can_trade[i] and needs_trade(pos, target[i - 1]):
            pos = target[i - 1]
        held[i] = pos
    return held


# Days of financing charged at each weekday's 17:00 rollover (Mon = 0): Wednesday rolls three.
FIN_DAYS = np.array([1, 1, 3, 1, 1, 0, 0])


def daily_pnl(held, bars, long_rate, short_rate, quote_to_gbp, pair, slip=None):
    """Daily P&L in GBP, split into parts, for a position path (1-D or 2-D: runs x bars).

    price: units held overnight x close change + units traded x (close - mid fill)
    cost:  -|units traded| x (half spread + slippage) at the 08:00 candle
    fin:   |units at close| x close x rate for the side x days / 365
    """
    held = np.asarray(held, dtype=float)
    prev = np.concatenate([np.zeros(held.shape[:-1] + (1,)), held[..., :-1]], axis=-1)
    traded = held - prev
    c = bars["c"].values
    dc = np.concatenate([[0.0], np.diff(c)])
    mid_fill = np.nan_to_num(((bars["exec_bid"] + bars["exec_ask"]) / 2).values)
    half_spread = np.nan_to_num(((bars["exec_ask"] - bars["exec_bid"]) / 2).values)

    price = prev * dc + traded * (c - mid_fill)
    slip = slippage(pair) if slip is None else slip   # strategy 4 passes its own slippage
    cost = -np.abs(traded) * (half_spread + slip)
    days = FIN_DAYS[bars.index.dayofweek]
    rate = np.where(held > 0, np.nan_to_num(long_rate), np.nan_to_num(short_rate))
    fin = np.abs(held) * c * rate * days / 365
    fx = np.asarray(quote_to_gbp, dtype=float)
    return {"price": price * fx, "cost": cost * fx, "fin": fin * fx}
