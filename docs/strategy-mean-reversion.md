# Strategy 6: short-term mean reversion in an uptrend (US index CFDs)

**Status: PASSED its development test (2026-11-25): Sharpe 0.54. Hold-out not yet run.** Rules fixed 2026-11-25, before any result was seen. The owner asked to "build the mean reversion
strategy", the last of the ideas proposed after strategy 2 failed. As for strategies 2, 4 and 5, Claude made every
rule decision *(Claude)*, keeping to the version the owner was shown ("buy when the index closes at a 5-day low
while it is above its 200-day average, and sell when it closes back above its 5-day average"). Data checks looked
only at availability and spreads, never at results. Nothing may change after the first run; a fail is final.

## The idea and why it might work
US stock indices have tended to bounce after short pullbacks, at least while the longer trend is up (Connors &
Alvarez 2008 and many replications; at the index level, daily returns showed negative short-horizon
autocorrelation after the 1990s). The usual explanation is liquidity: investors who must sell in a dip pay a
premium to whoever takes the other side. Trades last days, so OANDA's financing markup, which sank strategy 4,
costs much less.

## 1. Instruments and data
- SPX500_USD, NAS100_USD, US30_USD, US2000_USD, judged together (as in strategy 5).
- H1 bid/ask from OANDA practice from 2005-01-01. NYSE trading days from `src/tom/nyse.py`.
- **Development:** 2006-01-02 → 2024-11-13. **Hold-out:** 2024-11-14 → 2026-11-13, run once and only after a
  development pass.

## 2. Signals, at each NYSE close *(Claude)*
- **Close** = the close of the H1 candle opening at 15:00 New York (the 16:00 cash close); on early-close days, the
  last candle that day opening before 15:00 (strategy 5's rule, `src/tom/run.closes`). Mid prices.
- **Uptrend filter:** close > the simple average of the last 200 closes, today's included.
- **Entry signal:** flat, the filter holds, and today's close is the lowest of the last 5 closes, today's included
  (a tie with an earlier close counts).
- **Exit signal:** in a trade and today's close is above the simple average of the last 5 closes, today's included.
- **Time stop:** exit at the close of the 10th trading day after entry if the exit signal hasn't fired, so no
  trade is held for long. No price stop and no target: published tests of this pattern find stops hurt it.
- One position per instrument. A new entry needs a new signal on a later day than the exit.

## 3. Execution and sizing *(Claude)*
- A signal is acted on at the **open of the next H1 candle after the signal candle** (normally the 16:00 New York
  candle; on early-close days the next candle that exists). The signal needs the close, so it can't fill at it.
  Measured 16:00 opening spreads: 0.5–1.6 bp median; the candle is missing on 39–46 of 4,749 days.
- Buys at the ask open + slippage, sells at the bid open − slippage. Slippage = half the median opening spread of
  that candle over the development period (strategy 4's rule).
- Size: units whose one-ATR20 daily move is worth GBP 25 (0.25% of GBP 10,000), from the last completed daily bar
  before entry, rounded to 0.01 units.

## 4. Financing and accounting
- Strategy 5's model, unchanged: long rate = dividend yield − USD cash rate − 2.5%, charged on each 17:00 New York
  rollover the trade is open through (Mon–Thu 1 day, Fri 3 days), on entry notional. Dividend yields: SPX500 2.0%,
  NAS100 0.9%, US30 2.2%, US2000 1.4%.
- GBP at GBP_USD's mid close on the exit day. Each trade's P&L is booked on its exit day; daily portfolio P&L is
  the sum over instruments on NYSE days. Sharpe = mean ÷ s.d. of daily P&L ÷ GBP 10,000 × √252.

## 5. Evaluation (pre-registered)
**Development pass bar (all must hold), the same as strategies 2, 4 and 5:**
1. Sharpe ≥ 0.4 after spread, slippage and financing.
2. Total P&L positive in at least 2 of 3 sub-periods: 2006–2011, 2012–2017, 2018 → 2024-11-13.
3. **Beats a random-entry control** at the 95th percentile over 1,000 runs. Each real trade is replaced by one in
   the same instrument and calendar year, entered on a random day **when the uptrend filter holds**, and held for
   the same number of trading days, with the same sizing and costs. Seed 0. This separates the dip-buying timing
   from simply being long in uptrends.
4. Sharpe > 0.2 with the instrument that made the most money removed.

**Hold-out pass bar:** Sharpe > 0 and max drawdown < 15%.

**Reported but not judged:** trades, win rate, mean P&L and t-statistic, mean hold, time-stop exits, per
instrument, per year, P&L parts, financing off, and the same rules without the uptrend filter.

Caveat: this pattern is widely traded by retail and systematic traders, which may have eroded it, so a fail is a
real possibility.
