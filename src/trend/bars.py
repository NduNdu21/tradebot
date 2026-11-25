"""Daily bars and execution prices built from H1 bid/ask candles (strategy §2, §4).

A daily bar runs 17:00 -> 17:00 New York and is named after the day it closes:
an H1 candle opening at New York time t belongs to the bar dated (t + 7h).date().
"""
import pandas as pd

NY = "America/New_York"
MIN_H1_PER_BAR = 6   # fewer than this is a holiday stub, merged into the next bar
EXEC_HOUR = 8        # orders go in at the open of the 08:00 New York H1 candle


def bar_labels(h1_index):
    """Daily-bar date (naive Timestamp) for each H1 open time, before stub merging."""
    ny = h1_index.tz_convert(NY)
    return (ny + pd.Timedelta(hours=7)).tz_localize(None).normalize()


def in_trading_week(h1_index):
    """False for candles between Friday 17:00 and Sunday 17:00 New York, when FX is closed.

    OANDA's history has thin quotes there in some years (2005, 2011); they are dropped.
    """
    ny = h1_index.tz_convert(NY)
    dow, hour = ny.dayofweek, ny.hour
    closed = (dow == 5) | ((dow == 4) & (hour >= 17)) | ((dow == 6) & (hour < 17))
    return ~closed


def merged_labels(h1_index):
    """Bar labels with holiday stubs (< 6 H1 candles) merged into the next bar."""
    labels = pd.Series(bar_labels(h1_index), index=h1_index)
    counts = labels.value_counts().sort_index()
    remap = {}
    pending = []
    for day, n in counts.items():
        if n < MIN_H1_PER_BAR:
            pending.append(day)
            continue
        for stub in pending:
            remap[stub] = day
        pending = []
    # a stub at the very end has no next bar; keep it as it is
    return labels.map(lambda d: remap.get(d, d))


def daily_bars(h1):
    """Daily mid OHLC plus the 08:00 New York execution candle's bid/ask open.

    Columns: o, h, l, c (mid), exec_bid, exec_ask (NaN when the bar has no candle at or
    after 08:00 New York on the bar's own date).
    """
    h1 = h1[in_trading_week(h1.index)]
    labels = merged_labels(h1.index)
    mid = pd.DataFrame({
        "o": (h1["bid_o"] + h1["ask_o"]) / 2, "h": (h1["bid_h"] + h1["ask_h"]) / 2,
        "l": (h1["bid_l"] + h1["ask_l"]) / 2, "c": (h1["bid_c"] + h1["ask_c"]) / 2,
    })
    g = mid.groupby(labels.values)
    bars = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last()})

    # Execution candle: first H1 opening at or after 08:00 New York on the bar's own calendar date.
    ny = h1.index.tz_convert(NY)
    ny_date = ny.tz_localize(None).normalize()
    eligible = (ny_date == labels.values) & (ny.hour >= EXEC_HOUR)
    ex = h1.loc[eligible, ["bid_o", "ask_o"]]
    ex = ex.groupby(labels.values[eligible]).first()
    bars["exec_bid"] = ex["bid_o"]
    bars["exec_ask"] = ex["ask_o"]
    bars.index.name = "day"
    return bars


def atr(bars, n=20):
    """Simple mean of the last n daily true ranges (mid prices)."""
    prev_c = bars["c"].shift()
    tr = pd.concat([bars["h"] - bars["l"], (bars["h"] - prev_c).abs(), (bars["l"] - prev_c).abs()], axis=1).max(axis=1)
    tr.iloc[0] = bars["h"].iloc[0] - bars["l"].iloc[0]
    return tr.rolling(n).mean()
