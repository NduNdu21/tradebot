# Demo trading of strategy 6: design

**Date:** 2026-11-26. **Owner's choices:** run on a cloud server; "go with your picks" for everything else, so
every item below not marked *(owner)* is Claude's call.

## Why
Strategy 6 (short-term mean reversion on US index CFDs, `docs/strategy-mean-reversion.md`) passed development and
the one-time hold-out. The last step in the project's build order is demo execution: trade it live on the OANDA
**practice** account to find out whether real signals, fills and costs match the backtest, before any thought of
real money.

## Goals
- Trade the frozen strategy-6 rules on the practice account every NYSE trading day, unattended.
- Leave a record good enough to compare live with backtest: same signals, fill prices, costs.
- Fail safe: when anything is unclear (missing data, an unexpected position, an API error), do nothing and say so.

## Non-goals
- Real money. The code refuses any environment but `practice`, as everywhere in this project.
- Changing the strategy. Rules are frozen; the live code reuses `src/meanrev/run.signals` and its constants.
- Judging profitability from the demo. Three months is about 20 trades per index, far too few to measure a Sharpe
  of about 0.5. The demo judges *execution*, not edge.
- Alerts, dashboards, a web UI. Render's logs and OANDA's own transaction history are the record.

## Anti-goals (a failure even if it "works")
- Any order on the live environment, any order that isn't a strategy-6 entry or exit, shorts, more than one
  position per index, or sizes far from the backtest's.
- Touching positions the bot didn't open (tagged trades only).
- Printing or logging the API token.

## Constraints
- Signals need the 16:00 New York close, i.e. the H1 candle opening at 15:00 New York being complete, so the job
  runs just after 16:00 New York: 20:0x UTC in US summer time, 21:0x UTC in winter.
- `CLAUDE.md`: practice only; credentials from `.env` only. On the server there is no `.env`; the same three
  variables are set as Render secret environment variables. `src/config.load_config` already reads either (it
  calls `load_dotenv`, which never overrides real environment variables). *(owner: cloud server accepted with
  this.)*
- The practice account is GBP 100,000; the backtest base is GBP 10,000. Sizing uses the backtest rule unchanged
  (GBP 25 per ATR20, i.e. 0.25% of a GBP 10,000 base), so live sizes match the backtest's.

## Approaches considered (host)
- **This Mac with a scheduled job:** free and keeps the token local, but this Mac sleeps after 1 minute idle, and
  a missed evening can skip an exit. Rejected for a months-long comparison.
- **Render cron job (chosen, owner):** runs whether or not the Mac is on; about $1–7 a month; already connected
  to the owner's GitHub (it hosts EpiskopOS). Render cron jobs have no disk, so the job must be stateless.
- **Always-on server or VPS:** more control, more upkeep, and nothing here needs a long-running process.

## Design
- **Stateless run.** Each run fetches what it needs from OANDA: about 430 days of H1 bid/ask candles per index
  (for the 200-day average and the ATR) and GBP_USD H1 (for sizing). Open positions come from OANDA's open trades,
  filtered to trades tagged `meanrev`. Each trade's entry NY date is stored in its client extensions, so the
  10-day time stop survives between runs.
- **Schedule:** Render cron `2 20,21 * * 1-5` (UTC). The job goes ahead only if New York local time is between
  16:00 and 16:45, today is an NYSE trading day (`src/tom/nyse.py`), and today's signal candle exists and is
  complete. Otherwise it logs why and exits. So exactly one of the two daily runs acts, whatever the clocks do.
- **Decisions, per index**, from the backtest's own `signals()` on closes up to today:
  - **Exit** an open tagged trade if an exit signal has fired on any day after its entry day, today included, or
    if today is the 10th NYSE trading day after entry or later. This matches `walk()`, where a pending exit stays
    pending until it can execute, so a missed run catches up.
  - **Enter** if there's no tagged trade, today's entry signal fires, and no tagged trade on that index was
    closed today. No catch-up for missed entries: a stale dip isn't the strategy's signal.
- **Orders:** market orders (fill-or-kill), long only. Entry units = the backtest sizing, rounded to 0.01. Exits
  close the whole tagged trade. No stops or take-profits are sent, as in the backtest.
- **Safety rails:** `load_config` refuses anything but practice. `TRADING_ENABLED` must equal `yes` for any order;
  otherwise the run is a dry run that logs what it would do. Hard caps: notional per order ≤ GBP 10,000 (the
  backtest's typical size is about GBP 2,000), at most one tagged trade per index, and only the four index
  instruments. Untagged trades on those instruments cause a warning and no action on that index.
- **Record:** one JSON line per run and per decision to stdout (Render keeps logs), never the token.
  OANDA's transaction history is the durable record. `python -m src.live.compare --since YYYY-MM-DD`, run
  locally, rebuilds the backtest's trades over the same dates and lines them up with the live fills: matched
  trades, missed or extra trades, fill price vs the backtest's 16:00 open, and costs.
- **Early-close days:** the signal is the same as the backtest's, from the last candle before 15:00. The live
  fill happens at about 16:02, a few hours after the backtest's fill. These are logged and reported separately.
- **Rollout:** deploy with `TRADING_ENABLED=no` for at least 3 NYSE days and check the dry-run decisions against
  a local backtest run over those days. Then switch to `yes`. Both steps need the owner's go-ahead, because they
  create a cloud service and then start placing (demo) orders.

## Demo success criteria (fixed now, judged after 3 months)
1. **Signal agreement:** every backtest trade in the period appears live, entered and exited on the same NY days,
   apart from runs the logs show as missed, which are counted.
2. **Fill quality:** the median live fill is within 2× the backtest's assumed spread + slippage of the
   backtest's 16:00-open price.
3. **No safety-rail breaches:** no rejected orders from the caps, no untagged positions touched, no live-env
   attempts.

Profitability over 3 months is reported, not judged.

## Left to the builder
- Retry and backoff on API errors (reuse `src/download.request_with_retry`).
- Exact JSON log fields, as long as each decision line carries the index, today's close, both signals, the
  action, units and order or transaction IDs.
