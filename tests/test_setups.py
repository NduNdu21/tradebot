"""Tests for src.setups: levels, arming, re-anchoring, freeze at first fill, levels in
force at each bar's open, entry modes, cancels, pair priority, robustness run,
warm-up, and a lookahead test (docs/strategy.md §3-§6).

Most tests run the real engine on M5 candles built from H1 bars. The setup is the
worked example: swing low 1.1000 (bar 3) -> high 1.1100 (bar 8). With a fixed ATR
of 0.0026 and K = 3, a swing needs 0.0078. That's less than the 0.0100 range (so it
arms) but more than the pullback to either leg (so the high isn't confirmed by
reaching the zone).
"""
import random
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.backtest import Config, Engine
from src.setups import FibStrategy, H1Bar, Params, PairSignals, build_h1, fib_levels

START = pd.Timestamp("2024-01-08", tz="UTC")   # a Monday
SPREAD = 0.0002
ATR = 0.0026

BASE = [                                   # mid (open, high, low, close)
    (1.1090, 1.1095, 1.1085, 1.1090),      # 0
    (1.1090, 1.1092, 1.1040, 1.1045),      # 1
    (1.1045, 1.1046, 1.1010, 1.1015),      # 2  confirms swing high 1.1095 (bar 0)
    (1.1015, 1.1018, 1.1000, 1.1005),      # 3  swing low 1.1000
    (1.1005, 1.1030, 1.1004, 1.1028),      # 4
    (1.1028, 1.1055, 1.1026, 1.1052),      # 5
    (1.1052, 1.1080, 1.1050, 1.1078),      # 6  confirms swing low 1.1000: buy candidate
    (1.1078, 1.1090, 1.1076, 1.1088),      # 7  span 4 bars: not armed
    (1.1088, 1.1100, 1.1086, 1.1098),      # 8  span 5, range 0.0100: armed
]
# Exact levels of the worked example (the spec's table rounds them to 4 decimals).
LEG1, LEG2, STOP, TP1, TP3 = 1.10382, 1.10236, 1.0990, 1.10618, 1.11618
QUIET = (1.1080, 1.1085, 1.1075, 1.1080)
DIP_TO_ZONE = (1.1098, 1.1099, 1.1030, 1.1040)   # reaches 61.8% (fills leg 1), not 76.4%
NEW_HIGH = (1.1040, 1.1120, 1.1038, 1.1115)


def bar_start(n):
    return START + pd.Timedelta(hours=n)


class FixedATR:
    """ATR stand-in: `value`, or a per-bar override (bar number = update call count)."""

    def __init__(self, value=ATR, overrides=None):
        self.value, self.overrides, self.n = value, overrides or {}, -1

    def update(self, high, low, close):
        self.n += 1
        return self.overrides.get(self.n, self.value)


def m5_from_h1(rows):
    """12 M5 candles per H1 bar of mid prices: open -> high -> low -> close for a down
    bar, open -> low -> high -> close otherwise. Bid/ask sit SPREAD/2 either side."""
    frames = []
    for n, (o, h, l, c) in enumerate(rows):
        first, second = (h, l) if c < o else (l, h)
        path = np.interp(range(13), [0, 4, 8, 12], [o, first, second, c])
        mid = pd.DataFrame({"o": path[:-1], "c": path[1:]})
        mid["h"], mid["l"] = mid.max(axis=1), mid.min(axis=1)
        df = pd.DataFrame(index=pd.date_range(bar_start(n), periods=12, freq="5min"))
        for f in "ohlc":
            df[f"bid_{f}"] = mid[f].values - SPREAD / 2
            df[f"ask_{f}"] = mid[f].values + SPREAD / 2
        df["volume"] = 1
        frames.append(df)
    return pd.concat(frames)


def run(rows, atr=None, **params):
    """Run engine + strategy; the last row only exists so the bar before it gets its H1 close."""
    m5 = m5_from_h1(rows)
    flat = pd.DataFrame(1.25, index=m5.index, columns=m5.columns)
    data = {"EUR_USD": m5, "GBP_USD": flat}
    strategy = FibStrategy(build_h1(data), Params(warmup=0, **params), atr_factory=lambda: atr or FixedATR())
    engine = Engine(data, strategy, Config(financing_mode="off")).run()
    return engine, strategy


def actions(strategy, name):
    return [d for d in strategy.log if d[2] == name]


def pending(engine):
    return {o.leg: o for o in engine.pending_orders("EUR_USD")}


class LevelsTest(unittest.TestCase):
    def test_worked_example(self):
        lv = fib_levels("long", 1.1000, 1.1100)
        for name, price in dict(leg1=1.1038, leg2=1.1024, stop=1.0990,
                                tp1=1.1062, tp2=1.1100, tp3=1.1162).items():
            self.assertAlmostEqual(getattr(lv, name), price, places=4, msg=name)

    def test_sell_mirrors(self):
        lv = fib_levels("short", 1.1100, 1.1000)
        for name, price in dict(leg1=1.1062, leg2=1.1076, stop=1.1110,
                                tp1=1.1038, tp2=1.1000, tp3=1.0938).items():
            self.assertAlmostEqual(getattr(lv, name), price, places=4, msg=name)

    def test_r_targets_from_each_legs_entry(self):
        lv = fib_levels("long", 1.1000, 1.1100)
        self.assertAlmostEqual(lv.target("2r", lv.leg1), LEG1 + 2 * (LEG1 - STOP))
        self.assertAlmostEqual(lv.target("1r", lv.leg2), LEG2 + (LEG2 - STOP))
        self.assertAlmostEqual(lv.target("tp1", lv.leg2), TP1)


class ArmingTest(unittest.TestCase):
    def test_not_armed_before_min_bars_span(self):
        engine, strategy = run(BASE[:8] + [QUIET])
        self.assertEqual(actions(strategy, "arm"), [])
        self.assertEqual(pending(engine), {})

    def test_armed_with_worked_example_orders(self):
        engine, strategy = run(BASE + [QUIET])
        self.assertEqual(len(actions(strategy, "arm")), 1)
        orders = pending(engine)
        self.assertAlmostEqual(orders[1].entry, LEG1, places=6)
        self.assertAlmostEqual(orders[2].entry, LEG2, places=6)
        for o in orders.values():
            self.assertAlmostEqual(o.stop, 1.0990, places=6)
            self.assertAlmostEqual(o.target, 1.1100, places=6)

    def test_not_armed_when_range_below_k_atr(self):
        # ATR jumps to 0.0040 at bar 8: K x ATR = 0.0120 > range 0.0100.
        engine, strategy = run(BASE + [QUIET], atr=FixedATR(overrides={8: 0.0040}))
        self.assertEqual(actions(strategy, "arm"), [])


class ModeAndTargetTest(unittest.TestCase):
    def test_modes_place_the_right_legs(self):
        for mode, legs in {"a": {1, 2}, "b": {2}, "c": {1}, "d": {2}}.items():
            with self.subTest(mode=mode):
                engine, _ = run(BASE + [QUIET], mode=mode)
                self.assertEqual(set(pending(engine)), legs)

    def test_target_variants(self):
        expected = {"tp1": (TP1, TP1), "tp3": (TP3, TP3),
                    "1r": (LEG1 + (LEG1 - STOP), LEG2 + (LEG2 - STOP)),
                    "3r": (LEG1 + 3 * (LEG1 - STOP), LEG2 + 3 * (LEG2 - STOP))}
        for target, (t1, t2) in expected.items():
            with self.subTest(target=target):
                orders = pending(run(BASE + [QUIET], target=target)[0])
                self.assertAlmostEqual(orders[1].target, t1, places=6)
                self.assertAlmostEqual(orders[2].target, t2, places=6)


class ReanchorTest(unittest.TestCase):
    def test_new_high_before_fill_reprices_and_never_cancels(self):
        engine, strategy = run(BASE + [(1.1098, 1.1120, 1.1096, 1.1118), QUIET])
        orders = pending(engine)
        self.assertAlmostEqual(orders[1].entry, 1.1000 + 0.382 * 0.0120, places=6)
        self.assertAlmostEqual(orders[1].stop, 1.1000 - 0.0012, places=6)
        self.assertEqual(actions(strategy, "cancel"), [])
        self.assertEqual(engine.active["EUR_USD"], ("long", bar_start(3)))


class FreezeAtFirstFillTest(unittest.TestCase):
    """§3: the geometry freezes at the first fill of any leg, never at a zone touch."""

    def test_new_high_after_a_fill_leaves_other_leg_unchanged(self):
        # Leg 1 fills on the dip; then a new high 1.1120. TP3 (1.1162) keeps the trade open.
        engine, strategy = run(BASE + [DIP_TO_ZONE, NEW_HIGH, QUIET], target="tp3")
        [trade] = engine.open_trades("EUR_USD")
        self.assertEqual(trade.leg, 1)
        leg2 = pending(engine)[2]
        self.assertAlmostEqual(leg2.entry, LEG2, places=6)
        self.assertAlmostEqual(leg2.stop, STOP, places=6)
        self.assertAlmostEqual(leg2.target, TP3, places=6)
        self.assertEqual(len(actions(strategy, "freeze")), 1)

    def test_zone_touch_without_fill_is_forgotten(self):
        # Mode d: only leg 2 (1.1024). The dip touches the zone at 1.1030 but leg 2
        # doesn't fill, so the new high re-anchors it to the 1.1000 -> 1.1120 swing.
        engine, strategy = run(BASE + [DIP_TO_ZONE, NEW_HIGH, QUIET], mode="d")
        self.assertEqual(engine.open_trades("EUR_USD"), [])
        leg2 = pending(engine)[2]
        self.assertAlmostEqual(leg2.entry, 1.1000 + 0.236 * 0.0120, places=6)
        self.assertAlmostEqual(leg2.stop, 1.0988, places=6)
        self.assertEqual(actions(strategy, "freeze"), [])


class LevelsInForceAtOpenTest(unittest.TestCase):
    """Timing rule: each H1 bar is judged against the levels in force at its open;
    re-anchoring from that bar's own high takes effect after it closes."""

    # Bar 9 first makes a new high 1.1150, then dips to 1.1050 and closes 1.1060.
    # Old leg 1 = 1.1038 (not reached); re-anchored leg 1 = 1.1000 + 0.382 x 0.0150 = 1.1057.
    OUTSIDE = (1.1098, 1.1150, 1.1050, 1.1060)
    AFTER = (1.1060, 1.1070, 1.1059, 1.1065)

    def test_bar_cannot_fill_at_levels_from_its_own_high(self):
        engine, strategy = run(BASE + [self.OUTSIDE, self.AFTER], mode="c")
        self.assertEqual(engine.open_trades("EUR_USD") + engine.closed, [])
        self.assertAlmostEqual(pending(engine)[1].entry, 1.1000 + 0.382 * 0.0150, places=6)

    def test_mode_b_confirmation_uses_levels_at_open(self):
        # Against the re-anchored levels bar 9 would confirm (low 1.1050 <= 1.1057 < close 1.1060).
        engine, strategy = run(BASE + [self.OUTSIDE, self.AFTER], mode="b")
        self.assertEqual(actions(strategy, "confirm"), [])

    def test_mode_b_confirmation_enters_at_next_open(self):
        confirm = (1.1098, 1.1099, 1.1030, 1.1045)   # into the zone, closes back above 1.1038
        engine, strategy = run(BASE + [confirm, (1.1045, 1.1050, 1.1040, 1.1048), QUIET], mode="b")
        self.assertEqual(len(actions(strategy, "confirm")), 1)
        [trade] = engine.open_trades("EUR_USD")
        self.assertEqual((trade.leg, trade.entry_time), (1, bar_start(10)))
        self.assertAlmostEqual(trade.entry_price, 1.1045 + SPREAD / 2)   # the ask at the open
        self.assertAlmostEqual(trade.stop, 1.0990, places=6)
        self.assertAlmostEqual(trade.target, 1.1100, places=6)
        self.assertEqual(len(actions(strategy, "freeze")), 1)


class CancelTest(unittest.TestCase):
    def test_time_limit_counts_from_arming_and_ignores_reanchoring(self):
        # Armed at bar 8. A new high at bar 30 re-anchors; the 96-bar limit still ends at bar 104.
        rows = BASE + [QUIET] * 96 + [QUIET]
        rows[30] = (1.1080, 1.1105, 1.1078, 1.1100)
        engine, strategy = run(rows)
        [cancel] = actions(strategy, "cancel")
        self.assertEqual(cancel[-1], "time")
        self.assertEqual(cancel[0], bar_start(105))      # the H1 close of bar 104
        self.assertEqual(len([d for d in strategy.log if d[2] == "limit"]), 4)   # placed, then re-priced
        self.assertIsNone(engine.active["EUR_USD"])

    def test_newer_swing_low_replaces_pending_buy(self):
        # ATR drops to 0.0010 (K x ATR = 0.0030). Bar 9 dips to 1.1060: that confirms the
        # 1.1100 high, which must NOT cancel the buy. Bar 10 rallies 0.0035 from 1.1060:
        # a newer swing low, which replaces it.
        atr = FixedATR(overrides={9: 0.0010, 10: 0.0010})
        engine, strategy = run(BASE + [(1.1098, 1.1099, 1.1060, 1.1065), (1.1065, 1.1095, 1.1064, 1.1090), QUIET],
                               atr=atr)
        pivots = [(d[0], d[3]) for d in actions(strategy, "pivot")]
        self.assertIn((bar_start(10), "high"), pivots)
        [cancel] = actions(strategy, "cancel")
        self.assertEqual((cancel[0], cancel[3], cancel[4]), (bar_start(11), ("long", bar_start(3)), "replaced"))

    def test_stop_traded_through_cancels(self):
        # Without fills (fake engine), a bar trading below 1.0990 cancels the pending setup.
        fake, sig, log = FakeEngine(), None, []
        sig = PairSignals("EUR_USD", Params(warmup=0), log)
        feed_signals(sig, fake, BASE + [(1.1098, 1.1099, 1.0985, 1.0995)])
        self.assertEqual([d[-1] for d in log if d[2] == "cancel"], ["stop"])
        self.assertIsNone(fake.active["EUR_USD"])


class FakeEngine:
    """Records orders and never fills anything."""

    def __init__(self):
        self.time = None
        self.active = {"EUR_USD": None}
        self.orders = {}
        self.calls = []

    def place_limit(self, pair, setup_id, leg, side, entry, stop, target):
        self.active[pair] = setup_id
        self.orders[leg] = SimpleNamespace(leg=leg, setup_id=setup_id, side=side, entry=entry)
        self.calls.append(("limit", setup_id, leg, entry))

    def place_market(self, pair, setup_id, leg, side, stop, target=None, target_r=None):
        self.active[pair] = setup_id
        self.calls.append(("market", setup_id, leg))

    def cancel_setup(self, pair, setup_id):
        if self.active[pair] == setup_id:
            self.active[pair] = None
            self.orders.clear()

    def open_trades(self, pair):
        return []

    def pending_orders(self, pair):
        return list(self.orders.values())


def feed_signals(sig, fake, rows, first=0):
    for n, row in enumerate(rows, start=first):
        fake.time = bar_start(n + 1)   # decisions happen at the next bar's open
        sig.on_bar(fake, H1Bar(bar_start(n), *row), ATR)


class PairPriorityTest(unittest.TestCase):
    def test_buy_keeps_the_pair_while_a_sell_arms_then_sell_takes_over(self):
        # After the buy arms (bar 8), price drifts down to 1.1020 by bar 14. That
        # confirms the 1.1100 high and arms a sell (origin 1.1100, span 6 bars, range 0.0080).
        fake, log = FakeEngine(), []
        sig = PairSignals("EUR_USD", Params(warmup=0), log)
        drift = [(1.1098, 1.1099, 1.1080, 1.1082), (1.1082, 1.1083, 1.1070, 1.1072),
                 (1.1072, 1.1073, 1.1060, 1.1062), (1.1062, 1.1063, 1.1050, 1.1052),
                 (1.1052, 1.1053, 1.1040, 1.1042), (1.1042, 1.1043, 1.1020, 1.1025)]
        feed_signals(sig, fake, BASE + drift)
        buy, sell = ("long", bar_start(3)), ("short", bar_start(8))
        self.assertIn(sell, [d[3] for d in log if d[2] == "arm"])
        self.assertEqual({c[1] for c in fake.calls}, {buy})          # only the buy has orders
        self.assertEqual(fake.active["EUR_USD"], buy)

        fake.active["EUR_USD"] = None                                # the buy's position closes
        feed_signals(sig, fake, [(1.1025, 1.1030, 1.1021, 1.1028)], first=len(BASE) + len(drift))
        self.assertEqual(fake.active["EUR_USD"], sell)
        sell_leg1 = [c for c in fake.calls if c[1] == sell and c[2] == 1][0]
        self.assertAlmostEqual(sell_leg1[3], 1.1100 - 0.382 * 0.0080, places=6)


class RobustnessRunTest(unittest.TestCase):
    """anchoring="confirmed": trade only once both ends of the swing are confirmed."""

    def test_no_setup_until_the_extreme_is_confirmed(self):
        engine, strategy = run(BASE + [QUIET], anchoring="confirmed")
        self.assertEqual(pending(engine), {})

    def test_legs_already_passed_at_confirmation_are_skipped(self):
        # Bar 9 falls to 1.1020 (0.0080 from the high): 1.1100 is confirmed. It closes at
        # 1.1030, below leg 1 (1.1038) but above leg 2 (1.1024): only leg 2 is placed.
        engine, strategy = run(BASE + [(1.1098, 1.1099, 1.1020, 1.1030), (1.1030, 1.1040, 1.1028, 1.1035)],
                               anchoring="confirmed")
        orders = pending(engine)
        self.assertEqual(set(orders), {2})
        self.assertAlmostEqual(orders[2].entry, LEG2, places=6)


class WarmupTest(unittest.TestCase):
    def test_first_200_bars_only_feed_atr(self):
        rows = (BASE + [QUIET] * 40) * 4          # 196 bars -> 195 H1 closes
        m5 = m5_from_h1(rows)
        data = {"EUR_USD": m5, "GBP_USD": pd.DataFrame(1.25, index=m5.index, columns=m5.columns)}
        strategy = FibStrategy(build_h1(data))
        Engine(data, strategy, Config(financing_mode="off")).run()
        self.assertEqual(strategy.signals["EUR_USD"].i, -1)
        self.assertEqual(strategy.log, [])
        self.assertIsNotNone(strategy.atr["EUR_USD"].value)

    def test_signals_start_after_warmup(self):
        _, strategy = run(BASE + [QUIET] * 3, atr=FixedATR())   # 11 H1 closes, warmup 0
        self.assertEqual(strategy.signals["EUR_USD"].i, 10)


def random_walk(days, seed):
    """Synthetic EUR_USD-like M5 bid/ask candles (continuous, no weekends)."""
    rng = np.random.default_rng(seed)
    n = days * 288
    close = 1.10 + np.cumsum(rng.normal(0, 0.0003, n))
    open_ = np.concatenate([[1.10], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0001, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0001, n))
    df = pd.DataFrame({"bid_o": open_, "bid_h": high, "bid_l": low, "bid_c": close},
                      index=pd.date_range(START, periods=n, freq="5min"))
    for f in "ohlc":
        df[f"ask_{f}"] = df[f"bid_{f}"] + 0.0001
    df["volume"] = 1
    return df


class FutureGuard(dict):
    """H1 bars that refuse to be read past `now`, the hour whose close is being handled."""
    now = None

    def __getitem__(self, hour):
        if hour > self.now:
            raise AssertionError(f"read the {hour} bar at the close of {self.now}")
        return super().__getitem__(hour)

    def get(self, hour, default=None):
        return self[hour] if hour in self else default


class GuardedStrategy(FibStrategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bars = {pair: FutureGuard(bars) for pair, bars in self.bars.items()}

    def on_h1_close(self, engine, pair, hour):
        self.bars[pair].now = hour
        super().on_h1_close(engine, pair, hour)


PARAM_SETS = (Params(), Params(mode="b", target="2r"), Params(anchoring="confirmed", mode="c"))


class LookaheadTest(unittest.TestCase):
    """No decision may depend on data from after it was made."""

    def run_on(self, m5, params, strategy_class=FibStrategy):
        data = {"EUR_USD": m5, "GBP_USD": pd.DataFrame(1.25, index=m5.index, columns=m5.columns)}
        strategy = strategy_class(build_h1(data), params)
        engine = Engine(data, strategy, Config(financing_mode="off")).run()
        return strategy.log, engine

    def test_decisions_before_each_cut_are_identical(self):
        # Truncate at random points; each run builds its H1 bars from its own data.
        # Most cuts fall 5 minutes into an hour, so a peek at the next bar would see
        # a one-candle stub instead of the full bar; the rest are any M5 time.
        m5 = random_walk(days=30, seed=7)
        rng = random.Random(11)
        hours = rng.sample(range(300, len(m5) // 12), 20)
        cuts = [h * 12 + 1 for h in hours] + rng.sample(range(300 * 12, len(m5)), 5)
        for params in PARAM_SETS:
            full, engine = self.run_on(m5, params)
            with self.subTest(params=params):
                self.assertGreater(len([d for d in full if d[2] == "arm"]), 3)   # the test has teeth
                self.assertGreater(len(engine.closed), 0)
                for cut in cuts:
                    cut_time = m5.index[cut]
                    truncated, _ = self.run_on(m5[m5.index < cut_time], params)
                    self.assertEqual(truncated, [d for d in full if d[0] < cut_time], f"cut at {cut_time}")

    def test_strategy_never_reads_a_later_h1_bar(self):
        m5 = random_walk(days=30, seed=7)
        for params in PARAM_SETS:
            with self.subTest(params=params):
                self.run_on(m5, params, GuardedStrategy)   # FutureGuard raises on any peek


if __name__ == "__main__":
    unittest.main()
