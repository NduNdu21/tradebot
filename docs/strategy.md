# Strategy: Fibonacci swing-to-swing retracement

Pairs: EUR_USD, GBP_JPY. Signals on H1, all fills simulated on M5.
Rules agreed 2026-11-14, revised then and on 2026-11-15. Items marked *(proposed)* were
filled in by Claude to make a rule testable and still need the owner's sign-off.

Worked example used throughout (up-swing; sells are the mirror image):
swing low 1.1000 → swing high 1.1100, range 0.0100.
Levels are measured from the origin (0%) toward the extreme (100%).

| Level                | Price  | Measured from origin | Retracement from extreme |
|----------------------|--------|----------------------|--------------------------|
| TP3                  | 1.1162 | 161.8%               | —                        |
| TP2 / swing extreme  | 1.1100 | 100%                 | 0%                       |
| TP1                  | 1.1062 | 61.8%                | 38.2%                    |
| Leg 1 entry          | 1.1038 | 38.2%                | 61.8%                    |
| Leg 2 entry          | 1.1024 | 23.6%                | 76.4%                    |
| Swing origin         | 1.1000 | 0%                   | 100%                     |
| Stop                 | 1.0990 | −10%                 | 110%                     |

## Timing: bot side vs broker side
- **Bot-side decisions happen at H1 close only:** arm, re-anchor, cancel,
  re-place orders, leg 1 confirmation, time limit, Friday 16:00 pull.
- **Broker-side events are simulated on M5:** limit fills, stops, targets.
- The live bot will run the same way.
- Each H1 bar is evaluated (limit fills, mode-b confirmation) against the levels
  in force at its open. Re-anchoring from that bar's own high takes effect only
  after it closes.

## 1. Data
- M5 bid and ask candles from OANDA (practice API), one source, 2016-10-03 onward.
  The first six weeks only warm up ATR and swings; evaluation starts 2016-11-14.
- GBP_USD M5 is downloaded too, only to convert EUR_USD profit and loss (USD) to GBP.
- H1 candles are resampled from M5, not downloaded separately.
  Mid OHLC = (bid + ask) / 2, field by field.
- ATR(14) uses H1 mid prices with Wilder smoothing, seeded with the simple average
  of the first 14 true ranges. True range keeps weekend gaps (it uses the previous
  close). The very first bar has no previous close, so its TR is high − low *(proposed)*.
- The first 200 H1 bars are warm-up: they feed ATR only, with no swings or setups.
- Swings and setups use H1 mid prices too *(proposed)*; orders placed at those levels fill on bid/ask (§8).

## 2. Swings
- A swing ends when price reverses at least K × ATR(14) from its extreme
  (ATR read at the bar where the reversal completes).
- A swing must span at least `min_bars` H1 bars from origin to extreme;
  this stops single-candle spikes from counting as structure.
- Baseline K = 3, min_bars = 5. Grid: K ∈ {2, 3, 4} × min_bars ∈ {3, 5, 8}.
- The zigzag itself is pure K × ATR: min_bars is only an arming rule (§3).
- A bar that makes a new extreme can't also confirm it, because on H1 we can't tell
  whether its high or its low came first. Confirmation needs a later bar.
- Before the first pivot, both sides are tracked; whichever first sees a K × ATR
  reversal gives the first pivot *(proposed)*.

## 3. Setup (anchoring)
**Main run: confirmed origin with a running extreme (no lookahead).**
- **Origin** = always the most recent confirmed swing low (for buys) / high (for
  sells) from the ATR zigzag.
- **Extreme** = the highest high (lowest low) since the origin. It's known at every bar.
- The setup is **armed** once range ≥ K × ATR (ATR at that H1 close) and the
  origin→extreme span ≥ min_bars (bar-index difference). Once armed it stays armed,
  even if ATR later grows *(proposed)*.
- Before any fill, a new high (new low for sells) beyond the extreme **re-anchors**
  the fib in every mode: the levels move to the new extreme at the next H1 close.
  A new extreme never cancels a setup.
- The geometry (extreme, entries, stop, targets) **freezes at the first fill of
  any leg**, never at a zone touch. A touch of the zone without a fill is forgotten.
- Entry, stop and target prices come from the extreme as of the last completed
  H1 bar and are re-priced only at each H1 close. This prevents a new high and a
  pullback inside the same hour from using a level that wasn't known yet.
- Direction = direction of the swing from origin to extreme. No trend filter.
- **One setup holds the pair at a time.** Confirming a buy's extreme as a swing high
  doesn't cancel the buy, because its origin is still the latest confirmed low. A sell
  from that high may arm too, but it waits. The holder keeps the pair until it
  finishes; then a waiting armed setup takes over. If several are waiting, the newest
  origin goes first *(proposed)*.

**Robustness run: fully confirmed swings.** Only trade a swing after both ends are
ATR-confirmed. If price is already past the entry level at confirmation, skip that
leg: "past" means the confirmation bar closed at or beyond the entry *(proposed)*.

## 4. Entry modes
Legs are independent. Each leg is either filled or not; neither waits on the other.

| Mode            | Leg 1 (38.2%, 1.1038)                 | Leg 2 (23.6%, 1.1024) |
|-----------------|---------------------------------------|-----------------------|
| **a (baseline)**| limit                                 | limit                 |
| b               | confirmation candle, next H1 open     | limit                 |
| c               | limit                                 | —                     |
| d               | —                                     | limit                 |

- **Confirmation (mode b):** an H1 candle whose low enters the 61.8–76.4%
  retracement zone, stays above the stop, and closes back above the 61.8%
  retracement level. Leg 1 then enters at the next H1 open, at the ask for buys
  and the bid for sells. This can happen even after leg 2 has already filled.
- Limits are live from arming until the setup is cancelled or the position closes.
- A leg the engine skipped (exposure cap, or a market entry through its stop or
  target) isn't re-placed. A later confirmation candle can still trigger a skipped
  mode-b leg 1 *(proposed)*.
- After a mode-b confirmation, leg 2 stays at the confirmation's levels until the
  market entry has gone through, so both legs share one geometry *(proposed)*.
- Both legs share one stop. No further adds. Unfilled legs cancel when the
  position closes. One position per pair at a time; each setup trades at most once.

## 5. Cancelling a pending setup
A setup with no filled leg is cancelled when any of these happens, checked at H1 close:
1. price has traded through the stop level (origin − 10% of range), judged on the
   H1 mid low for buys and the mid high for sells *(proposed)*;
2. a newer confirmed swing of the origin's kind replaces it: a new swing low for a
   buy, a new swing high for a sell;
3. 96 H1 bars pass without a fill (sensitivity: 48 and 200), counted from arming.
   Re-anchoring doesn't restart the count.

A new extreme is not a cancel: it re-anchors the setup (§3).

## 6. Exits
- **Stop:** origin − 10% of range, triggered on the bid for longs and the ask for shorts.
- **Targets**, each tested as a separate all-out exit on the same trades:
  - TP1 = 61.8% of the swing above origin
  - **TP2 = swing extreme (headline)**
  - TP3 = 161.8% extension
  - Controls: fixed 1R, 2R, 3R from each leg's own entry.
- **Friday close (variant, on/off):** a bot-side decision at the last H1 close
  before forex shuts: 16:00 New York on Fridays, and on 24 and 31 Dec when they fall
  Monday–Thursday. On those days OANDA forex closes at 16:59 NY and reopens at 17:05
  the next day (OANDA holiday trading hours, 2025 schedule; the M5 data shows the same
  pattern every year 2016–2023). OANDA lists no other forex early closes.
  16:00 NY is 21:00 UTC in winter and 20:00 UTC in summer, so always an H1 boundary.
  Open legs close at the open of the 16:00 NY M5 candle (bid for longs, ask for
  shorts, plus slippage). If the market is unexpectedly shut at 16:00, they close
  at the first candle after it. Pending setups with no fill survive, but their
  orders are pulled until the market reopens. Any leg still held across a market
  closure (a gap of 12 hours or more) is logged as a weekend hold.

## 7. Sizing and exposure
- Account currency is GBP, matching the practice account. Start with 10,000 GBP.
  Both pairs share this one account, simulated on a merged M5 timeline.
- **Research runs (all of §10) don't compound:** every leg risks 0.5% of the fixed
  10,000 GBP starting balance (£50 per leg, £100 per full setup). One run then gives
  financing on, off and capped as three P&L columns. Drawdown is measured against 10,000 GBP.
- **Compounding is only for a final illustration:** risk 0.5% of the closed balance.
  Open P&L doesn't count; realised P&L and financing do.
- Units = risk in GBP / (stop distance in GBP per unit), rounded down to a whole
  unit; a leg that rounds to 0 is skipped. Stop distance = order price − stop: the
  limit price for limits, the fill price for market entries. This matches a live order,
  whose units are set when it's placed. A gap fill can then lose more or less than 1R.
- **R is planned risk:** (order price − stop) × units, in GBP at entry. Every
  result is reported in planned R.
  EUR_USD amounts convert USD → GBP at the GBP_USD mid; GBP_JPY amounts convert
  JPY → GBP at the GBP_JPY mid. Sizing uses the mid at the open of the fill candle,
  P&L the mid when it's realised.
- Skip a leg if it would push total notional above **5×** the sizing base, or
  margin used above **20%** of it. Margin rates are per instrument; 3.33% for both
  pairs, the account's rates from the API. Both caps count open legs on both pairs at current
  prices. Each cap is checked separately, and how often each one binds is logged.
  At 3.33%, 5× notional uses 16.7% margin, so the margin cap only binds alongside
  the notional cap.

## 8. Fills and costs
- Real bid/ask spread from the data on every fill. Longs enter at the ask and exit
  (stop and target) at the bid; shorts the opposite.
- **Market entries** (mode b, next H1 open) fill at the open, spread only. Skip the
  entry if the open is at or beyond the stop, or at or beyond the variant's target,
  measured on the side that triggers them (bid for longs, ask for shorts). Both
  kinds of skip are counted.
- **A gap through the stop on a resting limit** fills at the open, and the leg is
  stopped out at that same open with stop slippage.
- **Limit orders** (entries and targets) get no slippage. If price gaps past a
  limit, it fills at the bar open when that's better than the limit.
- **Stops and forced exits** (Friday close) pay slippage on top of the spread:
  0.5 pip EUR_USD, 1 pip GBP_JPY. If price gaps through a stop, it fills at the
  bar open when that's worse than the stop (plus slippage).
- **Financing** (`src/financing.py`): amount = units × price × annual rate × days / 365,
  in the quote currency. The annual rate is the base minus quote policy rate (Fed
  range midpoint, ECB deposit rate, Bank Rate, BoJ policy rate), by effective date,
  2016–2026, minus a markup per pair and side. The markup is calibrated so the model
  reproduces OANDA's rates on 2026-11-14.
- **Rollover:** 17:00 New York on weekdays (moves with US daylight saving).
  - Main run: 1 day Mon/Tue/Thu/Fri, 3 days Wed (OANDA help pages, verified).
  - Sensitivity: the API's schedule, Mon 0 / Tue 1 / Wed 3 / Thu 1 / Fri 1.
  - A position is charged at a rollover if it's open at that moment. Market holidays are not modelled.
- Every result is reported three ways: financing on, off, and credits capped at zero
  (capped per rollover). Each research run records all three on every trade.

## 9. Same-bar rules (M5)
- **Stop and target in one bar:** assume the stop was hit first, and count it as a
  fallback decision.
- **Fill inside the bar and target in the same bar:** count the fill, not the
  target. The target can only be hit from the next bar on.
- **Fill at the bar open** (a gap fill or a market entry): the leg was in for the
  whole bar, so its target in that bar counts too. If stop and target are both
  touched, it's stop first (a fallback, as above).
- **Fill and stop in one bar:** the leg filled and was then stopped. For a limit,
  price must pass the entry to reach the stop, so this order is certain.
- **One leg's target and the other leg's limit both touched in one bar:** treat
  the target as first. The position closes and the unfilled leg cancels. Count these.

## 10. Evaluation protocol (pre-registered)
- **Development:** 2016-11-14 → 2024-11-13. **Hold-out:** 2024-11-14 → 2026-11-13,
  looked at once, with frozen settings. The data loader refuses hold-out data
  unless it's explicitly asked for.
- **Primary configuration** (ruling 2026-11-16): mode a, TP2, 96-bar time limit,
  running-extreme anchoring, financing on, **Friday close on** (flatten at the first
  H1 close at or after 16:00 New York on Fridays and before the known holiday early
  closes). K × min_bars is chosen by the walk-forward. The pass bar, the random-entry
  control, the zone-shift control and the hold-out apply to the primary only.
- **Sensitivities:** every other variant (modes b–d, TP1/TP3/1R/2R/3R, 48/200-bar
  limits, Friday close off, the API rollover schedule, fully confirmed swings,
  selection scored on financing off or capped) gets its own walk-forward on the same
  nine cells and is reported beside the primary. A sensitivity can't replace a failed
  primary. Any variant adopted after seeing results needs fresh data (the hold-out if
  unused, otherwise demo).
- **Unit:** one observation is a setup with at least one filled leg. Setup R = the sum
  of its legs' P&L / £50 (one leg's planned risk), so leg 1 −£50 and leg 2 +£80 is
  +0.6R and a two-leg loss is −2R. Expectancy = mean setup R, financing on.
- **Walk-forward** over development: 3-year train, 1-year test, rolling yearly,
  giving 5 out-of-sample years from 2019-11 to 2024-11.
  - Each grid cell is one continuous run over development; a setup belongs to the
    window of its first fill *(proposed)*. Setups with a leg still open when the data
    ends are dropped *(proposed)*.
  - **Selection rule**, inside each training window: score = mean setup R minus one
    standard error. A cell is eligible with at least 30 setups and positive mean R
    of its own *(proposed: from "neighbours are also positive")*, and every orthogonal
    neighbour in the K × min_bars grid must have positive mean R (a neighbour with no
    setups doesn't count as positive *(proposed)*). Highest score wins; ties go to
    more setups. Apply it blind to the next year.
  - If no cell is eligible, that test year is sat out with no trades *(proposed)*.
  - Never select anything on out-of-sample results.
- **Random-entry control** (primary only; 1,000 runs; the primary's OOS expectancy
  must beat the 95th percentile). Each run re-enters every primary OOS setup:
  - same pair, side and test year; entry at market at the open of a random H1 bar,
    the hour drawn from that pair's real first-fill hours (UTC hours *(proposed)*);
    the decision is at the previous H1 close, like a mode-b entry;
  - each real leg is one market leg with its real stop and target distances in price
    (taken from the fill price *(proposed)*), placed around the decision bar's mid
    close *(proposed)*;
  - the same engine and primary config: spread, slippage, financing, caps, Friday
    close, one setup per pair. Bars inside the pulled Friday/holiday window can't be
    drawn *(proposed)*. A draw that finds the pair busy is redrawn later in the same
    test year; if no bar at the drawn hour is left it's dropped and counted *(proposed)*.
- **Zone-shift control** (primary only): equal-width zones shifted in 5-point steps
  (retracement %): 51.8–66.4, 56.8–71.4, **61.8–76.4**, 66.8–81.4, 71.8–86.4. Report
  the full curve. 61.8–76.4 must beat both immediate neighbours on OOS expectancy.
  Only the two entries move; stop and targets stay where they are, and each zone gets
  its own walk-forward *(proposed)*.

### Reporting (docs/backtests.md)
For every cell, with all results in planned R: trade counts, longs and shorts
separately, results with financing on / off / credits capped at zero, and the
number of fallback-resolved trades, target-before-fill decisions (§9), legs skipped
by each exposure cap (§7), mode-b entries skipped through the stop or the target
(§8), and weekend holds (§6). For the primary, also: OOS results by entry day of
week (New York weekday of the first fill), and how many legs the Friday exit closed
and their average R.

### Pass criteria
Development (walk-forward out-of-sample, 200+ setups):
- expectancy ≥ +0.1R per setup after spread, slippage and financing;
- beats the random-entry and zone-shift controls;
- positive on both pairs;
- stable across neighbouring parameter cells.

Hold-out (50+ setups, both pairs pooled):
- expectancy > 0 and ≥ half the development figure;
- max drawdown < 15% at 0.5% risk per leg;
- < 2% of trades settled by the stop-first fallback.

**A fail means no demo trading and no re-tuning on the same hold-out.**
