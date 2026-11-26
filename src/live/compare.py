"""Line up the demo's live trades with what the backtest would have done, and judge the demo criteria.

Local report, run by the owner: python -m src.live.compare --since YYYY-MM-DD. Places no orders.
`match` and `report` are pure (DataFrames in, DataFrame/str out); `main` does the I/O.

Frames:
  live      instrument, entry, exit (NY dates; exit NaT while open), entry_price, exit_price
  backtest  instrument, entry, exit (NY dates), entry_mid, exit_mid (the 16:00-open mid),
            entry_fill (ask + slip), exit_fill (bid - slip)
"""
from __future__ import annotations

import argparse
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import oandapyV20.endpoints.trades as trades
import pandas as pd

from src.download import request_with_retry
from src.live.broker import TAG, _tag_and_entry_day

NY = ZoneInfo("America/New_York")
DAYS_BACK = 430
FILL_TOLERANCE = 2.0   # criterion 2: median live cost within 2x the backtest's assumed cost
COLUMNS = ["instrument", "bt_entry", "bt_exit", "live_entry", "live_exit", "status",
           "entry_fill_diff_bp", "exit_fill_diff_bp", "live_cost_bp", "bt_cost_bp"]


def match(live: pd.DataFrame, backtest: pd.DataFrame) -> pd.DataFrame:
    """One row per (instrument, entry day) seen in either frame, with a status.

    matched        both sides, same exit day
    exit_mismatch  both sides, different exit day
    matched_open   live trade still open and the backtest has no completed trade for it (the
                   backtest only counts trades that have exited)
    missed_live    the backtest traded, the bot did not
    extra_live     the bot traded, the backtest did not (and the trade is already closed)
    """
    lv = {(r.instrument, r.entry): r for r in live.itertuples()}
    bk = {(r.instrument, r.entry): r for r in backtest.itertuples()}
    rows = []
    for key in sorted(set(lv) | set(bk), key=lambda k: (k[0], k[1])):
        l, b = lv.get(key), bk.get(key)
        row = dict.fromkeys(COLUMNS, np.nan)
        row["instrument"] = key[0]
        row["bt_entry"] = b.entry if b else pd.NaT
        row["bt_exit"] = b.exit if b else pd.NaT
        row["live_entry"] = l.entry if l else pd.NaT
        row["live_exit"] = l.exit if l else pd.NaT
        if b is None:
            row["status"] = "matched_open" if pd.isna(l.exit) else "extra_live"
        elif l is None:
            row["status"] = "missed_live"
        else:
            row["status"] = "matched" if l.exit == b.exit else "exit_mismatch"
            row["entry_fill_diff_bp"] = (l.entry_price - b.entry_fill) / b.entry_fill * 1e4
            if pd.notna(l.exit_price):
                row["exit_fill_diff_bp"] = (l.exit_price - b.exit_fill) / b.exit_fill * 1e4
                # Cost against the backtest's mid, in bp of the entry mid: pay above mid in, give up below mid out.
                live_cost = (l.entry_price - b.entry_mid) + (b.exit_mid - l.exit_price)
                row["live_cost_bp"] = live_cost / b.entry_mid * 1e4
            bt_cost = (b.entry_fill - b.entry_mid) + (b.exit_mid - b.exit_fill)
            row["bt_cost_bp"] = bt_cost / b.entry_mid * 1e4
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def report(matched: pd.DataFrame) -> str:
    """The three demo success criteria (spec, 'Demo success criteria') with pass/fail."""
    counts = matched["status"].value_counts()
    n = lambda s: int(counts.get(s, 0))
    bad = n("missed_live") + n("extra_live") + n("exit_mismatch")
    lines = ["Demo report: " + ", ".join(f"{s} {n(s)}" for s in
             ("matched", "matched_open", "exit_mismatch", "missed_live", "extra_live")), ""]
    lines.append(f"Criterion 1 (signal agreement): {'PASS' if bad == 0 else 'FAIL'} "
                 f"- {bad} missed, extra or exit-mismatched trade(s)")
    pairs = matched.dropna(subset=["live_cost_bp", "bt_cost_bp"])
    if pairs.empty:
        lines.append("Criterion 2 (fill quality): n/a - no closed matched trades yet")
    else:
        live_med, bt_med = pairs["live_cost_bp"].median(), pairs["bt_cost_bp"].median()
        ok = live_med <= FILL_TOLERANCE * bt_med
        lines.append(f"Criterion 2 (fill quality): {'PASS' if ok else 'FAIL'} - median live cost {live_med:.2f} bp "
                     f"vs backtest assumed {bt_med:.2f} bp (limit {FILL_TOLERANCE:g}x = {FILL_TOLERANCE * bt_med:.2f} bp), "
                     f"{len(pairs)} trade(s)")
    lines.append("Criterion 3 (no safety-rail breaches): MANUAL - check the Render logs for records "
                 "with status refused/failed/error")
    return "\n".join(lines)


def live_trades(client, account_id: str) -> pd.DataFrame:
    """Our tagged trades (open and closed) as a live frame, dates in New York."""
    resp = request_with_retry(client, trades.TradesList(account_id, params={"state": "ALL", "count": 500}))
    rows = []
    for t in resp.get("trades", []):
        entry = _tag_and_entry_day(t)   # None unless tag == meanrev and the comment reads "entry YYYY-MM-DD"
        if entry is None:
            continue
        closed = t.get("state") == "CLOSED" and t.get("closeTime")
        exit_day = pd.NaT
        if closed:
            utc = datetime.strptime(t["closeTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
            exit_day = pd.Timestamp(utc.astimezone(NY).date())
        rows.append({"instrument": t["instrument"], "entry": pd.Timestamp(entry), "exit": exit_day,
                     "entry_price": float(t["price"]),
                     "exit_price": float(t["averageClosePrice"]) if closed else np.nan})
    return pd.DataFrame(rows, columns=["instrument", "entry", "exit", "entry_price", "exit_price"])


def backtest_trades(client, today: date, since: date) -> pd.DataFrame:
    """What the backtest rules would have done on recent data, from `since`."""
    from src.live.market import fetch_h1
    from src.meanrev.run import DEV, Instrument, build, signals, walk
    from src.tom.nyse import trading_days
    from src.tom.run import INSTRUMENTS
    from src.trend.bars import daily_bars
    from src.trend.rates import load_rates

    days = trading_days(today - timedelta(days=DAYS_BACK), today)
    gbp = daily_bars(fetch_h1(client, "GBP_USD", today, days_back=DAYS_BACK))
    rates = load_rates()
    dev_slip = {i: m.slip for i, m in build()[0].items()}   # slippage = half the median spread over DEV
    rows = []
    for inst in INSTRUMENTS:
        m = Instrument(inst, fetch_h1(client, inst, today, days_back=DAYS_BACK), gbp, rates, days, DEV)
        m.slip = dev_slip[inst]
        entry, exit_, _ = signals(m.close)
        for e, x in walk(entry, exit_, m.can_trade):
            if m.days[e] >= pd.Timestamp(since):
                rows.append({"instrument": inst, "entry": m.days[e], "exit": m.days[x],
                             "entry_mid": m.mid[e], "exit_mid": m.mid[x],
                             "entry_fill": m.ask[e] + m.slip, "exit_fill": m.bid[x] - m.slip})
    return pd.DataFrame(rows, columns=["instrument", "entry", "exit", "entry_mid", "exit_mid",
                                       "entry_fill", "exit_fill"])


def main(argv=None) -> None:
    import oandapyV20
    from src.config import load_config

    ap = argparse.ArgumentParser(description="Compare demo trades with the backtest (places no orders)")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD, first NY entry date to compare")
    since = date.fromisoformat(ap.parse_args(argv).since)
    cfg = load_config()   # refuses anything but practice; the token is never printed
    client = oandapyV20.API(access_token=cfg["token"], environment=cfg["env"])
    today = datetime.now(NY).date()
    with redirect_stdout(sys.stderr):   # download helpers print progress
        live = live_trades(client, cfg["account_id"])
        bt = backtest_trades(client, today, since)
    live = live[live["entry"] >= pd.Timestamp(since)]
    out = match(live, bt)
    pd.set_option("display.width", 200)
    print(out.to_string(index=False))
    print()
    print(report(out))


if __name__ == "__main__":
    main()
