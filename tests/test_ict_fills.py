"""Tests for src.ict.fills: the M1 order simulator (docs/strategy-ict.md §6-§9).

Worked-example short on Thursday 2024-01-11 (New York winter time, 09:00 NY = 14:00 UTC):
entry 18,078, stop 18,112, target 17,990, risk 34. The order is placed at 09:00 (the close of the
08:55 M5 candle). Bars are written as New York time -> (bid open, high, low, close); ask = bid + spread
(spread 1 unless given). Time exit is 12:00 NY, entry cut-off 11:00 NY. Slippage (NAS100_USD) is 1.0.
"""
import unittest
from datetime import date

import pandas as pd

from src.ict.calendar import NY
from src.ict.fills import SLIPPAGE, simulate
from src.ict.setups import Order

DAY = date(2024, 1, 11)


def ts(hhmm):
    return pd.Timestamp(f"2024-01-11 {hhmm}", tz=NY).tz_convert("UTC")


def frame(bars, spread=1.0):
    """bars: {"09:01": (o, h, l, c)} in bid prices; value may be (o, h, l, c, spread) for a wide bar."""
    rows = {}
    for t, v in bars.items():
        o, h, l, c = v[:4]
        s = v[4] if len(v) > 4 else spread
        rows[ts(t)] = dict(bid_o=o, bid_h=h, bid_l=l, bid_c=c,
                           ask_o=o + s, ask_h=h + s, ask_l=l + s, ask_c=c + s)
    return pd.DataFrame.from_dict(rows, orient="index")


def short_order():
    return Order("NAS100_USD", DAY, "short", ts("09:00"), 18078.0, 18112.0, 17990.0, 18100.0, {}, 1)


def long_order():
    # Mirror image: entry 18,000, stop 17,966, target 18,088 (risk 34).
    return Order("NAS100_USD", DAY, "long", ts("09:00"), 18000.0, 17966.0, 18088.0, 17980.0, {}, 1)


FLAT = (18050, 18055, 18045, 18050)   # touches nothing (ask_h 18,056; ask_l 18,046)
FILL = (18070, 18078, 18065, 18070)   # bid_h reaches the entry exactly


class MarketTests(unittest.TestCase):
    """market=True (random-entry control): fill at the first bar's open, no pre-fill cancel rules."""

    def order(self):
        # Entered at 18,050: stop 18,084, target 17,962 (risk 34).
        return Order("NAS100_USD", DAY, "short", ts("09:00"), 18050.0, 18084.0, 17962.0, 18050.0, {}, 1)

    def test_first_bar_target_touch_is_not_cancelled(self):
        m1 = frame({"09:00": (18050, 18055, 17960, 18000), "09:01": (18000, 18005, 17961, 17963)})
        r = simulate(self.order(), m1, market=True)
        self.assertEqual((r.status, r.fill_time, r.fill_price), ("filled", ts("09:00"), 18050.0))
        # the target is only reachable from the next bar (ask_l 17,962 there)
        self.assertEqual((r.exit_time, r.exit_reason), (ts("09:01"), "target"))
        self.assertAlmostEqual(r.r, 88 / 34)
        self.assertEqual(simulate(self.order(), m1).status, "cancelled_target")   # default unchanged

    def test_first_bar_stop_touch_stops_out(self):
        m1 = frame({"09:00": (18050, 18090, 18045, 18080)})     # ask_h 18,091 >= stop 18,084
        r = simulate(self.order(), m1, market=True)
        self.assertEqual((r.status, r.exit_time, r.exit_reason), ("filled", ts("09:00"), "stop"))
        self.assertAlmostEqual(r.r, (18050 - 18085) / 34)       # stop 18,084 + 1 slippage


class ShortTests(unittest.TestCase):
    def test_slippage_values(self):
        self.assertEqual(SLIPPAGE, {"NAS100_USD": 1.0, "SPX500_USD": 0.5})

    def test_target_hit(self):
        m1 = frame({"09:01": FILL, "09:02": FLAT, "09:03": (18000, 18005, 17989, 17995)})  # ask_l 17,990
        r = simulate(short_order(), m1)
        self.assertEqual(r.status, "filled")
        self.assertEqual((r.fill_time, r.fill_price), (ts("09:01"), 18078.0))
        self.assertEqual((r.exit_time, r.exit_price, r.exit_reason), (ts("09:03"), 17990.0, "target"))
        self.assertAlmostEqual(r.r, 88 / 34)
        self.assertFalse(r.stop_first)

    def test_stop_hit_with_slippage(self):
        m1 = frame({"09:01": FILL, "09:02": (18090, 18111, 18085, 18100)})  # ask_h 18,112
        r = simulate(short_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_price, r.exit_time), ("stop", 18113.0, ts("09:02")))
        self.assertAlmostEqual(r.r, -35 / 34)

    def test_same_bar_stop_first(self):
        # One bar reaches both ask_h 18,112 (stop) and ask_l 17,990 (target): the stop counts first.
        m1 = frame({"09:01": FILL, "09:02": (18050, 18111, 17989, 18050)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_price), ("stop", 18113.0))
        self.assertTrue(r.stop_first)

    def test_gap_through_stop_on_fill_bar(self):
        # Opens at bid 18,120 (ask 18,121, past the 18,112 stop): fills at the open, stopped at once.
        m1 = frame({"09:01": (18120, 18125, 18118, 18122)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.fill_price, r.exit_price, r.exit_reason), (18120.0, 18122.0, "stop"))
        self.assertEqual(r.exit_time, ts("09:01"))
        # P&L from the actual fill 18,120: (18120 - 18122) = -2 over planned risk 34 (order entry 18,078 - stop 18,112).
        self.assertAlmostEqual(r.r, (18120 - 18122) / 34)

    def test_gap_better_than_target_fills_at_open(self):
        m1 = frame({"09:01": FILL, "09:02": (17980, 17985, 17975, 17980)})  # ask_o 17,981 < target
        r = simulate(short_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_price), ("target", 17981.0))

    def test_gap_entry_fills_at_open(self):
        m1 = frame({"09:01": (18085, 18090, 18080, 18085), "09:02": (18000, 18005, 17989, 17995)})
        r = simulate(short_order(), m1)
        self.assertEqual(r.fill_price, 18085.0)
        # P&L from the actual fill 18,085 to target 17,990 = 95; risk stays the planned 34 -> 95/34.
        self.assertAlmostEqual(r.r, (18085 - 17990) / 34)

    def test_cancel_at_1100(self):
        m1 = frame({"09:01": FLAT, "10:59": FLAT, "11:00": (18070, 18100, 18065, 18070)})
        r = simulate(short_order(), m1)
        self.assertEqual(r.status, "cancelled_time")
        self.assertIsNone(r.fill_time)
        self.assertIsNone(r.exit_price)
        self.assertIsNone(r.r)

    def test_cancel_when_target_touched_first(self):
        m1 = frame({"09:01": (18060, 18070, 17989, 18000), "09:02": FILL})  # ask_l 17,990, no fill yet
        r = simulate(short_order(), m1)
        self.assertEqual(r.status, "cancelled_target")
        self.assertIsNone(r.fill_price)

    def test_cancel_when_bar_reaches_entry_and_target(self):
        m1 = frame({"09:01": (18060, 18078, 17989, 18000)})
        self.assertEqual(simulate(short_order(), m1).status, "cancelled_target")

    def test_cancel_when_stop_touched_first(self):
        # Wide spread: ask_h 18,112 reaches the stop while bid_h 18,077 stays below the entry.
        m1 = frame({"09:01": (18060, 18077, 18055, 18070, 35.0), "09:02": FILL})
        r = simulate(short_order(), m1)
        self.assertEqual(r.status, "cancelled_stop")

    def test_fill_and_stop_in_same_bar(self):
        m1 = frame({"09:01": (18070, 18111, 18065, 18100)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.status, r.exit_reason, r.exit_price), ("filled", "stop", 18113.0))

    def test_time_exit_1200(self):
        m1 = frame({"09:01": FILL, "11:30": FLAT, "12:00": (18040, 18045, 18035, 18040),
                    "12:01": (18000, 18005, 17989, 17995)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_time, r.exit_price), ("time", ts("12:00"), 18042.0))
        self.assertAlmostEqual(r.r, 36 / 34)

    def test_time_exit_uses_first_bar_after_1200_when_missing(self):
        m1 = frame({"09:01": FILL, "12:02": (18040, 18045, 18035, 18040)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_time), ("time", ts("12:02")))

    def test_fill_stop_and_target_in_one_bar(self):
        m1 = frame({"09:01": (18060, 18111, 17989, 18050)})
        r = simulate(short_order(), m1)
        self.assertEqual((r.status, r.exit_reason, r.exit_price), ("filled", "stop", 18113.0))
        self.assertTrue(r.stop_first)

    def test_no_exit_data(self):
        # Filled, but the data stops before 12:00: no exit bar, so status says so.
        r = simulate(short_order(), frame({"09:01": FILL, "09:02": FLAT}))
        self.assertEqual(r.status, "no_exit_data")
        self.assertEqual(r.fill_price, 18078.0)
        self.assertIsNone(r.exit_price)
        self.assertIsNone(r.r)

    def test_summer_time_boundaries(self):
        # July: New York is UTC-4, so 11:00 NY = 15:00 UTC and 12:00 NY = 16:00 UTC.
        day = date(2024, 7, 11)
        o = Order("NAS100_USD", day, "short", pd.Timestamp("2024-07-11 13:00", tz="UTC"),
                  18078.0, 18112.0, 17990.0, 18100.0, {}, 1)

        def f(bars):
            m = frame({"09:01": FLAT})
            m = pd.DataFrame.from_dict({pd.Timestamp(f"2024-07-11 {k}", tz="UTC"): dict(
                bid_o=v[0], bid_h=v[1], bid_l=v[2], bid_c=v[3], ask_o=v[0] + 1, ask_h=v[1] + 1,
                ask_l=v[2] + 1, ask_c=v[3] + 1) for k, v in bars.items()}, orient="index")
            return m
        # A fill-range bar at 14:59 UTC (10:59 NY) fills; the 16:00 UTC bar is the time exit.
        r = simulate(o, f({"14:59": FILL, "15:30": FLAT, "16:00": (18040, 18045, 18035, 18040)}))
        self.assertEqual((r.fill_time, r.exit_reason, r.exit_time, r.exit_price),
                         (pd.Timestamp("2024-07-11 14:59", tz="UTC"), "time",
                          pd.Timestamp("2024-07-11 16:00", tz="UTC"), 18042.0))
        # The same fill bar at 15:00 UTC (11:00 NY) is too late.
        self.assertEqual(simulate(o, f({"15:00": FILL, "16:00": FLAT})).status, "cancelled_time")

    def test_order_filled_after_1100_is_not_possible(self):
        m1 = frame({"11:00": FILL, "12:00": FLAT})
        self.assertEqual(simulate(short_order(), m1).status, "cancelled_time")

    def test_no_fill_in_creating_candle(self):
        # 08:57 is inside the 08:55 candle that created the order: ignored.
        m1 = frame({"08:57": FILL, "09:01": FLAT, "11:00": FLAT})
        r = simulate(short_order(), m1)
        self.assertEqual(r.status, "cancelled_time")


class LongTests(unittest.TestCase):
    # Long entry 18,000, stop 17,966, target 18,088. Fill on ask_l; stop on bid_l; target on bid_h.
    LFILL = (18010, 18015, 17999, 18010)       # ask_l 18,000 reaches the entry
    LFLAT = (18040, 18045, 18035, 18040)

    def test_long_mirror(self):
        m1 = frame({"09:01": self.LFILL, "09:02": self.LFLAT, "09:03": (18080, 18088, 18075, 18085)})
        r = simulate(long_order(), m1)
        self.assertEqual((r.status, r.fill_price), ("filled", 18000.0))
        self.assertEqual((r.exit_reason, r.exit_price), ("target", 18088.0))
        self.assertAlmostEqual(r.r, 88 / 34)

    def test_long_stop_with_slippage(self):
        m1 = frame({"09:01": self.LFILL, "09:02": (18000, 18005, 17966, 17990)})  # bid_l 17,966
        r = simulate(long_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_price), ("stop", 17965.0))
        self.assertAlmostEqual(r.r, -35 / 34)

    def test_long_same_bar_stop_first(self):
        m1 = frame({"09:01": self.LFILL, "09:02": (18040, 18088, 17966, 18040)})
        r = simulate(long_order(), m1)
        self.assertEqual(r.exit_reason, "stop")
        self.assertTrue(r.stop_first)

    def test_long_gap_through_stop_on_fill_bar(self):
        m1 = frame({"09:01": (17960, 17962, 17955, 17958)})  # ask_o 17,961 < entry: fills there
        r = simulate(long_order(), m1)
        self.assertEqual((r.fill_price, r.exit_price, r.exit_reason), (17961.0, 17959.0, "stop"))

    def test_long_cancels(self):
        self.assertEqual(simulate(long_order(), frame({"09:01": self.LFLAT, "11:00": self.LFILL})).status,
                         "cancelled_time")
        self.assertEqual(simulate(long_order(), frame({"09:01": (18040, 18088, 18030, 18050)})).status,
                         "cancelled_target")
        # Wide spread: bid_l 17,966 reaches the stop while ask_l 17,967 stays above the entry.
        wide = frame({"09:01": (18040, 18045, 17966, 18040, 0.0)})
        wide[["ask_o", "ask_h", "ask_l", "ask_c"]] += 100
        self.assertEqual(simulate(long_order(), wide).status, "cancelled_stop")

    def test_long_time_exit(self):
        m1 = frame({"09:01": self.LFILL, "12:00": (18040, 18045, 18035, 18040)})
        r = simulate(long_order(), m1)
        self.assertEqual((r.exit_reason, r.exit_price), ("time", 18039.0))
        self.assertAlmostEqual(r.r, 39 / 34)


class ResolvedTimeTests(unittest.TestCase):
    """Result.resolved_time: when an unfilled order stopped being pending (used by the runner)."""

    def test_cancel_times(self):
        stop = simulate(short_order(), frame({"09:01": (18050, 18055, 18045, 18050, 60)}))  # wide spread
        self.assertEqual((stop.status, stop.resolved_time), ("cancelled_stop", ts("09:01")))
        target = simulate(short_order(), frame({"09:01": (18000, 18005, 17989, 17995)}))
        self.assertEqual((target.status, target.resolved_time), ("cancelled_target", ts("09:01")))
        late = simulate(short_order(), frame({"09:01": FLAT, "11:00": FILL}))
        self.assertEqual((late.status, late.resolved_time), ("cancelled_time", ts("11:00")))

    def test_cancelled_time_without_bars_resolves_at_1100(self):
        r = simulate(short_order(), frame({"09:01": FLAT}))
        self.assertEqual((r.status, r.resolved_time), ("cancelled_time", ts("11:00")))

    def test_filled_has_no_resolved_time(self):
        r = simulate(short_order(), frame({"09:01": FILL, "12:00": FLAT}))
        self.assertEqual(r.status, "filled")
        self.assertIsNone(r.resolved_time)


if __name__ == "__main__":
    unittest.main()
