"""Download H1 candles from the OANDA practice API and save them to CSV.

Usage (from the repo root):
    python -m src.download              # 5 years of EURUSD and GBPJPY
    python -m src.download --years 1

Writes data/<INSTRUMENT>_H1.csv with one row per completed candle:
    time (UTC), volume, mid/bid/ask open-high-low-close.
Bid and ask prices are kept so the backtester can fill longs at the ask,
shorts at the bid, and check stops against the correct side of the spread.
"""
import argparse
import csv
import os
from datetime import datetime, timedelta, timezone

import oandapyV20
import oandapyV20.endpoints.instruments as instruments

from src.config import load_config

INSTRUMENTS = ["EUR_USD", "GBP_JPY"]
GRANULARITY = "H1"
MAX_COUNT = 5000  # OANDA's limit on candles per request
DATA_DIR = "data"

COLUMNS = ["time", "volume"] + [
    f"{side}_{field}" for side in ("mid", "bid", "ask") for field in ("o", "h", "l", "c")
]


def candle_to_row(candle):
    """Flatten one OANDA candle (price="MBA") into a CSV row dict."""
    row = {"time": candle["time"], "volume": candle["volume"]}
    for side in ("mid", "bid", "ask"):
        for field in ("o", "h", "l", "c"):
            row[f"{side}_{field}"] = candle[side][field]
    return row


def fetch_candles(client, instrument, start):
    """Page through candles from `start` to now, MAX_COUNT at a time.

    Only completed candles are returned; the still-forming current hour is
    skipped so the CSV never contains a bar whose values may still change.
    """
    rows = []
    params = {
        "granularity": GRANULARITY,
        "price": "MBA",
        "count": MAX_COUNT,
        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    while True:
        response = client.request(instruments.InstrumentsCandles(instrument, params=params))
        candles = response["candles"]
        if not candles:
            break
        rows.extend(candle_to_row(c) for c in candles if c["complete"])
        # The still-forming current hour is always the last candle OANDA has,
        # so seeing it means we've reached the present. (Don't use page size:
        # with includeFirst=False a full page holds MAX_COUNT - 1 candles.)
        if not candles[-1]["complete"]:
            break
        # Next page starts after the last candle we received.
        params["from"] = candles[-1]["time"]
        params["includeFirst"] = False
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", type=int, default=5)
    args = parser.parse_args()

    cfg = load_config()
    client = oandapyV20.API(access_token=cfg["token"], environment=cfg["env"])
    start = datetime.now(timezone.utc) - timedelta(days=365 * args.years)

    os.makedirs(DATA_DIR, exist_ok=True)
    for instrument in INSTRUMENTS:
        rows = fetch_candles(client, instrument, start)
        path = os.path.join(DATA_DIR, f"{instrument.replace('_', '')}_{GRANULARITY}.csv")
        write_csv(rows, path)
        print(f"{instrument}: {len(rows)} candles, {rows[0]['time']} -> {rows[-1]['time']} -> {path}")


if __name__ == "__main__":
    main()
