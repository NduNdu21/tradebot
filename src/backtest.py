"""Bar-by-bar M5 simulator: fills, exits, sizing, financing (docs/strategy.md §6-§9).

The engine knows nothing about Fibonacci levels or swings. A strategy object
places orders at each H1 close; the engine fills them on M5 bid/ask candles,
sizes each leg, charges financing and keeps the account balance.

Both pairs trade on one shared GBP account, on a merged M5 timeline.

What happens at each M5 timestamp:
  1. charge financing for every rollover since the previous timestamp;
  2. for each traded pair with a candle at this time:
     a. if the candle starts a new hour, call strategy.on_h1_close() for the
        hour that just completed (so orders placed there are live from this candle);
     b. Friday close (variant): flatten at the open;
     c. stops and targets on legs that were already open;
     d. fills on pending orders, then the stop check for those fresh fills;
     e. once the position has closed, cancel its unfilled legs.

Prices on each side (§6, §8):
  long:  enters at the ask, exits (stop and target) at the bid
  short: enters at the bid, exits at the ask
"""
import math
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.data import load_m5
from src.financing import NEW_YORK, annual_rate, apply_mode, financing_amount, rollovers

Bar = namedtuple("Bar", "bid_o bid_h bid_l bid_c ask_o ask_h ask_l ask_c")

# Pairs the engine trades, and the GBP_<quote> rate used to convert each pair's
# quote currency to GBP. GBP_JPY converts with its own price.
TRADED_PAIRS = ("EUR_USD", "GBP_JPY")
CONVERSION_PAIRS = ("GBP_USD",)


@dataclass
class Config:
    starting_balance: float = 10_000.0  # GBP
    risk_per_leg: float = 0.005         # 0.5% of closed balance per leg
    max_notional: float = 10.0          # total open notional <= 10 x balance
    margin_rate: float = 0.0333         # the account's rate for both pairs (API)
    max_margin_used: float = 0.5        # margin used <= 50% of balance
    slippage_pips: dict = field(default_factory=lambda: {"EUR_USD": 0.5, "GBP_JPY": 1.0})
    friday_close: bool = False          # variant: flatten Friday 16:00 New York
    financing_mode: str = "on"          # "on", "off" or "cap" (src/financing.apply_mode)
    financing_schedule: str = "help"    # "help" (main run) or "api" (sensitivity)


@dataclass
class Order:
    """A pending entry for one leg of a setup."""
    pair: str
    setup_id: object
    leg: int
    side: str                 # "long" or "short"
    kind: str                 # "limit", or "market" (fills at the pair's next candle open)
    entry: Optional[float]    # limit price; None for market orders
    stop: float
    target: float


@dataclass
class Trade:
    """One filled leg, from entry to exit."""
    pair: str
    setup_id: object
    leg: int
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    units: int
    stop: float
    target: float
    risk_gbp: float           # loss at the stop price before slippage, in GBP at entry
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # "stop", "target" or "friday"
    pnl_gbp: float = 0.0                # price P&L only
    financing_gbp: float = 0.0
    fallback: bool = False    # stop and target both touched in one candle; stop assumed first

    @property
    def r(self):
        """Result in R: total P&L (price + financing) over the risk taken."""
        return (self.pnl_gbp + self.financing_gbp) / self.risk_gbp


def pip_size(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001


def load_data(include_holdout=False):
    """Load M5 candles for the traded and conversion pairs, keyed by OANDA name."""
    return {name: load_m5(name.replace("_", ""), include_holdout=include_holdout)
            for name in TRADED_PAIRS + CONVERSION_PAIRS}


class NoStrategy:
    """Places nothing. Useful for tests and timing runs."""

    def on_h1_close(self, engine, pair, hour):
        pass


class Engine:
    def __init__(self, data, strategy=None, config=None, pairs=None):
        """
        data:     {name: M5 DataFrame with bid_*/ask_* OHLC, UTC index}. Must include
                  GBP_<quote> for every traded pair whose quote currency isn't GBP.
        strategy: object with on_h1_close(engine, pair, hour), `hour` being the
                  open time of the H1 candle that just completed.
        pairs:    which names in `data` to trade (default: the ones in TRADED_PAIRS).
        """
        self.config = config or Config()
        self.strategy = strategy or NoStrategy()
        self.pairs = list(pairs or [p for p in TRADED_PAIRS if p in data])
        self.names = list(data)

        self.timeline = data[self.names[0]].index
        for name in self.names[1:]:
            self.timeline = self.timeline.union(data[name].index)
        # Row of each name at each timeline step (-1 = no candle at that time).
        self.rows = {name: data[name].index.get_indexer(self.timeline) for name in self.names}
        self.bars = {name: [Bar(*r) for r in data[name][list(Bar._fields)].itertuples(index=False)]
                     for name in self.names}
        self.hours = self.timeline.floor("h")
        ny = self.timeline.tz_convert(NEW_YORK)
        self.friday_cutoff = (ny.dayofweek == 4) & (ny.hour >= 16)

        self.time = None
        self.balance = self.config.starting_balance
        self.equity = [(self.timeline[0], self.balance)]   # (time, balance) after every change
        self.last_mid = {}
        self.active = {p: None for p in self.pairs}        # setup id currently using the pair
        self.pending = {p: {} for p in self.pairs}         # leg number -> Order
        self.open = {p: [] for p in self.pairs}            # open Trades
        self.has_filled = {p: False for p in self.pairs}   # active setup has had a fill
        self.finished = set()                              # (pair, setup_id) that can't trade again
        self.closed = []                                   # closed Trades, in exit order
        self.skipped = []                                  # (time, order, reason)

    # ---- order API for the strategy ----

    def place_limit(self, pair, setup_id, leg, side, entry, stop, target):
        """Place or re-price a limit entry for one leg."""
        self._add_order(Order(pair, setup_id, leg, side, "limit", entry, stop, target))

    def place_market(self, pair, setup_id, leg, side, stop, target):
        """Enter one leg at the open of the pair's next M5 candle (spread, no slippage)."""
        self._add_order(Order(pair, setup_id, leg, side, "market", None, stop, target))

    def cancel_setup(self, pair, setup_id):
        """Cancel the setup's unfilled legs. A setup with no fill is finished for good."""
        if self.active[pair] != setup_id:
            return
        self.pending[pair].clear()
        if not self.open[pair]:
            self._finish_setup(pair)

    def open_trades(self, pair):
        return list(self.open[pair])

    def pending_orders(self, pair):
        return list(self.pending[pair].values())

    def _add_order(self, order):
        pair, setup_id = order.pair, order.setup_id
        if (pair, setup_id) in self.finished:
            raise ValueError(f"setup {setup_id!r} on {pair} has already finished")
        # One setup per pair at a time: the strategy must cancel the old one first.
        if self.active[pair] not in (None, setup_id):
            raise ValueError(f"{pair} is busy with setup {self.active[pair]!r}")
        if any(t.leg == order.leg for t in self.open[pair]):
            raise ValueError(f"leg {order.leg} of setup {setup_id!r} is already filled")
        sign = 1 if order.side == "long" else -1
        if order.entry is not None and not sign * order.stop < sign * order.entry < sign * order.target:
            raise ValueError(f"need stop < entry < target for a {order.side}: {order}")
        self.active[pair] = setup_id
        self.pending[pair][order.leg] = order

    # ---- main loop ----

    def run(self):
        cfg = self.config
        moments = [(pd.Timestamp(m), days) for m, days in
                   rollovers(self.timeline[0], self.timeline[-1], cfg.financing_schedule)]
        next_rollover = 0
        last_hour = {p: None for p in self.pairs}

        for i, t in enumerate(self.timeline):
            self.time = t
            # 1. Financing for rollovers since the last candle, charged on positions
            #    open at that moment, at the last known mid price.
            while next_rollover < len(moments) and moments[next_rollover][0] <= t:
                self._charge_financing(*moments[next_rollover])
                next_rollover += 1

            present = [(name, self.bars[name][self.rows[name][i]])
                       for name in self.names if self.rows[name][i] >= 0]
            for name, bar in present:
                self.last_mid[name] = (bar.bid_o + bar.ask_o) / 2

            for name, bar in present:
                if name not in self.active:
                    continue  # conversion pair, not traded
                # 2a. A new hour means the previous H1 candle is complete.
                hour = self.hours[i]
                if last_hour[name] is not None and hour != last_hour[name]:
                    self.strategy.on_h1_close(self, name, last_hour[name])
                last_hour[name] = hour
                self._process_bar(name, t, bar, self.friday_cutoff[i])

            for name, bar in present:
                self.last_mid[name] = (bar.bid_c + bar.ask_c) / 2
        return self

    def _process_bar(self, pair, t, bar, friday_cutoff):
        if not self.open[pair] and not self.pending[pair]:
            return
        if self.config.friday_close and friday_cutoff:
            # 2b. Flatten at the open of the first candle from 16:00 New York Friday.
            # Pending orders survive the weekend but can't fill until the market
            # reopens, or they'd be carried over the weekend after all (proposed).
            for trade in list(self.open[pair]):
                price = bar.bid_o if trade.side == "long" else bar.ask_o
                self._close(trade, t, self._slipped(trade, price), "friday")
            self._end_setup_if_closed(pair)
            return

        # 2c. Legs filled in earlier candles can hit their stop or target.
        for trade in list(self.open[pair]):
            self._check_exit(trade, t, bar, target_allowed=True)
        # 2d. Pending entries. A leg filled in this candle can be stopped in it
        #     too, but can only reach its target from the next candle (§9).
        for order in list(self.pending[pair].values()):
            trade = self._try_fill(order, t, bar)
            if trade:
                self._check_exit(trade, t, bar, target_allowed=False)
        # 2e. Unfilled legs cancel when the position closes (§4).
        self._end_setup_if_closed(pair)

    # ---- fills ----

    def _try_fill(self, order, t, bar):
        """Fill `order` in this candle if price reached it. Returns the Trade or None."""
        long = order.side == "long"
        if order.kind == "market":
            price = bar.ask_o if long else bar.bid_o
        elif long:
            if bar.ask_o <= order.entry:
                price = bar.ask_o          # gapped below the limit: fills at the better open
            elif bar.ask_l <= order.entry:
                price = order.entry
            else:
                return None
        else:
            if bar.bid_o >= order.entry:
                price = bar.bid_o
            elif bar.bid_h >= order.entry:
                price = order.entry
            else:
                return None

        del self.pending[order.pair][order.leg]
        # Price opened at or past the stop (a gap, or a market order after a big
        # move): the stop has been traded through, so cancel instead of filling (§5, proposed).
        if (long and price <= order.stop) or (not long and price >= order.stop):
            self.skipped.append((t, order, "through_stop"))
            return None

        units, risk_gbp, reason = self._size(order.pair, price, order.stop)
        if reason:
            self.skipped.append((t, order, reason))
            return None
        trade = Trade(order.pair, order.setup_id, order.leg, order.side, t, price, units,
                      order.stop, order.target, risk_gbp)
        self.open[order.pair].append(trade)
        self.has_filled[order.pair] = True
        return trade

    def _size(self, pair, price, stop):
        """Units for one leg: 0.5% of the closed balance lost if the stop is hit (§7).

        Returns (units, risk in GBP, skip reason or None).
        """
        cfg = self.config
        loss_per_unit = self._to_gbp(pair, abs(price - stop))
        units = math.floor(self.balance * cfg.risk_per_leg / loss_per_unit)
        if units <= 0:
            return 0, 0.0, "zero_units"
        # Exposure caps count every open leg on both pairs, at current prices.
        notional = self._notional(pair, units) + sum(
            self._notional(t.pair, t.units) for p in self.pairs for t in self.open[p])
        if notional > cfg.max_notional * self.balance:
            return 0, 0.0, "notional_cap"
        if notional * cfg.margin_rate > cfg.max_margin_used * self.balance:
            return 0, 0.0, "margin_cap"
        return units, units * loss_per_unit, None

    # ---- exits ----

    def _check_exit(self, trade, t, bar, target_allowed):
        """Close `trade` if this candle reached its stop or (when allowed) its target."""
        long = trade.side == "long"
        # A long exits by selling at the bid; a short by buying at the ask.
        o, h, l = (bar.bid_o, bar.bid_h, bar.bid_l) if long else (bar.ask_o, bar.ask_h, bar.ask_l)
        sign = 1 if long else -1
        # Gaps first: the open already tells us which level came first.
        if sign * o <= sign * trade.stop:
            self._close(trade, t, self._slipped(trade, o), "stop")        # worse than the stop
            return
        if target_allowed and sign * o >= sign * trade.target:
            self._close(trade, t, o, "target")                           # better than the target
            return
        stop_hit = (l <= trade.stop) if long else (h >= trade.stop)
        target_hit = target_allowed and ((h >= trade.target) if long else (l <= trade.target))
        if stop_hit:
            # Both touched in one candle: we can't tell the order, so assume the
            # stop came first and count it (§9).
            trade.fallback = target_hit
            self._close(trade, t, self._slipped(trade, trade.stop), "stop")
        elif target_hit:
            self._close(trade, t, trade.target, "target")

    def _slipped(self, trade, price):
        """Stops and forced exits pay slippage: a long sells lower, a short buys higher."""
        slip = self.config.slippage_pips[trade.pair] * pip_size(trade.pair)
        return price - slip if trade.side == "long" else price + slip

    def _close(self, trade, t, price, reason):
        sign = 1 if trade.side == "long" else -1
        trade.exit_time, trade.exit_price, trade.exit_reason = t, price, reason
        trade.pnl_gbp = self._to_gbp(trade.pair, sign * (price - trade.entry_price) * trade.units)
        self._book(trade.pnl_gbp)
        self.open[trade.pair].remove(trade)
        self.closed.append(trade)

    def _end_setup_if_closed(self, pair):
        if self.has_filled[pair] and not self.open[pair]:
            self.pending[pair].clear()
            self._finish_setup(pair)

    def _finish_setup(self, pair):
        self.finished.add((pair, self.active[pair]))
        self.active[pair] = None
        self.has_filled[pair] = False

    # ---- money ----

    def _charge_financing(self, moment, days):
        cfg = self.config
        day = moment.tz_convert(NEW_YORK).date()
        for pair in self.pairs:
            for trade in self.open[pair]:
                rate = annual_rate(pair, trade.side, day)
                amount = financing_amount(trade.units, self.last_mid[pair], rate, days)
                gbp = self._to_gbp(pair, apply_mode(amount, cfg.financing_mode))
                trade.financing_gbp += gbp
                self._book(gbp)

    def _to_gbp(self, pair, amount):
        """Convert an amount in the pair's quote currency to GBP at the current GBP_<quote> mid."""
        quote = pair.split("_")[1]
        return amount if quote == "GBP" else amount / self.last_mid[f"GBP_{quote}"]

    def _notional(self, pair, units):
        """Position value in GBP: units of the base currency at the current mid."""
        return self._to_gbp(pair, units * self.last_mid[pair])

    def _book(self, gbp):
        if gbp:
            self.balance += gbp
            self.equity.append((self.time, self.balance))
