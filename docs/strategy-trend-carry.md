# Strategy 2: trend + carry

**Status: rules fixed 2026-11-25, before any result was seen.** The owner handed every remaining decision to
Claude ("make all trading decisions … determine entry and exit rules", 2026-11-25). Items marked *(Claude)* are
those decisions. Each one was chosen from published trend and carry research and from this project's earlier
results, not from looking at this data. Nothing below may be changed after the first run; a fail is final.

The Fibonacci (`docs/strategy.md`) and ICT (`docs/strategy-ict.md`) rules stay frozen; nothing here changes them.

## Owner choices (2026-11-17)
- Strategy family: **trend following combined with carry**.
- Markets: **a basket of 10–15 currency pairs**.
- Timeframe: **daily signals, positions held for weeks**.

## 1. Universe *(Claude)*
12 pairs over 8 currencies: EUR_USD, GBP_USD, USD_JPY, USD_CHF, USD_CAD, AUD_USD, NZD_USD, EUR_GBP, EUR_JPY,
GBP_JPY, AUD_JPY, EUR_CHF. GBP_CHF and GBP_CAD are downloaded only to convert P&L to GBP.

## 2. Data *(Claude)*
- H1 bid/ask candles from OANDA practice, from 2005-01-01. Mid = (bid + ask) / 2.
- Daily bars are built from H1 and run 17:00 → 17:00 New York, named after the day they close. A daily bar with
  fewer than 6 H1 candles (a holiday stub) is merged into the next bar.
- Interest rates: OECD "immediate rates: call money / interbank", monthly, from FRED (`IRSTCI01<cc>M156N`, cc =
  US, EZ, GB, JP, AU, NZ, CA, CH). A month's average is used only from the first day of the **next** month, so the
  signal never uses a rate that wasn't published yet.
- **Development:** 2006-01-02 → 2024-11-13 (2005 is warm-up). **Hold-out:** 2024-11-14 → 2026-11-13, cut off at
  load time, run once and only if development passes. Where a rate series stops before the hold-out ends (NZ ends
  2024-12, EZ 2026-01), the hold-out run fills it from the central bank's policy rate.
- A pair that starts later than 2005 joins once it has 272 daily bars.

## 3. Signals, at each daily close *(Claude)*
- **Trend score:** the mean of the signs of the mid-close change over 63, 126 and 252 daily bars (about 3, 6 and
  12 months): −1, −⅓, +⅓ or +1. A change of exactly 0 counts as 0. Time-series momentum with several lookbacks
  is the most replicated form of FX trend (Moskowitz–Ooi–Pedersen 2012, Hurst–Ooi–Pedersen 2017), and averaging
  the lookbacks avoids choosing one.
- **Carry signal:** +1 if a long position earns financing after OANDA's markup, −1 if a short does, 0 otherwise.
  With a 1% markup each side (§6), that means +1 when base rate − quote rate > 1%, −1 when it is < −1%. This trades
  carry only where it survives the broker's charge.
- **Combining: two sleeves.** Target position = (trend score + carry signal) × U, so each sleeve carries equal risk
  and they net within the pair. The range is −2U … +2U.
- **U (volatility sizing):** the units whose one-ATR move equals 0.25% of the GBP 10,000 base (GBP 25):
  U = 25 / (ATR20 in quote currency × quote→GBP rate). ATR20 = the simple mean of the last 20 daily true ranges on
  mid prices. Research runs don't compound.

## 4. Entry, exit and rebalancing *(Claude)*
- Signals are read at the 17:00 New York close. Orders go in at the **open of the 08:00 New York H1 candle** on the
  next daily bar (London/New York overlap, when spreads are tight; the 17:00 rollover is when they are widest). If
  that candle is missing, the first H1 candle after 08:00 in the same daily bar is used; if there is none, the
  order waits for the next bar.
- **Trade when** the target's sign differs from the position's sign (including to or from flat), or when the
  position is more than 25% away from the target (|position − target| > 0.25 × |target|). A trade always goes to
  the target exactly, rounded to whole units.
- **Exits are signal exits only:** a position shrinks or closes when its target does. There is **no stop and no
  take-profit.** Trend research uses signal exits; a stop on a weeks-long hold mostly adds whipsaw and cost.
- No Friday close; weekend holds are normal.
- No per-currency cap. At these sizes the margin need stays well under the account (checked and reported).

## 5. Fills
- Buys fill at the ask open plus slippage; sells fill at the bid open minus slippage.
- Slippage *(Claude)*: 0.5 pip on every pair except EUR_JPY, GBP_JPY and AUD_JPY, which pay 1.0 pip (wider crosses).

## 6. Financing
- At each 17:00 New York weekday rollover: amount = |units| × mid close × annual rate × days / 365, in the quote
  currency. Days: 1 on Mon/Tue/Thu/Fri, 3 on Wed (OANDA help pages, as in strategy 1).
- Annual rate *(Claude)*: long = (base rate − quote rate) − 1%; short = (quote rate − base rate) − 1%. The 1% markup
  per side was measured: OANDA's rates for all 12 pairs on 2026-11-25 sum (long + short) to −2.0% to −2.1%, i.e.
  about 1% each side (`data/rates/oanda_financing_2026-11-25.json`; USD_CAD showed 0/0 and is ignored).
- Financing is always on in the primary, because it is the carry sleeve's return. A financing-off run is reported.

## 7. Accounting
- Each pair is marked to market at the daily mid close. Daily P&L in the quote currency =
  units held overnight × (today's close − yesterday's close) + units traded × (today's close − fill price) +
  financing, then converted to GBP at today's mid close of GBP_USD, GBP_JPY, GBP_CHF or GBP_CAD (or 1 for GBP).
- Daily portfolio return = sum over pairs ÷ GBP 10,000. Sharpe = mean ÷ standard deviation of daily returns ×
  √(daily bars per year, counted from the data). Max drawdown = largest peak-to-trough fall of cumulative P&L, as a
  share of GBP 10,000.

## 8. Evaluation (pre-registered) *(Claude, from the 2026-11-17 draft)*
There are no parameters to choose, so the whole development period is one test.

**Development pass bar (all must hold):**
1. Sharpe ≥ 0.4 after spread, slippage and financing.
2. Total P&L positive in at least 2 of 3 sub-periods: 2006–2011, 2012–2017, 2018 → 2024-11-13.
3. Beats a random-sign control at the 95th percentile over 1,000 runs. Control: each holding episode (a run of days
   with the same position sign in a pair) gets a random sign, so holding lengths, sizes and trades are identical
   and only the direction is random. Financing follows the flipped side. Seed 0.
4. Sharpe > 0.2 with the pair that made the most money removed.

**Hold-out pass bar:** Sharpe > 0 and max drawdown < 15%.

**Reported but not judged:** trend sleeve alone, carry sleeve alone, financing off, per year, per pair, spread and
slippage paid, financing earned, peak margin use, average holding length.

A fail means no demo trading and no re-tuning on the same data. Published trend and carry results are modest
(historical Sharpe roughly 0.5–0.8, lower since 2010), so a fail is a real possibility.
