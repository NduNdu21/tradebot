"""Tests for src.backtest on synthetic M5 candles: fills, exits, same-candle rules,
sizing, exposure caps, Friday close and financing.

Prices follow the worked example in docs/strategy.md: swing 1.1000 -> 1.1100,
leg 1 at 1.1038, leg 2 at 1.1024, stop 1.0990, TP2 1.1100.
"""
import math
import unittest
from datetime import date

import pandas as pd

from src.backtest import Config, Engine, last_friday_cutoff, trade_table
from src.financing import annual_rate

SPREAD = 0.0002            # EUR_USD ask = bid + 2 pips
GBPUSD = 1.25              # flat conversion rate
START = "2024-01-09 10:00"  # a Tuesday, far from the 22:00 UTC rollover
ENTRY1, ENTRY2, STOP, TP2 = 1.1038, 1.1024, 1.0990, 1.1100
SLIP = 0.00005             # 0.5 pip EUR_USD


def utc(s):
    return pd.Timestamp(s, tz="UTC")


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
    df = candles([(price,) * 4] * len(like), spread=0.0)
    df.index = like.index
    return df


def engine_for(eu, config=None, strategy=None):
    return Engine({"EUR_USD": eu, "GBP_USD": flat(GBPUSD, eu)}, strategy, config)


def eurusd_engine(rows, config=None, strategy=None, start=START):
    return engine_for(candles(rows, start=start), config, strategy)


def no_financing(**kwargs):
    return Config(financing_mode="off", **kwargs)


QUIET = (1.1060, 1.1065, 1.1055, 1.1060)   # nowhere near entry, stop or target
DIP1 = (1.1050, 1.1052, 1.1036, 1.1045)    # ask low 1.1038: fills leg 1 only
STOPPED = (1.1000, 1.1002, 1.0990, 1.0995)  # bid low 1.0990: hits the stop
UNITS_LEG1 = math.floor(50 / ((ENTRY1 - STOP) / GBPUSD))   # £50 risk = 13,020 units


class LimitFillTest(unittest.TestCase):
    def test_long_limit_fills_when_ask_touches_entry(self):
        e = eurusd_engine([QUIET, DIP1], no_financing())
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

    def test_gap_below_limit_fills_at_the_better_open_sized_from_the_limit(self):
        e = eurusd_engine([QUIET, (1.1028, 1.1030, 1.1026, 1.1028)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        trade = e.open_trades("EUR_USD")[0]
        self.assertAlmostEqual(trade.entry_price, 1.1030)
        self.assertEqual(trade.units, UNITS_LEG1)

    def test_short_limit_fills_on_the_bid(self):
        # Mirror: swing 1.1100 -> 1.1000, sell limit at 1.1062, stop 1.1110, target 1.1000.
        e = eurusd_engine([(1.1050, 1.1062, 1.1048, 1.1055)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "short", 1.1062, 1.1110, 1.1000)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD")[0].entry_price, 1.1062)

    def test_gap_through_stop_fills_and_stops_at_the_open(self):
        e = eurusd_engine([QUIET, (1.0980, 1.0985, 1.0975, 1.0980)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        [trade] = e.closed
        self.assertAlmostEqual(trade.entry_price, 1.0980 + SPREAD)   # ask open
        self.assertAlmostEqual(trade.exit_price, 1.0980 - SLIP)      # bid open minus slippage
        self.assertEqual(trade.exit_reason, "stop")
        self.assertEqual(trade.units, UNITS_LEG1)                    # sized from the limit price

    def test_market_order_fills_at_next_open_ask_without_slippage(self):
        e = eurusd_engine([QUIET, QUIET], no_financing())
        e.place_market("EUR_USD", "s1", 1, "long", STOP, TP2)
        e.run()
        self.assertAlmostEqual(e.open_trades("EUR_USD")[0].entry_price, 1.1060 + SPREAD)

    def test_market_order_opening_past_the_stop_is_skipped(self):
        e = eurusd_engine([(1.0980, 1.0985, 1.0975, 1.0980)], no_financing())
        e.place_market("EUR_USD", "s1", 1, "long", STOP, TP2)
        e.run()
        self.assertEqual(e.closed, [])
        self.assertEqual(e.skipped[0][2], "through_stop")


class ExitTest(unittest.TestCase):
    def filled_long(self, *later_rows, target=TP2):
        e = eurusd_engine([DIP1, *later_rows], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, target)
        return e.run()

    def test_stop_triggers_on_bid_with_slippage(self):
        e = self.filled_long(STOPPED)
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
        e = eurusd_engine([DIP1, (1.1050, 1.1100, 1.0990, 1.1050)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.closed[0].exit_reason, "stop")
        self.assertTrue(e.closed[0].fallback)

    def test_fill_inside_candle_and_target_counts_only_the_fill(self):
        # Fills at 1.1038 and reaches the 1R target (1.1086) in the same candle.
        e = eurusd_engine([(1.1050, 1.1090, 1.1036, 1.1060), QUIET, (1.1080, 1.1090, 1.1075, 1.1085)],
                          no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.run()
        [trade] = e.closed
        self.assertEqual(trade.entry_time, e.timeline[0])
        self.assertEqual(trade.exit_time, e.timeline[2])

    def test_fill_at_open_and_target_in_one_candle_counts_the_target(self):
        # Opens below the limit (fills at the open), then rallies to the 1R target.
        e = eurusd_engine([(1.1030, 1.1090, 1.1028, 1.1080)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.run()
        [trade] = e.closed
        self.assertEqual(trade.exit_reason, "target")
        self.assertEqual(trade.exit_time, trade.entry_time)

    def test_fill_at_open_with_stop_and_target_is_stop_first(self):
        e = eurusd_engine([(1.1030, 1.1090, 1.0985, 1.1000)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.run()
        self.assertEqual(e.closed[0].exit_reason, "stop")
        self.assertTrue(e.closed[0].fallback)

    def test_fill_and_stop_in_one_candle_fills_then_stops(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.0985, 1.0990)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        [trade] = e.closed
        self.assertEqual(trade.entry_price, ENTRY1)
        self.assertEqual(trade.exit_reason, "stop")
        self.assertFalse(trade.fallback)

    def test_target_and_other_legs_limit_in_one_candle_takes_target_first(self):
        # Leg 1 (1R target 1.1086) is open. The next candle reaches 1.1086 and
        # also dips to leg 2's 1.1024: the target wins, leg 2 cancels unfilled.
        e = eurusd_engine([DIP1, (1.1050, 1.1090, 1.1020, 1.1050)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        self.assertEqual([(t.leg, t.exit_reason) for t in e.closed], [(1, "target")])
        self.assertEqual(e.pending_orders("EUR_USD"), [])
        self.assertEqual(e.counts["target_before_fill"], 1)

    def test_target_without_other_leg_touched_is_not_counted(self):
        e = eurusd_engine([DIP1, (1.1050, 1.1090, 1.1040, 1.1050)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, 1.1086)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        self.assertEqual(e.counts["target_before_fill"], 0)
        self.assertEqual(e.pending_orders("EUR_USD"), [])


class SetupLifecycleTest(unittest.TestCase):
    def test_both_legs_fill_and_share_the_stop(self):
        e = eurusd_engine([(1.1050, 1.1052, 1.1020, 1.1025), STOPPED], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        self.assertEqual([t.leg for t in e.closed], [1, 2])
        self.assertTrue(all(t.exit_reason == "stop" for t in e.closed))

    def test_unfilled_leg_cancels_when_position_closes(self):
        e = eurusd_engine([DIP1, (1.1090, 1.1100, 1.1088, 1.1095), (1.1030, 1.1032, 1.1020, 1.1025)],
                          no_financing())
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
        e = eurusd_engine([DIP1], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        # £50 risk / (0.0048 USD per unit / 1.25) = 13,020.8 -> 13,020 units.
        trade = e.open_trades("EUR_USD")[0]
        self.assertEqual(trade.units, 13_020)
        self.assertAlmostEqual(trade.risk_gbp, trade.units * 0.0048 / GBPUSD)

    def test_gbpjpy_converts_with_its_own_price(self):
        # Long GBP_JPY limit 190.00, stop 189.00: 1 JPY per unit = 1/190 GBP.
        gj = candles([(190.50, 190.60, 189.95, 190.20)], spread=0.03)
        e = Engine({"GBP_JPY": gj}, config=no_financing())
        e.place_limit("GBP_JPY", "s1", 1, "long", 190.00, 189.00, 192.00)
        e.run()
        mid_open = 190.50 + 0.015
        self.assertEqual(e.open_trades("GBP_JPY")[0].units, math.floor(50 / (1.0 / mid_open)))

    def setups_after_a_loss(self, config):
        # Setup s1 loses, then setup s2 fills at the same prices.
        e = eurusd_engine([DIP1, STOPPED], config)
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertLess(e.balance, 10_000 - 49)   # lost about £50 plus slippage
        e2 = eurusd_engine([DIP1], config)
        e2.balance = e.balance                    # carry the closed balance over
        e2.place_limit("EUR_USD", "s2", 1, "long", ENTRY1, STOP, TP2)
        e2.run()
        return e2.open_trades("EUR_USD")[0].units

    def test_research_runs_size_from_the_starting_balance(self):
        self.assertEqual(self.setups_after_a_loss(no_financing()), UNITS_LEG1)

    def test_compounding_sizes_from_the_closed_balance(self):
        self.assertLess(self.setups_after_a_loss(no_financing(compounding=True)), UNITS_LEG1)

    def test_compounding_ignores_open_profit(self):
        # Leg 1 shows an open loss when leg 2 fills; leg 2 is still sized on £10,000.
        e = eurusd_engine([DIP1, (1.1030, 1.1032, 1.1020, 1.1025)], no_financing(compounding=True))
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, STOP, TP2)
        e.run()
        leg2 = e.open_trades("EUR_USD")[1]
        self.assertEqual(leg2.units, math.floor(50 / ((ENTRY2 - STOP) / GBPUSD)))


class ExposureCapTest(unittest.TestCase):
    def test_tight_stop_breaks_both_caps(self):
        # Stop 1 pip away: £50 risk needs 625,000 units, about £553k notional:
        # over 5 x £10,000, and its margin (3.33%) is over 20% of £10,000.
        e = eurusd_engine([DIP1], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, 1.1037, TP2)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD"), [])
        self.assertEqual(e.skipped[0][2], "notional_cap+margin_cap")
        self.assertEqual(e.counts, {"notional_cap": 1, "margin_cap": 1})

    def test_notional_cap_counts_both_legs(self):
        # Each leg risks £50 over 21 pips: 29,761 units, about £26k notional.
        # One leg fits under £50,000; the second would take the total to ~£52.5k.
        # Margin on £52.5k at 3.33% is ~£1,750, under the £2,000 limit.
        e = eurusd_engine([DIP1, (1.1030, 1.1032, 1.1020, 1.1025)], no_financing())
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, ENTRY1 - 0.0021, TP2)
        e.place_limit("EUR_USD", "s1", 2, "long", ENTRY2, ENTRY2 - 0.0021, TP2)
        e.run()
        self.assertEqual(len(e.open_trades("EUR_USD")), 1)
        self.assertEqual(e.skipped[0][2], "notional_cap")
        self.assertEqual(e.counts, {"notional_cap": 1})

    def test_margin_cap_alone(self):
        # At a 10% margin rate, 20% of balance allows only 2 x balance of notional.
        # Stop 18 pips away: 34,722 units, ~£30.7k notional (3x): margin breaks, notional doesn't.
        e = eurusd_engine([DIP1], no_financing(margin_rate=0.10))
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, ENTRY1 - 0.0018, TP2)
        e.run()
        self.assertEqual(e.skipped[0][2], "margin_cap")
        self.assertEqual(e.counts, {"margin_cap": 1})


class FridayCloseTest(unittest.TestCase):
    # 16:00 New York on Fridays either side of both 2024 US clock changes.
    CUTOFFS = {
        "2024-03-08": "21:00",   # last Friday on EST (UTC-5)
        "2024-03-15": "20:00",   # first Friday on EDT (UTC-4)
        "2024-11-01": "20:00",   # last Friday on EDT
        "2024-11-08": "21:00",   # first Friday on EST
    }

    def test_cutoff_follows_new_york_daylight_saving(self):
        for day, hhmm in self.CUTOFFS.items():
            with self.subTest(day=day):
                cutoff = utc(f"{day} {hhmm}")
                times = pd.DatetimeIndex([cutoff - pd.Timedelta("5min"), cutoff,
                                          cutoff + pd.Timedelta("2D")])   # the Sunday after
                got = last_friday_cutoff(times)
                self.assertEqual(got[1], cutoff)
                self.assertEqual(got[2], cutoff)
                # Just before the cutoff: the previous Friday's 16:00 NY, which can sit
                # at a different UTC hour when the clocks changed in between.
                prev_friday = pd.Timestamp(day) - pd.Timedelta("7D") + pd.Timedelta("16h")
                self.assertEqual(got[0], prev_friday.tz_localize("America/New_York"))
                self.assertEqual(cutoff.minute, 0)                       # an H1 boundary

    def test_flattens_at_the_first_h1_close_from_1600_new_york(self):
        # 17 candles from 19:50 to 21:10 UTC span both possible cutoff hours.
        for day, hhmm in self.CUTOFFS.items():
            with self.subTest(day=day):
                e = eurusd_engine([DIP1] + [QUIET] * 16, no_financing(friday_close=True),
                                  start=f"{day} 19:50")
                e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
                e.run()
                [trade] = e.closed
                self.assertEqual(trade.exit_reason, "friday")
                self.assertEqual(trade.exit_time, utc(f"{day} {hhmm}"))
                self.assertAlmostEqual(trade.exit_price, 1.1060 - SLIP)

    def test_off_by_default_keeps_position(self):
        e = eurusd_engine([DIP1] + [QUIET] * 16, no_financing(), start="2024-01-12 19:50")
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(len(e.open_trades("EUR_USD")), 1)

    def friday_then_sunday(self, friday_rows, sunday_rows, friday_start="2024-01-12 20:50"):
        """Candles from a Friday afternoon, then from the Sunday 17:00 NY reopen (22:00 UTC)."""
        return pd.concat([candles(friday_rows, start=friday_start),
                          candles(sunday_rows, start="2024-01-14 22:00")])

    def test_pending_setup_is_pulled_after_cutoff_and_back_at_reopen(self):
        # Winter: cutoff 21:00 UTC. A dip at 21:00 Friday can't fill; Sunday's can.
        eu = self.friday_then_sunday([QUIET, QUIET, DIP1], [QUIET, DIP1])
        e = engine_for(eu, no_financing(friday_close=True))
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.open_trades("EUR_USD")[0].entry_time, utc("2024-01-14 22:05"))

    def test_market_shut_at_1600_flattens_at_reopen(self):
        # No candles after 20:55 UTC Friday (15:55 NY), e.g. an early holiday close.
        eu = self.friday_then_sunday([DIP1, QUIET], [QUIET, QUIET])
        e = engine_for(eu, no_financing(friday_close=True))
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.closed[0].exit_reason, "friday")
        self.assertEqual(e.closed[0].exit_time, utc("2024-01-14 22:00"))


class FinancingTest(unittest.TestCase):
    def run_over_rollover(self, side="long", start="2024-01-09 21:50", config=None):
        # Rollover is 17:00 New York = 22:00 UTC in January.
        e = eurusd_engine([DIP1 if side == "long" else (1.1050, 1.1062, 1.1048, 1.1055)] + [QUIET] * 3,
                          config or Config(), start=start)
        if side == "long":
            e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        else:
            e.place_limit("EUR_USD", "s1", 1, "short", 1.1062, 1.1110, 1.1000)
        return e.run()

    def test_long_eurusd_charged_once_at_rollover(self):
        e = self.run_over_rollover()
        trade = e.open_trades("EUR_USD")[0]
        mid = 1.1060 + SPREAD / 2      # close of the 21:55 candle
        expected = trade.units * mid * annual_rate("EUR_USD", "long", date(2024, 1, 9)) / 365 / GBPUSD
        self.assertAlmostEqual(trade.financing_gbp, expected)
        self.assertLess(trade.financing_gbp, 0)   # 2024: USD rates above EUR, longs pay
        self.assertAlmostEqual(trade.financing_capped_gbp, expected)   # a charge isn't capped
        self.assertAlmostEqual(e.balance, 10_000 + expected)

    def test_credit_is_recorded_but_capped_at_zero(self):
        # Short EUR_USD in 2024 earns the USD-EUR rate difference minus markup: a credit.
        trade = self.run_over_rollover("short").open_trades("EUR_USD")[0]
        self.assertGreater(trade.financing_gbp, 0)
        self.assertEqual(trade.financing_capped_gbp, 0)

    def test_wednesday_charges_three_days(self):
        tue = self.run_over_rollover().open_trades("EUR_USD")[0].financing_gbp
        wed = self.run_over_rollover(start="2024-01-10 21:50").open_trades("EUR_USD")[0].financing_gbp
        self.assertAlmostEqual(wed / tue, 3, places=2)

    def test_financing_mode_only_changes_the_booked_balance(self):
        e = self.run_over_rollover(config=Config(financing_mode="off"))
        self.assertLess(e.open_trades("EUR_USD")[0].financing_gbp, 0)
        self.assertEqual(e.balance, 10_000)

    def test_no_charge_if_closed_before_rollover(self):
        # Filled 21:50, stopped 21:55, rollover at 22:00.
        e = eurusd_engine([DIP1, STOPPED, QUIET], Config(), start="2024-01-09 21:50")
        e.place_limit("EUR_USD", "s1", 1, "long", ENTRY1, STOP, TP2)
        e.run()
        self.assertEqual(e.closed[0].financing_gbp, 0)

    def test_trade_table_has_three_financing_columns(self):
        # Short held over one rollover (a credit), then stopped out.
        e = eurusd_engine([(1.1050, 1.1062, 1.1048, 1.1055), QUIET, QUIET, (1.1100, 1.1110, 1.1095, 1.1100)],
                          start="2024-01-09 21:50")
        e.place_limit("EUR_USD", "s1", 1, "short", 1.1062, 1.1110, 1.1000)
        e.run()
        row = trade_table(e.closed).iloc[0]
        credit = e.closed[0].financing_gbp
        self.assertGreater(credit, 0)
        self.assertAlmostEqual(row.pnl_on, row.pnl_off + credit)
        self.assertAlmostEqual(row.pnl_capped, row.pnl_off)
        self.assertAlmostEqual(row.r_off, row.pnl_off / row.risk_gbp)


class H1HookTest(unittest.TestCase):
    def test_called_once_per_completed_hour_before_next_hour_trades(self):
        calls = []

        class Recorder:
            def on_h1_close(self, engine, pair, hour):
                calls.append((pair, hour, engine.time))

        e = eurusd_engine([QUIET] * 30, strategy=Recorder())   # 10:00 to 12:25
        e.run()
        self.assertEqual(calls, [
            ("EUR_USD", utc("2024-01-09 10:00"), utc("2024-01-09 11:00")),
            ("EUR_USD", utc("2024-01-09 11:00"), utc("2024-01-09 12:00")),
        ])

    def test_order_placed_at_h1_close_is_live_from_next_candle(self):
        class PlaceOnce:
            def on_h1_close(self, engine, pair, hour):
                if not engine.closed and not engine.open_trades(pair) and not engine.pending_orders(pair):
                    engine.place_limit(pair, "s1", 1, "long", ENTRY1, STOP, TP2)

        # The dip in the last 10:xx candle is before the order exists; the one at 11:00 fills it.
        e = eurusd_engine([QUIET] * 11 + [DIP1, DIP1], strategy=PlaceOnce())
        e.run()
        self.assertEqual(e.open_trades("EUR_USD")[0].entry_time, utc("2024-01-09 11:00"))


if __name__ == "__main__":
    unittest.main()
