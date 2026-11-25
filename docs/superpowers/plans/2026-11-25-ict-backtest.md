# ICT Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest the approved ICT sweep → structure shift → FVG strategy on NAS100_USD and SPX500_USD
development data and report whether it passes its pre-registered bar.

**Architecture:** A new package `src/ict/` holds everything ICT-specific; the Fibonacci engine (`src/backtest.py`,
`src/setups.py`, `src/swings.py`) is frozen and untouched. Signals are found on M5 candles at each M5 close
(`setups.py`), producing at most one limit order per instrument per day; a small M1 simulator (`fills.py`) plays
each order out and returns a trade in R. A runner applies the §10 protocol and writes `docs/backtests.md`.

**Tech Stack:** Python 3.9 (`.venv`), pandas 2.3.3, oandapyV20 0.7.2, `unittest` (`python -m unittest`).

**Spec:** `docs/strategy-ict.md` (approved 2026-11-25). Read it first; section numbers below (§n) refer to it.

## Global Constraints

- Practice API only: `load_config()` already refuses anything but `OANDA_ENV="practice"`. Never print the token.
- Instruments: `NAS100_USD`, `SPX500_USD`. Data from 2016-10-03.
- Development 2016-11-14 → 2024-11-13. Hold-out 2024-11-14 → 2026-11-13 is cut off at load time; nothing in this
  plan reads it (`include_holdout=True` is never passed). The hold-out run is a later, owner-approved step.
- All session times are America/New_York with daylight saving: sweeps and entries 08:30–11:00, flat at 12:00.
- Signals on mid prices, fills on bid/ask. Shorts enter at the bid, exit at the ask; longs the opposite.
- Slippage on stops and the 12:00 exit: NAS100_USD 1.0 point, SPX500_USD 0.5 point (owner's ruling after the
  Task 1 spread check).
- Results in R = P&L ÷ |entry − stop| in price.
- Explain trade logic in comments; one test per strategy rule (CLAUDE.md). Log decisions in `docs/decisions.md`.
- Every *(proposed)* rule in the spec is implemented exactly as written; any doubt goes back to the owner.

## Review Focus

1. **Daylight saving weeks:** 08:30 New York is 13:30 UTC in winter and 12:30 UTC in summer; the US switches
   2–3 weeks before Europe. A session in the week of 2024-03-11 must still start at 08:30 New York. (Task 2)
2. **Lookahead:** a swing used before the candle to its right has closed, or an FVG whose candle 3 hasn't closed.
   Cutting the data at a random time must not change any order placed before the cut. (Tasks 3, 4, 7)
3. **Sweep, shift and FVG in the same few candles:** the sweep candle can be FVG candle 1, and the shift candle
   can be candle 3, as in the worked example. The worked example must produce exactly its order. (Task 4)
4. **Missing or short days:** a US holiday, an early close, or a gap in M5 candles between 08:00 and 12:00 skips
   the day and is counted, rather than crashing or trading on partial data. (Tasks 2, 7)
5. **The first M1 bar after the order:** it can gap through the stop, or touch the stop and target together;
   stop-first and gap fills must apply there too. (Task 5)

---

### Task 1: Download M1 index candles and load them

**Files:**
- Modify: `src/download.py` (add `--granularity`; keep defaults for the FX pairs)
- Modify: `src/data.py` (add `load_candles`, `to_timeframe`; `load_m5` becomes a wrapper)
- Test: `tests/test_data.py`, `tests/test_download.py`

**Interfaces:**
- Produces: `load_candles(instrument: str, granularity: str, include_holdout=False, data_dir=DATA_DIR) -> DataFrame`
  reading `data/<INSTRUMENT without _>_<granularity>.csv` (e.g. `data/NAS100USD_M1.csv`), UTC index, hold-out cut
  exactly as `load_m5`.
- Produces: `output_path(instrument: str, granularity: str) -> str` in `src/download.py`.
- Produces: `to_timeframe(df: DataFrame, rule: str) -> DataFrame` resampling bid/ask OHLC (+ mid via `add_mid`)
  with `label="left", closed="left"`, dropping empty bins. `to_h1(m5)` becomes `to_timeframe(m5, "1h")`.

- [ ] **Step 1: Write failing tests**
  - `test_load_candles_cuts_holdout`: a CSV with rows at 2024-11-13 23:59 and 2024-11-14 00:00 → only the first is
    returned by default; both with `include_holdout=True`.
  - `test_to_timeframe_m1_to_m5`: ten M1 rows 13:30–13:39 UTC → two M5 rows at 13:30 and 13:35; `bid_h` = max,
    `bid_o` = first, `ask_c` = last, `mid_h` = (bid_h + ask_h)/2 for the bin.
  - `test_download_granularity_in_filename`: the output path for `NAS100_USD`, `M1` is `data/NAS100USD_M1.csv`.
- [ ] **Step 2:** `.venv/bin/python -m unittest tests.test_data tests.test_download` → the new tests FAIL.
- [ ] **Step 3:** Implement. `GRANULARITY` becomes the `--granularity` default (`"M5"`); `fetch_candles` takes the
  granularity.
- [ ] **Step 4:** Run the whole suite: `.venv/bin/python -m unittest` → all pass (157 existing + new).
- [ ] **Step 5:** Download (about 700 requests per instrument):
  `.venv/bin/python -m src.download --granularity M1 --instruments NAS100_USD SPX500_USD`
  Expected: two files, first candle 2016-10-03, last candle within the past day.
- [ ] **Step 6:** Check the data and the cost placeholders. With a one-off script in the scratchpad (not
  committed), print per instrument and per year: the share of 08:00–12:00 New York M5 slots with no candle, and the
  median and 90th-percentile spread (ask_c − bid_c) on M1 candles 08:30–12:00 New York. Write the numbers to
  `docs/decisions.md` under a dated heading. If the median spread exceeds the slippage placeholder, **stop and
  ask the owner** before changing §8.
- [ ] **Step 7:** Commit `src/download.py src/data.py tests/ docs/decisions.md`, message
  "Download M1 index candles; generic candle loader".

### Task 2: New York session calendar

**Files:**
- Create: `src/ict/__init__.py`, `src/ict/calendar.py`
- Test: `tests/test_ict_calendar.py`

**Interfaces:**
- Produces: `NY = "America/New_York"`.
- Produces: `ny_time(utc_index: DatetimeIndex) -> DatetimeIndex` (tz-converted).
- Produces: `session_times(day: datetime.date) -> dict` with UTC Timestamps for keys `"levels"` (08:30),
  `"entry_end"` (11:00), `"exit"` (12:00), `"check_start"` (08:00).
- Produces: `is_skipped_market_day(day: date) -> Optional[str]`: `"holiday"` or `"early_close"` for NYSE full
  closures and early-close days from 2016-10-03 to 2026-11-13 (hard-coded lists from the NYSE's published
  calendars, one line per date with its name in a comment), else `None`.
- Produces: `missing_m5(m5: DataFrame, day: date) -> bool`: True if any of the 48 M5 slots 08:00–11:55 New York
  has no candle.

- [ ] **Step 1: Write failing tests**
  - `test_session_winter`: 2024-01-09 → levels 13:30 UTC, exit 17:00 UTC.
  - `test_session_us_dst_before_europe`: 2024-03-12 → levels 12:30 UTC (Review Focus 1).
  - `test_holidays`: 2023-07-04 → "holiday"; 2024-03-29 (Good Friday) → "holiday"; 2023-11-24 → "early_close";
    2024-01-09 → None.
  - `test_missing_m5`: a full 08:00–11:55 frame → False; the same frame without 10:15 → True.
- [ ] **Step 2:** Run `tests.test_ict_calendar` → FAIL.
- [ ] **Step 3:** Implement with `zoneinfo`/pandas tz conversion.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit, "Add New York session calendar for ICT".

### Task 3: Three-candle swings

**Files:**
- Create: `src/ict/swings.py`
- Test: `tests/test_ict_swings.py`

**Interfaces:**
- Produces: `Swing = namedtuple("Swing", "kind price candle_time confirmed_time")`, `kind` ∈ {"high", "low"};
  `candle_time` is the swing candle's open; `confirmed_time` is the **close** of the candle to its right
  (its open + the bar length).
- Produces: `find_swings(df: DataFrame, bar: Timedelta) -> list[Swing]` on `mid_h`/`mid_l`, sorted by
  `confirmed_time`. A swing high needs its high strictly above both neighbours' highs (§2; ties are not swings).
- Produces: `latest(swings, kind, before: Timestamp, field="confirmed_time") -> Optional[Swing]`: the last swing of
  that kind whose `field` is strictly before `before`.

- [ ] **Step 1: Write failing tests**
  - `test_swing_high_three_candles`: highs 10, 12, 11 → one swing high at 12, confirmed at candle 3's close.
  - `test_equal_highs_not_swing`: highs 10, 12, 12, 11 → no swing high.
  - `test_closes_ignored`: same highs with any closes → same result.
  - `test_not_usable_before_confirmed` (Review Focus 2): `latest(..., before=candle 3's open)` → None;
    `before=candle 3's close + 1 min` → the swing.
  - `test_daily_swing_low`: works unchanged on daily candles with `bar=1 day`.
- [ ] **Step 2–4:** Run → FAIL; implement; run → PASS.
- [ ] **Step 5:** Commit, "Add three-candle swing detection".

### Task 4: Setup detection (sweep → shift → FVG → order)

**Files:**
- Create: `src/ict/setups.py`
- Test: `tests/test_ict_setups.py`

**Interfaces:**
- Consumes: `session_times` (Task 2), `find_swings`, `latest`, `Swing` (Task 3).
- Produces:
  ```python
  @dataclass
  class Order:
      instrument: str
      day: date
      side: str            # "short" or "long"
      placed_time: Timestamp   # close of the candle that completed shift + FVG
      entry: float         # FVG near edge (candle 3's high for a short)
      stop: float          # candle 1's high for a short
      target: float
      sweep_level: float
      notes: dict          # sweep extreme, short-term swing, FVG bounds, dealing-range 50%, daily 50%
      cycle: int           # 1 for the first cycle on a level that day, 2+ for re-armed cycles (§5)
  ```
- Produces: `daily_bias_mid(daily: DataFrame, at: Timestamp) -> Optional[float]`: midpoint of the latest daily swing
  high and low confirmed before `at` (§3); daily candles are 17:00→17:00 New York, built by
  `to_daily(m1_or_m5) -> DataFrame` in this module.
- Produces: `find_orders(m5: DataFrame, daily: DataFrame, instrument: str, day: date, shift_rule: str) -> tuple[list[Order], Counter]`
  with `shift_rule` ∈ {"close", "wick"}. Walks M5 closes from 08:30 to 11:00 New York and returns every order the
  rules produce that day in time order, plus a Counter of skip reasons (`"no_shift"`, `"no_fvg"`,
  `"fvg_not_premium"`, `"bias"`, `"no_target"`, `"too_late"`). Task 6 applies the one-trade-per-day rule.

Rules to implement, all from the spec (shorts; longs mirror): levels = latest M5 swing high and low confirmed
before 08:30 (§4); a sweep is a candle 08:30–11:00 whose `mid_h` exceeds the level, each level once a day;
until 11:00, any candle trading above the current sweep extreme becomes the new extreme and restarts the cycle
(new short-term low, shift and FVG), whether before the shift, after a shift that produced no order, or after an
order was placed (§5 re-arm); no order placed at or after 11:00 (§6); a target swing low is traded through if any
M5 `mid_l` ≤ its price after the swing candle up to the placement candle (§7); a daily swing is usable only after
the daily candle to its right closes at 17:00 (§3); short-term low =
latest swing low whose `candle_time` is before the sweep-extreme candle; shift = `mid_c` below it ("close") or
`mid_l` below it ("wick"); FVG window and selection, premium check against the dealing range fixed at placement,
entry, stop and target exactly per §6–§7; bias check per §3 against the entry price.

- [ ] **Step 1: Write failing tests** (each builds M5 mid candles from a small table; ask = bid + 1 point)
  - `test_worked_example_short`: the spec's table (levels 18,100 / 17,990; sweep 18,112 at 08:45; short-term low
    18,060 at 08:35; shift close 18,052 at 08:55; FVG 18,090 → 18,078; daily range 17,600–18,400) → exactly one
    Order: entry 18,078, stop 18,112, target 17,990, placed at 09:00 New York (08:55 candle's close)
    (Review Focus 3).
  - `test_worked_example_long`: the same prices mirrored around 18,000 → entry 17,922, stop 17,888, target 18,010.
  - `test_wick_rule_fires_where_close_does_not`: the 08:55 candle's low is 18,055 and close 18,065 → "wick" gives
    an order, "close" gives `no_shift`.
  - `test_no_fvg_no_trade`: candle 1's low 18,076 (overlaps candle 3's high 18,078) → `no_fvg`.
  - `test_fvg_must_be_premium`: lowest low at placement 18,000 (50% = 18,056) with the FVG edge 18,078 → order
    exists; lowest low 18,050 (50% = 18,081) → `fvg_not_premium`.
  - `test_bias_blocks_short_in_discount`: daily range 18,000–19,000 (50% 18,500) → `bias`.
  - `test_no_target`: no swing low below the dealing-range 50% in the prior 24 h → `no_target`.
  - `test_two_fvgs_uses_nearest_sweep`: two qualifying FVGs → the one whose candle 1 is closest to the sweep.
  - `test_new_high_before_shift_moves_extreme`: a higher high at 08:50 before the shift → stop at the new
    extreme's candle-1 high.
  - `test_no_sweep_after_1100`: a sweep candle at 11:00 → no order.
  - `test_rearm_after_no_fvg`: the worked-example shift with no FVG (`no_fvg`), then a 09:30 candle trades to
    18,130, a new short-term low breaks and an FVG forms → one Order with `cycle == 2`, stop above the new
    cycle's candle 1.
  - `test_rearm_after_order_placed`: the worked-example order at 09:00, then a 09:20 high of 18,125 and a new
    shift + FVG → two Orders, cycles 1 and 2, the second placed after 09:20.
  - `test_too_late`: shift candle 10:55 (closes 11:00) with FVG candle 3 at 11:00 → `too_late`, no order.
  - `test_target_traded_through_is_skipped`: an M5 low touching 17,990 exactly between the swing and placement →
    17,990 is not the target; the next qualifying low (or `no_target`) is used.
  - `test_stop_below_sweep_extreme`: candle 1 is the candle after the sweep extreme, high 18,095 → stop 18,095,
    not 18,112.
  - `test_daily_bias_waits_for_confirmation`: a daily swing high closing Tuesday 17:00 is ignored by
    `daily_bias_mid` at Wednesday 09:00 and used at Thursday 09:00; changing only the forming day's candle never
    changes the result.
  - `test_truncation_no_lookahead` (Review Focus 2): for the worked example, cutting the frame at each M5 time from
    08:30 to 09:00 gives no order before 09:00 and the same order at and after it.
- [ ] **Step 2:** Run `tests.test_ict_setups` → FAIL.
- [ ] **Step 3:** Implement. Comment each rule with its spec section and episode reference.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit, "Add ICT setup detection".

### Task 5: M1 order simulator

**Files:**
- Create: `src/ict/fills.py`
- Test: `tests/test_ict_fills.py`

**Interfaces:**
- Consumes: `Order` (Task 4), `session_times` (Task 2).
- Produces: `SLIPPAGE = {"NAS100_USD": 1.0, "SPX500_USD": 0.5}`.
- Produces: `@dataclass Result: order, status ("filled", "cancelled_time", "cancelled_stop", "cancelled_target"),
  fill_time, fill_price, exit_time, exit_price, exit_reason ("stop", "target", "time"), r: Optional[float],
  stop_first: bool`.
- Produces: `simulate(order: Order, m1: DataFrame) -> Result`.

Rules (§6–§9): only M1 bars opening at or after `placed_time` are used. Before a fill, the order is cancelled by
11:00 New York, by price touching the stop, or by price touching the target, whichever comes first (checked on the
side that would trigger each: shorts fill on `bid_h ≥ entry`, stop on `ask_h ≥ stop`, target on `ask_l ≤ target`).
A bar that opens beyond the entry fills at its open. If one bar reaches both the entry and the stop, it fills
and is stopped out; if it reaches both the entry and the target, the order counts as `cancelled_target` (both are
the conservative reading of a bar we can't see inside). After the fill: gap through the stop exits at the open plus
slippage; stop and target in one bar → stop first, `stop_first=True`; a bar filled inside its range can be stopped
in it but reaches its target only from the next bar; open at 12:00 → exit at that bar's open plus slippage.
R = signed price P&L ÷ |entry − stop|, where `entry` is the order price.

- [ ] **Step 1: Write failing tests** (worked-example short: entry 18,078, stop 18,112, target 17,990; spread 1)
  - `test_target_hit`: fill at 18,078 bid, later ask_l ≤ 17,990 → exit 17,990, r = +88/34.
  - `test_stop_hit_with_slippage`: ask_h reaches 18,112 → exit 18,113, r = −35/34.
  - `test_same_bar_stop_first` (Review Focus 5): the first bar after the fill touches both → stop, `stop_first`.
  - `test_gap_through_stop_on_fill_bar` (Review Focus 5): a bar opening at bid 18,120 → fills at 18,120 and exits at
    the same open's ask + slippage.
  - `test_cancel_at_1100`, `test_cancel_when_target_touched_first`, `test_cancel_when_stop_touched_first`.
  - `test_time_exit_1200`: still open at 12:00 New York → exit at that bar's ask open + 1.0.
  - `test_no_fill_in_creating_candle`: a bid_h ≥ 18,078 at 08:57 (inside the 08:55 candle) is ignored.
  - `test_long_mirror`: the mirrored long fills on the ask and exits on the bid.
- [ ] **Step 2–4:** Run → FAIL; implement; run → PASS.
- [ ] **Step 5:** Commit, "Add M1 order simulator for ICT".

### Task 6: Daily runner and trade table

**Files:**
- Create: `src/ict/run.py`
- Test: `tests/test_ict_run.py`

**Interfaces:**
- Consumes: `load_candles`, `to_timeframe` (Task 1); `is_skipped_market_day`, `missing_m5` (Task 2);
  `to_daily`, `find_orders` (Task 4); `simulate` (Task 5).
- Produces: `run(instrument: str, shift_rule: str, exec_tf: str = "5min", m1: Optional[DataFrame] = None) -> tuple[DataFrame, Counter]`.
  One row per filled trade with columns `instrument, day, side, placed_time, entry, stop, target, fill_time,
  exit_time, exit_reason, r, stop_first, cycle, year`; Counter of skipped days and setups by reason.
  `exec_tf` is the signal chart (`"5min"` primary; `"1min"`, `"2min"` for the §10 sensitivity).
- Rule: per instrument per day, simulate orders in time order; an order placed while another is pending is
  dropped (`"busy"`); after the first fill, later orders are dropped (`"one_per_day"`).
- Produces: `python -m src.ict.run` writes `data/ict/trades_<instrument>_<shift_rule>_<exec_tf>.csv` for both
  instruments × {"close", "wick"} × {"5min"}, and the 1min/2min sensitivities for the close rule.

- [ ] **Step 1: Write failing tests**
  - `test_one_trade_per_day`: a synthetic day with two orders that would both fill → one trade, Counter
    `one_per_day` = 1.
  - `test_pending_blocks_second_order`: the second order arrives while the first is pending → `busy`.
  - `test_rearmed_order_after_cancel`: cycle 1's order is cancelled by the new high touching its stop; cycle 2's
    order then fills → one trade with `cycle == 2`, no `busy`.
  - `test_skips_holiday_and_missing_day` (Review Focus 4): 2023-07-04 → `holiday`; a day with 10:15 missing →
    `missing_m5`; neither raises.
- [ ] **Step 2–4:** Run → FAIL; implement; run → PASS.
- [ ] **Step 5:** Run `.venv/bin/python -m src.ict.run` on real development data. Expected: CSVs written; print trade
  counts per instrument and rule. Don't look at R yet beyond confirming the column is filled; results are read in
  Task 8.
- [ ] **Step 6:** Commit, "Add ICT daily runner".

### Task 7: Statistics, random-entry control and lookahead check

**Files:**
- Create: `src/ict/stats.py`
- Test: `tests/test_ict_stats.py`

**Interfaces:**
- Consumes: trade tables from Task 6; `simulate`, `Order` (Tasks 4–5).
- Produces: `summary(trades: DataFrame) -> dict` with `n, mean_r, se, mean_minus_1se, mean_minus_2se`, and the same
  per `instrument`, `side`, `year`; `stop_first` count; `max_drawdown_pct(trades, risk=0.005)` on the R sequence in
  fill-time order.
- Produces: `random_control(trades: DataFrame, m1_by_instrument: dict, runs=1000, seed=2026) -> list[float]`: for
  each real trade, a market entry at the open of a random M5 candle 08:30–10:55 New York on the same day, same side,
  same stop and target distances from that fill price, simulated with `simulate`; returns each run's mean R.
  The market entry is an `Order` whose `entry` is that candle's open on the fill side and whose `placed_time` is
  the candle's open, so `simulate` fills it at the first M1 open.
- Produces: `truncation_check(instrument, shift_rule, cuts=25, seed=2026) -> bool`: orders placed before each random
  cut are identical with and without the data after it (Review Focus 2).

- [ ] **Step 1: Write failing tests**
  - `test_summary_values`: R = [1, −1, 2, −1] → mean 0.25, se = std(ddof=1)/2.
  - `test_drawdown`: R = [1, −1, −1, −1, 2] at 0.5% → 1.5%.
  - `test_random_control_reproducible`: same seed → same list; length = runs.
  - `test_truncation_catches_peek`: a planted one-candle peek in a copy of `find_orders` makes the check fail.
- [ ] **Step 2–4:** Run → FAIL; implement; run → PASS.
- [ ] **Step 5:** Commit, "Add ICT statistics, random control, truncation check".

### Task 8: Pre-registered evaluation and report

**Files:**
- Create: `src/ict/evaluate.py`
- Modify: `docs/backtests.md` (new section "ICT, development"), `docs/decisions.md`, `README.md` (status row)
- Test: `tests/test_ict_evaluate.py`

**Interfaces:**
- Consumes: `summary`, `random_control`, `truncation_check` (Task 7); trade CSVs (Task 6).
- Produces: `passes(stats: dict, control_p95: float, se_multiple: int) -> tuple[bool, list[str]]` returning the
  failed criteria. Criteria (§10): `n ≥ 200`; `mean_r ≥ 0.1`; `mean_r − se_multiple × se > 0`; mean R > 0 on each
  instrument; `mean_r > control_p95`.
- Produces: `verdict(primary_ok: bool, rescue_ok: Optional[bool]) -> str` → `"primary to hold-out"`,
  `"rescue to hold-out"` or `"fail"`. The rescue is only evaluated when the primary fails.

- [ ] **Step 1: Write failing tests**
  - `test_passes_all`: n 250, mean 0.15, se 0.04, both instruments positive, p95 0.05 → (True, []).
  - `test_rescue_needs_two_se`: mean 0.12, se 0.07 → passes with `se_multiple=1`, fails with 2 on the SE criterion.
  - `test_fails_on_one_instrument_negative`.
  - `test_verdict`: (True, None) → "primary to hold-out"; (False, True) → "rescue to hold-out";
    (False, False) → "fail".
- [ ] **Step 2–4:** Run → FAIL; implement; run → PASS.
- [ ] **Step 5:** Run `.venv/bin/python -m src.ict.evaluate`. It runs the truncation check first (abort if it
  fails), then the primary's control and pass test, then the rescue only if needed, and writes the report: verdict,
  every criterion with its value, per-instrument/side/year tables, skip counts, stop-first count, the other shift
  rule and the M1/M2 sensitivities (reported, not judged).
- [ ] **Step 6:** Full suite: `.venv/bin/python -m unittest` → all pass.
- [ ] **Step 7:** Record the verdict in `docs/decisions.md` and the README status row; commit
  "Evaluate ICT strategy on development data". **Stop here and show the owner the result.** The hold-out run is not
  part of this plan.
