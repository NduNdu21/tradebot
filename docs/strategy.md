# Strategy: Fibonacci swing-to-swing retracement

Pairs: EUR_USD, GBP_JPY. Signals on H1, all fills simulated on M5.
Rules agreed 2026-11-14, revised the same day. Items marked *(proposed)* were
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
- **Origin** = the most recent ATR-confirmed swing low (for buys) / high (for sells).
- **Extreme** = the highest high (lowest low) since the origin. It's known at every bar.
- The setup is **armed** once range ≥ K × ATR and the origin→extreme span ≥ min_bars.
- The extreme is **frozen** when price first trades into the entry zone.
- Entry, stop and target prices come from the extreme as of the last completed
  H1 bar and are re-priced only at each H1 close *(proposed)*. This prevents a new
  high and a pullback inside the same hour from using a level that wasn't known yet.
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
A setup with no filled leg is cancelled when any of these happens:
1. price trades through the stop level (origin − 10% of range);
2. price returns to the swing extreme (TP2);
3. a newer confirmed swing replaces it;
4. 96 H1 bars pass without a fill (sensitivity: 48 and 200).

## 6. Exits
- **Stop:** origin − 10% of range, triggered on the bid for longs and the ask for shorts.
- **Targets**, each tested as a separate all-out exit on the same trades:
  - TP1 = 61.8% of the swing above origin
  - **TP2 = swing extreme (headline)**
  - TP3 = 161.8% extension
  - Controls: fixed 1R, 2R, 3R from each leg's own entry.
- **Friday close (variant, on/off):** when on, flatten everything at 16:00
  New York on Friday, at the open of the M5 candle starting 16:00 NY (bid for longs,
  ask for shorts, plus slippage). Pending setups survive the weekend *(proposed)*,
  but can't fill from 16:00 Friday until the market reopens *(proposed)*.

## 7. Sizing and exposure
- Account currency is GBP, matching the practice account. Start with 10,000 GBP.
  Both pairs share this one account, simulated on a merged M5 timeline.
- Risk 0.5% of the **closed balance** per leg (1% per full setup), compounding.
  Open P&L doesn't count; realised P&L and financing do.
- Units = risk in GBP / (stop distance in GBP per unit), rounded down to a whole
  unit; a leg that rounds to 0 is skipped. Stop distance = fill price − stop.
  EUR_USD amounts convert USD → GBP at the GBP_USD mid; GBP_JPY amounts convert
  JPY → GBP at the GBP_JPY mid. Sizing uses the mid at the open of the fill candle,
  P&L the mid when it's realised.
- Skip a leg if it would push total notional above 10× equity, or margin used
  above 50%. Margin rate is 3.33% for both pairs, the account's rate from the API.
  Both caps count open legs on both pairs at current prices. At 3.33%, 10× notional
  uses 33% margin, so the notional cap always binds first.

## 8. Fills and costs
- Real bid/ask spread from the data on every fill. Longs enter at the ask and exit
  (stop and target) at the bid; shorts the opposite.
- **Market entries** (mode b, next H1 open) fill at the open, spread only.
- If a fill would happen at or beyond the stop (a gap through the stop), the order
  is cancelled instead *(proposed)*; this is §5 rule 1 on a gap.
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
- Every result is reported three ways: financing on, off, and credits capped at zero.

## 9. Same-bar rules (M5)
- **Stop and target in one bar:** assume the stop was hit first, and count it as a
  fallback decision.
- **Fill and target in one bar:** count the fill, not the target. The target
  can only be hit from the next bar on.
- **Fill and stop in one bar:** the leg filled and was then stopped. For a limit,
  price must pass the entry to reach the stop, so this order is certain.
- **One leg hits its target while the other leg fills, in one bar:** both happen.
  The filled leg stays open; its target counts from the next bar *(proposed)*.

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
For every cell: trade counts, longs and shorts separately, results with financing
on / off / credits capped at zero, and the number of fallback-resolved trades.

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
