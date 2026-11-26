import logging
import unittest
from datetime import date

from src.live.broker import Broker, MAX_NOTIONAL_GBP, TAG
from src.live.decide import OpenTrade


class FakeClient:
    """Records every endpoint object; returns canned JSON by endpoint class name."""

    def __init__(self, responses=None, raise_on=None):
        self.responses = responses or {}
        self.raise_on = raise_on
        self.sent = []

    def request(self, endpoint):
        self.sent.append(endpoint)
        name = type(endpoint).__name__
        if name == self.raise_on:
            raise RuntimeError("boom")
        return self.responses.get(name, {})

    def names(self):
        return [type(e).__name__ for e in self.sent]


FILL = {"orderFillTransaction": {"id": "55", "price": "100.5", "tradeOpened": {"tradeID": "77"}}}


def trade(tid, inst, units="1", tag=None, comment=None):
    t = {"id": tid, "instrument": inst, "currentUnits": units, "openTime": "2026-12-01T15:00:00.000000000Z"}
    if tag is not None or comment is not None:
        t["clientExtensions"] = {k: v for k, v in (("tag", tag), ("comment", comment)) if v is not None}
    return t


class BrokerTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)   # keep expected refusals/failures out of test output
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_dry_run_sends_nothing(self):
        c = FakeClient()
        r = Broker(c, "acc", False).buy("SPX500_USD", 1.5, date(2026, 12, 1), 1000)
        self.assertEqual(r["status"], "dry_run")
        self.assertNotIn("OrderCreate", c.names())
        r = Broker(c, "acc", False).close(OpenTrade("7", "SPX500_USD", 1.0, date(2026, 12, 1)))
        self.assertEqual(r["status"], "dry_run")
        self.assertEqual(c.sent, [])

    def test_buy_sends_fok_market_long_with_tag(self):
        c = FakeClient({"OrderCreate": FILL})
        r = Broker(c, "acc", True).buy("SPX500_USD", 1.5678, date(2026, 12, 1), 1000)
        self.assertEqual(r, {"status": "sent", "transaction_id": "55", "price": "100.5", "trade_id": "77"})
        order = c.sent[0].data["order"]
        ext = {"tag": TAG, "comment": "entry 2026-12-01"}
        self.assertEqual(order["type"], "MARKET")
        self.assertEqual(order["timeInForce"], "FOK")
        self.assertEqual(order["units"], "1.57")
        self.assertEqual(order["clientExtensions"], ext)
        self.assertEqual(order["tradeClientExtensions"], ext)

    def test_order_over_notional_cap_is_refused(self):
        c = FakeClient()
        r = Broker(c, "acc", True).buy("SPX500_USD", 1.0, date(2026, 12, 1), MAX_NOTIONAL_GBP + 1)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(c.sent, [])

    def test_non_positive_or_unknown_instrument_refused(self):
        c = FakeClient()
        b = Broker(c, "acc", True)
        for inst, units in [("SPX500_USD", 0), ("SPX500_USD", -1), ("SPX500_USD", 0.001), ("EUR_USD", 1)]:
            r = b.buy(inst, units, date(2026, 12, 1), 100)
            self.assertEqual(r["status"], "refused", (inst, units))
            self.assertIn("reason", r)
        self.assertEqual(c.sent, [])

    def test_untagged_trade_blocks_index(self):
        c = FakeClient({"OpenTrades": {"trades": [
            trade("1", "SPX500_USD"),
            trade("2", "NAS100_USD", tag=TAG, comment="entry 2026-12-01")]}})
        tagged, untagged = Broker(c, "acc", True).open_trades()
        self.assertEqual(untagged, {"SPX500_USD"})
        self.assertEqual(list(tagged), ["NAS100_USD"])

    def test_tagged_with_bad_comment_counts_as_untagged(self):
        c = FakeClient({"OpenTrades": {"trades": [trade("1", "US30_USD", tag=TAG, comment="hello")]}})
        tagged, untagged = Broker(c, "acc", True).open_trades()
        self.assertEqual((tagged, untagged), ({}, {"US30_USD"}))

    def test_open_trades_parses_entry_day_from_comment(self):
        c = FakeClient({"OpenTrades": {"trades": [
            trade("2", "NAS100_USD", "1.57", tag=TAG, comment="entry 2026-12-01")]}})
        tagged, untagged = Broker(c, "acc", True).open_trades()
        self.assertEqual(tagged["NAS100_USD"], OpenTrade("2", "NAS100_USD", 1.57, date(2026, 12, 1)))
        self.assertEqual(untagged, set())

    def test_closed_today_uses_new_york_date(self):
        closed = trade("3", "SPX500_USD", tag=TAG, comment="entry 2026-11-27")
        closed["closeTime"] = "2026-12-01T21:30:00.000000000Z"   # 16:30 in New York
        c = FakeClient({"TradesList": {"trades": [closed]}})
        b = Broker(c, "acc", True)
        self.assertTrue(b.closed_today("SPX500_USD", date(2026, 12, 1)))
        self.assertFalse(b.closed_today("SPX500_USD", date(2026, 12, 2)))
        self.assertEqual(c.sent[0].params["state"], "CLOSED")

    def test_closed_today_ignores_untagged(self):
        closed = trade("3", "SPX500_USD")
        closed["closeTime"] = "2026-12-01T21:30:00.000000000Z"
        c = FakeClient({"TradesList": {"trades": [closed]}})
        self.assertFalse(Broker(c, "acc", True).closed_today("SPX500_USD", date(2026, 12, 1)))

    def test_close_sends_trade_close_all(self):
        c = FakeClient({"TradeClose": {"orderFillTransaction": {"id": "60", "price": "101.0"}}})
        r = Broker(c, "acc", True).close(OpenTrade("77", "SPX500_USD", 1.5, date(2026, 12, 1)))
        self.assertEqual(r, {"status": "sent", "transaction_id": "60", "price": "101.0"})
        self.assertEqual(c.sent[0].data, {"units": "ALL"})

    def test_order_exception_returns_failed_and_is_not_retried(self):
        c = FakeClient(raise_on="OrderCreate")
        r = Broker(c, "acc", True).buy("SPX500_USD", 1.0, date(2026, 12, 1), 100)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(c.names().count("OrderCreate"), 1)

    def test_close_exception_returns_failed_and_is_not_retried(self):
        c = FakeClient(raise_on="TradeClose")
        r = Broker(c, "acc", True).close(OpenTrade("7", "SPX500_USD", 1.0, date(2026, 12, 1)))
        self.assertEqual(r["status"], "failed")
        self.assertEqual(c.names().count("TradeClose"), 1)

    def test_cancelled_fok_returns_failed(self):
        c = FakeClient({"OrderCreate": {"orderCancelTransaction": {"reason": "INSUFFICIENT_LIQUIDITY"}}})
        r = Broker(c, "acc", True).buy("SPX500_USD", 1.0, date(2026, 12, 1), 100)
        self.assertEqual(r["status"], "failed")
        self.assertIn("INSUFFICIENT_LIQUIDITY", r["reason"])


if __name__ == "__main__":
    unittest.main()
