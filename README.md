# Tradebot

An attempt to build a trading bot by testing one strategy at a time, honestly. Each strategy gets written-down
rules and a pass bar *before* any results are seen, and is backtested on historical data. Only a strategy that
passes would move on to demo trading.

It's written in Python and uses the OANDA v20 REST API in the **practice (demo) environment only**. It never
places live orders. Credentials are read from `.env` (gitignored), and the code refuses to run unless
`OANDA_ENV` is `practice`.

## How each strategy is tested

1. **Rules first.** The strategy is written up in `docs/strategy-*.md` and signed off before any code produces a
   result. Anything filled in to make a rule testable is marked *(proposed)* until it's agreed.
2. **Build in order:** data download → backtester → signals → demo execution.
3. **Realistic fills:** real bid/ask candles, slippage on stops, financing, and the conservative call on every
   ambiguous candle.
4. **Pre-registered evaluation:** development data runs 2016-11-14 → 2024-11-13. The hold-out,
   2024-11-14 → 2026-11-13, is kept back and looked at **once**, by a strategy that has already passed on
   development data.
5. **A fail is final.** A failed strategy isn't re-tuned on the same data. The result is recorded and the next
   idea starts from scratch.

Every choice and its reason is logged in `docs/decisions.md`, and results in `docs/backtests.md`.

## Strategies so far

| # | Strategy | Markets | Status |
|---|---|---|---|
| 1 | Fibonacci swing-to-swing retracement, H1 | EUR_USD, GBP_JPY | **Failed** its development test (2026-11-16): −0.16 to −0.24R per setup, no better than random entries. Rules frozen in `docs/strategy.md`. |
| 2 | Trend following + carry, daily | 12 FX pairs | **Failed** its development test (2026-11-25): Sharpe −0.04 over 2006–2024 (needed ≥ 0.4); trend sleeve alone 0.05, carry alone −0.16; hold-out not run. Rules in `docs/strategy-trend-carry.md`. |
| 3 | ICT liquidity sweep → market structure shift → fair value gap, intraday | NAS100_USD, SPX500_USD | **Failed** its development test (2026-11-25): primary mean R −0.15 (n 231), rescue −0.10 (n 269); hold-out not run. Rules in `docs/strategy-ict.md`, results in `docs/backtests.md`. |
| 4 | Multi-asset trend (indices, bonds, metals, FX), daily | 24 CFDs and FX pairs | **Failed** its development test (2026-11-25): Sharpe −0.10 (needed ≥ 0.4). Price moves alone made Sharpe 0.38; OANDA's financing markups (2.5% a year on index and bond CFDs) and spreads turned it negative. Hold-out not run. Rules in `docs/strategy-multi-asset-trend.md`. |
| 5 | Turn of the month (long day −2 close → day +3 close), US index CFDs | SPX500, NAS100, US30, US2000 | **Failed** its development test (2026-11-25): Sharpe −0.09, below random timing (p95 0.52). In 2006–2024 the turn-of-month days earned less than an average day; the month's last day averaged −0.09%. Hold-out not run. Rules in `docs/strategy-turn-of-month.md`. |
| 6 | Short-term mean reversion in an uptrend (buy a 5-day-low close above the 200-day average, sell above the 5-day average), US index CFDs | SPX500, NAS100, US30, US2000 | **Passed** its development test (2026-11-25): Sharpe 0.54, 1,298 trades, 71% winners, beat random entries (p95 0.48). **Passed the hold-out** (2024-11 → 2026-11): Sharpe 1.82, 166 trades, max drawdown 4.1%. Demo trading is built (dry run by default, not yet deployed). Rules in `docs/strategy-mean-reversion.md`. |

The hold-out period (2024-11-14 → 2026-11-13) has now been used, once, by strategy 6. Any future strategy needs new out-of-sample data, such as live demo results.

## Layout

| Path | What it does |
|---|---|
| `src/download.py`, `src/data.py` | Bid/ask candle download and data checks; the hold-out is cut off at load time. |
| `src/backtest.py` | The fill engine: limits, stops, targets, gaps, slippage, same-candle rules, sizing, exposure caps. |
| `src/financing.py` | Overnight financing from historical policy rates. |
| `src/swings.py`, `src/setups.py` | Strategy 1's swing detection and Fibonacci setups. |
| `src/trend/` | Strategy 2: daily bars from H1, FRED rates, signals, simulation, evaluation (`python -m src.trend.evaluate`). |
| `src/meanrev/` | Strategy 6: mean-reversion signals, trade walk, random-entry control (`python -m src.meanrev.evaluate`). |
| `src/checks/` | Planted-edge tests of the backtesters. |
| `src/tom/` | Strategy 5: NYSE calendar, turn-of-month trades, random-timing control (`python -m src.tom.evaluate`). |
| `src/multi/` | Strategy 4: multi-asset universe, CFD financing model, evaluation (`python -m src.multi.evaluate`). |
| `src/live/` | Strategy 6 demo trading: the daily job (`python -m src.live.run`, dry run unless `TRADING_ENABLED=yes`) and the backtest comparison report (`python -m src.live.compare`). Deployed by `render.yaml` (not yet deployed). |
| `src/ict/` | Strategy 3: ICT sessions, swings, setups, M1 fills, evaluation. |
| `src/experiments.py`, `src/diagnostics.py` | Strategy 1's walk-forward, controls and post-mortem. |
| `docs/` | Strategy rules, decisions log, backtest results, ICT source material. |

```
python -m unittest              # run the tests
python -m src.experiments       # strategy 1 walk-forward and controls (cached in data/experiments/)
python -m src.diagnostics       # strategy 1 diagnostics
python -m src.live.run          # strategy 6 daily demo job (dry run unless TRADING_ENABLED=yes)
python -m src.live.compare --since YYYY-MM-DD   # compare demo fills with the backtest
```

## Strategy 1 in brief

Two limit orders at the 61.8% and 76.4% retracements of an ATR-defined swing on H1, a stop at 110% and a target
at the swing extreme. Over all development data every parameter cell lost money. With costs set to zero it was
about break-even (−0.036R per setup, SE 0.041), so the roughly 0.12R per setup that costs took is what made it a
clear loss. Full method and tables are in `docs/strategy.md` and `docs/backtests.md`.
