# Demo Trading (Strategy 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stateless job, run by a Render cron, that trades strategy 6 on the OANDA practice account each NYSE day just after 16:00 New York, plus a local tool that compares live fills with the backtest.

**Architecture:** `src/live/` has four focused modules: `market.py` fetches candles from the API into the backtest's DataFrame shape; `decide.py` holds pure entry/exit decisions built on `src.meanrev.run.signals`; `broker.py` wraps open trades, orders and the safety rails; `run.py` gates on time and orchestrates. `compare.py` is a local report. Deployment is a `render.yaml` cron blueprint.

**Tech Stack:** Python (3.9 locally, 3.11 on Render), pandas, oandapyV20 0.7.2, unittest, Render cron jobs.

**Spec:** `docs/superpowers/specs/2026-11-26-demo-trading-design.md` (strategy rules: `docs/strategy-mean-reversion.md`).

## Global Constraints
- Never place live orders: every API client comes from `src.config.load_config()`, which refuses `OANDA_ENV != "practice"`.
- Never print or log the token. Log lines are built from explicit fields; never dump `cfg` or a client.
- Instruments: exactly `SPX500_USD, NAS100_USD, US30_USD, US2000_USD` (`src.tom.run.INSTRUMENTS`).
- Rules frozen: use `src.meanrev.run.signals`, `MAX_HOLD = 10`, `RISK_GBP = 25.0`, `PRECISION = 2`, `CLOSE_HOUR = 15`; no copies of their logic.
- Trade tag: client extensions `tag = "meanrev"`, `comment = "entry YYYY-MM-DD"` (the NY entry date).
- Orders only with env `TRADING_ENABLED == "yes"`; anything else is a dry run.
- Caps: notional per order ≤ GBP 10,000; at most one tagged trade per index; long only; market orders, FOK.
- Run window: New York time 16:00 ≤ t < 16:45 on an NYSE trading day (`src.tom.nyse.trading_days`).
- Render cron schedule `2 20,21 * * 1-5`, region `frankfurt`, `PYTHON_VERSION=3.11.9`.
- Write a test for every rule (`CLAUDE.md`); `python -m unittest` must stay green.

## Review Focus
1. **Duplicate runs or retries on the same day** (both cron times pass the gate if clocks are odd, or Render retries): expect no second entry and no buy-after-sell on one index in one NY day → `test_no_reentry_same_day_after_close` and `test_second_run_same_day_is_noop` (Tasks 2, 4).
2. **Today's 15:00 candle not yet complete or missing** (API lag, unexpected closure): expect no action → `test_incomplete_signal_candle_means_no_action` (Task 1), `test_missing_todays_close_skips_index` (Task 4).
3. **Untagged or manually opened position on an index:** expect a warning and no action on that index → `test_untagged_trade_blocks_index` (Task 3).
4. **A missed run while a trade is open** (Render outage): next run must exit if an exit fired on any day since entry → `test_missed_exit_is_caught_up` (Task 2).
5. **An absurd size** (bad ATR or FX value): the order must be refused by the cap, not sent → `test_order_over_notional_cap_is_refused` (Task 3).

---

### Task 1: Market data from the API

**Files:**
- Create: `src/live/__init__.py`, `src/live/market.py`
- Test: `tests/test_live_market.py`

**Interfaces:**
- Consumes: `src.download.fetch_candles(client, instrument, start, granularity)` (rows of dicts, completed candles only), `src.download.COLUMNS`.
- Produces:
  - `rows_to_frame(rows: list[dict]) -> pd.DataFrame`, the same shape as `src.data.load_candles`: UTC `time` index and float bid/ask OHLC.
  - `fetch_h1(client, instrument: str, today: date, days_back: int = 430) -> pd.DataFrame`.
  - `todays_close_ready(h1: pd.DataFrame, today: date, now_utc: datetime) -> bool`: True if an H1 candle opening at 15:00 New York on `today` is present. Otherwise True only for an early close: New York time ≥ 16:00, and `today`'s last candle opens at or before 13:00. A day with a 14:00 candle but no 15:00 one is a late candle, so False.

- [ ] **Step 1: Write the failing tests** in `tests/test_live_market.py`:
  - `test_rows_to_frame_matches_load_candles_shape`: two rows → index is tz-aware UTC, columns include `bid_c`, `ask_o`, values are floats.
  - `test_incomplete_signal_candle_means_no_action`: an H1 frame whose last candle opens at 14:00 New York on `today`, at 16:02 → False; add the 15:00 candle → True.
  - `test_early_close_day_is_ready_after_1600`: last candle opens at 12:00 New York → False at 15:30, True at 16:02.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_live_market -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement the three functions. `fetch_h1` calls `fetch_candles(client, instrument, (today - days_back days).isoformat(), "H1")` and returns `rows_to_frame`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `git add src/live tests/test_live_market.py && git commit -m "Live: fetch H1 candles into backtest shape"`.

### Task 2: Pure decisions

**Files:**
- Create: `src/live/decide.py`
- Test: `tests/test_live_decide.py`

**Interfaces:**
- Consumes: `src.meanrev.run.signals(close: pd.Series, use_filter=True) -> (entry, exit, up)` arrays; `src.meanrev.run.signal_and_exec(h1)` for closes; `MAX_HOLD`.
- Produces:
  - `@dataclass OpenTrade(trade_id: str, instrument: str, units: float, entry_day: date)`.
  - `@dataclass Decision(instrument: str, action: str, reason: str, close: float, entry_signal: bool, exit_signal: bool, units: float = 0.0)`; `action` is one of `"buy"`, `"close"`, `"none"`.
  - `decide(instrument: str, closes: pd.Series, nyse_days: pd.DatetimeIndex, today: date, open_trade: Optional[OpenTrade], closed_today: bool, units_if_entering: float) -> Decision`. `closes` is indexed by NYSE days up to and including today.

- [ ] **Step 1: Write the failing tests** (build closes with `np.linspace` rising then a dip, as in `tests/test_meanrev.py`):
  - `test_enter_on_signal_when_flat`: action `"buy"`, units = `units_if_entering`.
  - `test_no_entry_without_signal`, and `test_no_entry_below_200_day_average` → `"none"`.
  - `test_exit_on_exit_signal`: open trade from 2 NYSE days ago, today's close above the 5-day average → `"close"`.
  - `test_exit_signal_on_entry_day_does_not_count`: entered today → `"none"`.
  - `test_missed_exit_is_caught_up`: an exit signal 2 days ago, none today, still open → `"close"`, reason mentions catch-up.
  - `test_time_stop_on_10th_trading_day`: entry 10 NYSE days before today, no exit signals → `"close"`; 9 days → `"none"`.
  - `test_no_reentry_same_day_after_close`: flat, entry signal, `closed_today=True` → `"none"`.
  - `test_matches_backtest_walk`: on a real-shaped synthetic 400-day series, driving `decide` day by day (feeding back its own actions) gives the same (entry, exit) pairs as `walk(entry, exit_, all True)`.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_live_decide -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `decide`. Hold = the number of NYSE days after `entry_day` up to today. Exit if hold ≥ `MAX_HOLD`, or any exit signal on the NYSE days in (entry_day, today].
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "Live: entry/exit decisions matching the backtest walk"`.

### Task 3: Broker adapter and safety rails

**Files:**
- Create: `src/live/broker.py`
- Test: `tests/test_live_broker.py` (fake client: a class with `request(endpoint)` that records endpoints and returns canned JSON)

**Interfaces:**
- Consumes: `OpenTrade` (Task 2); oandapyV20 `trades.OpenTrades`, `trades.TradesList`, `orders.OrderCreate`, `trades.TradeClose`.
- Produces:
  - `class Broker(client, account_id: str, trading_enabled: bool)`.
  - `.open_trades() -> tuple[dict[str, OpenTrade], set[str]]`: tagged trades by instrument, and the instruments that have untagged trades.
  - `.closed_today(instrument: str, today: date) -> bool`: a tagged trade on it closed on today's New York date (from `TradesList(state="CLOSED", instrument=..., count=10)`).
  - `.buy(instrument: str, units: float, entry_day: date, notional_gbp: float) -> dict`: returns `{"status": "sent"|"dry_run"|"refused", ...ids}`.
  - `.close(trade: OpenTrade) -> dict`: same statuses.
  - `MAX_NOTIONAL_GBP = 10_000.0`, `TAG = "meanrev"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_dry_run_sends_nothing`: `trading_enabled=False` → `buy` returns `dry_run`; the fake recorded no `OrderCreate`.
  - `test_buy_sends_fok_market_long_with_tag`: the recorded order body has `type "MARKET"`, `timeInForce "FOK"`, positive units as a string with 2 decimals, `clientExtensions {"tag": "meanrev", "comment": "entry 2026-12-01"}`, and `tradeClientExtensions` the same.
  - `test_order_over_notional_cap_is_refused`: `notional_gbp=10_001` → `refused`, nothing sent.
  - `test_non_positive_or_unknown_instrument_refused`: units ≤ 0, or `EUR_USD` → `refused`.
  - `test_untagged_trade_blocks_index`: open trades with one untagged SPX500 trade → SPX500 in the untagged set and not in the tagged dict.
  - `test_open_trades_parses_entry_day_from_comment`.
  - `test_closed_today_uses_new_york_date`: closeTime 2026-12-01T21:30Z (16:30 New York) → True for 2026-12-01.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_live_broker -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Refusal checks run before any request. Use `request_with_retry` for reads only; never retry an order request automatically, because a retry could double an order. Log the failure instead.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "Live: broker adapter with dry run and safety caps"`.

### Task 4: The daily run

**Files:**
- Create: `src/live/run.py`
- Test: `tests/test_live_run.py`

**Interfaces:**
- Consumes: Tasks 1–3; `src.config.load_config`; `src.tom.nyse.trading_days`; `src.trend.bars.daily_bars`, `atr`; `src.tom.run.latest_before`, `RISK_GBP`, `PRECISION`.
- Produces:
  - `in_window(now_utc: datetime, nyse_days) -> tuple[bool, str]`.
  - `units_for(h1, gbpusd_h1, today) -> float`: the backtest sizing.
  - `run_once(now_utc, client, account_id, trading_enabled, fetch=fetch_h1) -> list[dict]`: one log record per index, plus a header record.
  - `main()`: builds the client from `load_config()`, reads `TRADING_ENABLED`, prints each record as one JSON line, and exits 0.

- [ ] **Step 1: Write the failing tests** (fake `fetch` returning synthetic H1; fake broker client from Task 3):
  - `test_window_open_1600_to_1645_new_york_on_trading_days`: 2026-12-01 21:02Z (16:02 EST) → open. 20:02Z → closed ("before 16:00"). 2026-07-01 20:02Z (16:02 EDT) → open. Saturday → closed. 2026-12-25 → closed (holiday).
  - `test_units_match_backtest_sizing`: equal to `Instrument(...).units` for the same day from `src.meanrev.run` on the same data.
  - `test_missing_todays_close_skips_index`: one index has no 15:00 candle → its record has action `"none"` with reason `"no close yet"`; the others are still decided.
  - `test_second_run_same_day_is_noop`: first run buys SPX500, and the fake now reports it open with entry today → second run → `"none"`.
  - `test_log_never_contains_token`: set `OANDA_TOKEN="secret-xyz"` in env, run `main()` with fakes, capture stdout → `"secret-xyz"` not in it.
  - `test_refuses_live_env`: `OANDA_ENV="live"` → `main()` raises before any request.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_live_run -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Order inside `run_once`: window gate → fetch GBP_USD and the 4 indices → open trades → per index: skip if untagged, skip if no close yet, decide, then act through `Broker`. The notional passed to `buy` = units × today's close × (1 / GBP_USD close).
- [ ] **Step 4:** Run the same command, then the full suite with `.venv/bin/python -m unittest`. Expected: all PASS.
- [ ] **Step 5:** Dry run locally against practice (reads only): `TRADING_ENABLED=no .venv/bin/python -m src.live.run`. Expected: either "outside window" or 4 decision lines, and no orders on the account (check the account summary `pendingOrderCount`/`openTradeCount` = 0).
- [ ] **Step 6:** Commit: `git commit -m "Live: daily run with time gate and JSON log"`.

### Task 5: Live vs backtest comparison

**Files:**
- Create: `src/live/compare.py`
- Test: `tests/test_live_compare.py`

**Interfaces:**
- Consumes: live trades from `trades.TradesList(state="ALL", count=500)` filtered by tag `meanrev`. Backtest: `src.meanrev.run.Instrument`, `walk`, `signals`, with data from `src.live.market.fetch_h1`.
- Produces: `match(live: pd.DataFrame, backtest: pd.DataFrame) -> pd.DataFrame`, with columns `instrument, bt_entry, bt_exit, live_entry, live_exit, status ("matched"|"missed_live"|"extra_live"), entry_fill_diff_bp, exit_fill_diff_bp`; `report(matched) -> str`, the three demo success criteria from the spec with pass/fail; `main()` with `--since YYYY-MM-DD`.

- [ ] **Step 1: Write the failing tests:** `test_match_pairs_by_instrument_and_entry_day`, `test_missed_and_extra_trades_flagged`, `test_fill_diff_in_bp`, `test_report_states_three_criteria`.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_live_compare -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "Live: compare demo fills with the backtest"`.

### Task 6: Deployment files and docs (no deploy)

**Files:**
- Create: `render.yaml`
- Modify: `README.md` (status row 6 and layout row for `src/live/`), `docs/decisions.md` (a demo-trading entry), `CLAUDE.md` (note that on Render the credentials are environment variables)
- Test: `tests/test_render_yaml.py`

- [ ] **Step 1: Write the failing test** `test_render_yaml_is_practice_cron`: parse `render.yaml` as text (no new dependency). Assert `type: cron`, `schedule: "2 20,21 * * 1-5"`, `startCommand: python -m src.live.run`, `region: frankfurt`, `OANDA_ENV` value `practice`, `TRADING_ENABLED` value `"no"`, and `OANDA_TOKEN` / `OANDA_ACCOUNT_ID` with `sync: false` (values entered in the dashboard, never in the repo).
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Write `render.yaml`: one cron service `tradebot-demo`, `runtime: python`, `plan: starter`, `buildCommand: pip install -r requirements.txt`, and env vars as above plus `PYTHON_VERSION=3.11.9`.
- [ ] **Step 4:** Run the full suite. Expected: all PASS.
- [ ] **Step 5:** Commit: `git commit -m "Render cron blueprint (dry run by default) and docs"`.

**Stops here.** Deploying (creating the Render service and entering secrets), and later switching `TRADING_ENABLED` to `yes`, are outward-facing steps for the owner to approve one at a time, as the spec's rollout says.
