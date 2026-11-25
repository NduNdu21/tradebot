# Strategy 4: multi-asset trend

**Status: rules fixed 2026-11-25, before any result was seen.** The owner asked for "number 3" of the three ideas
proposed after strategy 2 failed ("trend following across asset classes instead of FX alone"). As for strategy 2,
Claude made every rule decision *(Claude)*, from published research and from data checks that look at
availability, spreads and price construction, never at strategy results. Nothing may change after the first run;
a fail is final.

Strategies 1–3 stay frozen. This one reuses strategy 2's code for daily bars, the trend score, the trading rule and
P&L accounting, unchanged.

## Why this might work where strategy 2 didn't
Trend following's best evidence comes from spreading risk across asset classes that don't move together
(Moskowitz–Ooi–Pedersen 2012; Hurst–Ooi–Pedersen 2017, a century of data). Strategy 2's 12 currency pairs largely
moved together. Here FX is one class of four.

## 1. Universe *(Claude)*
| Class | Instruments |
|---|---|
| Equity indices | SPX500_USD, DE30_EUR, UK100_GBP, JP225_USD, AU200_AUD, EU50_EUR |
| Government bonds | USB10Y_USD, USB30Y_USD, DE10YB_EUR, UK10YB_GBP |
| Metals | XAU_USD, XAG_USD |
| FX | strategy 2's 12 pairs |

- **Excluded: energy and agricultural CFDs** (WTICO, BCO, NATGAS, CORN, WHEAT, SOYBN, SUGAR, XCU). Their prices are
  built from futures that roll, visible as month-end jumps in NATGAS, and OANDA's financing on them today ranges
  from −127% to +37% a year, which looks like roll cost charged as financing. That history can't be rebuilt, so
  these can't be tested honestly. Decided from the data check, before any result.
- Indices, bonds and metals showed no roll jumps: their largest hourly gaps are on news days (2008, 2016-06-24,
  2020-03), and WTICO didn't go negative in April 2020.

## 2. Data
- H1 bid/ask from OANDA practice from 2005-01-01 (metals from 2006-03-19). Daily bars, weekend-candle removal and
  holiday-stub merging exactly as strategy 2 (`src/trend/bars.py`).
- Cash rates: as strategy 2 (OECD monthly interbank, FRED, used from the month after). Bond yields: OECD 10-year
  (`IRLTLT01US/DE/GBM156N`) and the US 30-year (`GS30`), also used from the month after.
- **Development:** 2006-01-02 → 2024-11-13. **Hold-out:** 2024-11-14 → 2026-11-13, run once and only after a
  development pass. An instrument joins once it has 272 daily bars.

## 3. Signal and sizing *(Claude)*
- **Trend score:** strategy 2's, unchanged: the mean of the signs of the 63-, 126- and 252-bar close change.
- **No carry sleeve.** Position = trend score × U.
- **Equal risk per class:** each class gets GBP 25 per ATR20 (0.25% of the GBP 10,000 base), split equally across
  its members: U = (25 / class size) / (ATR20 × quote→GBP). So an index gets £4.17 per ATR, a bond £6.25, a metal
  £12.50, an FX pair £2.08. That is less than half of strategy 2's total risk.
- **Units are rounded to OANDA's trade precision** (fetched 2026-11-25): 0.01 for SPX500, DE30, JP225; 0.1 for
  UK100, AU200, EU50, XAU; whole units for bonds, XAG and FX. A target that rounds to 0 is flat. This is what a
  GBP 10,000 account could actually hold; at £4.17 per ATR some index targets round coarsely or to zero. An
  unrounded run is reported. *(Added before any result, after checking the precision.)*

## 4. Entry, exit, rebalancing
Strategy 2's §4, unchanged: signals at the 17:00 New York close; orders at the open of the next bar's 08:00 New York
H1 candle (or the first candle after it that day, else the next bar). Trade on a sign change or a drift of more
than 25% from target. Signal exits only, no stop and no take-profit.

## 5. Fills and costs *(Claude)*
- Buys at the ask plus slippage, sells at the bid minus slippage.
- **Slippage = half the instrument's median spread at the 08:00 candle over the development period**, one rule
  for every instrument (it puts FX at 0.6–1.4 pips, a little above strategy 2's 0.5–1.0).

## 6. Financing *(Claude)*
At each 17:00 New York weekday rollover (Wednesday counts 3 days): |units| × close × rate × days / 365.
- One formula for everything: long = (asset yield − funding rate) − m; short = (funding rate − asset yield) − m.

| Class | Asset yield | Funding rate | Markup m |
|---|---|---|---|
| FX | base currency's cash rate | quote currency's cash rate | 1.0% |
| Indices | dividend yield, fixed (below) | cash rate of the index currency (USD for JP225_USD) | 2.5% |
| Bonds | that bond's yield (10-year; 30-year for USB30Y) | the bond currency's cash rate | 2.5% |
| Metals | 0 | USD cash rate | XAU 1.1%, XAG 1.4% |

- **Markups were measured** as −(long + short) / 2 from OANDA's rates on 2026-11-25. Every index and bond comes out
  at 2.5%; gold 1.1%, silver 1.4%; FX about 1.0%.
- **Dividend yields** (fixed, long-run averages): SPX500 2.0%, UK100 3.7%, JP225 1.8%, AU200 4.3%, EU50 3.3%, DE30 0
  (the DAX is a total-return index). Longs are credited, shorts charged. A no-dividend run is reported.
- The bond model is the least certain part: today's OANDA bond rates match (yield − cash) only to within about
  1.5% a year. A run with bond asset yield = funding rate (carry 0, markup only) is reported.

## 7. Accounting
Strategy 2's §7, unchanged, with the conversions: USD via GBP_USD, EUR via EUR_GBP, AUD via GBP_AUD, CHF via GBP_CHF,
CAD via GBP_CAD, JPY via GBP_JPY, GBP = 1.

## 8. Evaluation (pre-registered)
**Development pass bar, the same as strategy 2 (all must hold):**
1. Sharpe ≥ 0.4 after spread, slippage and financing.
2. Total P&L positive in at least 2 of 3 sub-periods: 2006–2011, 2012–2017, 2018 → 2024-11-13.
3. Beats the random-sign control (random sign per holding episode, 1,000 runs, seed 0) at the 95th percentile.
4. Sharpe > 0.2 with the instrument that made the most money removed.

**Hold-out pass bar:** Sharpe > 0 and max drawdown < 15%.

**Reported but not judged:** each class on its own, unrounded units, no dividends, bond carry 0, financing off, per year, per
instrument, costs, peak margin, average holding length.

Published multi-asset trend results since 2010 are weaker than the long history (roughly Sharpe 0.3–0.6 for
managed-futures indices), and OANDA's 2.5% CFD markup costs more than futures do, so a fail is a real possibility.
