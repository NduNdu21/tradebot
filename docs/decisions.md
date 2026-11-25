# Decisions

## 2026-11-13 — Project setup
- Python 3.9.6 (macOS system Python) in `.venv/`; deps pinned in `requirements.txt`.
- `requests` pinned explicitly: oandapyV20 0.7.2 imports it but doesn't declare it.
- `src/config.py` loads `.env` and refuses to run unless `OANDA_ENV == "practice"`.
- Run scripts as modules from the repo root, e.g. `python -m src.balance`.

## 2026-11-13 — Historical data download
- `python -m src.download` writes `data/EURUSD_H1.csv` and `data/GBPJPY_H1.csv` (5 years by default, `--years N` to change).
- Saves mid, bid and ask OHLC so the backtester can model the spread (longs fill at ask, shorts at bid). Times are UTC.
- Only completed candles are saved. Paging stops at the still-forming candle, not on page size (with `includeFirst=False` a full page holds 4999 candles).
- `data/` is gitignored; regenerate it with the script.

## 2026-11-14 — Strategy rules and backtest plan
- Rules agreed with the owner are written in `docs/strategy.md`. Items marked *(proposed)* still need sign-off.
- Swings use the confirmed origin plus the running extreme, so there's no lookahead.
  A fully confirmed ATR swing high usually arrives after price has already passed the entry zone.
- 10 years of M5 bid/ask from OANDA replace the H1 download; H1 is resampled from M5.
- Hold-out 2024-11-14 → 2026-11-13 is pre-registered: one look only, no re-tuning if it fails.

## 2026-11-14 — M5 data (step 1)
- `python -m src.download` now fetches M5 bid/ask from 2016-10-03 (six weeks of warm-up before 2016-11-14).
  About 754k candles and 72 MB per pair. The old H1 files were removed.
- `src/data.py` builds H1 from M5 on the UTC hour grid. Mid = (bid + ask) / 2, field by field. Empty hours are dropped.
- `load_m5` drops everything from 2024-11-14 unless `include_holdout=True`.
- Data checks are clean on both pairs, development and hold-out (hold-out checked for integrity only; no prices or results viewed). Non-weekend gaps: the Christmas and New Year closures,
  plus a 2h50m hole on 2022-05-12 05:15–08:05 UTC in both pairs (cause unknown; it's in both pairs, so it looks like a feed gap rather than a market event).
  It's left as a gap, not filled.

## 2026-11-14 — Financing, account currency, margin (step 2)
- Triple rollover verified: OANDA's help pages (US, Canada, Singapore, Australia, UK, BVI) say FX takes 3 days at
  Wednesday 17:00 New York, with nothing on Saturday or Sunday. USD/CAD is the exception (Thursday).
- The v20 API's `financingDaysOfWeek` for both pairs returns Mon 0 / Tue 1 / Wed 3 / Thu 1 / Fri 1, only 6 days a week.
  The developer docs don't define the day or the timezone. The owner chose the help-page schedule (7 days/week) as the
  main run and the API schedule as a sensitivity run.
- Policy-rate tables were checked against each bank's history page (Fed openmarket, ECB key rates, BoE Bank Rate,
  BoJ statements). Effective dates are used, not announcement dates, since that's when rates actually changed.
  Two caveats: the ECB table came from a summarised page read, but it agrees with decision dates recalled independently
  plus news reports for 2026. The BoJ 2026 levels (1.00% Jun, 1.25% Sep) come from news reports; their effective dates
  assume the next business day.
- Markups calibrated on 2026-11-14 (annual): EUR_USD long 1.085%, short 0.935%; GBP_JPY long 0.92%, short 1.16%.
  They're all about 1%, which suggests the policy-rate model fits OANDA's pricing.
- Account currency is GBP (practice account). Backtest runs in GBP 10,000. GBP_USD M5 downloaded for conversion only (data clean).
- Margin rate: 3.33% for both pairs, from the API. The earlier 5% for GBP_JPY was Claude's assumption, not the owner's.

## 2026-11-14 — Backtest engine (step 3)
- `src/backtest.py`: an `Engine` simulates both pairs on one merged M5 timeline. The strategy is an object with
  `on_h1_close(engine, pair, hour)`, called at the first M5 candle of each new hour. Orders it places are live from
  that candle, so it can't act on prices inside the hour it's looking at.
- The engine owns fills, exits, sizing, caps, Friday close and financing. Setup logic (levels, cancel rules, time
  limit) stays in the strategy (step 4). One setup per pair at a time, enforced by the engine.
- Owner's choices: one shared GBP account; size on the closed balance (not NAV); Friday flatten at the open of the
  16:00 NY candle; units rounded down; conversion at mid.
- Claude's proposals, marked in strategy.md: pending orders can't fill from Friday 16:00 to reopen; a fill at or
  past the stop cancels the order; a target hit on one leg and a fill on the other in the same candle both count.
- Financing modes (on/off/cap) compound into the balance, so each mode is a separate run.
- Open question for step 4: whether cancel rules 1–2 (price through stop, back to extreme) are checked on M5 or at
  H1 close. The engine currently only cancels at H1 close.
- Speed: 8 years of dev data, both pairs, about 7 s per run, plus 2 s to load (a dummy strategy, no results recorded).

## 2026-11-15 — Engine revisions (owner's rulings on step 3)
- Bot-side decisions (arm, re-anchor, cancel, re-place, leg 1 confirmation, time limit, Friday pull) at H1 close
  only; broker-side events (fills, stops, targets) on M5. The live bot works the same way. This settles yesterday's
  open question: cancel rules are checked at H1 close.
- Running-extreme anchoring: a new high re-anchors the fib and never cancels. The old cancel rule "price returns to
  the swing extreme" is removed from strategy.md §5.
- Friday pull approved: pending orders are pulled from Friday 16:00 NY until the reopen. The pull is tied to the
  first H1 close at or after 16:00 NY Friday, computed on New York wall-clock time, so it follows US daylight saving
  and still fires (at the next candle) when a holiday shuts the market before 16:00. Tested on Fridays either side
  of both 2024 clock changes.
- A gap through the stop on a resting limit now fills at the open and stops out at that open with stop slippage
  (it used to be cancelled). To size such a fill, limit legs are sized from the limit price, as a live order's units
  are fixed when it's placed (Claude's proposal). Market entries opening past the stop are still skipped (proposal).
- Target and the other leg's limit touched in one candle: target first, position closes, the unfilled leg cancels,
  and it's counted. A fill at the bar open can reach its target in that same candle (stop first if both touch).
- Caps: notional 5× and margin 20% of the sizing base. Each cap is checked and counted separately. At 3.33% margin
  the margin cap only binds together with the notional cap.
- Research runs don't compound: a fixed £10,000 sizing base, so one run records financing on / off / capped on every
  trade (`trade_table`). `Config(compounding=True)` is for the final illustration only; `financing_mode` then picks
  which financing feeds the balance.

## 2026-11-15 — Freeze, H1 evaluation, holiday pulls (owner's rulings, second round)
- Geometry freezes at the first fill of any leg, never at a zone touch. Before a fill, a new extreme re-anchors in
  every mode; a zone touch without a fill is forgotten. Origin = most recent confirmed zigzag low/high.
- Each H1 bar is judged against the levels in force at its open; re-anchoring from its high applies after it closes.
  The engine already works this way (orders placed at an H1 close are live from the next candle). Step 4 must too.
- Approved: limit legs sized from the limit price; R = planned risk; all results in planned R (`trade_table`).
- Mode-b market entries are skipped at or beyond the stop or the target, on the triggering side (bid for longs).
  Each kind is counted.
- Margin rates are now per instrument, so the margin cap can bind on its own (tested with a synthetic 5% instrument).
- Holiday hours checked against OANDA's holiday trading hours pages (US and UK, read 2026-11-15): forex closes 16:59
  ET on 24 and 31 Dec 2025 and reopens 17:05 the next day; Thanksgiving, Good Friday, Juneteenth and the other US
  holidays trade regular FX hours. December 2026 isn't published yet; it falls after the hold-out, so the backtest
  doesn't need it. The M5 data (2016–2023) shows the same 16:55 last candle on every weekday 24/31 Dec, and no other
  early closes. So the pull runs at 16:00 NY on Fridays and on weekday 24/31 Dec.
- Weekend holds are logged (any open leg across a gap of 12h or more). Dummy-strategy check on 2016–2024: 836 holds
  with the pull off (16 over Christmas or New Year), zero with it on.
- Bug fixed: a setup whose only legs were skipped stayed active and blocked its pair for the rest of the run.

## 2026-11-15 — Signals (step 4)
- Owner's rulings: Wilder ATR(14) on mid H1, seeded with the mean of the first 14 TRs, weekend gaps kept in TR,
  200 H1 bars of warm-up. Trigger-side skip check confirmed (bid for longs); entry price is only for the fill and
  planned R. Holder keeps the pair over an opposite setup; min_bars is an arming rule only; a bar can't confirm its
  own new extreme; the time limit counts from arming and re-anchoring doesn't reset it.
- `src/swings.py`: online `WilderATR` and `ZigZag`. `src/setups.py`: `FibStrategy` (the engine's strategy) and
  `PairSignals` (all setup logic for one pair). Every decision is logged as (time decided, pair, action, details).
- The engine gained `place_market(..., target_r=n)`, because a fixed-R target on a market entry depends on its fill price.
- Lookahead: (1) truncation test at 25 random cuts (20 placed 5 minutes into an hour, where a peek at the next bar
  would see only one candle) on three parameter sets, all decisions before each cut identical; (2) a guard that
  fails if the strategy reads any H1 bar later than the one just closed. A planted one-bar peek is caught by both.
  The first version (5 cuts) missed the planted peek, which is why the test was strengthened.
  Real-data check: 2018, 4 cuts, 5,249 decisions identical.
- Activity on dev data, baseline (counts only; P&L and R are left for the step-5 protocol): 4,887 setups armed,
  2,800 got the pair, 2,642 legs filled, 1 stop-first fallback. About 10 s per run.
- Two constraints bind hard, worth knowing before step 5:
  - The 5× notional cap blocks 740 of ~3,400 attempted fills (22%), mostly leg 2 (596). Each leg risks £50 on a
    stop with a median distance of 40–45 pips (both pairs pooled), so two legs together often exceed 5 × £10,000 of notional.
  - 2,087 armed setups never got the pair, because an opposite setup was holding it.

## 2026-11-16 — Experiments (step 5)
- Owner's rulings: walk-forward selects only K × min_bars; primary = mode a, TP2, 96-bar limit, running-extreme
  anchoring, financing on, Friday close on. Score = mean setup R − 1 SE, ≥ 30 setups, orthogonal neighbours
  positive, ties to more setups. Unit = setup, setup R = summed leg P&L / £50, scored on financing on. Pass bar,
  random-entry and zone-shift controls and the hold-out apply to the primary only; every other variant is a
  sensitivity with its own walk-forward and can't replace a failed primary. Random control: per setup, market entry.
  The owner's answers first conflicted on Friday close (off in one, on in the other); confirmed as ON.
- `src/experiments.py`: 162 engine runs (18 run configurations × 9 cells, about 8 min on 7 workers), cached in
  data/experiments/. The report is generated into docs/backtests.md. Proposed details (window by first fill, own
  mean must be positive, empty neighbour isn't positive, sit out a year with no eligible cell, random-control and
  zone-shift mechanics) are marked in strategy.md §10.
- **Result: the primary fails.** Every one of the 9 cells has negative mean R in every training window (best
  training mean −0.119R), so the selection rule never picks a cell and there are 0 OOS setups. Whole-development
  mean R per cell is −0.157 to −0.244R (financing off −0.145 to −0.232R, so financing isn't the cause). Every
  sensitivity and every zone shift is negative in every cell too (best: mode c, −0.090R).
  The random-entry control was not run: there are no OOS setups to mirror.
- Sanity check that it's the strategy, not the engine (K=3, mb=5, primary): leg 1 wins 40.3% with mean win
  +1.13R and mean loss −0.90R; at that payoff it needs about 44% to break even. Leg 2 wins 30.8% (+1.74R / −0.95R).
  Stop-first fallbacks: 1 setup in 1,910.
- Bug found by the first real run: `trade_table` had no stop/target columns, which the random-entry plan needs.
  The unit test had used synthetic legs that included them; there is now a test on real `trade_table` output.
- Per §10 a fail means no demo trading and no re-tuning on the same data. The hold-out is still unused: it stays
  available, once, for a new pre-registered variant. Step 6 (hold-out lock) only matters if one is pre-registered.

## 2026-11-16 — Fail accepted; diagnostics; open items frozen
- Owner accepted the fail. Strategy rules unchanged, hold-out untouched. The 20 items Claude filled in are frozen as
  written (strategy.md now marks them *(frozen)*).
- `src/diagnostics.py` (development only; results in docs/backtests.md under "Diagnostics"):
  1. Baseline cell (K=3, mb=5) with zero spread (bid = ask = mid, so signals are unchanged), zero slippage and
     financing off: −0.036R per setup (SE 0.041), against −0.157R with costs. Costs are about 0.12R per setup;
     the cost-free result is indistinguishable from zero.
  2. Five legs drawn with seed 2026, listed with geometry, entry, stop, target, exit and R for chart checks.
     Trade 2 is a limit filled at the open beyond its price: the order went in after the market had already
     passed the limit, so it filled at the better price, and its R (−0.83) is measured against the planned risk
     from the limit.
  3. In-sample random-entry control on the baseline cell, 1,000 runs, random time within the same development
     year: median −0.103R, 5th–95th percentile −0.168 to −0.036R. The strategy (−0.157R) is at the 9.5th
     percentile. Caveat: busy-pair redraws and drops leave about 1,677 random setups per run against 1,910 real.
- README added with the method and the negative result.

## 2026-11-17 — New strategy: trend + carry
- The Fibonacci strategy is retired after its failed test. Larger swings (K=4) did worse than K=3 in the primary and
  with Friday close off, so changing how the swing low is picked was not pursued.
- Owner's choices: trend following combined with carry, a basket of 10–15 currency pairs, daily signals with
  positions held for weeks. Reason: H1 costs (about 0.12R per setup) left no room for an edge, and trend and carry
  are the FX effects with the longest published evidence, which needs many pairs.
- Draft rules in `docs/strategy-trend-carry.md`; everything except the three choices above is *(proposed)* and
  awaits the owner's rulings. The hold-out (2024-11-14 → 2026-11-13) is still unused and reserved for this strategy.

## 2026-11-25 — ICT: rule source
- Gemini extracts for 2016 Mentorship Month 1, Month 3 and 2/8 of Month 4 saved verbatim in `docs/ict-extracts/`.
  None of them define CHoCH/MSS; they teach turtle soup → structure break → order block / FVG entry.
- Owner's choice: keep the sweep → CHoCH → FVG model and fill the CHoCH/MSS rules from the 2022 ICT Mentorship
  (41-episode YouTube playlist, Jan–Jun 2022) before writing any rules.

## 2026-11-25 — ICT: market
- 2022 Mentorship Ep 1–10 extract saved in `docs/ict-extracts/2022-mentorship-ep01-10.md`. Gemini states it did not
  watch the videos, so core rules are to be checked against the videos before they go into the strategy.
- Owner's choice: test on US index CFDs (the model is taught on NQ/ES futures), not forex.
- Checked on the OANDA practice API: NAS100_USD, SPX500_USD, US30_USD, US2000_USD are available, with M5 and M1
  bid/ask candles from at least 2016-10-03. Oct-2016 spreads: 0.6, 0.5, 2.0, 0.6 index points.
- Caveat: OANDA CFDs are not the futures contracts; prices and session hours may differ slightly from NQ/ES.

## 2026-11-25 — ICT: verification and first rulings
- Claude checked Gemini's 2022 extract against the YouTube captions of Episodes 2–10 (`docs/ict-extracts/2022-verification.md`).
  The core model is confirmed; several details were wrong (swing definition, stop, partials, the two-FVG rule,
  an unsupported "4 trades/day", afternoon end time).
- Owner's rulings:
  - Structure shift: test both versions (candle close beyond the swing, Ep 6; wick through it, Ep 4). One is the
    primary, the other a sensitivity check (primary to be confirmed).
  - Daily bias: daily premium/discount. Buy only below 50% of the last daily 3-candle swing range, sell only above it.
  - Stop: above candle 1 of the FVG (below it for longs).
  - Sessions: New York morning only, 08:30–12:00; entries before 11:00; flat by 12:00.
- Evaluation (owner's ruling): the close-based shift is the primary. If it fails on development, the wick-based
  shift gets one rescue attempt at a stricter bar (mean R − 2 SE > 0 instead of − 1 SE). Whichever passes goes
  to the hold-out once; only a hold-out pass counts.
- Draft rules written to `docs/strategy-ict.md`; Claude's fill-ins are marked *(proposed)*.

## 2026-11-25 — ICT: rules approved; edge cases from an outside review
- Owner approved `docs/strategy-ict.md` and the build plan (`docs/superpowers/plans/2026-11-25-ict-backtest.md`).
- An outside review (pasted by the owner) raised edge cases. Owner's ruling: **re-arm on a new high.** Until 11:00,
  a candle above the current sweep extreme restarts the cycle (new short-term low, shift, FVG), including after a
  shift that produced no order or after an order was placed. Re-armed orders are counted in the report.
- Kept as ruled: the stop is above FVG candle 1, even when candle 1 is later than the sweep extreme.
- Claude's fill-ins (*proposed*): no order placed at or after 11:00; unfilled-order cancels are checked on the
  exit side (ask for shorts) and are final; "traded through" = any M5 mid low at or below the swing low, a touch
  counts; daily swings are usable only after the next daily close at 17:00; no lookback cap on the 08:30 levels.
- Not adopted: the review's lower slippage figures (0.5 / 0.15 points). Real bid/ask spreads are already used on
  every fill; the 1.0 / 0.25 slippage placeholders stay and are checked against measured spreads in Task 1.
- Execution: subagent-driven (owner's choice).

## 2026-11-25 — ICT: M1 data and spread check
- Downloaded M1 bid/ask candles (2016-10-03 to 2026-11-25) to `data/NAS100USD_M1.csv` (3,530,130 rows) and
  `data/SPX500USD_M1.csv` (3,183,672 rows). Development years only below (to 2024-11-13; hold-out not loaded).
- Spread = ask_c − bid_c on M1 candles 08:30–12:00 New York, weekdays. Missing = share of the 48 M5 slots per
  weekday (08:00–11:55 New York, built from M1) with no candle; weekday holidays count as missing.

| Instrument | Year | Median spread | p90 spread | Missing M5 slots |
|---|---|---|---|---|
| NAS100_USD | 2016 | 0.6 | 0.8 | 1.6% |
| NAS100_USD | 2017 | 0.8 | 1.0 | 1.2% |
| NAS100_USD | 2018 | 0.7 | 2.2 | 1.4% |
| NAS100_USD | 2019 | 0.7 | 2.2 | 1.1% |
| NAS100_USD | 2020 | 0.5 | 1.9 | 1.9% |
| NAS100_USD | 2021 | 0.5 | 1.7 | 1.0% |
| NAS100_USD | 2022 | 0.5 | 1.7 | 0.8% |
| NAS100_USD | 2023 | 0.5 | 1.7 | 1.0% |
| NAS100_USD | 2024 | 0.8 | 2.3 | 0.9% |
| NAS100_USD | all | 0.6 | 2.0 | 1.2% |
| SPX500_USD | 2016 | 0.5 | 0.7 | 4.2% |
| SPX500_USD | 2017 | 0.5 | 0.5 | 4.4% |
| SPX500_USD | 2018 | 0.5 | 0.5 | 1.7% |
| SPX500_USD | 2019 | 0.5 | 0.5 | 1.4% |
| SPX500_USD | 2020 | 0.3 | 0.5 | 2.1% |
| SPX500_USD | 2021 | 0.3 | 0.4 | 1.1% |
| SPX500_USD | 2022 | 0.3 | 0.5 | 0.8% |
| SPX500_USD | 2023 | 0.3 | 0.5 | 1.0% |
| SPX500_USD | 2024 | 0.4 | 0.5 | 0.9% |
| SPX500_USD | all | 0.4 | 0.5 | 1.8% |

- Against the slippage placeholders: NAS100_USD median 0.6 is under 1.0. **SPX500_USD median 0.4 is over its
  0.25 placeholder.** Placeholders left unchanged; owner to decide on §8.
- NAS100_USD's 90th-percentile spread (1.7–2.3 points in most years) is above its 1.0 slippage figure; that is
  the tail, and the median (0.6) is well below it.
- Owner's ruling: SPX500_USD slippage raised from 0.25 to **0.5 point** (its 90th-percentile spread). NAS100_USD
  stays at 1.0. Decided before any strategy results were seen.
