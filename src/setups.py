"""Fibonacci setups: arming, re-anchoring, freeze, entry modes, cancels (docs/strategy.md §3-§6).

FibStrategy plugs into src.backtest.Engine through on_h1_close. Every decision
here is bot-side and made at an H1 close; fills, stops and targets happen in the
engine on M5 (strategy.md "Timing").

At each H1 close, for each pair:
  1. sync with the engine: has the setup holding the pair filled or finished?
  2. judge the bar that just closed against the levels in force at its open:
     mode-b confirmation, stop traded through, time limit;
  3. feed the bar to the zigzag. A new swing low replaces a pending buy setup
     (a new swing high, a pending sell) and starts a new buy (sell) candidate;
  4. update running extremes with the bar: re-anchoring takes effect only now,
     after the bar has closed;
  5. arm candidates that qualify;
  6. give a free pair to an armed candidate, or re-price the holder's orders if
     it has no fill yet. After the first fill the geometry is frozen.

A "candidate" is one possible setup: a side and a confirmed origin. Several can
be alive at once (a buy and a sell), but only one holds the pair in the engine.
"""
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Optional

from src.data import to_h1
from src.swings import Point, WilderATR, ZigZag

H1Bar = namedtuple("H1Bar", "time o h l c")   # mid prices

# Fractions of the swing, measured from the origin (0) toward the extreme (1).
FIB = {"leg1": 0.382, "leg2": 0.236, "stop": -0.10, "tp1": 0.618, "tp2": 1.0, "tp3": 1.618}

# How each leg enters, per mode (§4): resting limit, confirmation candle, or not traded.
MODES = {"a": ("limit", "limit"), "b": ("confirm", "limit"), "c": ("limit", None), "d": (None, "limit")}
TARGETS = ("tp1", "tp2", "tp3", "1r", "2r", "3r")


@dataclass
class Params:
    k: float = 3                 # swing size in ATRs (§2)
    min_bars: int = 5            # origin -> extreme span needed to arm (§3)
    mode: str = "a"              # entry mode (§4)
    target: str = "tp2"          # exit variant (§6)
    time_limit: int = 96         # H1 bars from arming without a fill (§5)
    anchoring: str = "running"   # "running" (main run) or "confirmed" (robustness run)
    warmup: int = 200            # H1 bars discarded before any swing logic (§1)
    atr_period: int = 14

    def __post_init__(self):
        if self.mode not in MODES or self.target not in TARGETS or self.anchoring not in ("running", "confirmed"):
            raise ValueError(f"bad params: {self}")


@dataclass(frozen=True)
class Levels:
    """Prices for one setup. For a sell the origin is a high, so every level mirrors."""
    side: str
    leg1: float
    leg2: float
    stop: float
    tp1: float
    tp2: float
    tp3: float

    def entry(self, leg):
        return self.leg1 if leg == 1 else self.leg2

    def target(self, variant, entry):
        """Exit price for `variant`: a Fibonacci level, or n x R from this leg's own entry."""
        if variant.startswith("tp"):
            return getattr(self, variant)
        sign = 1 if self.side == "long" else -1
        return entry + sign * int(variant[0]) * abs(entry - self.stop)


def fib_levels(side, origin, extreme):
    """Levels for a swing from `origin` to `extreme` (works for both sides).

    Worked example (buy): origin 1.1000, extreme 1.1100 -> leg 1 1.1038, leg 2 1.1024,
    stop 1.0990, TP1 1.1062, TP2 1.1100, TP3 1.1162.
    """
    swing = extreme - origin          # negative for a sell
    return Levels(side, **{name: origin + f * swing for name, f in FIB.items()})


@dataclass
class Candidate:
    side: str                    # "long" (origin is a swing low) or "short"
    origin: object               # Pivot
    extreme: Point               # running extreme since the origin (fixed when anchoring="confirmed")
    fixed: bool = False
    armed_at: Optional[int] = None
    skip_legs: set = field(default_factory=set)
    placed: Optional[Levels] = None   # levels of the orders now resting in the engine
    frozen: Optional[Levels] = None   # geometry at the first fill; never changes after
    sent: bool = False                # the engine has seen an order for this setup
    market_sent: bool = False         # mode-b leg 1 goes in at this next open
    done: bool = False

    @property
    def id(self):
        return (self.side, self.origin.time)

    def levels(self):
        return self.frozen or fib_levels(self.side, self.origin.price, self.extreme.price)


class PairSignals:
    """All setup logic for one pair. Fed one completed H1 bar (mid) and its ATR at a time."""

    def __init__(self, pair, params, log):
        self.pair = pair
        self.p = params
        self.log = log
        self.zigzag = ZigZag(params.k)
        self.i = -1                                    # bar number (after warm-up)
        self.latest = {"long": None, "short": None}    # newest candidate per side
        self.holder = None                             # candidate holding the pair

    def live(self):
        found = [c for c in self.latest.values() if c and not c.done]
        if self.holder and self.holder not in found:
            found.append(self.holder)   # an older candidate still managing its position
        return found

    def on_bar(self, engine, bar, atr):
        self.i += 1
        self._sync(engine)
        self._judge_bar(engine, bar)
        pivot = self.zigzag.update(self.i, bar.time, bar.h, bar.l, atr)
        if pivot:
            self._on_pivot(engine, pivot)
        self._update_extremes(bar)
        self._arm(engine, bar, atr)
        self._manage_orders(engine)

    # ---- 1. engine state ----

    def _sync(self, engine):
        h = self.holder
        if h is None:
            return
        if h.sent and engine.active[self.pair] != h.id:
            # Position closed, or every leg was skipped: each setup trades at most once.
            h.done = True
            self.holder = None
            self._log(engine, "finished", h.id)
            return
        if h.frozen is None and any(t.setup_id == h.id for t in engine.open_trades(self.pair)):
            h.frozen = h.placed   # the first fill happened at the levels then in force
            self._log(engine, "freeze", h.id, h.frozen)
        h.market_sent = False     # the mode-b entry was filled or skipped at this open

    # ---- 2. judge the closed bar against the levels in force at its open ----

    def _judge_bar(self, engine, bar):
        for c in self.live():
            if c.armed_at is None:
                continue
            lv = c.levels()
            if c.frozen is None:
                # §5 cancels apply only while no leg has filled.
                if _beyond(c.side, bar, lv.stop):
                    self._cancel(engine, c, "stop")
                    continue
                if self.i - c.armed_at >= self.p.time_limit:
                    self._cancel(engine, c, "time")
                    continue
            if c is self.holder and MODES[self.p.mode][0] == "confirm" and self._confirms(c, lv, bar, engine):
                self._enter_leg1_at_market(engine, c, lv)

    def _confirms(self, c, lv, bar, engine):
        """Mode b (§4): low enters the zone, stays above the stop, closes back above leg 1's level."""
        if any(t.setup_id == c.id and t.leg == 1 for t in engine.open_trades(self.pair)):
            return False
        if c.side == "long":
            return bar.l <= lv.leg1 and bar.l > lv.stop and bar.c > lv.leg1
        return bar.h >= lv.leg1 and bar.h < lv.stop and bar.c < lv.leg1

    def _enter_leg1_at_market(self, engine, c, lv):
        if self.p.target.startswith("tp"):
            engine.place_market(self.pair, c.id, 1, c.side, lv.stop, target=lv.target(self.p.target, None))
        else:
            engine.place_market(self.pair, c.id, 1, c.side, lv.stop, target_r=int(self.p.target[0]))
        # Keep leg 2 at the same levels until the market entry is through, so both
        # legs share one stop and one geometry.
        c.placed, c.sent, c.market_sent = lv, True, True
        self._log(engine, "confirm", c.id, lv)

    # ---- 3. new swings ----

    def _on_pivot(self, engine, pivot):
        # A new swing low is a newer origin for buys: it replaces a pending buy (§5 rule 2).
        side = "long" if pivot.kind == "low" else "short"
        old = self.latest[side]
        if old and not old.done and old.frozen is None:
            if old.armed_at is None:
                old.done = True   # never armed, so never a setup: just dropped
            else:
                self._cancel(engine, old, "replaced")
        if self.p.anchoring == "running":
            # Origin confirmed; the extreme runs from the new leg's extreme so far.
            self.latest[side] = Candidate(side, pivot, self.zigzag.extreme)
        elif len(self.zigzag.pivots) >= 2:
            # Robustness run: the pivot completes a swing from the previous pivot,
            # both ends confirmed. A swing high completes a buy swing, and vice versa.
            prev = self.zigzag.pivots[-2]
            swing_side = "long" if pivot.kind == "high" else "short"
            self.latest[swing_side] = Candidate(swing_side, prev, Point(pivot.price, pivot.index, pivot.time),
                                                fixed=True)
        self._log(engine, "pivot", pivot.kind, pivot.price, pivot.time)

    # ---- 4. re-anchoring ----

    def _update_extremes(self, bar):
        for c in self.live():
            if c.fixed:
                continue
            if c.side == "long" and bar.h > c.extreme.price:
                c.extreme = Point(bar.h, self.i, bar.time)
            elif c.side == "short" and bar.l < c.extreme.price:
                c.extreme = Point(bar.l, self.i, bar.time)

    # ---- 5. arming ----

    def _arm(self, engine, bar, atr):
        for c in self.live():
            if c.armed_at is not None:
                continue
            swing = abs(c.extreme.price - c.origin.price)
            span = c.extreme.index - c.origin.index
            if swing >= self.p.k * atr and span >= self.p.min_bars:
                c.armed_at = self.i   # stays armed even if ATR later grows (proposed)
                if c.fixed:
                    # Robustness run: skip legs price has already passed at confirmation.
                    lv = c.levels()
                    for leg in (1, 2):
                        if _at_or_past(c.side, bar.c, lv.entry(leg)):
                            c.skip_legs.add(leg)
                self._log(engine, "arm", c.id, c.levels(), tuple(sorted(c.skip_legs)))

    # ---- 6. orders ----

    def _manage_orders(self, engine):
        h = self.holder
        if h is None:
            armed = [c for c in self.live() if c.armed_at is not None]
            if armed:
                # If several wait for a free pair, the newest origin goes first (proposed).
                self.holder = max(armed, key=lambda c: c.origin.time)
                self._place(engine, self.holder, reprice=False)
        elif h.frozen is None and not h.market_sent and h.levels() != h.placed:
            self._place(engine, h, reprice=True)   # re-anchor before any fill

    def _place(self, engine, c, reprice):
        lv = c.levels()
        resting = {o.leg for o in engine.pending_orders(self.pair)} if c.sent else set()
        legs = [leg for leg, how in zip((1, 2), MODES[self.p.mode]) if how == "limit" and leg not in c.skip_legs]
        if reprice:
            legs = [leg for leg in legs if leg in resting]   # a leg the engine skipped stays skipped (proposed)
        for leg in legs:
            entry = lv.entry(leg)
            engine.place_limit(self.pair, c.id, leg, c.side, entry, lv.stop, lv.target(self.p.target, entry))
            self._log(engine, "limit", c.id, leg, entry, lv.stop, lv.target(self.p.target, entry))
        c.placed = lv
        c.sent = c.sent or bool(legs)
        if not c.sent and MODES[self.p.mode][0] != "confirm":
            # Nothing left to trade (robustness run, every leg already passed).
            c.done = True
            self.holder = None
            self._log(engine, "nothing_to_trade", c.id)

    def _cancel(self, engine, c, reason):
        c.done = True
        if c is self.holder:
            engine.cancel_setup(self.pair, c.id)
            self.holder = None
        self._log(engine, "cancel", c.id, reason)

    def _log(self, engine, action, *details):
        self.log.append((engine.time, self.pair, action) + details)


def _at_or_past(side, price, level):
    """Is `price` at or past `level` in the losing direction (below for a buy, above for a sell)?"""
    return price <= level if side == "long" else price >= level


def _beyond(side, bar, level):
    """Has the bar traded at or past `level`? Checks the low for a buy, the high for a sell."""
    return _at_or_past(side, bar.l if side == "long" else bar.h, level)


def build_h1(data, pairs=("EUR_USD", "GBP_JPY")):
    """H1 mid bars per pair, from the engine's M5 data."""
    return {pair: to_h1(data[pair]) for pair in pairs if pair in data}


class FibStrategy:
    """The engine's strategy object (see src.backtest.Engine)."""

    def __init__(self, h1, params=None, atr_factory=None):
        """
        h1:          {pair: H1 DataFrame from src.data.to_h1}; only the bar just
                     completed is ever read.
        atr_factory: makes one ATR per pair (default WilderATR(params.atr_period)).
        """
        self.params = params or Params()
        make_atr = atr_factory or (lambda: WilderATR(self.params.atr_period))
        self.bars = {pair: {r.Index: H1Bar(r.Index, r.mid_o, r.mid_h, r.mid_l, r.mid_c)
                            for r in df.itertuples()} for pair, df in h1.items()}
        self.atr = {pair: make_atr() for pair in h1}
        self.seen = {pair: 0 for pair in h1}
        self.log = []   # every decision: (time decided, pair, action, details...)
        self.signals = {pair: PairSignals(pair, self.params, self.log) for pair in h1}

    def on_h1_close(self, engine, pair, hour):
        bar = self.bars[pair][hour]
        atr = self.atr[pair].update(bar.h, bar.l, bar.c)
        self.seen[pair] += 1
        if self.seen[pair] <= self.params.warmup or atr is None:
            return   # warm-up: ATR only, no swings or setups yet (§1)
        self.signals[pair].on_bar(engine, bar, atr)
