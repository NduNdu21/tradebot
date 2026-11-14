"""Download M5 bid/ask candles from the OANDA practice API and save them to CSV.

Usage (from the repo root):
    python -m src.download
    python -m src.download --start 2016-10-03

Writes data/<INSTRUMENT>_M5.csv with one row per completed candle:
    time (UTC), volume, bid and ask open-high-low-close.
H1 candles are built from these files (see src/data.py), so signals and fills
come from the same source. Bid and ask are kept separately so the backtester can
fill longs at the ask and shorts at the bid.
"""
import argparse
import csv
import os
import time

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
from oandapyV20.exceptions import V20Error

from src.config import load_config

# GBP_USD is not traded; it converts EUR_USD profit and loss (in USD) to the GBP account currency.
INSTRUMENTS = ["EUR_USD", "GBP_JPY", "GBP_USD"]
GRANULARITY = "M5"
# Six weeks before the development period (2016-11-14) so ATR and swings are warmed up.
DEFAULT_START = "2016-10-03"
MAX_COUNT = 5000  # OANDA's limit on candles per request
MAX_RETRIES = 5
DATA_DIR = "data"

COLUMNS = ["time", "volume"] + [
    f"{side}_{field}" for side in ("bid", "ask") for field in ("o", "h", "l", "c")
]


def candle_to_row(candle):
    """Flatten one OANDA candle (price="BA") into a CSV row dict."""
    row = {"time": candle["time"], "volume": candle["volume"]}
    for side in ("bid", "ask"):
        for field in ("o", "h", "l", "c"):
            row[f"{side}_{field}"] = candle[side][field]
    return row


def request_with_retry(client, endpoint):
    """Retry transient network/server errors with a growing pause."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.request(endpoint)
        except (V20Error, ConnectionError) as err:
            if attempt == MAX_RETRIES - 1:
                raise
            print(f"  request failed ({err.__class__.__name__}), retrying")
            time.sleep(2 ** attempt)


def fetch_candles(client, instrument, start):
    """Page through candles from `start` to now, MAX_COUNT at a time.

    Only completed candles are kept; a still-forming candle may change.
    """
    rows = []
    params = {
        "granularity": GRANULARITY,
        "price": "BA",
        "count": MAX_COUNT,
        "from": f"{start}T00:00:00Z",
    }
    while True:
        endpoint = instruments.InstrumentsCandles(instrument, params=params)
        candles = request_with_retry(client, endpoint)["candles"]
        if not candles:
            break  # nothing after the last candle (e.g. market closed for the weekend)
        rows.extend(candle_to_row(c) for c in candles if c["complete"])
        # The still-forming current candle is always the last one OANDA has,
        # so seeing it means we've reached the present. (Don't use page size:
        # with includeFirst=False a full page holds MAX_COUNT - 1 candles.)
        if not candles[-1]["complete"]:
            break
        params["from"] = candles[-1]["time"]
        params["includeFirst"] = False
        print(f"  {instrument}: {len(rows)} candles, up to {candles[-1]['time'][:10]}", flush=True)
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", default=DEFAULT_START, help="first day, YYYY-MM-DD (UTC)")
    parser.add_argument("--instruments", nargs="+", default=INSTRUMENTS)
    args = parser.parse_args()

    cfg = load_config()
    client = oandapyV20.API(access_token=cfg["token"], environment=cfg["env"])

    os.makedirs(DATA_DIR, exist_ok=True)
    for instrument in args.instruments:
        rows = fetch_candles(client, instrument, args.start)
        path = os.path.join(DATA_DIR, f"{instrument.replace('_', '')}_{GRANULARITY}.csv")
        write_csv(rows, path)
        print(f"{instrument}: {len(rows)} candles, {rows[0]['time']} -> {rows[-1]['time']} -> {path}")


if __name__ == "__main__":
    main()
