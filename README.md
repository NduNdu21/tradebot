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
| 2 | Trend following + carry, daily | 10–15 FX pairs | **Shelved** as a draft (`docs/strategy-trend-carry.md`); no code. |
| 3 | ICT liquidity sweep → market structure shift → fair value gap, intraday | NAS100_USD, SPX500_USD | **Failed** its development test (2026-11-25): primary mean R −0.15 (n 231), rescue −0.10 (n 269); hold-out not run. Rules in `docs/strategy-ict.md`, results in `docs/backtests.md`. |

The hold-out period has not been used yet.

## Layout

| Path | What it does |
|---|---|
| `src/download.py`, `src/data.py` | Bid/ask candle download and data checks; the hold-out is cut off at load time. |
| `src/backtest.py` | The fill engine: limits, stops, targets, gaps, slippage, same-candle rules, sizing, exposure caps. |
| `src/financing.py` | Overnight financing from historical policy rates. |
| `src/swings.py`, `src/setups.py` | Strategy 1's swing detection and Fibonacci setups. |
| `src/experiments.py`, `src/diagnostics.py` | Strategy 1's walk-forward, controls and post-mortem. |
| `docs/` | Strategy rules, decisions log, backtest results, ICT source material. |

```
python -m unittest              # run the tests
python -m src.experiments       # strategy 1 walk-forward and controls (cached in data/experiments/)
python -m src.diagnostics       # strategy 1 diagnostics
```

## Strategy 1 in brief

Two limit orders at the 61.8% and 76.4% retracements of an ATR-defined swing on H1, a stop at 110% and a target
at the swing extreme. Over all development data every parameter cell lost money. With costs set to zero it was
about break-even (−0.036R per setup, SE 0.041), so the roughly 0.12R per setup that costs took is what made it a
clear loss. Full method and tables are in `docs/strategy.md` and `docs/backtests.md`.
