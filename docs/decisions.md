# Decisions

## 2026-11-13 — Project setup
- Python 3.9.6 (macOS system Python) in `.venv/`; deps pinned in `requirements.txt`.
- `requests` pinned explicitly: oandapyV20 0.7.2 imports it but doesn't declare it.
- `src/config.py` loads `.env` and refuses to run unless `OANDA_ENV == "practice"`.
- Run scripts as modules from the repo root, e.g. `python -m src.balance`.

## 2026-11-13 — Historical data download
- `python -m src.download` writes `data/EURUSD_H1.csv` and `data/GBPJPY_H1.csv` (5 years by default, `--years N` to change).
- Saves mid, bid and ask OHLC so the backtester can model the spread (longs fill at ask, shorts at bid). Times are UTC.
- Only completed candles are saved. Paging stops at the still-forming candle, not on page size (with `includeFirst=False` a full page holds 4999 candles).
- `data/` is gitignored; regenerate it with the script.

## 2026-11-14 — Strategy rules and backtest plan
- Rules agreed with the owner are written in `docs/strategy.md`. Items marked *(proposed)* still need sign-off.
- Swings use the confirmed origin plus the running extreme, so there's no lookahead.
  A fully confirmed ATR swing high usually arrives after price has already passed the entry zone.
- 10 years of M5 bid/ask from OANDA replace the H1 download; H1 is resampled from M5.
- Hold-out 2024-11-14 → 2026-11-13 is pre-registered: one look only, no re-tuning if it fails.

## 2026-11-14 — M5 data (step 1)
- `python -m src.download` now fetches M5 bid/ask from 2016-10-03 (six weeks of warm-up before 2016-11-14).
  About 754k candles and 72 MB per pair. The old H1 files were removed.
- `src/data.py` builds H1 from M5 on the UTC hour grid. Mid = (bid + ask) / 2, field by field. Empty hours are dropped.
- `load_m5` drops everything from 2024-11-14 unless `include_holdout=True`.
- Data checks are clean on both pairs, development and hold-out (hold-out checked for integrity only; no prices or results viewed). Non-weekend gaps: the Christmas and New Year closures,
  plus a 2h50m hole on 2022-05-12 05:15–08:05 UTC in both pairs (cause unknown; it's in both pairs, so it looks like a feed gap rather than a market event).
  It's left as a gap, not filled.

## 2026-11-14 — Financing, account currency, margin (step 2)
- Triple rollover verified: OANDA's help pages (US, Canada, Singapore, Australia, UK, BVI) say FX takes 3 days at
  Wednesday 17:00 New York, with nothing on Saturday or Sunday. USD/CAD is the exception (Thursday).
- The v20 API's `financingDaysOfWeek` for both pairs returns Mon 0 / Tue 1 / Wed 3 / Thu 1 / Fri 1, only 6 days a week.
  The developer docs don't define the day or the timezone. The owner chose the help-page schedule (7 days/week) as the
  main run and the API schedule as a sensitivity run.
- Policy-rate tables were checked against each bank's history page (Fed openmarket, ECB key rates, BoE Bank Rate,
  BoJ statements). Effective dates are used, not announcement dates, since that's when rates actually changed.
  Two caveats: the ECB table came from a summarised page read, but it agrees with decision dates recalled independently
  plus news reports for 2026. The BoJ 2026 levels (1.00% Jun, 1.25% Sep) come from news reports; their effective dates
  assume the next business day.
- Markups calibrated on 2026-11-14 (annual): EUR_USD long 1.085%, short 0.935%; GBP_JPY long 0.92%, short 1.16%.
  They're all about 1%, which suggests the policy-rate model fits OANDA's pricing.
- Account currency is GBP (practice account). Backtest runs in GBP 10,000. GBP_USD M5 downloaded for conversion only (data clean).
- Margin rate: 3.33% for both pairs, from the API. The earlier 5% for GBP_JPY was Claude's assumption, not the owner's.

## 2026-11-14 — Backtest engine (step 3)
- `src/backtest.py`: an `Engine` simulates both pairs on one merged M5 timeline. The strategy is an object with
  `on_h1_close(engine, pair, hour)`, called at the first M5 candle of each new hour. Orders it places are live from
  that candle, so it can't act on prices inside the hour it's looking at.
- The engine owns fills, exits, sizing, caps, Friday close and financing. Setup logic (levels, cancel rules, time
  limit) stays in the strategy (step 4). One setup per pair at a time, enforced by the engine.
- Owner's choices: one shared GBP account; size on the closed balance (not NAV); Friday flatten at the open of the
  16:00 NY candle; units rounded down; conversion at mid.
- Claude's proposals, marked in strategy.md: pending orders can't fill from Friday 16:00 to reopen; a fill at or
  past the stop cancels the order; a target hit on one leg and a fill on the other in the same candle both count.
- Financing modes (on/off/cap) compound into the balance, so each mode is a separate run.
- Open question for step 4: whether cancel rules 1–2 (price through stop, back to extreme) are checked on M5 or at
  H1 close. The engine currently only cancels at H1 close.
- Speed: 8 years of dev data, both pairs, about 7 s per run, plus 2 s to load (a dummy strategy, no results recorded).
