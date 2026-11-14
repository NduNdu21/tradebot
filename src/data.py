"""Load downloaded M5 candles, build H1 candles from them, and check data quality.

The hold-out period (see docs/strategy.md §10) is cut off here, at the source,
so no development code can see it by accident.

Quick check of the downloaded files (from the repo root):
    python -m src.data
"""
import os

import pandas as pd

DATA_DIR = "data"
PAIRS = ["EURUSD", "GBPJPY"]

DEV_START = pd.Timestamp("2016-11-14", tz="UTC")
HOLDOUT_START = pd.Timestamp("2024-11-14", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-11-14", tz="UTC")  # exclusive

PRICE_COLUMNS = [f"{side}_{field}" for side in ("bid", "ask") for field in ("o", "h", "l", "c")]

# How each column combines when 12 M5 candles become one H1 candle.
H1_AGG = {"volume": "sum"}
for _side in ("bid", "ask"):
    H1_AGG.update({f"{_side}_o": "first", f"{_side}_h": "max", f"{_side}_l": "min", f"{_side}_c": "last"})


def load_m5(pair, include_holdout=False, data_dir=DATA_DIR):
    """Read data/<PAIR>_M5.csv into a DataFrame indexed by UTC candle open time.

    Candles from HOLDOUT_START onward are dropped unless include_holdout=True.
    Only the single, final hold-out run should ever pass True.
    """
    path = os.path.join(data_dir, f"{pair}_M5.csv")
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    if include_holdout:
        return df[df.index < HOLDOUT_END]
    return df[df.index < HOLDOUT_START]


def add_mid(df):
    """Add mid OHLC columns: the average of bid and ask, field by field."""
    df = df.copy()
    for field in ("o", "h", "l", "c"):
        df[f"mid_{field}"] = (df[f"bid_{field}"] + df[f"ask_{field}"]) / 2
    return df


def to_h1(m5):
    """Combine M5 candles into H1 candles on the UTC hour grid.

    Each H1 candle is labelled with its open time and covers [hh:00, hh+1:00).
    Hours with no M5 candles (weekends, holidays) are dropped, not filled.
    """
    h1 = m5.resample("1h", label="left", closed="left").agg(H1_AGG)
    h1 = h1.dropna(subset=["bid_o"])
    h1["volume"] = h1["volume"].astype(int)
    return add_mid(h1)


def check_candles(df):
    """Return a list of data problems (empty list = clean)."""
    problems = []
    if df.index.has_duplicates:
        problems.append(f"{df.index.duplicated().sum()} duplicate timestamps")
    if not df.index.is_monotonic_increasing:
        problems.append("timestamps not sorted")
    if df[PRICE_COLUMNS].isna().any().any():
        problems.append(f"{df[PRICE_COLUMNS].isna().any(axis=1).sum()} rows with missing prices")
    for side in ("bid", "ask"):
        o, h, l, c = (df[f"{side}_{f}"] for f in ("o", "h", "l", "c"))
        bad = (h < l) | (h < o) | (h < c) | (l > o) | (l > c)
        if bad.any():
            problems.append(f"{bad.sum()} {side} candles with high/low outside open/close")
    crossed = (df["ask_l"] < df["bid_l"]) | (df["ask_h"] < df["bid_h"]) | (df["ask_c"] < df["bid_c"])
    if crossed.any():
        problems.append(f"{crossed.sum()} candles where ask is below bid")
    return problems


def find_gaps(df, min_gap="30min"):
    """List gaps between candles longer than `min_gap`, ignoring weekends.

    FX closes Friday 17:00 New York and reopens Sunday 17:00 New York, so a gap
    that contains a Saturday (New York time) is the normal weekend close.
    Returns a DataFrame with gap start (last candle before), end, and length.
    """
    times = df.index.to_series()
    length = times.diff()
    gaps = pd.DataFrame({"start": times.shift(), "end": times, "length": length})
    gaps = gaps[gaps["length"] > pd.Timedelta(min_gap)]

    def spans_saturday(row):
        days = pd.date_range(row.start.tz_convert("America/New_York").normalize(),
                             row.end.tz_convert("America/New_York").normalize())
        return any(d.dayofweek == 5 for d in days)

    if gaps.empty:
        return gaps
    return gaps[~gaps.apply(spans_saturday, axis=1)].reset_index(drop=True)


def main():
    """Print a quality summary of the development data for each pair."""
    for pair in PAIRS:
        m5 = load_m5(pair)
        h1 = to_h1(m5)
        print(f"{pair}: {len(m5)} M5 / {len(h1)} H1 candles, {m5.index[0]} -> {m5.index[-1]}")
        for name, df in (("M5", m5), ("H1", h1)):
            problems = check_candles(df)
            print(f"  {name} checks: {'clean' if not problems else '; '.join(problems)}")
        gaps = find_gaps(m5, min_gap="1h")
        print(f"  non-weekend gaps over 1h: {len(gaps)}")
        for g in gaps.sort_values("length", ascending=False).head(5).itertuples():
            print(f"    {g.start} -> {g.end} ({g.length})")


if __name__ == "__main__":
    main()
