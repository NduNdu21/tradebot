# Tradebot

A Python research bot for a Fibonacci swing-to-swing retracement strategy on EUR_USD and
GBP_JPY (H1 signals, M5 fills), using OANDA v20 data from the practice environment only.
It never places live orders.

**Status: the strategy failed its pre-registered development test (2026-11-16). There will be
no demo trading, and the rules won't be re-tuned on this data.** The hold-out period has never
been looked at.

## Layout

| Path | What it does |
|---|---|
| `src/download.py`, `src/data.py` | M5 bid/ask download, H1 resampling, data checks. The hold-out is cut off at load time. |
| `src/financing.py` | Overnight financing from historical policy rates, calibrated to OANDA's current rates. |
| `src/backtest.py` | M5 engine: fills, stops, targets, gaps, slippage, same-bar rules, GBP sizing, exposure caps, Friday close. |
| `src/swings.py`, `src/setups.py` | Wilder ATR, the K × ATR zigzag, Fibonacci setups, entry modes and cancels. |
| `src/experiments.py` | The step-5 protocol: grid, walk-forward, sensitivities, random-entry and zone-shift controls. |
| `src/diagnostics.py` | Post-mortem diagnostics on the failed primary. |
| `docs/strategy.md` | The rules, exactly as tested. |
| `docs/decisions.md` | Why each choice was made. |
| `docs/backtests.md` | Generated results. |

```
python -m unittest              # 157 tests
python -m src.experiments       # walk-forward and controls (about 8 min on 7 workers; cached in data/experiments/)
python -m src.diagnostics       # diagnostics (about 30 min for 1,000 random draws)
```

Credentials are read from `.env` (gitignored), and `OANDA_ENV` must be `practice`.

## Method

All rules are in `docs/strategy.md`. They were written and signed off before any results
were seen, and the 20 details Claude filled in were frozen after the result.

- **Signal:** an ATR(14) zigzag (K × ATR reversal) on H1 mid prices finds swing lows and
  highs. A buy setup runs from the latest confirmed swing low to the highest high since
  then, and arms once that range is at least K × ATR and spans at least `min_bars` bars.
  The fib re-anchors on new highs until the first fill. Sells are the mirror image.
- **Orders:** two limit legs at the 61.8% and 76.4% retracements, a shared stop at 110%,
  and the target at the swing extreme (TP2). Setups are cancelled when price trades
  through the stop, a newer swing low forms, or 96 bars pass without a fill. Positions
  are flattened before the weekend and before the holiday early closes.
- **Simulation:** fills on real M5 bid/ask candles, with slippage on stops and forced
  exits and the conservative call on every same-candle ambiguity. Overnight financing
  is modelled. The two pairs share one GBP account, risking 0.5% (£50) per leg, with 5×
  notional and 20% margin caps.
- **Protocol:** development data runs 2016-11-14 → 2024-11-13; the hold-out is
  2024-11-14 → 2026-11-13. A rolling walk-forward trains on 3 years and tests on 1,
  choosing K ∈ {2,3,4} × min_bars ∈ {3,5,8} by a fixed rule: mean R minus one standard
  error, at least 30 setups, and all orthogonal neighbours positive. The primary had to
  reach ≥ +0.1R per setup out of sample over at least 200 setups, beat a random-entry
  control and a zone-shift control, and be positive on both pairs. Every other variant
  (entry modes, targets, time limits, weekend holding, confirmed swings) was reported
  only as a sensitivity.

## Result: negative

Full tables are in `docs/backtests.md`.

- **No configuration was ever selected.** All 9 cells lost money in every training
  window (the best training mean was −0.12R per setup), so the walk-forward traded
  nothing and the primary failed every criterion.
- **The loss is broad.** Over all development data the 9 cells range from −0.16 to −0.24R
  per setup. Every sensitivity and every shifted zone is negative in every cell; the best
  is leg 1 only, at −0.09R.
- **Without costs there's no edge left to find.** With spread, slippage and financing
  all set to zero, the baseline cell (K=3, min_bars=5) is −0.036R per setup (SE 0.041),
  statistically indistinguishable from zero. Costs (about 0.12R per setup) turn that
  into a clear loss.
- **It's no better than random timing.** Re-entering the baseline's own setups at random
  times, with the same direction, stop and target distances and costs, gives a median of
  −0.10R. The strategy's −0.16R sits at the 9.5th percentile of 1,000 random runs.
- **Spot-checks** of five randomly drawn trades (prices, times and R) are listed in
  `docs/backtests.md` for checking against a chart.

Under the pre-registered rules this ends the strategy as specified. Any new idea would need
its own rules written down first, and could use the untouched hold-out once.
