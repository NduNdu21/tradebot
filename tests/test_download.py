"""Tests for src.download that don't need network access."""
import unittest

from src.download import COLUMNS, candle_to_row


class CandleToRowTest(unittest.TestCase):
    def test_flattens_mid_bid_ask(self):
        candle = {
            "time": "2024-01-02T10:00:00.000000000Z",
            "volume": 42,
            "complete": True,
            "mid": {"o": "1.1000", "h": "1.1010", "l": "1.0990", "c": "1.1005"},
            "bid": {"o": "1.0999", "h": "1.1009", "l": "1.0989", "c": "1.1004"},
            "ask": {"o": "1.1001", "h": "1.1011", "l": "1.0991", "c": "1.1006"},
        }
        row = candle_to_row(candle)
        self.assertEqual(list(row), COLUMNS)
        self.assertEqual(row["bid_c"], "1.1004")
        self.assertEqual(row["ask_l"], "1.0991")


if __name__ == "__main__":
    unittest.main()
