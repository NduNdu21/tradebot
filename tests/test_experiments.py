"""Tests for src.experiments: setup R, walk-forward windows, the selection rule,
walk-forward, the random-entry control and reporting helpers (docs/strategy.md §10)."""
import unittest

import numpy as np
import pandas as pd

from src.backtest import Config, Engine, Trade, trade_table
from src.data import DEV_START, HOLDOUT_START
from src.experiments import (CELLS, MIN_SETUPS, PRIMARY, SETUP_COLUMNS, UNIT_GBP, ZONES, RandomEntryStrategy,
                             entry_bars, entry_hours, in_window, max_drawdown_pct, neighbours, random_plan,
                             select_cell, setup_table, summarize, walk_forward, weekday_table, wf_windows)
from src.setups import build_h1


def ts(s):
    return pd.Timestamp(s, tz="UTC")


def leg(pair="EUR_USD", setup_id=("long", ts("2020-01-01")), leg=1, pnl=0.0, entry_time="2020-01-02 10:00",
        exit_time="2020-01-03 10:00", exit_reason="target", fallback=False, side="long",
        entry_price=1.1038, stop=1.0990, target=1.1100):
    return {"pair": pair, "setup_id": setup_id, "leg": leg, "side": side, "entry_time": ts(entry_time),
            "entry_price": entry_price, "stop": stop, "target": target, "exit_time": ts(exit_time),
            "exit_reason": exit_reason, "fallback": fallback, "pnl_on": pnl + 1, "pnl_off": pnl,
            "pnl_capped": pnl - 1, "r_on": (pnl + 1) / 50}


def setups(rs, start="2017-01-02", pair="EUR_USD", side="long", spacing_days=1):
    """A setup table with the given R (on) values, first fills one day apart from `start`."""
    times = pd.date_range(ts(start), periods=len(rs), freq=f"{spacing_days}D")
    return pd.DataFrame({"pair": pair, "setup_id": [("x", t) for t in times], "side": side, "first_fill": times,
                         "last_exit": times + pd.Timedelta(hours=5), "legs": 1, "r_on": rs, "r_off": rs,
                         "r_capped": rs, "fallbacks": 0, "friday_legs": 0})[SETUP_COLUMNS]


def noisy(mean, n, spread):
    """n values with exactly this mean, alternating mean +/- spread."""
    return [mean + spread * (1 if i % 2 else -1) for i in range(n)]


class SetupTableTest(unittest.TestCase):
    def test_legs_summed_in_units_of_one_legs_risk(self):
        # Leg 1 loses £50, leg 2 wins £80: +0.6R (strategy ruling 2026-11-16).
        legs = pd.DataFrame([leg(leg=1, pnl=-50, exit_reason="stop"), leg(leg=2, pnl=80)])
        s = setup_table(legs)
        self.assertEqual(len(s), 1)
        self.assertAlmostEqual(s.r_off[0], 30 / UNIT_GBP)
        self.assertAlmostEqual(s.r_off[0], 0.6)
        self.assertAlmostEqual(s.r_on[0], 32 / 50)
        self.assertAlmostEqual(s.r_capped[0], 28 / 50)
        self.assertEqual(s.legs[0], 2)

    def test_two_leg_loss_is_minus_two_r(self):
        legs = pd.DataFrame([leg(leg=1, pnl=-50, exit_reason="stop"), leg(leg=2, pnl=-50, exit_reason="stop")])
        self.assertAlmostEqual(setup_table(legs).r_off[0], -2.0)

    def test_first_fill_last_exit_and_counts(self):
        legs = pd.DataFrame([
            leg(leg=1, entry_time="2020-01-02 10:00", exit_time="2020-01-03 20:00", exit_reason="friday"),
            leg(leg=2, entry_time="2020-01-02 14:00", exit_time="2020-01-03 21:00", fallback=True,
                exit_reason="stop")])
        s = setup_table(legs).iloc[0]
        self.assertEqual(s.first_fill, ts("2020-01-02 10:00"))
        self.assertEqual(s.last_exit, ts("2020-01-03 21:00"))
        self.assertEqual(s.friday_legs, 1)
        self.assertEqual(s.fallbacks, 1)

    def test_same_setup_id_on_two_pairs_is_two_setups(self):
        legs = pd.DataFrame([leg(pair="EUR_USD"), leg(pair="GBP_JPY")])
        self.assertEqual(len(setup_table(legs)), 2)

    def test_empty(self):
        self.assertEqual(list(setup_table(pd.DataFrame()).columns), SETUP_COLUMNS)


class WindowTest(unittest.TestCase):
    def test_five_rolling_years_cover_development(self):
        w = wf_windows()
        self.assertEqual(len(w), 5)
        self.assertEqual(w[0], (DEV_START, ts("2019-11-14"), ts("2020-11-14")))
        self.assertEqual(w[-1][2], HOLDOUT_START)   # never touches the hold-out
        for train_start, test_start, test_end in w:
            self.assertEqual(test_start - pd.DateOffset(years=3), train_start)
            self.assertEqual(test_start + pd.DateOffset(years=1), test_end)

    def test_setup_belongs_to_window_of_first_fill(self):
        s = setups([1.0, 2.0], start="2019-11-13")   # first fills 13th and 14th Nov
        self.assertEqual(list(in_window(s, ts("2019-11-14"), ts("2020-11-14")).r_on), [2.0])


class SelectionRuleTest(unittest.TestCase):
    def cells(self, **overrides):
        """Every cell positive (mean 0.2, 40 setups) unless overridden by name 'k3m5' etc."""
        out = {}
        for k, m in CELLS:
            out[(k, m)] = setups(overrides.get(f"k{k}m{m}", noisy(0.2, 40, 0.5)))
        return out

    def test_score_is_mean_minus_one_standard_error(self):
        r = [1.0, -1.0, 2.0, 0.0]
        s = summarize(r)
        self.assertAlmostEqual(s["score"], np.mean(r) - np.std(r, ddof=1) / 2)

    def test_neighbours_are_orthogonal(self):
        self.assertEqual(sorted(neighbours((3, 5))), [(2, 5), (3, 3), (3, 8), (4, 5)])
        self.assertEqual(sorted(neighbours((2, 3))), [(2, 5), (3, 3)])

    def test_highest_score_not_highest_mean(self):
        # K=4,mb=8 has the higher mean but is noisy; K=3,mb=5 has the higher mean - SE.
        chosen, stats = select_cell(self.cells(k4m8=noisy(0.6, 40, 4.0), k3m5=noisy(0.4, 40, 0.2)))
        self.assertGreater(stats[(4, 8)]["mean"], stats[(3, 5)]["mean"])
        self.assertEqual(chosen, (3, 5))

    def test_needs_thirty_setups(self):
        chosen, _ = select_cell(self.cells(k3m5=noisy(0.9, MIN_SETUPS - 1, 0.1)))
        self.assertNotEqual(chosen, (3, 5))
        chosen, _ = select_cell(self.cells(k3m5=noisy(0.9, MIN_SETUPS, 0.1)))
        self.assertEqual(chosen, (3, 5))

    def test_every_orthogonal_neighbour_must_be_positive(self):
        chosen, _ = select_cell(self.cells(k3m5=noisy(0.9, 40, 0.1), k3m8=noisy(-0.1, 40, 0.5)))
        self.assertNotEqual(chosen, (3, 5))

    def test_diagonal_neighbour_does_not_count(self):
        chosen, _ = select_cell(self.cells(k3m5=noisy(0.9, 40, 0.1), k4m8=noisy(-0.1, 40, 0.5)))
        self.assertEqual(chosen, (3, 5))

    def test_neighbour_with_no_setups_is_not_positive(self):
        chosen, _ = select_cell(self.cells(k3m5=noisy(0.9, 40, 0.1), k2m5=[]))
        self.assertNotEqual(chosen, (3, 5))

    def test_cell_itself_must_be_positive(self):
        # Every cell negative except the neighbours' means; nothing positive can be chosen.
        c = {cell: setups(noisy(-0.2, 40, 0.1)) for cell in CELLS}
        self.assertIsNone(select_cell(c)[0])

    def test_tie_goes_to_more_setups(self):
        same = noisy(0.25, 40, 0.0)   # zero spread: score = mean, exactly representable
        more = noisy(0.25, 60, 0.0)
        c = {cell: setups(noisy(0.125, 40, 0.0)) for cell in CELLS}
        c[(3, 5)], c[(2, 5)] = setups(same), setups(more)
        self.assertEqual(select_cell(c)[0], (2, 5))

    def test_scores_the_requested_financing_column(self):
        c = self.cells()
        c[(3, 5)] = c[(3, 5)].assign(r_capped=-1.0, r_on=2.0)
        self.assertEqual(select_cell(c, "r_on")[0], (3, 5))
        self.assertNotEqual(select_cell(c, "r_capped")[0], (3, 5))


class WalkForwardTest(unittest.TestCase):
    def test_choice_uses_training_years_only(self):
        # Every cell: +0.2 throughout. K=3,mb=5 is best in training and terrible after;
        # K=4,mb=8 is great after. Year 1 must still trade K=3,mb=5.
        base = {c: setups(noisy(0.2, 2900, 0.3), start="2016-11-15") for c in CELLS}
        best = base[(3, 5)].copy()
        best.loc[best.first_fill < ts("2019-11-14"), "r_on"] += 0.5
        best.loc[best.first_fill >= ts("2019-11-14"), "r_on"] -= 3
        later = base[(4, 8)].copy()
        later.loc[later.first_fill >= ts("2019-11-14"), "r_on"] += 3
        base[(3, 5)], base[(4, 8)] = best, later
        years, oos = walk_forward(base)
        self.assertEqual(years[0]["chosen"], (3, 5))
        first_year = oos[oos.test_start == ts("2019-11-14")]
        self.assertTrue((first_year.cell == (3, 5)).all())
        self.assertLess(first_year.r_on.mean(), 0)

    def test_year_with_no_eligible_cell_is_sat_out(self):
        cells = {c: setups(noisy(-0.2, 2900, 0.3), start="2016-11-15") for c in CELLS}
        years, oos = walk_forward(cells)
        self.assertTrue(all(y["chosen"] is None for y in years))
        self.assertEqual(len(oos), 0)

    def test_empty_result_keeps_column_types(self):
        cells = {c: setups(noisy(-0.2, 2900, 0.3), start="2016-11-15") for c in CELLS}
        _, oos = walk_forward(cells)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(oos.first_fill))
        self.assertEqual(weekday_table(oos), [])
        self.assertEqual(max_drawdown_pct(oos), 0.0)

    def test_oos_only_contains_test_years(self):
        cells = {c: setups(noisy(0.2, 2900, 0.3), start="2016-11-15") for c in CELLS}
        _, oos = walk_forward(cells)
        self.assertGreaterEqual(oos.first_fill.min(), ts("2019-11-14"))
        self.assertLess(oos.first_fill.max(), HOLDOUT_START)


class ReportHelpersTest(unittest.TestCase):
    def test_weekday_is_new_york(self):
        # Sunday 22:30 UTC in January is Sunday 17:30 New York: the week's reopen.
        s = setups([1.0], start="2020-01-05 22:30")
        self.assertEqual(weekday_table(s)[0][0], "Sunday")
        s = setups([1.0], start="2020-01-07 03:00")   # Monday 22:00 New York
        self.assertEqual(weekday_table(s)[0][0], "Monday")

    def test_max_drawdown_from_closed_pnl(self):
        # +2R, -3R, -1R, +5R in £50 units: peak 100, trough -100 -> £200 = 2%.
        self.assertAlmostEqual(max_drawdown_pct(setups([2, -3, -1, 5])), 2.0)

    def test_primary_and_zone_variants(self):
        self.assertEqual((PRIMARY.mode, PRIMARY.target, PRIMARY.time_limit, PRIMARY.anchoring),
                         ("a", "tp2", 96, "running"))
        self.assertTrue(PRIMARY.friday_close)
        self.assertEqual(PRIMARY.score_column, "r_on")
        self.assertIs(ZONES[0.0], PRIMARY)
        self.assertEqual(ZONES[-0.05].name, "zone 56.8-71.4")
        self.assertEqual(ZONES[0.10].name, "zone 71.8-86.4")


# ---- random-entry control ----

def hourly(start, hours, price=1.1000):
    """Flat M5 bid/ask candles for `hours` hours (EUR_USD and GBP_USD)."""
    idx = pd.date_range(ts(start), periods=hours * 12, freq="5min")
    eu = pd.DataFrame(index=idx)
    for f in "ohlc":
        eu[f"bid_{f}"], eu[f"ask_{f}"] = price - 0.0001, price + 0.0001
    eu["volume"] = 1
    gu = eu.copy()
    gu[gu.columns] = 1.25
    return {"EUR_USD": eu, "GBP_USD": gu}


class FakeEngine:
    def __init__(self):
        self.active = {"EUR_USD": None}
        self.placed = []

    def place_market(self, pair, setup_id, leg, side, stop, target=None, target_r=None):
        self.placed.append((pair, setup_id, leg, side, stop, target))


def drive(strategy, h1, engine):
    """Call on_h1_close for every bar, as the engine would; return the decision hours that placed."""
    hours = []
    for hour in h1["EUR_USD"].index:
        n = len(engine.placed)
        strategy.on_h1_close(engine, "EUR_USD", hour)
        if len(engine.placed) > n:
            hours.append(hour)
    return hours


class RandomEntryTest(unittest.TestCase):
    start = "2020-01-06"   # a Monday
    window = (ts("2020-01-06"), ts("2020-01-11"))

    def item(self, side="long", legs=((1, 0.0040, 0.0060),)):
        return {"pair": "EUR_USD", "side": side, "window": self.window, "legs": list(legs)}

    def test_entry_bars_skip_the_friday_pull_window(self):
        idx = build_h1(hourly(self.start, 24 * 5))["EUR_USD"].index
        bars = entry_bars(idx)
        self.assertNotIn(idx[0], bars)                       # no decision bar before it
        self.assertIn(ts("2020-01-10 20:00"), bars)          # Friday 15:00 New York
        self.assertNotIn(ts("2020-01-10 21:00"), bars)       # Friday 16:00 New York: pulled
        self.assertNotIn(ts("2020-01-10 23:00"), bars)

    def test_hour_of_day_drawn_from_real_fills(self):
        h1 = build_h1(hourly(self.start, 24 * 5))
        rng = np.random.default_rng(0)
        strategy = RandomEntryStrategy(h1, [self.item() for _ in range(3)], {"EUR_USD": np.array([14])}, rng)
        engine = FakeEngine()
        decided = drive(strategy, h1, engine)
        # Entry opens at 14:00 UTC, so each decision is the 13:00 bar's close, on three different days.
        self.assertEqual(len(decided), 3)
        self.assertTrue(all(h.hour == 13 for h in decided))
        self.assertTrue(all(self.window[0] <= h < self.window[1] for h in decided))

    def test_entry_hours_are_utc_hours_of_first_fills(self):
        s = setups([1, 1], start="2020-01-06 14:20")
        self.assertEqual(list(entry_hours(s)["EUR_USD"]), [14, 14])

    def test_stop_and_target_at_real_distances_from_decision_close(self):
        h1 = build_h1(hourly(self.start, 24 * 5, price=1.2000))
        strategy = RandomEntryStrategy(h1, [self.item(legs=((1, 0.0040, 0.0060), (2, 0.0030, 0.0070)))],
                                       {"EUR_USD": np.array([10])}, np.random.default_rng(1))
        engine = FakeEngine()
        drive(strategy, h1, engine)
        (_, _, l1, side, stop1, target1), (_, _, l2, _, stop2, target2) = engine.placed
        self.assertEqual((l1, l2, side), (1, 2, "long"))
        self.assertAlmostEqual(stop1, 1.1960)
        self.assertAlmostEqual(target1, 1.2060)
        self.assertAlmostEqual(stop2, 1.1970)
        self.assertAlmostEqual(target2, 1.2070)

    def test_short_mirrors(self):
        h1 = build_h1(hourly(self.start, 24 * 5, price=1.2000))
        strategy = RandomEntryStrategy(h1, [self.item(side="short")], {"EUR_USD": np.array([10])},
                                       np.random.default_rng(1))
        engine = FakeEngine()
        drive(strategy, h1, engine)
        _, _, _, side, stop, target = engine.placed[0]
        self.assertEqual(side, "short")
        self.assertAlmostEqual(stop, 1.2040)
        self.assertAlmostEqual(target, 1.1940)

    def test_busy_pair_redraws_later_in_the_same_year(self):
        h1 = build_h1(hourly(self.start, 24 * 5))
        strategy = RandomEntryStrategy(h1, [self.item()], {"EUR_USD": np.array([14])}, np.random.default_rng(3))
        engine = FakeEngine()
        engine.active["EUR_USD"] = "busy"
        first_due = min(h for _, h in strategy.due)
        for hour in h1["EUR_USD"].index[h1["EUR_USD"].index <= first_due]:
            strategy.on_h1_close(engine, "EUR_USD", hour)
        self.assertEqual(engine.placed, [])
        later = [h for _, h in strategy.due]
        self.assertEqual(strategy.redraws, 1)
        if later:   # redrawn to a later day at the same hour
            self.assertGreater(later[0], first_due)
            self.assertEqual(later[0].hour, 13)
        else:
            self.assertEqual(strategy.dropped, 1)

    def test_dropped_when_no_bar_at_that_hour_is_left(self):
        h1 = build_h1(hourly(self.start, 10))   # Monday 00:00-09:00 only
        strategy = RandomEntryStrategy(h1, [self.item()], {"EUR_USD": np.array([14])}, np.random.default_rng(0))
        self.assertEqual(strategy.dropped, 1)
        self.assertEqual(dict(strategy.due), {})

    def test_runs_through_the_real_engine(self):
        data = hourly(self.start, 24 * 5)
        h1 = build_h1(data)
        strategy = RandomEntryStrategy(h1, [self.item(legs=((1, 0.0040, 0.0060),))], {"EUR_USD": np.array([14])},
                                       np.random.default_rng(0))
        engine = Engine(data, strategy, Config(financing_mode="off", friday_close=True)).run()
        # Flat prices: never stopped or hit target, so it is open or closed by the Friday pull.
        (trade,) = engine.open["EUR_USD"] + engine.closed
        self.assertIn(trade.exit_reason, (None, "friday"))
        self.assertEqual(trade.entry_time.hour, 14)
        self.assertEqual(trade.entry_time.minute, 0)
        self.assertAlmostEqual(trade.entry_price, 1.1001)          # the ask at the open
        self.assertAlmostEqual(trade.stop, 1.1000 - 0.0040)

    def test_plan_from_real_trade_table(self):
        # The plan reads stop and target from the engine's own trade table.
        sid = ("long", ts("2020-01-01"))
        trade = Trade("EUR_USD", sid, 1, "long", ts("2020-01-02 10:00"), 1.1038, 1000, 1.0990, 1.1100, 50.0,
                      exit_time=ts("2020-01-03"), exit_price=1.1100, exit_reason="target", pnl_gbp=49.6)
        legs = trade_table([trade])
        oos = setup_table(legs).assign(cell=[(3, 5)], test_start=ts("2019-11-14"), test_end=ts("2020-11-14"))
        (item,) = random_plan(oos, {(3, 5): legs})
        self.assertAlmostEqual(item["legs"][0][1], 0.0048)
        self.assertAlmostEqual(item["legs"][0][2], 0.0062)

    def test_plan_copies_pair_side_window_and_leg_distances(self):
        sid = ("long", ts("2020-01-01"))
        legs = pd.DataFrame([leg(setup_id=sid, leg=2, entry_price=1.1024, stop=1.0990, target=1.1100),
                             leg(setup_id=sid, leg=1, entry_price=1.1038, stop=1.0990, target=1.1100),
                             leg(setup_id=("long", ts("2020-02-01")), leg=1)])
        oos = setup_table(legs.iloc[:2]).assign(cell=[(3, 5)], test_start=ts("2019-11-14"),
                                                test_end=ts("2020-11-14"))
        (item,) = random_plan(oos, {(3, 5): legs})
        self.assertEqual((item["pair"], item["side"]), ("EUR_USD", "long"))
        self.assertEqual(item["window"], (ts("2019-11-14"), ts("2020-11-14")))
        self.assertEqual([l for l, _, _ in item["legs"]], [1, 2])
        self.assertAlmostEqual(item["legs"][0][1], 0.0048)
        self.assertAlmostEqual(item["legs"][0][2], 0.0062)
        self.assertAlmostEqual(item["legs"][1][1], 0.0034)
        self.assertAlmostEqual(item["legs"][1][2], 0.0076)


if __name__ == "__main__":
    unittest.main()
