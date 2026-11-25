# Strategy 5: turn of the month (US index CFDs)

**Status: rules fixed 2026-11-25, before any result was seen.** The owner asked to "build turn of the month", the
first of the two short-hold ideas left after strategy 4 failed. As for strategies 2 and 4, Claude made every rule
decision *(Claude)*, from the published definition of the effect and from data checks of availability and spreads,
never from strategy results. Nothing may change after the first run; a fail is final.

## The effect and why it might work
US stock returns have been concentrated in a 4-day window around the turn of each month: the last trading day of
the month through the third trading day of the next (Ariel 1987; Lakonishok–Smidt 1988; McConnell–Xu 2008, who
find that from 1926 to 2005 the US market's whole excess return came in those days, in 30 of 35 countries). The
usual explanation is month-end cash flows: salaries, pension contributions and fund inflows getting invested.
Holding about 4 days a month keeps OANDA's financing markup (which sank strategy 4) to about a fifth of a full-time
position's.

## 1. Instruments *(Claude)*
SPX500_USD, NAS100_USD, US30_USD, US2000_USD, judged together as one portfolio. They're highly correlated, so this
is close to a single test of the US effect; using all four guards against one CFD's data quirks.

## 2. Data
- H1 bid/ask from OANDA practice from 2005-01-01. NYSE trading days from the exchange's holiday rules
  (`src/tom/nyse.py`), which match the hand-checked 2016–2026 list in `src/ict/calendar.py` exactly.
- **Development:** 2006-01-02 → 2024-11-13. **Hold-out:** 2024-11-14 → 2026-11-13, run once and only after a
  development pass.

## 3. Rules *(Claude)*
- **Day −1** = the last NYSE trading day of the month; **day +3** = the third NYSE trading day of the next month.
- **Entry:** buy at the close of day −2 (so day −1's return is captured, as in McConnell–Xu).
  **Exit:** sell at the close of day +3. That holds 4 trading days, longs only.
- **"Close"** = the close of the H1 candle opening at 15:00 New York, i.e. the 16:00 cash close. On early-close days,
  which have no 15:00 candle, the last H1 candle that day opening before 15:00 is used.
- **Size:** units whose one-ATR20 daily move (mid, from daily bars as in strategy 2) is worth GBP 25 (0.25% of the
  GBP 10,000 base), set at entry, rounded to OANDA's precision (0.01 units for all four).
- No stop, no target, no other filter. A month's trade is skipped only if the instrument has no candle at entry or
  exit, and that is counted.

## 4. Costs
- Buy at the ask close + slippage, sell at the bid close − slippage. Slippage = half the median spread at that
  candle over the development period (strategy 4's rule). Measured spreads: 0.4–1.5 bp median.
- **Financing** (strategy 4's index model): long rate = dividend yield − USD cash rate − 2.5% markup, charged at each
  17:00 New York rollover the position is held through, with OANDA's CFD schedule (Mon–Thu 1 day, Fri 3 days; from
  the API, 2026-11-25). Dividend yields fixed: SPX500 2.0%, NAS100 0.9%, US30 2.2%, US2000 1.4%. Rates from FRED as
  in strategy 2.
- P&L converted to GBP at GBP_USD's mid close on the exit day.

## 5. Accounting
Each trade's P&L is booked on its exit day. Daily portfolio P&L = the sum over instruments on NYSE trading days
(0 on days with no exit). Sharpe = mean ÷ standard deviation of daily P&L ÷ GBP 10,000 × √252. Max drawdown on
cumulative P&L, as a share of GBP 10,000.

## 6. Evaluation (pre-registered)
**Development pass bar (all must hold):**
1. Sharpe ≥ 0.4 after spread, slippage and financing.
2. Total P&L positive in at least 2 of 3 sub-periods: 2006–2011, 2012–2017, 2018 → 2024-11-13.
3. **Beats a random-timing control** at the 95th percentile over 1,000 runs. Each month, the control buys at the close
   of a random NYSE trading day of that month (the same day for all four instruments) and holds 4 trading days, with
   the same sizing and costs. Seed 0. This separates the timing edge from simply being long stocks 4 days a month.
4. Sharpe > 0.2 with the instrument that made the most money removed.

**Hold-out pass bar:** Sharpe > 0 and max drawdown < 15%.

**Reported but not judged:** per instrument, per year, per calendar month, mean P&L per trade and its t-statistic,
win rate, financing off, buy-and-hold of the same four (for scale), costs, skipped months.

Caveat: the effect is famous and has been studied since the 1980s; well-known calendar effects often shrink after
publication, so a fail is a real possibility.
