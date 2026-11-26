"""OANDA practice adapter: the only live code that reads or places trades. Carries the safety rails.

Rails: refusals happen before any request; a dry run sends nothing; orders and closes are never
retried automatically (a retry could double an order), a failure is logged and returned instead.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades

from src.download import request_with_retry
from src.live.decide import OpenTrade
from src.tom.run import INSTRUMENTS

log = logging.getLogger(__name__)

MAX_NOTIONAL_GBP = 10_000.0   # hard cap on one order's size
TAG = "meanrev"               # marks trades this bot opened
NY = ZoneInfo("America/New_York")
_COMMENT = re.compile(r"^entry (\d{4}-\d{2}-\d{2})$")


def _tag_and_entry_day(t: dict) -> Optional[date]:
    """Entry day of a trade this bot opened, or None if it is not ours or the comment is unreadable."""
    ext = t.get("clientExtensions") or {}
    if ext.get("tag") != TAG:
        return None
    m = _COMMENT.match(ext.get("comment") or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


class Broker:
    def __init__(self, client, account_id: str, trading_enabled: bool):
        self.client = client
        self.account_id = account_id
        self.trading_enabled = trading_enabled   # False = dry run: no order or close is ever sent

    def open_trades(self) -> tuple[dict[str, OpenTrade], set[str]]:
        """Our trades by instrument, and instruments blocked by trades we cannot manage.

        An untagged trade, a tagged one with an unreadable comment (no safe time stop), or a second
        trade on the same instrument all block that index, so the bot never stacks or touches them.
        """
        resp = request_with_retry(self.client, trades.OpenTrades(self.account_id))
        mine: dict[str, OpenTrade] = {}
        blocked: set[str] = set()
        for t in resp.get("trades", []):
            inst = t["instrument"]
            day = _tag_and_entry_day(t)
            if day is None or inst in mine:
                blocked.add(inst)
                if day is None:
                    continue
            if inst not in mine:
                mine[inst] = OpenTrade(t["id"], inst, float(t["currentUnits"]), day)
        return mine, blocked

    def closed_today(self, instrument: str, today: date) -> bool:
        """True if one of our trades on this instrument closed on today's New York date."""
        params = {"state": "CLOSED", "instrument": instrument, "count": 10}
        resp = request_with_retry(self.client, trades.TradesList(self.account_id, params=params))
        for t in resp.get("trades", []):
            if (t.get("clientExtensions") or {}).get("tag") != TAG or not t.get("closeTime"):
                continue
            closed = datetime.strptime(t["closeTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
            if closed.astimezone(NY).date() == today:
                return True
        return False

    def buy(self, instrument: str, units: float, entry_day: date, notional_gbp: float) -> dict:
        """Market buy, fill-or-kill, tagged so tomorrow's run can find it and apply the time stop."""
        # Refusals first: nothing below this block runs, and nothing is requested, if any rule fails.
        if instrument not in INSTRUMENTS:
            return self._refuse(f"instrument {instrument} not allowed")
        if not units > 0 or f"{units:.2f}" == "0.00":
            return self._refuse(f"units {units} not positive")
        if not notional_gbp <= MAX_NOTIONAL_GBP:
            return self._refuse(f"notional {notional_gbp} over cap {MAX_NOTIONAL_GBP}")
        if not self.trading_enabled:
            return {"status": "dry_run"}
        ext = {"tag": TAG, "comment": f"entry {entry_day.isoformat()}"}
        order = {"type": "MARKET", "instrument": instrument, "units": f"{units:.2f}",
                 "timeInForce": "FOK", "positionFill": "DEFAULT",
                 "clientExtensions": dict(ext), "tradeClientExtensions": dict(ext)}
        return self._send(orders.OrderCreate(self.account_id, data={"order": order}), "buy", instrument)

    def close(self, trade: OpenTrade) -> dict:
        """Close one of our trades in full."""
        if trade.instrument not in INSTRUMENTS or not trade.trade_id:
            return self._refuse(f"cannot close {trade.instrument} trade {trade.trade_id!r}")
        if not self.trading_enabled:
            return {"status": "dry_run"}
        ep = trades.TradeClose(self.account_id, trade.trade_id, data={"units": "ALL"})
        return self._send(ep, "close", trade.instrument)

    @staticmethod
    def _refuse(reason: str) -> dict:
        log.warning("refused: %s", reason)
        return {"status": "refused", "reason": reason}

    def _send(self, endpoint, what: str, instrument: str) -> dict:
        """One attempt only. Never retried: the request may have reached OANDA even if it errored."""
        try:
            resp = self.client.request(endpoint)
        except Exception as err:   # the message is logged, never the client or token
            log.error("%s %s failed: %s: %s", what, instrument, type(err).__name__, err)
            return {"status": "failed", "reason": f"{type(err).__name__}: {err}"}
        fill = resp.get("orderFillTransaction")
        if not fill:
            reason = (resp.get("orderCancelTransaction") or {}).get("reason", "no fill returned")
            log.error("%s %s not filled: %s", what, instrument, reason)
            return {"status": "failed", "reason": reason}
        result = {"status": "sent", "transaction_id": fill["id"], "price": fill.get("price")}
        if what == "buy":
            trade_id = (fill.get("tradeOpened") or {}).get("tradeID")
            if trade_id is None:   # the order did fill; do not crash after a real execution
                log.warning("buy %s filled (transaction %s) but the fill has no tradeOpened", instrument, fill["id"])
            result["trade_id"] = trade_id
        return result
