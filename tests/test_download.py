"""Tests for src.download that don't need network access."""
import unittest
from unittest import mock

import requests

from src.download import COLUMNS, candle_to_row, output_path, request_with_retry


class CandleToRowTest(unittest.TestCase):
    def test_flattens_bid_ask(self):
        candle = {
            "time": "2024-01-02T10:00:00.000000000Z",
            "volume": 42,
            "complete": True,
            "bid": {"o": "1.0999", "h": "1.1009", "l": "1.0989", "c": "1.1004"},
            "ask": {"o": "1.1001", "h": "1.1011", "l": "1.0991", "c": "1.1006"},
        }
        row = candle_to_row(candle)
        self.assertEqual(list(row), COLUMNS)
        self.assertEqual(row["bid_c"], "1.1004")
        self.assertEqual(row["ask_l"], "1.0991")


class OutputPathTest(unittest.TestCase):
    def test_download_granularity_in_filename(self):
        self.assertEqual(output_path("NAS100_USD", "M1"), "data/NAS100USD_M1.csv")


class RequestWithRetryTest(unittest.TestCase):
    def test_retries_after_requests_connection_error_then_succeeds(self):
        client = mock.Mock()
        client.request.side_effect = [requests.exceptions.ConnectionError("reset"), {"candles": []}]
        with mock.patch("src.download.time.sleep") as sleep:
            self.assertEqual(request_with_retry(client, object()), {"candles": []})
        self.assertEqual(client.request.call_count, 2)
        sleep.assert_called_once()

    def test_retries_after_timeout(self):
        client = mock.Mock()
        client.request.side_effect = [requests.exceptions.Timeout("slow"), {"ok": 1}]
        with mock.patch("src.download.time.sleep"):
            self.assertEqual(request_with_retry(client, object()), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
