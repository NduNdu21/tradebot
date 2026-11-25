"""Tests for src.ict.setups: sweep -> shift -> FVG -> order (docs/strategy-ict.md §3-§7).

Every test starts from the spec's worked example (NAS100_USD short) on Thursday 2024-01-11
(New York winter time, so 08:30 New York = 13:30 UTC) and changes a few candles. Candles are
written as New York open time -> (mid high, mid low[, mid close]); bid = mid - 0.5 and
ask = mid + 0.5 (spread 1 point). A missing close defaults to the candle's midpoint.
"""
import unittest
from collections import Counter
from datetime import date

import pandas as pd

from src.data import add_mid
from src.ict.calendar import NY
from src.ict.setups import Order, daily_bias_mid, find_orders, to_daily

DAY = date(2024, 1, 11)

# The worked example. Swings marked SH / SL (confirmed at the close of the candle to the right).
BASE = {
    "06:50": (17990, 17960),
    "06:55": (17985, 17950),          # SL 17,950 (an older, deeper low)
    "07:00": (17995, 17965),
    "07:05": (18000, 17970),
    "07:10": (18005, 17975),
    "07:15": (18010, 17980),
    "07:20": (18015, 17985),
    "07:25": (18020, 17995),
    "07:30": (18030, 18000),          # SH 18,030
    "07:35": (18020, 17990),          # SL 17,990: low level for longs, short target (conf 07:45)
    "07:40": (18040, 18005),
    "07:45": (18060, 18030),
    "07:50": (18080, 18050),
    "07:55": (18100, 18070),          # SH 18,100: the liquidity level (conf 08:05)
    "08:00": (18095, 18068),          # 08:00-08:35: highs and lows step down, no swings
    "08:05": (18092, 18066),
    "08:10": (18090, 18065),
    "08:15": (18088, 18064),
    "08:20": (18086, 18063),
    "08:25": (18084, 18062),
    "08:30": (18082, 18061),
    "08:35": (18080, 18060),          # SL 18,060: the short-term low (conf 08:45)
    "08:40": (18095, 18070),
    "08:45": (18112, 18090, 18092),   # sweep to 18,112; FVG candle 1 (low 18,090)
    "08:50": (18091, 18070, 18075),   # FVG candle 2
    "08:55": (18078, 18040, 18052),   # FVG candle 3 (high 18,078); closes 18,052 < 18,060: shift
    "09:00": (18072, 18045, 18055),
}


def clocks(first, last):
    """New York clock strings every 5 minutes from `first` to `last` inclusive."""
    return [t.strftime("%H:%M") for t in pd.date_range(f"2024-01-01 {first}", f"2024-01-01 {last}", freq="5min")]


def flat(first, last, high, low, close=None):
    """The same candle at every 5 minutes from `first` to `last` (equal lows and highs: no swings)."""
    candle = (high, low) if close is None else (high, low, close)
    return {c: candle for c in clocks(first, last)}


def rows(overrides=None):
    """Worked example through 11:00 (quiet after 09:00), with `overrides` replacing candles."""
    out = dict(BASE)
    out.update(flat("09:05", "11:00", 18060, 18045))
    out.update(overrides or {})
    return out


def frame(table, day=DAY, step="5min", start=None):
    """Bid/ask candles (UTC index) from a clock -> (high, low[, close]) table of mid prices.

    With `start`, candles are placed `step` apart from that New York time in table order
    instead of at their clock times (used for the 1-minute test).
    """
    keys = sorted(table)
    if start is None:
        idx = [pd.Timestamp(f"{day} {k}", tz=NY).tz_convert("UTC") for k in keys]
    else:
        idx = list(pd.date_range(pd.Timestamp(f"{day} {start}", tz=NY), periods=len(keys), freq=step).tz_convert("UTC"))
    data = {f"{side}_{f}": [] for side in ("bid", "ask") for f in "ohlc"}
    for k in keys:
        high, low = table[k][0], table[k][1]
        close = table[k][2] if len(table[k]) > 2 else (high + low) / 2
        for side, offset in (("bid", -0.5), ("ask", 0.5)):
            for f, price in zip("ohlc", (close, high, low, close)):
                data[f"{side}_{f}"].append(price + offset)
    return add_mid(pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="time")))


def mirror(table):
    """Reflect every price around 18,000 (p -> 36,000 - p); highs and lows swap."""
    out = {}
    for k, v in table.items():
        high, low = v[0], v[1]
        m = (36000 - low, 36000 - high)
        out[k] = m + ((36000 - v[2],) if len(v) > 2 else ())
    return out


def daily_frame(low, high):
    """Daily candles before 2024-01-11 with a confirmed swing low `low` and swing high `high`.

    Each candle runs 17:00 -> 17:00 New York (22:00 UTC in January) and is indexed by its open.
    The swing low closes Mon 01-08 (confirmed Tue 17:00), the swing high closes Tue 01-09
    (confirmed Wed 17:00). The Thursday 01-11 candle is still forming on the trading day.
    """
    candles = [  # (close date, high, low)
        ("2024-01-05", high - 300, low + 200),
        ("2024-01-08", high - 200, low),
        ("2024-01-09", high, low + 100),
        ("2024-01-10", high - 100, low + 150),
        ("2024-01-11", high - 120, low + 180),
    ]
    return daily_table(candles)


def daily_table(candles):
    """Daily mid candles from (close date, high, low); open = 17:00 New York the day before."""
    idx = [
        (pd.Timestamp(d, tz=NY) - pd.Timedelta("1 day") + pd.Timedelta("17h")).tz_convert("UTC")
        for d, _, _ in candles
    ]
    return pd.DataFrame({"mid_h": [c[1] for c in candles], "mid_l": [c[2] for c in candles]},
                        index=pd.DatetimeIndex(idx))


DAILY = daily_frame(17600, 18400)

# Overrides shared by a rule test and a truncation check.
# Shift 08:55; gap 08:45 -> 08:55 not in premium; gap 08:50 -> 09:00 (edge 18,085) is.
AFTER_SHIFT = {"08:50": (18091, 18089, 18090), "08:55": (18078, 18050, 18052),
               "09:00": (18085, 18060, 18070)}
# Worked-example order at 09:00, then a re-arm at 09:20 and a second order at 09:35.
REARM_AFTER_ORDER = {
    "09:05": (18085, 18050, 18080), "09:10": (18100, 18075, 18095),
    "09:15": (18108, 18070, 18100), "09:20": (18125, 18100, 18110),
    "09:25": (18105, 18085, 18090), "09:30": (18095, 18050, 18060),
    **flat("09:35", "11:00", 18080, 18065),
}  # 50% = 18,000, as in the worked example


def ny(clock, day=DAY):
    return pd.Timestamp(f"{day} {clock}", tz=NY).tz_convert("UTC")


def run(table, shift_rule="close", daily=DAILY):
    return find_orders(frame(table), daily, "NAS100_USD", DAY, shift_rule)


class TestWorkedExample(unittest.TestCase):
    def test_worked_example_short(self):
        # Review Focus 3: the sweep candle (08:45) is FVG candle 1, the shift candle (08:55) is
        # candle 3. Dealing range 18,112 -> 18,040, 50% 18,076; target 17,990 (the 08:35 low
        # 18,060 was traded through by the shift candle).
        orders, skips = run(rows())
        self.assertEqual(skips, Counter())
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertIsInstance(o, Order)
        self.assertEqual((o.instrument, o.day, o.side), ("NAS100_USD", DAY, "short"))
        self.assertEqual(o.placed_time, ny("09:00"))
        self.assertEqual((o.entry, o.stop, o.target), (18078, 18112, 17990))
        self.assertEqual(o.sweep_level, 18100)
        self.assertEqual(o.cycle, 1)
        self.assertEqual(o.notes["sweep_extreme"], 18112)
        self.assertEqual(o.notes["short_term_swing"], 18060)
        self.assertEqual((o.notes["fvg_far"], o.notes["fvg_near"]), (18090, 18078))
        self.assertEqual(o.notes["dealing_range_mid"], 18076)
        self.assertEqual(o.notes["daily_mid"], 18000)

    def test_worked_example_long(self):
        # The same day reflected around 18,000: low level 17,900 swept to 17,888, short-term
        # high 17,940, FVG 17,910 -> 17,922, daily 50% still 18,000 (entry 17,922 is in discount).
        orders, skips = run(mirror(rows()))
        self.assertEqual(skips, Counter())
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o.side, "long")
        self.assertEqual(o.placed_time, ny("09:00"))
        self.assertEqual((o.entry, o.stop, o.target), (17922, 17888, 18010))
        self.assertEqual(o.sweep_level, 17900)
        self.assertEqual(o.notes["dealing_range_mid"], 17924)


class TestLevelsAndSweep(unittest.TestCase):
    def test_levels_use_swings_confirmed_by_0830(self):
        # §4: a swing high at 08:20 (18,098) is confirmed at 08:30 and becomes the level.
        orders, _ = run(rows({"08:20": (18098, 18063)}))
        self.assertEqual(orders[0].sweep_level, 18098)
        # A swing high at 08:25 is confirmed at 08:35, after the levels are fixed: level stays 18,100.
        orders, _ = run(rows({"08:25": (18098, 18062)}))
        self.assertEqual(orders[0].sweep_level, 18100)

    def test_no_sweep_after_1100(self):
        # §4: nothing above 18,100 until the 11:00 candle, which is outside the sweep window.
        table = rows({**flat("08:45", "10:55", 18080, 18065),
                      "11:00": (18112, 18090, 18092), "11:05": (18091, 18070, 18075),
                      "11:10": (18078, 18040, 18052)})
        orders, skips = run(table)
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter())


class TestShift(unittest.TestCase):
    def test_wick_rule_fires_where_close_does_not(self):
        # §5: the 08:55 candle trades to 18,055 (below 18,060) but closes 18,065, and no later
        # candle closes below 18,060. Its high is 18,085 so the FVG stays in premium of the
        # wick case's range (18,112 -> 18,055, 50% 18,083.5).
        table = rows({"08:55": (18085, 18055, 18065), **flat("09:00", "11:00", 18075, 18062, 18068)})
        orders, skips = run(table, "wick")
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].entry, orders[0].stop, orders[0].target), (18085, 18112, 17990))
        self.assertEqual(orders[0].placed_time, ny("09:00"))
        orders, skips = run(table, "close")
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"no_shift": 1}))

    def test_new_high_before_shift_moves_extreme(self):
        # §4-§5: 08:50 trades to 18,120 before any shift; it becomes the extreme (same cycle).
        # FVG 08:50 -> 09:00 (18,095 -> 18,085), shift at 09:00, range 18,120 -> 18,040 (50% 18,080).
        table = rows({"08:45": (18112, 18090, 18100), "08:50": (18120, 18095, 18098),
                      "08:55": (18096, 18075, 18080), "09:00": (18085, 18040, 18050)})
        orders, skips = run(table)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual((o.entry, o.stop, o.cycle), (18085, 18120, 1))
        self.assertEqual(o.notes["sweep_extreme"], 18120)
        self.assertEqual(o.placed_time, ny("09:05"))


class TestFVG(unittest.TestCase):
    def test_no_fvg_no_trade(self):
        # §6: candle 1's low 18,076 overlaps candle 3's high 18,078, and 08:50 -> 09:00 has
        # no gap either (18,070 vs 18,072).
        orders, skips = run(rows({"08:45": (18112, 18076, 18092)}))
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"no_fvg": 1}))

    def test_fvg_must_be_premium(self):
        # §6: lowest low 18,000 -> 50% 18,056; edge 18,078 is above it.
        orders, _ = run(rows({"08:55": (18078, 18000, 18052)}))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].notes["dealing_range_mid"], 18056)
        # Lowest low 18,050 -> 50% 18,081; edge 18,078 is below it.
        orders, skips = run(rows({"08:55": (18078, 18050, 18052)}))
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"fvg_not_premium": 1}))

    def test_two_fvgs_uses_nearest_sweep(self):
        # §6 ("nearest the sweep extreme, i.e. the deepest in premium" = a price measure, controller
        # ruling): gaps 08:45 -> 08:55 (18,100 -> 18,075) and 08:50 -> 09:00 (18,095 -> 18,090), shift
        # at 09:00, range 18,112 -> 18,030, 50% 18,071: both in premium. The later gap's near edge
        # 18,090 is closer in price to the 18,112 extreme than 18,075, so it is used.
        table = rows({"08:45": (18112, 18100, 18105), "08:50": (18108, 18095, 18098),
                      "08:55": (18075, 18040, 18065), "09:00": (18090, 18030, 18050)})
        orders, _ = run(table)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual((o.entry, o.stop), (18090, 18108))
        self.assertEqual(o.notes["fvg_candle1_time"], ny("08:50"))
        self.assertEqual(o.placed_time, ny("09:05"))

    def test_two_fvgs_equal_price_uses_earlier(self):
        # §6: both near edges 18,090 (gaps 18,100 -> 18,090 and 18,095 -> 18,090): a tie on price
        # goes to the earlier candle 1 (08:45, stop 18,112).
        table = rows({"08:45": (18112, 18100, 18105), "08:50": (18108, 18095, 18098),
                      "08:55": (18090, 18040, 18065), "09:00": (18090, 18030, 18050)})
        orders, _ = run(table)
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].entry, orders[0].stop), (18090, 18112))
        self.assertEqual(orders[0].notes["fvg_candle1_time"], ny("08:45"))

    def test_nearest_fvg_not_premium_uses_farther_premium_one(self):
        # §6 (controller ruling): "qualifies" = in the window AND in premium. Gaps 08:45 -> 08:55
        # (18,100 -> 18,070) and 08:50 -> 09:00 (18,095 -> 18,090); shift at 09:00; range
        # 18,112 -> 18,030, 50% 18,071. The nearer gap's edge 18,070 is in discount, so the
        # farther one (edge 18,090, candle 1 high 18,108) is used.
        table = rows({"08:45": (18112, 18100, 18105), "08:50": (18108, 18095, 18098),
                      "08:55": (18070, 18040, 18065), "09:00": (18090, 18030, 18050)})
        orders, skips = run(table)
        self.assertEqual(skips, Counter())
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual((o.entry, o.stop, o.target), (18090, 18108, 17990))
        self.assertEqual(o.notes["fvg_candle1_time"], ny("08:50"))
        self.assertEqual(o.placed_time, ny("09:05"))

    def test_fvg_after_shift_used_when_earlier_one_not_premium(self):
        # §6: shift at 08:55. Gap 08:45 -> 08:55 (edge 18,078) is below the 50% of
        # 18,112 -> 18,050 (18,081). The gap 08:50 -> 09:00 (18,089 -> 18,085) ends on the candle
        # after the shift; range through 09:00 is still 18,112 -> 18,050, and 18,085 is in premium.
        # Placed at 09:05. Target: the 08:55 swing low 18,050, confirmed at 09:05.
        orders, skips = run(rows(AFTER_SHIFT))
        self.assertEqual(skips, Counter())
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual((o.entry, o.stop, o.target), (18085, 18091, 18050))
        self.assertEqual(o.placed_time, ny("09:05"))

    def test_too_late(self):
        # §6: sweep at 10:45, shift at 10:55, FVG 10:50 -> 11:00 (18,075 -> 18,070): placed at
        # 11:05, after the 11:00 limit.
        table = rows({**flat("08:45", "10:40", 18080, 18065),
                      "10:45": (18112, 18085, 18090), "10:50": (18095, 18075, 18080),
                      "10:55": (18086, 18045, 18050), "11:00": (18070, 18040, 18045)})
        orders, skips = run(table)
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"too_late": 1}))
        # FVG 10:45 -> 10:55 instead: placed exactly at 11:00, still too late.
        table["10:55"] = (18078, 18045, 18050)
        orders, skips = run(table)
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"too_late": 1}))


class TestBias(unittest.TestCase):
    def test_bias_blocks_short_in_discount(self):
        # §3: daily range 18,000 -> 19,000, 50% 18,500; entry 18,078 is in discount.
        orders, skips = run(rows(), daily=daily_frame(18000, 19000))
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"bias": 1}))

    def test_daily_bias_waits_for_confirmation(self):
        # §3: the Tuesday 01-09 swing high (18,400) needs Wednesday's candle to close (Wed 17:00).
        candles = [
            ("2024-01-02", 18100, 17800),
            ("2024-01-03", 18300, 17750),  # swing high 18,300, confirmed Thu 01-04 17:00
            ("2024-01-04", 18000, 17700),
            ("2024-01-05", 17900, 17600),  # swing low 17,600, confirmed Mon 01-08 17:00
            ("2024-01-08", 18100, 17700),
            ("2024-01-09", 18400, 17900),  # swing high 18,400, confirmed Wed 01-10 17:00
            ("2024-01-10", 18200, 17800),
            ("2024-01-11", 18100, 17750),
        ]
        daily = daily_table(candles)
        wed = ny("09:00", "2024-01-10")
        thu = ny("09:00", "2024-01-11")
        self.assertEqual(daily_bias_mid(daily, wed), (18300 + 17600) / 2)
        self.assertEqual(daily_bias_mid(daily, thu), (18400 + 17600) / 2)
        # Changing only the candle still forming (Wednesday's at Wed 09:00) changes nothing.
        for high, low in ((18500, 17800), (18200, 17000)):
            changed = list(candles)
            changed[6] = ("2024-01-10", high, low)
            self.assertEqual(daily_bias_mid(daily_table(changed), wed), (18300 + 17600) / 2)

    def test_daily_bias_none_without_swings(self):
        self.assertIsNone(daily_bias_mid(daily_table([("2024-01-09", 18100, 17900)]), ny("09:00")))

    def test_to_daily_17_to_17_new_york(self):
        # §1: candles 17:00 -> 17:00 New York, indexed by the UTC open, named by the closing day.
        stamps = ["2024-01-08 16:55", "2024-01-08 17:00", "2024-01-09 09:00", "2024-01-09 16:55",
                  "2024-01-09 17:00", "2024-07-09 17:00"]
        bids = [(100, 101, 99, 100), (200, 205, 198, 201), (201, 210, 190, 195), (195, 196, 194, 196),
                (300, 301, 299, 300), (400, 401, 399, 400)]
        idx = pd.DatetimeIndex([pd.Timestamp(s, tz=NY) for s in stamps]).tz_convert("UTC")
        df = pd.DataFrame({f"bid_{f}": [b[i] for b in bids] for i, f in enumerate("ohlc")}, index=idx)
        for f in "ohlc":
            df[f"ask_{f}"] = df[f"bid_{f}"] + 1
        daily = to_daily(df)
        self.assertEqual(list(daily.index), [
            pd.Timestamp("2024-01-07 22:00", tz="UTC"),   # Sunday 17:00 open -> Monday's candle
            pd.Timestamp("2024-01-08 22:00", tz="UTC"),   # Tuesday's candle
            pd.Timestamp("2024-01-09 22:00", tz="UTC"),   # Wednesday's candle
            pd.Timestamp("2024-07-09 21:00", tz="UTC"),   # summer: 17:00 New York = 21:00 UTC
        ])
        tue = daily.iloc[1]
        self.assertEqual((tue.bid_o, tue.bid_h, tue.bid_l, tue.bid_c), (200, 210, 190, 196))
        self.assertEqual((tue.mid_h, tue.mid_l), (210.5, 190.5))


class TestTarget(unittest.TestCase):
    def test_no_target(self):
        # §7: equal lows at 06:50/06:55 and 07:35/07:40 are not swings, so the only swing low
        # below the 50% (18,060 at 08:35) has been traded through.
        table = rows({"06:55": (17985, 17960), "07:40": (18040, 17990)})
        orders, skips = run(table)
        self.assertEqual(orders, [])
        self.assertEqual(skips, Counter({"no_target": 1}))

    def test_target_traded_through_is_skipped(self):
        # §7: 08:10 and 08:15 touch 17,990 exactly (equal lows, so no new swing). The 07:35 low
        # is traded through; the next one down, 17,950 at 06:55, is the target.
        orders, _ = run(rows({"08:10": (18090, 17990), "08:15": (18088, 17990)}))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].target, 17950)

    def test_target_older_than_24h_is_ignored(self):
        # §7: the no-target day, plus a swing low of 17,950 on the previous day.
        today = frame(rows({"06:55": (17985, 17960), "07:40": (18040, 17990)}))
        for swing_clock, expected in (("08:55", None), ("09:05", 17950)):
            # 08:55 New York yesterday is 24h05m before the 09:00 placement; 09:05 is 23h55m.
            t = pd.Timestamp(f"2024-01-10 {swing_clock}", tz=NY)
            prev = {(t - pd.Timedelta("5min")).strftime("%H:%M"): (18000, 17970),
                    t.strftime("%H:%M"): (17990, 17950),
                    (t + pd.Timedelta("5min")).strftime("%H:%M"): (18000, 17970)}
            m5 = pd.concat([frame(prev, day=date(2024, 1, 10)), today])
            orders, skips = find_orders(m5, DAILY, "NAS100_USD", DAY, "close")
            if expected is None:
                self.assertEqual(orders, [])
                self.assertEqual(skips, Counter({"no_target": 1}))
            else:
                self.assertEqual(orders[0].target, expected)

    def test_stop_below_sweep_extreme(self):
        # §7: FVG 08:50 -> 09:00 (18,085 -> 18,078); candle 1 is the candle after the sweep
        # extreme, so the stop is its high 18,095, below the 18,112 extreme.
        table = rows({"08:45": (18112, 18080, 18090), "08:50": (18095, 18085, 18088),
                      "08:55": (18086, 18065, 18070), "09:00": (18078, 18040, 18050)})
        orders, _ = run(table)
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].entry, orders[0].stop, orders[0].target), (18078, 18095, 17990))
        self.assertEqual(orders[0].notes["sweep_extreme"], 18112)


class TestRearm(unittest.TestCase):
    def test_rearm_after_no_fvg(self):
        # §5 re-arm: cycle 1 shifts at 08:55 without an FVG. 09:30 trades to 18,130: cycle 2,
        # short-term low 18,080 (09:20), FVG 09:30 -> 09:40 (18,110 -> 18,100), shift at 09:40.
        # Range 18,130 -> 18,060 (50% 18,095). Target: the 08:55 low 18,040, never traded through.
        table = rows({
            "08:45": (18112, 18076, 18092),
            "09:05": (18080, 18055, 18075), "09:10": (18095, 18070, 18090),
            "09:15": (18100, 18085, 18095), "09:20": (18105, 18080, 18090),
            "09:25": (18110, 18082, 18105), "09:30": (18130, 18110, 18115),
            "09:35": (18112, 18090, 18095), "09:40": (18100, 18060, 18070),
            **flat("09:45", "11:00", 18075, 18065),
        })
        orders, skips = run(table)
        self.assertEqual(skips, Counter({"no_fvg": 1}))
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o.cycle, 2)
        self.assertEqual((o.entry, o.stop, o.target), (18100, 18130, 18040))
        self.assertEqual(o.notes["short_term_swing"], 18080)
        self.assertEqual(o.placed_time, ny("09:45"))

    def test_rearm_after_order_placed(self):
        # §5 re-arm: the worked-example order at 09:00, then 09:20 trades to 18,125: cycle 2,
        # short-term low 18,070 (09:15), FVG 09:20 -> 09:30 (18,100 -> 18,095), shift at 09:30.
        orders, skips = run(rows(REARM_AFTER_ORDER))
        self.assertEqual(skips, Counter())
        self.assertEqual([o.cycle for o in orders], [1, 2])
        self.assertEqual(orders[0].placed_time, ny("09:00"))
        second = orders[1]
        self.assertGreater(second.placed_time, ny("09:20"))
        self.assertEqual(second.placed_time, ny("09:35"))
        self.assertEqual((second.entry, second.stop, second.target), (18095, 18125, 18040))


class TestNoLookahead(unittest.TestCase):
    def test_truncation_no_lookahead(self):
        # Review Focus 2: keep only candles that have closed by the cut. Before 09:00 there is
        # no order; from 09:00 on the order is exactly the full-data order.
        full = frame(rows())
        expected, _ = find_orders(full, DAILY, "NAS100_USD", DAY, "close")
        for cut in pd.date_range(ny("08:30"), ny("09:30"), freq="5min"):
            orders, _ = find_orders(full[full.index < cut], DAILY, "NAS100_USD", DAY, "close")
            if cut < ny("09:00"):
                self.assertEqual(orders, [], cut)
            else:
                self.assertEqual(orders, expected, cut)

    def test_truncation_other_tables(self):
        # Review Focus 2: cut at every M5 time 08:30 -> 11:05. The orders found on the cut data
        # are exactly the full-data orders placed by the cut (shift+1 FVG path, and a re-arm day).
        for name, overrides, n_orders in (("after_shift", AFTER_SHIFT, 1),
                                          ("rearm", REARM_AFTER_ORDER, 2)):
            full = frame(rows(overrides))
            expected, _ = find_orders(full, DAILY, "NAS100_USD", DAY, "close")
            self.assertEqual(len(expected), n_orders, name)
            for cut in pd.date_range(ny("08:30"), ny("11:05"), freq="5min"):
                orders, _ = find_orders(full[full.index < cut], DAILY, "NAS100_USD", DAY, "close")
                self.assertEqual(orders, [o for o in expected if o.placed_time <= cut], (name, cut))


class TestBarLength(unittest.TestCase):
    def test_one_minute_chart(self):
        # Ruling A: the same candles one minute apart (08:10 ... 08:29, then 08:30 onward).
        # The shift candle is the 6th from 08:30, i.e. 08:35, so the order is placed at 08:36.
        table = {k: v for k, v in rows().items() if k <= "09:30"}
        m1 = frame(table, step="1min", start="08:10")
        orders, skips = find_orders(m1, DAILY, "NAS100_USD", DAY, "close", bar=pd.Timedelta("1min"))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].placed_time, ny("08:36"))
        self.assertEqual((orders[0].entry, orders[0].stop, orders[0].target), (18078, 18112, 17990))


if __name__ == "__main__":
    unittest.main()
