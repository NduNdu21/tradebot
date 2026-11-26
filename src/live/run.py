"""The daily job: at about 16:00 New York, decide and (if enabled) trade strategy 6 on the practice account.

Stateless. Each run reads the market and the account, applies the same rules as the backtest
(src.live.decide) and acts through the guarded Broker. Every step is logged as one JSON line.
A cron fires twice (20:02 and 21:02 UTC) so that one of them lands inside 16:00-16:45 New York
whether the US is on summer or winter time; the other run exits at the time gate.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import oandapyV20
import pandas as pd

from src.config import load_config
from src.live.broker import Broker
from src.live.decide import decide
from src.live.market import fetch_h1, todays_close_ready
from src.meanrev.run import signal_and_exec
from src.tom.nyse import trading_days
from src.tom.run import INSTRUMENTS, PRECISION, RISK_GBP, latest_before
from src.trend.bars import atr, daily_bars

NY = ZoneInfo("America/New_York")
WINDOW_START_MIN, WINDOW_END_MIN = 16 * 60, 16 * 60 + 45   # 16:00 <= NY time < 16:45
DAYS_BACK = 430    # calendar days of H1 history: enough for the 200-day average and the 20-day ATR


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_client(cfg: dict):
    return oandapyV20.API(access_token=cfg["token"], environment=cfg["env"])


def in_window(now_utc: datetime, nyse_days) -> tuple[bool, str]:
    """True from 16:00 to 16:45 New York on an NYSE trading day (the 15:00 candle has just closed)."""
    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be timezone-aware UTC")
    now_ny = now_utc.astimezone(NY)
    if pd.Timestamp(now_ny.date()) not in nyse_days:
        return False, f"{now_ny.date()} is not an NYSE trading day"
    minutes = now_ny.hour * 60 + now_ny.minute
    if minutes < WINDOW_START_MIN:
        return False, f"before 16:00 New York ({now_ny:%H:%M})"
    if minutes >= WINDOW_END_MIN:
        return False, f"after 16:45 New York ({now_ny:%H:%M})"
    return True, f"inside window ({now_ny:%H:%M} New York)"


def units_for(h1: pd.DataFrame, gbpusd_h1: pd.DataFrame, today: date) -> float:
    """Position size in index units: risk RISK_GBP on one 20-day ATR. Same formula as meanrev.run.Instrument.

    Uses the last completed daily bar strictly before today, never today's unfinished bar.
    """
    days = pd.DatetimeIndex([pd.Timestamp(today)])
    a = latest_before(atr(daily_bars(h1), 20), days)
    usd_gbp = 1 / latest_before(daily_bars(gbpusd_h1)["c"], days)
    return float((RISK_GBP / (a * usd_gbp)).round(PRECISION).iloc[0])


def _record(kind: str, now_utc: datetime, **fields) -> dict:
    return {"ts": now_utc.isoformat(), "kind": kind, **fields}


def _error(now_utc: datetime, err: Exception, instrument=None, fatal=False) -> dict:
    rec = _record("error", now_utc, status="error", error=f"{type(err).__name__}: {err}", fatal=fatal)
    if instrument:
        rec["instrument"] = instrument
    return rec


def _handle_index(instrument, now_utc, today, days, broker, fetch, client, gbp_h1, usd_to_gbp, mine, blocked) -> dict:
    """Decide and act for one index; returns its log record."""
    base = {"instrument": instrument, "close": None, "entry_signal": None, "exit_signal": None,
            "units": 0.0, "status": "no_order"}

    def skip(reason):
        return _record("decision", now_utc, **{**base, "action": "none", "reason": reason})

    if instrument in blocked:   # a trade we did not open (or cannot read) sits on this index: never touch it
        return skip("blocked: untagged or duplicate trade")
    h1 = fetch(client, instrument, today, days_back=DAYS_BACK)
    closes = signal_and_exec(h1)["close"].reindex(days)
    # Stale-data guard: decide() trusts that the last close is today's.
    if not todays_close_ready(h1, today, now_utc) or pd.isna(closes.iloc[-1]):
        return skip("no close yet")

    open_trade = mine.get(instrument)
    closed = broker.closed_today(instrument, today) if open_trade is None else False
    units = units_for(h1, gbp_h1, today)
    d = decide(instrument, closes, days, today, open_trade, closed, units)

    result = {}
    if d.action == "buy":
        notional = d.units * d.close * usd_to_gbp   # GBP value of the position
        result = broker.buy(instrument, d.units, today, notional)
    elif d.action == "close":
        result = broker.close(open_trade)
    rec = {**base, "close": d.close, "entry_signal": d.entry_signal, "exit_signal": d.exit_signal,
           "action": d.action, "reason": d.reason, "units": d.units if d.action == "buy" else units}
    rec["status"] = result.get("status", "no_order")
    for key, out in (("trade_id", "trade_id"), ("transaction_id", "transaction_id"),
                     ("price", "fill_price"), ("reason", "broker_reason")):
        if key in result:
            rec[out] = result[key]
    if d.action == "close":
        rec["trade_id"] = open_trade.trade_id
    return _record("decision", now_utc, **rec)


def run_once(now_utc: datetime, client, account_id: str, trading_enabled: bool, fetch=fetch_h1) -> list[dict]:
    """One log record for the header and one per index. Header-level failures end with a fatal error record."""
    today = now_utc.astimezone(NY).date()
    days = trading_days(today - timedelta(days=DAYS_BACK), today)
    ok, why = in_window(now_utc, days)
    header = _record("header", now_utc, in_window=ok, window=why, trading_enabled=bool(trading_enabled), today=today)
    if not ok:
        return [header]
    records = [header]
    try:
        gbp_h1 = fetch(client, "GBP_USD", today, days_back=DAYS_BACK)
        usd_to_gbp = 1 / float(((gbp_h1["bid_c"] + gbp_h1["ask_c"]) / 2).iloc[-1])
        broker = Broker(client, account_id, trading_enabled)
        mine, blocked = broker.open_trades()
    except Exception as err:
        return records + [_error(now_utc, err, fatal=True)]
    for instrument in INSTRUMENTS:
        try:
            records.append(_handle_index(instrument, now_utc, today, days, broker, fetch, client,
                                         gbp_h1, usd_to_gbp, mine, blocked))
        except Exception as err:   # one index failing must not stop the others
            records.append(_error(now_utc, err, instrument))
    return records


def main(now_utc: datetime | None = None) -> None:
    """Run once and print each record as a JSON line. Raises SystemExit(1) on a header-level error."""
    now_utc = now_utc or utc_now()
    try:
        cfg = load_config()   # raises unless OANDA_ENV is "practice"; no request is made before this
        client = make_client(cfg)
        enabled = os.getenv("TRADING_ENABLED", "no").strip().lower() in ("yes", "true", "1")
        with redirect_stdout(sys.stderr):   # keep stdout for the JSON log only (download helpers print)
            records = run_once(now_utc, client, cfg["account_id"], enabled, fetch=fetch_h1)
    except Exception as err:   # the message never carries the token: config errors name the variable only
        records = [_error(now_utc, err, fatal=True)]
    for rec in records:
        print(json.dumps(rec, default=str))
    if any(r.get("fatal") for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
