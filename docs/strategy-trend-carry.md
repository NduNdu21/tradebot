# Strategy 2: trend + carry (DRAFT)

**Status: draft, 2026-11-17. Nothing here is agreed except the three owner choices below.**
Every item marked *(proposed)* was filled in by Claude and needs the owner's ruling
before any code is written or any result is looked at.

The Fibonacci strategy (`docs/strategy.md`) failed its development test and is retired.
Its rules stay frozen; nothing here changes them.

## Owner choices (2026-11-17)
- Strategy family: **trend following combined with carry**.
- Markets: **a basket of 10–15 currency pairs**.
- Timeframe: **daily signals, positions held for weeks**.

## 1. Universe *(proposed)*
12 pairs over 8 currencies:
EUR_USD, GBP_USD, USD_JPY, USD_CHF, USD_CAD, AUD_USD, NZD_USD,
EUR_GBP, EUR_JPY, GBP_JPY, AUD_JPY, EUR_CHF.
- Many pairs share a currency (USD appears in 7), so a per-currency exposure cap is needed (§4).

## 2. Data *(proposed)*
- H1 bid/ask candles from OANDA practice, as far back as each pair allows (target 2006-01).
  M5 over 20 years × 12 pairs is roughly 2 GB, and daily signals don't need it.
- Daily bars are built from H1 and close at 17:00 New York (OANDA's daily boundary). Mid = (bid + ask) / 2.
- Stops and fills are simulated on H1. With daily-sized stops, a stop and a target in the same hour should be rare; count them.
- **Development:** first daily bar + 1 year warm-up → 2024-11-13.
  **Hold-out:** 2024-11-14 → 2026-11-13, unchanged and still unused. One look, frozen settings.
- Pair history depth must be checked first; a pair that starts late joins when its warm-up ends.

## 3. Signals, at each daily close *(proposed)*
- **Trend:** for lookbacks of 63, 126 and 252 trading days (about 3, 6 and 12 months),
  take the sign of the price change over that period. Trend score = mean of the three
  signs: −1, −⅓, +⅓ or +1.
- **Carry:** the annual financing a long would earn or pay minus what a short would,
  from `src/financing.py` (policy rates plus OANDA markups). Carry signal = +1 if holding
  long earns, −1 if holding short earns, 0 if the gap between them is under 0.5% a year.
  The rate tables currently cover USD, EUR, GBP and JPY only. AUD, NZD, CAD and CHF must
  be added and checked the same way.
- **Combining them — owner's ruling needed.** Options:
  - **A. Filter:** trade the trend direction only when carry is not against it.
  - **B. Score:** position = trend score + carry signal, traded when the total is at least a threshold.
  - **C. Two sleeves (Claude's recommendation):** half the risk follows trend, half follows
    carry, netted per pair. Each sleeve can be reported on its own, so we can see which part works.

## 4. Sizing and exposure *(proposed)*
- Account GBP 10,000, as before. Research runs don't compound.
- Volatility sizing: each sleeve's position in a pair risks 0.25% of the base per one
  ATR(20) of daily mid prices, so quiet and volatile pairs carry equal risk.
- Rebalance only when the signal changes or the size has drifted more than 25% from target (keeps costs down).
- Caps: net exposure per currency, and the existing notional and margin caps. Levels to be agreed.

## 5. Exits *(proposed)*
- A position closes when its signal changes (or the sleeve's net target becomes zero).
- Owner's ruling needed: add a disaster stop (e.g. 4 × ATR(20) from entry) or not.
  Trend-following research mostly uses signal exits only; a stop limits single-pair damage
  but whipsaws more.
- No Friday close: weekend holds are normal for this strategy.

## 6. Costs
- Bid/ask spread from the data, slippage as in the Fibonacci engine, financing from
  `src/financing.py` (financing is part of the carry signal's return, so it is always on).

## 7. Evaluation *(proposed)*
- **No parameter selection.** Every parameter above is fixed before any result is seen,
  so the whole development period is one test. A grid of other lookbacks and thresholds
  is reported only as a sensitivity and can't replace the primary.
- **Unit:** daily portfolio return in GBP. Report Sharpe, annual return, max drawdown,
  per year, per pair, and per sleeve.
- **Development pass bar:**
  - Sharpe ≥ 0.4 after spread, slippage and financing;
  - positive in at least 2 of 3 sub-periods (2006–2011, 2012–2017, 2018–2024);
  - beats a random-sign control (signal signs shuffled per pair and month, same holding lengths) at the 95th percentile;
  - still Sharpe > 0.2 with the best pair removed.
- **Hold-out:** Sharpe > 0 and max drawdown < 15%.
- A fail means no demo trading and no re-tuning on the same data.

Published trend and carry results are modest (historical Sharpe roughly 0.5–0.8,
lower since 2010), so a fail is a real possibility.
