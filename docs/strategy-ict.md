# Strategy: ICT sweep → market structure shift → FVG

Instruments: NAS100_USD, SPX500_USD (OANDA practice CFDs). Execution chart M5, fills simulated on M1.
New York morning only. Status: **approved by the owner, 2026-11-25.** Items marked *(proposed)* were filled in by Claude to make
a rule testable and were approved as written; the cost placeholders (§8) are re-checked after the download. Everything else is either the owner's ruling (dated) or confirmed
from the 2022 ICT Mentorship captions (see `docs/ict-extracts/2022-verification.md`; "Ep6 36:00" = episode 6 at
36:00).

All times are New York local time (America/New_York, with daylight saving). Shorts are described; longs are the
mirror image.

## Worked example (NAS100_USD short, used throughout)

| Step | What happens | Price |
|---|---|---|
| Daily range | last daily swing low 17,600 → swing high 18,400; 50% = 18,000 | — |
| Liquidity | most recent M5 swing high before 08:30 | 18,100 |
| Target pool | most recent M5 swing low before 08:30 | 17,990 |
| Sweep | 08:45 bar trades to 18,112 (above 18,100) | 18,112 |
| Short-term low | last M5 swing low before the sweep high (08:35) | 18,060 |
| Shift | 08:55 bar closes at 18,052, below 18,060 | — |
| FVG | candle 1 = 08:45 (low 18,090), candle 2 = 08:50, candle 3 = 08:55 (high 18,078) | 18,090 → 18,078 |
| Dealing range | 18,112 (sweep high) → 18,040 (lowest low at 08:55 close); 50% = 18,076 | — |
| Checks | FVG near edge 18,078 is above 18,076 (premium) ✓; 18,078 is above the daily 50% 18,000 (premium) ✓ | — |
| Entry | sell limit at the FVG's near edge | 18,078 |
| Stop | above candle 1's high | 18,112 (34 pts = 1R) |
| Target | nearest swing low below the dealing-range 50% | 17,990 (88 pts = 2.6R) |
| Time exit | if still open at 12:00, close at market | — |

## 1. Data
- M1 bid/ask candles from the OANDA practice API, NAS100_USD and SPX500_USD, from 2016-10-03 (availability
  checked 2026-11-25). M5 and daily candles are built from M1, with mid = (bid + ask) / 2.
- Signals use mid prices; fills use bid/ask (§8) *(proposed, same as the Fibonacci build)*.
- The daily candle runs 17:00 → 17:00 New York, named after the day it closes *(proposed)*.
- Development 2016-11-14 → 2024-11-13; hold-out 2024-11-14 → 2026-11-13, unused so far and looked at once (§10).
- Caveat: these CFDs follow the futures ICT trades (NQ, ES) but are not identical. There is no contract roll to
  model.

## 2. Swings
- A swing high is a candle whose high is above the highs of the candles on each side of it; a swing low is the
  mirror image. Closes don't matter. (Ep2 43:20, Ep5 14:40, Ep10 18:20.)
- A swing is confirmed at the close of the candle to its right. It can't be used before then.
- Equal highs: if two highs are exactly equal, neither is a swing *(proposed)*. "Relative equal highs" are not
  modelled separately *(proposed: they are swing highs anyway)*.

## 3. Daily bias (owner's ruling 2026-11-25)
- Range = the most recent confirmed daily swing high and the most recent confirmed daily swing low.
  50% = their midpoint.
- **Shorts only if the entry price is above 50% (premium); longs only if it is below (discount).**
- Checked against the limit price when the order is placed *(proposed)*. A price exactly at 50% allows
  neither *(proposed)*.
- Based on Ep7 03:40–09:40 and Ep10 04:00–07:00 (premium/discount of the daily dealing range). ICT decides bias
  by eye; this mechanical version is ours.

## 4. Liquidity and the sweep
- Levels, fixed at 08:30: the most recent confirmed M5 swing high and swing low formed before 08:30
  ("the first one, not going back days", Ep5 32:00). One high and one low per day *(proposed: most recent =
  "first" looking back from 08:30)*.
- **Sweep (short):** an M5 candle between 08:30 and 11:00 trades above the pre-08:30 swing high (Ep4 03:00).
  A wick is enough. There's no minimum or maximum distance *(proposed; ICT gives none for indices)*.
- **Sweep extreme** = the highest high from the sweep candle until the shift (§5).
- A level can be swept only once a day. If both levels are swept, each can make its own setup, but only one
  trade per instrument per day is allowed (§7).

## 5. Market structure shift
- **Short-term low** = the most recent confirmed M5 swing low that formed before the sweep extreme's candle
  (Ep2 43:20, Ep4 07:20, Ep6 20:40).
- **Primary (owner's ruling 2026-11-25):** an M5 candle **closes** below the short-term low (Ep6 19:40–21:00,
  23:40).
- **Rescue variant:** an M5 candle **trades** below it; a wick is enough (Ep4 07:20). How it is judged is in §10.
- If price makes a new high above the sweep extreme before the shift, the sweep extreme moves up to it, and the
  short-term low is re-read from before the new extreme *(proposed)*.
- Cancel if no shift by 11:00 *(proposed; follows the 11:00 entry limit)*.

## 6. FVG and entry
- **Bearish FVG:** three consecutive M5 candles where candle 1's low is above candle 3's high, wicks included
  (Ep6 12:00–12:20). The gap runs from candle 1's low down to candle 3's high.
- **It must form in the displacement leg** (Ep6 20:00–24:20): candle 1 at or after the sweep-extreme candle, and
  candle 3 no later than the candle after the shift candle *(proposed: allows an FVG whose third candle closes
  just after the break)*. **No FVG, no trade** (Ep6 22:00, Ep4 06:40).
- **Dealing range** = sweep extreme → lowest low from the sweep extreme to the candle where the order is placed.
  The FVG's near edge must be above its 50% (premium) (Ep4 01:20, Ep6) *(proposed: fixed when the order is
  placed, not updated later)*.
- If more than one FVG qualifies: use the one nearest the sweep extreme, i.e. the deepest in premium
  *(proposed; Ep3 42:00 describes waiting for the deeper gap but gives only one example)*.
- **Entry:** sell limit at the FVG's near edge (candle 3's high), placed at the close of the candle that completes
  both the shift and the FVG (Ep6 35:40–36:20). No extra tick *(proposed)*.
- The order is cancelled if, before filling: 11:00 arrives; price trades through the stop level; or price reaches
  the target *(proposed)*.
- Minimum FVG size: none *(proposed; ICT gives none)*.

## 7. Stop, target, exits, limits
- **Stop:** above candle 1's high (owner's ruling 2026-11-25, from Ep6 36:00). No buffer *(proposed)*.
- **Target:** the nearest confirmed M5 swing low below the dealing range's 50%, formed in the 24 hours before
  the order is placed and not yet traded through *(proposed; from Ep4 02:00–02:40, "an old low below equilibrium")*.
  If there is none, skip the setup *(proposed)*.
- No minimum reward-to-risk *(proposed; ICT gives none)*.
- **Time exit:** anything still open at 12:00 closes at market (owner's ruling 2026-11-25).
- No partials and no stop moves in the primary *(proposed; Ep6 41:00 takes a partial but gives no size or level)*.
- **One trade per instrument per day** *(proposed)*. The two instruments trade independently, so both can be open
  at once *(proposed)*.
- Skip a day if any M5 candle between 08:00 and 12:00 is missing *(proposed)*. US holidays and early closes are
  skipped *(proposed)*.

## 8. Fills and costs *(proposed, same approach as the Fibonacci build)*
- Real bid/ask from M1 on every fill: shorts enter at the bid and exit at the ask; longs the opposite.
- Limit entries and targets: no slippage; if price gaps past a limit it fills at the bar open when that's better.
- Stops and the 12:00 exit: spread plus slippage, NAS100_USD 1.0 point, SPX500_USD 0.25 point *(placeholders,
  to be checked against current spreads)*. A gap through a stop fills at the open when that's worse.
- No financing: every trade is closed by 12:00, before the 17:00 rollover.
- Results are in R: P&L ÷ planned risk (entry to stop, in price).

## 9. Same-bar rules (M1)
- If an M1 bar touches both stop and target, the stop counts first *(proposed, conservative)*. The count of these
  is reported.
- An order can't fill in the M1 bars of the M5 candle that created it *(proposed: the order exists only after that
  candle closes)*.

## 10. Evaluation (pre-registered, owner's ruling 2026-11-25)
There are no parameters to tune, so there is no walk-forward: each version is one run over the whole development
period *(proposed)*.

1. **Primary (shift by close)** is judged on development data against the pass bar.
2. **If it fails,** the rescue variant (shift by wick) gets one attempt, against a **stricter** bar: mean R minus
   **2** standard errors instead of 1, to pay for the second try.
3. **Whichever version passes on development goes to the hold-out once.** Only a hold-out pass counts.
4. If both fail on development, or the hold-out fails, the strategy fails. No re-tuning on the same data.

**Development pass bar** *(proposed, modelled on the Fibonacci bar)*:
- at least 200 trades, both instruments pooled;
- mean R ≥ +0.1 after costs, and mean R − 1 SE > 0 (primary) or mean R − 2 SE > 0 (rescue);
- mean R positive on each instrument;
- beats a random-entry control: same days, side, stop and target distances, entry at a random M5 open between
  08:30 and 11:00; must beat the control's 95th percentile over 1,000 runs.

**Hold-out pass bar** *(proposed)*: at least 50 trades; mean R > 0 and at least half the development figure;
max drawdown < 15% at 0.5% risk per trade.

**Reported but not judged** *(proposed)*: the other shift version (if not used), longs and shorts separately,
per instrument, per year, the stop-first count (§9), skipped setups by reason, and an execution-chart sensitivity
on M1 and M2.

## Still open
- Pass bar numbers, cost placeholders and every *(proposed)* item above: for the owner to confirm.
- Episode 27 ("Counter Trend Ideas") hasn't been reviewed.
