"""Tests for src.backtest on synthetic M5 candles: fills, exits, same-candle rules,
sizing, exposure caps, Friday close and financing.

Prices follow the worked example in docs/strategy.md: swing 1.1000 -> 1.1100,
leg 1 at 1.1038, leg 2 at 1.1024, stop 1.0990, TP2 1.1100.
"""
import math
import unittest

import pandas as pd

from src.backtest import Config, Engine

SPREAD = 0.0002            # EUR_USD ask = bid + 2 pips
GBPUSD = 1.25              # flat conversion rate
START = "2024-01-09 10:00"  # a Tuesday, far from the 22:00 UTC rollover
ENTRY1, ENTRY2, STOP, TP2 = 1.1038, 1.1024, 1.0990, 1.1100
SLIP = 0.00005             # 0.5 pip EUR_USD


def candles(rows, start=START, spread=SPREAD):
    """M5 frame from bid (open, high, low, close) rows; ask = bid + spread."""
    start = pd.Timestamp(start)
    if start.tz is None:
        start = start.tz_localize("UTC")
    index = pd.date_range(start, periods=len(rows), freq="5min")
    df = pd.DataFrame(rows, index=index, columns=["bid_o", "bid_h", "bid_l", "bid_c"])
    for f in ("o", "h", "l", "c"):
        df[f"ask_{f}"] = df[f"bid_{f}"] + spread
    return df


def flat(price, like):
    """Unchanging candles (zero spread) on the same times as `like`."""
    return candles([(price,) * 4] * len(like), start=like.index[0], spread=0.0)


def eurusd_engine(rows, config=None, strategy=None, start=START):
    eu = candles(rows, start=start)
    return Engine({"EUR_USD": eu, "GBP_USD": flat(GBPUSD, eu)}, strategy, config)


def no_financing(**kwargs):
    return Config(financing_mode="off", **kwargs)


QUIET = (1.1060, 1.1065, 1.1055, 1.1060)   # nowhere near entry, stop or target


class LimitFillTest(unittest.TestCase):
    def test_long_limit_fills_when_ask_touches_entry(self):
        # bid low 1.1036 -> ask low 1.1038 = entry.
        e = eurusd_engine([QUIET, (1.1050, 1.1052, 1.1036, 1.1045)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        [trade] = e.open_trades("EUR_USD")
        self.assertEqual(trade.entry_price, ENTRY1)
        self.assertEqual(trade.entry_time, e.timeline[1])

    def test_bid_touching_entry_is_not_enough(self):
        # bid low 1.1038 but ask low 1.1040: a buy limit at 1.1038 isn't reached.
        e = eurusd_engine([(1.1050, 1.1052, 1.1038, 1.1045)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD"), [])

    def test_gap_below_limit_fills_at_the_better_open(self):
        e = eurusd_engine([QUIET, (1.1028, 1.1030, 1.1026, 1.1028)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertAlmostEqual(e.open_trades("EUR_USD")[0].entry_price, 1.1030)

    def test_short_limit_fills_on_the_bid(self):
        # Mirror: swing 1.1100 -> 1.1000, sell limit at 1.1062, stop 1.1110, target 1.1000.
        e = eurusd_engine([(1.1050, 1.1062, 1.1048, 1.1055)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "short", 1.1062, 1.1110, 1.1000)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD")[0].entry_price, 1.1062)

    def test_gap_through_stop_cancels_instead_of_filling(self):
        e = eurusd_engine([QUIET, (1.0980, 1.0985, 1.0975, 1.0980)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD"), [])
        self.assertEqual(e.skipped[0][2], "through_stop")

    def test_market_order_fills_at_next_open_ask_without_slippage(self):
        e = eurusd_engine([QUIET, QUIET], no_financing())
        e.place_market("EUR_USD", "s1", 1, "long", STOP, TP2)
        e.run()
        self.assertAlmostEqual(e.open_trades("EUR_USD")[0].entry_price, 1.1060 + SPREAD)


class ExitTest(unittest.TestCase):
    def filled_long(self, *later_rows, entry=ENTRY1, target=TP2):
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        e = eurusd_engine([fill, *later_rows], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", entry, STOP, target)
        return e.run()

    def test_stop_triggers_on_bid_with_slippage(self):
        e = self.filled_long((1.1000, 1.1002, 1.0990, 1.0995))
        [trade] = e.closed
        self.assertEqual(trade.exit_reason, "stop")
        self.assertAlmostEqual(trade.exit_price, STOP - SLIP)
        self.assertFalse(trade.fallback)

    def test_gap_through_stop_fills_at_worse_open(self):
        e = self.filled_long((1.0980, 1.0985, 1.0975, 1.0980))
        self.assertAlmostEqual(e.closed[0].exit_price, 1.0980 - SLIP)

    def test_target_fills_at_target_without_slippage(self):
        e = self.filled_long((1.1090, 1.1100, 1.1088, 1.1095))
        self.assertEqual(e.closed[0].exit_reason, "target")
        self.assertEqual(e.closed[0].exit_price, TP2)

    def test_gap_past_target_fills_at_better_open(self):
        e = self.filled_long((1.1110, 1.1115, 1.1105, 1.1110))
        self.assertAlmostEqual(e.closed[0].exit_price, 1.1110)

    def test_short_stop_triggers_on_ask(self):
        # Ask high 1.1110 = stop; bid high is only 1.1108.
        e = eurusd_engine([(1.1050, 1.1062, 1.1048, 1.1055), (1.1100, 1.1108, 1.1095, 1.1100)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "short", 1.1062, 1.1110, 1.1000)
        e.run()
        self.assertEqual(e.closed[0].exit_reason, "stop")
        self.assertAlmostEqual(e.closed[0].exit_price, 1.1110 + SLIP)

    def test_pnl_converts_to_gbp(self):
        e = self.filled_long((1.1090, 1.1100, 1.1088, 1.1095))
        trade = e.closed[0]
        expected = (TP2 - ENTRY1) * trade.units / GBPUSD
        self.assertAlmostEqual(trade.pnl_gbp, expected)
        self.assertAlmostEqual(e.balance, 10_000 + expected)


class SameCandleTest(unittest.TestCase):
    """docs/strategy.md §9."""

    def test_stop_and_target_in_one_candle_is_stop_first_and_flagged(self):
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        both = (1.1050, 1.1100, 1.0990, 1.1050)
        e = eurusd_engine([fill, both], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.closed[0].exit_reason, "stop")
        self.assertTrue(e.closed[0].fallback)

    def test_fill_and_target_in_one_candle_counts_only_the_fill(self):
        # Fills at 1.1038 and reaches the 1R target (1.1086) in the same candle.
        e = eurusd_engine([(1.1050, 1.1090, 1.1036, 1.1060), QUIET, (1.1080, 1.1090, 1.1075, 1.1085)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.run()
        [trade] = e.closed
        self.assertEqual(trade.entry_time, e.timeline[0])
        self.assertEqual(trade.exit_time, e.timeline[2])

    def test_fill_and_stop_in_one_candle_fills_then_stops(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.0985, 1.0990)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        [trade] = e.closed
        self.assertEqual(trade.entry_price, ENTRY1)
        self.assertEqual(trade.exit_reason, "stop")
        self.assertFalse(trade.fallback)


class SetupLifecycleTest(unittest.TestCase):
    def test_both_legs_fill_and_share_the_stop(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.1020, 1.1025), (1.1000, 1.1002, 1.0990, 1.0995)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        self.assertEqual([t.leg for t in e.closed], [1, 2])
        self.assertTrue(all(t.exit_reason == "stop" for t in e.closed))

    def test_unfilled_leg_cancels_when_position_closes(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045), (1.1090, 1.1100, 1.1088, 1.1095),
                           (1.1030, 1.1032, 1.1020, 1.1025)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        self.assertEqual(len(e.closed), 1)                 # leg 2 never filled on the later dip
        self.assertEqual(e.pending_orders("EUR_USD"), [])
        self.assertIsNone(e.active["EUR_USD"])

    def test_one_setup_per_pair(self):
        e = eurusd_engine([QUIET])
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        with self.assertRaises(ValueError):
            e.place_limit("EUR_USD", "s2", 1, "long", ENTRY1, STOP, TP2)
        e.cancel_setup("EUR_USD", "s1")
        e.place_limit("EUR_USD", "s2", 1, "long", ENTRY1, STOP, TP2)

    def test_a_finished_setup_cannot_trade_again(self):
        e = eurusd_engine([QUIET])
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.cancel_setup("EUR_USD", "s1")
        with self.assertRaises(ValueError):
            e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)

    def test_rejects_stop_on_the_wrong_side(self):
        e = eurusd_engine([QUIET])
        with self.assertRaises(ValueError):
            e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, 1.1050, TP2)


class SizingTest(unittest.TestCase):
    def test_eurusd_units_risk_half_percent_in_gbp(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        # £50 risk / (0.0048 USD per unit / 1.25) = 13,020.8 -> 13,020 units.
        trade = e.open_trades("EUR_USD")[0]
        self.assertEqual(trade.units, math.floor(50 / (0.0048 / GBPUSD)))
        self.assertAlmostEqual(trade.risk_gbp, trade.units * 0.0048 / GBPUSD)

    def test_gbpjpy_converts_with_its_own_price(self):
        # Long GBP_JPY limit 190.00, stop 189.00: 1 JPY per unit = 1/190 GBP.
        gj = candles([(190.50, 190.60, 189.95, 190.20)], spread=0.03)
        e = Engine({"GBP_JPY": gj}, config=no_financing())
        e.place_limit("GBP_JPY", "s1", 1, "long", 190.00, 189.00, 192.00)
        e.run()
        mid_open = 190.50 + 0.015
        self.assertEqual(e.open_trades("GBP_JPY")[0].units, math.floor(50 / (1.0 / mid_open)))

    def test_sizing_uses_closed_balance_not_open_profit(self):
        # Leg 1 is showing an open loss when leg 2 fills; leg 2 is still sized on
        # the closed balance of £10,000.
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045), (1.1030, 1.1032, 1.1020, 1.1025)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        leg2 = e.open_trades("EUR_USD")[1]
        self.assertEqual(leg2.units, math.floor(50 / ((ENTRY2 - STOP) / GBPUSD)))

    def test_next_setup_compounds_on_new_balance(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045), (1.1000, 1.1002, 1.0990, 1.0995)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertLess(e.balance, 10_000 - 49)   # lost about £50 plus slippage

    def test_notional_cap_skips_leg(self):
        # Stop 1 pip away: £50 risk would need ~625k units (~£550k notional) > 10 x £10,000.
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, 1.1037, TP2)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD"), [])
        self.assertEqual(e.skipped[0][2], "notional_cap")

    def test_margin_cap_skips_leg(self):
        # With a 10% margin rate, 50% of balance allows only 5 x balance of notional.
        e = eurusd_engine([(1.1050, 1.1052, 1.1036, 1.1045)], no_financing(margin_rate=0.10))
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, 1.1030, TP2)   # ~78k units, ~£69k
        e.run()
        self.assertEqual(e.skipped[0][2], "margin_cap")


class FridayCloseTest(unittest.TestCase):
    # Friday 2024-01-12, New York winter time: 16:00 NY = 21:00 UTC.
    FRIDAY = "2024-01-12 20:50"

    def run_friday(self, friday_close, rows):
        e = eurusd_engine(rows, no_financing(friday_close=friday_close), start=self.FRIDAY)
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        return e.run()

    def test_flattens_at_open_of_1600_candle_with_slippage(self):
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        e = self.run_friday(True, [fill, QUIET, QUIET])     # 20:50, 20:55, 21:00 UTC
        [trade] = e.closed
        self.assertEqual(trade.exit_reason, "friday")
        self.assertEqual(trade.exit_time, pd.Timestamp("2024-01-12 21:00", tz="UTC"))
        self.assertAlmostEqual(trade.exit_price, 1.1060 - SLIP)

    def test_off_by_default_keeps_position(self):
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        e = self.run_friday(False, [fill, QUIET, QUIET])
        self.assertEqual(len(e.open_trades("EUR_USD")), 1)

    def test_pending_setup_survives_but_cannot_fill_after_cutoff(self):
        dip = (1.1050, 1.1052, 1.1036, 1.1045)
        e = self.run_friday(True, [QUIET, QUIET, dip])      # dip comes at 21:00 UTC
        self.assertEqual(e.open_trades("EUR_USD"), [])
        self.assertEqual(len(e.pending_orders("EUR_USD")), 2)


class FinancingTest(unittest.TestCase):
    def run_over_rollover(self, mode, start="2024-01-09 21:50", bars=4):
        # Rollover is 17:00 New York = 22:00 UTC in January.
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        rows = [fill] + [QUIET] * (bars - 1)
        e = eurusd_engine(rows, Config(financing_mode=mode), start=start)
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        return e.run()

    def test_long_eurusd_charged_once_at_rollover(self):
        from datetime import date
        from src.financing import annual_rate
        e = self.run_over_rollover("on")
        trade = e.open_trades("EUR_USD")[0]
        mid = 1.1060 + SPREAD / 2      # close of the 21:55 candle
        expected = trade.units * mid * annual_rate("EUR_USD", "long", date(2024, 1, 9)) / 365 / GBPUSD
        self.assertAlmostEqual(trade.financing_gbp, expected)
        self.assertLess(trade.financing_gbp, 0)   # 2024: USD rates above EUR, longs pay
        self.assertAlmostEqual(e.balance, 10_000 + expected)

    def test_wednesday_charges_three_days(self):
        tue = self.run_over_rollover("on").open_trades("EUR_USD")[0].financing_gbp
        wed = self.run_over_rollover("on", start="2024-01-10 21:50").open_trades("EUR_USD")[0].financing_gbp
        self.assertAlmostEqual(wed / tue, 3, places=2)

    def test_off_mode_charges_nothing(self):
        e = self.run_over_rollover("off")
        self.assertEqual(e.open_trades("EUR_USD")[0].financing_gbp, 0)

    def test_no_charge_if_closed_before_rollover(self):
        # Filled 21:50, stopped 21:55, rollover at 22:00.
        fill = (1.1050, 1.1052, 1.1036, 1.1045)
        stop = (1.1000, 1.1002, 1.0990, 1.0995)
        e = eurusd_engine([fill, stop, QUIET], Config(financing_mode="on"), start="2024-01-09 21:50")
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.closed[0].financing_gbp, 0)


class H1HookTest(unittest.TestCase):
    def test_called_once_per_completed_hour_before_next_hour_trades(self):
        calls = []

        class Recorder:
            def on_h1_close(self, engine, pair, hour):
                calls.append((pair, hour, engine.time))

        e = eurusd_engine([QUIET] * 30, strategy=Recorder())   # 10:00 to 12:25
        e.run()
        utc = lambda s: pd.Timestamp(s, tz="UTC")
        self.assertEqual(calls, [
            ("EUR_USD", utc("2024-01-09 10:00"), utc("2024-01-09 11:00")),
            ("EUR_USD", utc("2024-01-09 11:00"), utc("2024-01-09 12:00")),
        ])

    def test_order_placed_at_h1_close_is_live_from_next_candle(self):
        class PlaceAtTen:
            def on_h1_close(self, engine, pair, hour):
                if not engine.closed and not engine.open_trades(pair) and not engine.pending_orders(pair):
                    engine.place_limit(pair, "s1", 1, "long", ENTRY1, STOP, TP2)

        dip = (1.1050, 1.1052, 1.1036, 1.1045)
        # The dip in the last 10:xx candle is before the order exists; the one at 11:00 fills it.
        e = eurusd_engine([QUIET] * 11 + [dip, dip], strategy=PlaceAtTen())
        e.run()
        self.assertEqual(e.open_trades("EUR_USD")[0].entry_time, pd.Timestamp("2024-01-09 11:00", tz="UTC"))


if __name__ == "__main__":
    unittest.main()
