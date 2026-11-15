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
- ATR(14) uses H1 mid prices, Wilder smoothing *(proposed)*.

## 2. Swings
- A swing ends when price reverses at least K × ATR(14) from its extreme
  (ATR read at the bar where the reversal completes).
- A swing must span at least `min_bars` H1 bars from origin to extreme;
  this stops single-candle spikes from counting as structure.
- Baseline K = 3, min_bars = 5. Grid: K ∈ {2, 3, 4} × min_bars ∈ {3, 5, 8}.

## 3. Setup (anchoring)
**Main run: confirmed origin with a running extreme (no lookahead).**
- **Origin** = always the most recent confirmed swing low (for buys) / high (for
  sells) from the ATR zigzag.
- **Extreme** = the highest high (lowest low) since the origin. It's known at every bar.
- The setup is **armed** once range ≥ K × ATR and the origin→extreme span ≥ min_bars.
- Before any fill, a new high (new low for sells) beyond the extreme **re-anchors**
  the fib in every mode: the levels move to the new extreme at the next H1 close.
  A new extreme never cancels a setup.
- The geometry (extreme, entries, stop, targets) **freezes at the first fill of
  any leg**, never at a zone touch. A touch of the zone without a fill is forgotten.
- Entry, stop and target prices come from the extreme as of the last completed
  H1 bar and are re-priced only at each H1 close. This prevents a new high and a
  pullback inside the same hour from using a level that wasn't known yet.
- Direction = direction of the swing from origin to extreme. No trend filter.

**Robustness run: fully confirmed swings.** Only trade a swing after both ends are
ATR-confirmed. If price is already past the entry level at confirmation, skip that leg.

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
- Both legs share one stop. No further adds. Unfilled legs cancel when the
  position closes. One position per pair at a time; each setup trades at most once.

## 5. Cancelling a pending setup
A setup with no filled leg is cancelled when any of these happens, checked at H1 close:
1. price has traded through the stop level (origin − 10% of range);
2. a newer confirmed swing replaces it;
3. 96 H1 bars pass without a fill (sensitivity: 48 and 200).

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
- **Walk-forward** over development: 3-year train, 1-year test, rolling yearly,
  giving 5 out-of-sample years from 2019-11 to 2024-11.
  - Inside each training window, pick the configuration by a fixed rule: highest
    expectancy among grid cells with at least 30 trades whose grid neighbours are
    also positive. Apply it blind to the next year.
  - Never select anything on out-of-sample results.
- **Random-entry control:** draw entry times from the real trades' hour-of-day
  distribution, with the same pair, direction and R geometry, and the same spread,
  financing and position rules. 1,000 runs; the strategy must beat the 95th percentile.
- **Zone-shift control:** equal-width zones shifted in 5-point steps (retracement
  %): 51.8–66.4, 56.8–71.4, **61.8–76.4**, 66.8–81.4, 71.8–86.4. Report the full
  curve. 61.8–76.4 must beat both immediate neighbours.

### Reporting (docs/backtests.md)
For every cell, with all results in planned R: trade counts, longs and shorts
separately, results with financing on / off / credits capped at zero, and the
number of fallback-resolved trades, target-before-fill decisions (§9), legs skipped
by each exposure cap (§7), mode-b entries skipped through the stop or the target
(§8), and weekend holds (§6).

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
