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

## 2026-11-25 — ICT: build-time rulings (Claude, while building; none seen results)
Each settles something the spec left open. All were made before any R results were looked at.
- A sweep candle that itself closes beyond the short-term low counts as the shift; no FVG can fit, so the cycle
  ends `no_fvg` and waits for a re-arm (spec §5–§6 read literally).
- An FVG "qualifies" if it is inside the displacement window and its near edge is in premium. Among qualifying FVGs
  the one whose near edge is closest in price to the sweep extreme is used ("deepest in premium", §6); a tie goes
  to the earlier one. Bias and target are then checked on that FVG only.
- An FVG completed by the shift candle is placed at the shift's close; an FVG ending on the next candle is tried
  only if none qualified at the shift.
- Skip reasons are checked in the order no_fvg → too_late → premium → bias → target. With no confirmed daily
  swings yet, the setup is skipped as `bias`.
- A re-armed cycle is one started by a new high after a shift; a new high before any shift just moves the sweep
  extreme (§5).
- A filled trade with no M1 bar at or after 12:00 is recorded as `no_exit_data` and counted, not scored; it still
  uses up the day's one trade.
- For speed, the levels before 08:30 are searched in the last 5 days of candles, not all history (§4 says no
  limit). Five days of M5 always contain a three-candle swing, so this doesn't change any order in practice.
- Random-entry control: entries are market orders at the open of a random M5 candle 08:30–10:55 on the trade's
  day, same side and stop/target distances. They fill at once and are not subject to the limit-order cancel
  rules; without this, first-minute target hits were dropped and the control was easier to beat.
- SPX500_USD skips 206 development days for a missing M5 candle 08:00–12:00 (148 of them in 2017; scattered
  single gaps in OANDA's early data) against 9 for NAS100_USD. The §7 rule is applied as written.

## 2026-11-25 — ICT: development result
**Verdict: fail.** Both versions failed the pre-registered development bar (`docs/strategy-ict.md` §10), so
nothing goes to the hold-out, which has not been run. Details are in `docs/backtests.md`.
- Truncation check (no lookahead) passed for both instruments and both shift rules.
- Primary (shift by close, M5, pooled): n 231, mean R −0.154, SE 0.126, mean R − 1 SE −0.280. NAS100 −0.204,
  SPX500 −0.107. Random-control 95th percentile −0.333 (beaten). Failed: mean R ≥ 0.1, mean R − 1 SE > 0, positive on
  each instrument.
- Rescue (shift by wick, M5, mean R − 2 SE > 0), evaluated because the primary failed: n 269, mean R −0.096,
  SE 0.118, mean R − 2 SE −0.332. NAS100 −0.044, SPX500 −0.147. Control 95th percentile −0.366 (beaten). Failed
  the same three criteria.
- The random control averages about −0.49R, so beating its 95th percentile says little on its own.
- Reported, not judged: close rule on 1-minute chart n 977, mean R −0.689; on 2-minute n 576, −0.484. Longs
  +0.208 (n 72) and shorts −0.319 (n 159) under the primary. Max drawdown at 0.5% risk: 29.3 points.
- Under §10 item 4 the strategy fails; no re-tuning on the same data.
- Re-run after the final review fixed R to use the actual fill price on gapped limit fills (it had used the limit price; 15 of 231 primary trades affected). A bug fix, not re-tuning; the verdict was unchanged: fail.

## 2026-11-25 — Trend + carry: rules fixed under delegation
- The owner handed every remaining rule to Claude: "build out the step two trend with carry idea. make all trading
  decisions that will lead to profitability. determine entry and exit rules." No rule can be chosen to guarantee
  profit, and choosing rules by looking at results would only fit noise. So the rules and pass bar in
  `docs/strategy-trend-carry.md` were fixed from published research, before any data was looked at, and the
  pre-registered method (one development run, hold-out once, a fail is final) is kept.
- Main choices: two equal-risk sleeves (trend score from 63/126/252-day signs; carry sign net of OANDA's markup),
  volatility sizing at 0.25% per ATR20 per sleeve, signal exits only (no stop), orders at the 08:00 New York H1
  open, 0.5 pip slippage (1.0 on JPY crosses).
- Rates: OECD monthly interbank rates from FRED for all 8 currencies, lagged one month, instead of extending
  `src/financing.py`'s hand-built policy-rate tables (those start in 2015–2016 and cover 4 currencies; strategy 1's
  module stays frozen).
- Financing markup: OANDA's rates for the 12 pairs on 2026-11-25 sum (long + short) to −2.0% to −2.1% on every
  pair, so a flat 1% per side is used.
- Data check (before any result): all 14 pairs have H1 from 2005-01-02 with no candle errors. Each pair has only 2–4
  development bars with no 08:00 candle. Median spread at the 08:00 New York open is 1.3–2.8 pips (p90 1.6–6.0).
  About 80 daily bars per pair were dated Saturday, built from quotes between Friday 17:00 and Sunday 17:00 New
  York (2005, 2011). Ruling: those candles are dropped, since the FX market is closed then (added to §2).

## 2026-11-25 — Trend + carry: development result
- **FAIL.** Sharpe −0.04 (bar ≥ 0.4); sub-periods +£707 / −£165 / −£1,936 (needed 2 of 3 positive); −0.13 without
  the best pair, GBP_JPY (needed > 0.2). It did beat the random-sign control (p95 −0.07, median −0.47), but only
  because random directions pay the 1% financing markup on the wrong side, so that check is a low bar here.
- Neither sleeve works on its own: trend 0.05, carry −0.16 (2008 unwind, then little carry left in G10). With
  financing off it is −0.11. Spread and slippage cost £2,503, so with zero costs the strategy would still be
  roughly break-even; this time costs aren't the main problem, the signal is.
- Checks: rebuilding from H1 cut at 6 random times changed none of 188,676 pair-bar positions or targets (no
  lookahead); portfolio P&L reconciles with the saved daily file.
- Sizing note: 0.25% per ATR per sleeve gave 17% annual volatility and a 72% drawdown. That doesn't change the
  Sharpe verdict, but the level was too aggressive and would need halving or more in any future version.
- Per the rules there's no re-tuning. In particular, "trend on JPY crosses only" (the best pairs here) is not a
  candidate: it was picked by looking at these results. The hold-out (2024-11-14 → 2026-11-13) is still unused.

## 2026-11-25 — Strategy 4: multi-asset trend, rules fixed
- Owner: "merge and push, then build number 3" (multi-asset trend, the third idea proposed after strategy 2's
  fail). As with strategy 2, Claude made all rule decisions, fixed before any result in
  `docs/strategy-multi-asset-trend.md`.
- Data checks before the rules (no results looked at): OANDA H1 history from 2005 for indices, bonds and
  commodities, 2006-03 for metals. Indices, bonds and metals have no roll jumps; their biggest hourly gaps are news
  days. NATGAS jumps cluster at month-ends (futures rolls), and today's commodity financing runs from −127% to +37%
  a year, so energy and farm CFDs are excluded as not modellable. Every candidate has an 08:00 New York candle
  almost every day (AU200 misses 322 of 4,532; the rule waits for the next bar). Median 08:00 spreads are
  0.8–4.4 bp, silver 11.5 bp.
- Financing markups measured from OANDA's 2026-11-25 rates: indices and bonds 2.5% per side, gold 1.1%, silver
  1.4%. These are large next to futures costs and are charged in full.
- Code: `src/trend/sim.daily_pnl` gained an optional `slip` argument; strategy 2's results are unchanged by it.

## 2026-11-25 — Strategy 4: multi-asset trend, development result
- **FAIL.** Sharpe −0.10 (bar ≥ 0.4); sub-periods +£561 / −£103 / −£1,206; −0.24 without gold, the best
  instrument. It beat the random-sign control (p95 −0.42), but random directions pay the financing markup on every
  position, so that check is again a low bar.
- Where the money went: price moves +£2,913, spread and slippage −£1,125, financing −£2,535 (about £134 a year).
  With no costs at all the Sharpe would be 0.38, still just under the bar. With financing off it is 0.24.
- By class: metals 0.19, FX 0.03, equities −0.24, bonds −0.23. Bonds trend in price but pay OANDA's 2.5% markup on
  the full notional every day, which outweighs it at bond volatility.
- Sensitivities don't change the verdict: unrounded units −0.07, no dividends −0.15, bond carry 0 −0.10.
- Checks: rebuilding from H1 cut at 5 random times changed none of 231,178 instrument-bar positions or targets.
- Lesson for any future idea: on OANDA CFDs, holding positions for weeks costs about 2.5% a year of notional on
  indices and bonds (1% on FX). A strategy needs either short holds or a gross edge well above that.
- No re-tuning (e.g. "metals and FX only" was picked from these results and isn't a candidate). The hold-out is
  still unused.

## 2026-11-25 — Strategy 5: turn of the month, rules fixed
- Owner: "merge and push, then build turn of the month". Claude made all rule decisions, fixed before any result
  in `docs/strategy-turn-of-month.md`: SPX500/NAS100/US30/US2000 CFDs, long from the close of day −2 to the close
  of day +3 (McConnell–Xu's window), the close = the 15:00 New York H1 candle's close, 0.25% per ATR sizing, no
  stop, random-timing control.
- Data checks before the rules: H1 from 2005 for all four. On 4,749 development NYSE days each misses the 15:00
  candle on only 43–45 days, all early closes (July 3, day after Thanksgiving, Christmas Eve). The median spread at
  that close is 0.4–1.5 bp. OANDA's CFD financing schedule from the API is Mon–Thu 1 day, Fri 3 days.
- `src/tom/nyse.py` builds NYSE trading days from the holiday rules plus 5 one-off closures. For 2016-10 → 2026-11
  it matches the hand-checked `src/ict/calendar.py` list exactly.

## 2026-11-25 — Strategy 5: turn of the month, development result
- **FAIL.** Sharpe −0.09 (bar ≥ 0.4); sub-periods −£498 / +£270 / −£567; −0.14 without NAS100. It lost to random
  timing (control median 0.16, p95 0.52): random 4-day holds earn the equity premium, and the turn-of-month ones
  earned less.
- 903 trades, mean −£0.88, t −0.71, 53% winners. Price +£348, spread and slippage −£477, financing −£666; with
  financing off the Sharpe would be −0.01. Mean price move per trade was £0.39 at the turn of the month vs £2.62
  for any 4-day hold.
- Checks: the CFD's 16:00 mid matches official S&P 500 closes to within 0.1–0.7% on 5 spot dates. An independent
  day-by-day count over 2006–2024 (SPX500, 16:00 to 16:00) gives day −1 −0.093%, +1 +0.125%, +2 +0.069%,
  +3 +0.017%; turn-of-month days averaged 0.029% vs 0.043% on other days. The published effect (strongest on
  day −1) didn't hold in this period.
- Days −3 and −2 were the strongest (+0.10%, +0.13%), but a window picked from that table would be fitted to
  this data, so it isn't a candidate. No re-tuning; the hold-out is still unused.

## 2026-11-25 — Planted-edge test of the backtester
- Owner worried that five fails meant the testing was broken. Test (`src/checks/planted_edge.py`): add a known
  extra return δ to every turn-of-month day in the real H1 prices, run the unchanged turn-of-month backtester, and
  compare its extra P&L with the figure worked out by hand from the trades. Criteria fixed before running.
- Results: δ = 0 reproduces the published −0.09 / −£795 exactly. Measured vs hand-worked extra P&L: δ 0.05%/day
  £3,308 vs £3,331 (−0.7%); 0.10% £6,543 vs £6,668 (−1.9%); 0.20% £13,100 vs £13,355 (−1.9%). The small shortfall
  is unit rounding and costs scaling with price. Sharpe rose −0.09 → 0.27 → 0.62 → 1.26, while the random-timing
  control's median rose only 0.16 → 0.23 → 0.30 → 0.43. All three criteria passed.
- Verdicts: δ 0.20% passes the full bar. δ 0.10%, the published size, gets Sharpe 0.62 but just misses the
  random-timing check (p95 0.66), because it is added on top of this period's weak turn-of-month days. So the bar
  needs an edge at least the published size on top of what actually happened. That is strict, and it is the bar
  pre-registered for every strategy.

## 2026-11-25 — Planted-edge test of the trend engines (strategies 2 and 4)
- Test (`src/checks/planted_trend.py`, criteria fixed before running): random up/down phases (mean 126 bars,
  seed 0) with drift μ per bar planted into every instrument's real daily bars, run through the unchanged engines.
- Criterion 1, μ = 0 reproduces the published results: **PASS** for both (−0.04 / −£1,394; −0.10 / −£747).
- Criterion 3, positions follow the planted phases: **PASS** for both. Position-days on the trend's side went
  49.9% → 56.8% → 64.7% → 71.0% (strategy 2) and 49.0% → 56.5% → 64.4% → 71.5% (strategy 4).
- Criterion 4, an independent cash ledger matches the engine's daily P&L: **PASS**, largest gap 1e-8 of a unit.
- Criterion 2: **FAIL for both, as written.** Sharpe by μ (0, 0.02, 0.05, 0.10% per bar): strategy 2 −0.04, −0.03,
  0.45, 1.75; strategy 4 −0.10, −0.13, 0.47, 2.62. Strategy 4 dipped at μ 0.02%, and strategy 2's control median
  moved −0.47 → −0.26, beyond the ±0.1 allowed. Diagnosis, made after the result:
  - The gain from the planted trend itself rises steadily: strategy 4 £1,331 / £7,210 / £19,639, strategy 2
    £8,158 / £34,247 / £86,044. But once positions follow the planted phases, the real-price part becomes noise
    with a 19-year spread of about £1,700 (strategy 4) and £7,500 (strategy 2). The smallest drift's gain was
    below that noise, so a strictly rising Sharpe at every step was the wrong thing to require.
  - The control's mean P&L barely moved (strategy 2: −£8,787, −£8,526, −£8,355, then −£6,604 at the largest drift,
    where the strategy trades less often). Its run-to-run spread grew 3.5×, which pulls a losing control's Sharpe
    toward zero. The criterion should have used mean P&L, not Sharpe.
- So the fail is recorded as a fail of the criteria's design, not relabelled as a pass. The evidence underneath
  (criteria 1, 3, 4 and the planted-gain split) shows the engines capture planted trends with the right sign and
  size, and their accounting is exact.
- Scale: a planted drift of 0.05% per bar (about 12.6% a year in the trend's direction, a strong trend that lasts
  about 6 months at a time) gives Sharpe ≈ 0.45, just past the 0.4 bar. Real FX and multi-asset trend effects are
  smaller than that, consistent with strategies 2 and 4 failing.

## 2026-11-25 — Strategy 6: mean reversion, rules fixed
- Owner: "build the mean reversion strategy". Claude made all rule decisions, fixed before any result in
  `docs/strategy-mean-reversion.md`, keeping the version the owner was shown: close at a 5-day low above the
  200-day average → buy; close above the 5-day average → sell. Additions: a 10-trading-day time stop, execution at
  the next H1 open after the signal candle (the signal needs the close), and a random-entry control limited to
  days when the uptrend filter holds.
- Data check before the rules: the 16:00 New York candle is missing on 39–46 of 4,749 development NYSE days (early
  closes), and its opening spread is 0.5–1.6 bp median.

## 2026-11-25 — Strategy 6: mean reversion, development result
- **PASS**, the first of six strategies to pass development. Sharpe 0.54 (bar ≥ 0.4); sub-periods +£612 / +£3,557 /
  +£293; it beat the random uptrend-day entries (p95 0.48, median 0.15); 0.38 without NAS100 (bar > 0.2).
- 1,298 trades, mean £3.44, t 3.40, 71% winners, mean hold 3.4 trading days, 38 time-stop exits. Price +£6,314,
  spread and slippage −£938, financing −£914. Annual 2.37% on the base, max drawdown 13.6%.
- Margins are thin: the control check passed 0.54 vs 0.48, and 2018–2024 made only £293 (2018 −£970, 2020 −£447).
  Without the uptrend filter it would have been 0.62 (reported only; the filtered version is the one judged).
- Checks before using the hold-out: rebuilding from H1 cut at 6 random times left all 6,449 completed trades'
  dates unchanged (no lookahead). Three random trades recomputed by hand from raw H1 candles matched the entry and
  exit rules and the fill-to-fill P&L exactly; the GBP conversion differed by 0.02% (16:00 vs 17:00 rate).
- Per the protocol, the hold-out (2024-11-14 → 2026-11-13) is run once, with these rules frozen. It is the only
  hold-out, so Claude asked the owner before spending it.

## 2026-11-25 — Strategy 6: hold-out result
- Owner: "run the hold-out". Rules frozen, run once (`python -m src.meanrev.evaluate --holdout`).
- **PASS:** Sharpe 1.82 (bar > 0) and max drawdown 4.1% (bar < 15%). 166 trades, 73% winners, mean £7.42, t 3.27,
  +£1,232 (6.2% a year on the base). Profit came from all four indices: SPX500 £412, NAS100 £236, US30 £286,
  US2000 £298.
- Checks: hold-out H1 data clean (no candle errors). Execution spreads in points are close to development's
  (e.g. SPX500 0.50 vs 0.40), smaller in bp because prices are higher. That, plus slippage fixed in points from
  development spreads, explains costs of £0.26 a trade vs £0.72 in development. Costs (£43) are small next to
  price P&L (£1,478), so this doesn't drive the verdict.
- Caution: two years is short (a Sharpe of 1.82 over 2 years has a standard error of roughly 0.7), and 2025–26 was
  a rising market, which suits a long-only dip-buyer. The development figure (0.54) is the better guide to what to
  expect. Demo trading is the next test.
- The hold-out has now been used. Later strategies need fresh out-of-sample data (e.g. live demo results).

## 2026-11-26 — Demo trading: built, not deployed
- The owner chose a cloud server for the demo run and left the rest to Claude. Choice: a Render cron job
  (`render.yaml`, schedule `2 20,21 * * 1-5` UTC), because it runs whether or not the Mac is on. Render cron has no
  disk, so the job is stateless: open positions are OANDA trades tagged `meanrev`, with the entry NY date stored in
  the trade's client extensions so the 10-day time stop survives between runs.
- Safety rails: practice only; no order unless `TRADING_ENABLED` is exactly `yes` (otherwise a dry run that logs
  what it would do); notional per order capped at GBP 10,000; at most one tagged trade per index; long-only
  fill-or-kill market orders; an untagged or duplicate trade on an index blocks that index with a warning.
- Rollout, each step needing the owner's go-ahead: deploy with `TRADING_ENABLED=no`; dry run for at least 3 NYSE
  days and check the logs; then switch `TRADING_ENABLED` to `yes`.
- Success criteria, fixed now and judged after 3 months: (1) every backtest trade in the period appears live on the
  same NY days, apart from counted missed runs; (2) the median live fill is within 2x the backtest's assumed spread
  plus slippage of its 16:00-open price; (3) no safety-rail breaches. Profitability is reported, not judged.
- Nothing has been deployed and no order has been placed.
