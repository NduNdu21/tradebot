"""Short-term mean reversion in an uptrend on US index CFDs (docs/strategy-mean-reversion.md §2–§4).

Per instrument, everything is laid out on NYSE trading days. `walk` runs the entry/exit
rules; `trade_pnl` prices any (entry day, exit day) pairs, so real trades and the
random-entry control share sizing and costs exactly.
"""
import numpy as np
import pandas as pd

from src.data import load_candles
from src.trend.bars import atr, daily_bars
from src.trend.rates import load_rates, rate_on
from src.tom.nyse import trading_days
from src.tom.run import DIVIDEND, FIN_DAYS, INSTRUMENTS, MARKUP, NY, PRECISION, RISK_GBP, latest_before, on_or_before

SMA_LONG, LOW_DAYS, SMA_SHORT, MAX_HOLD = 200, 5, 5, 10
CLOSE_HOUR = 15
DEV = (pd.Timestamp("2006-01-02"), pd.Timestamp("2024-11-13"))
HOLDOUT = (pd.Timestamp("2024-11-14"), pd.Timestamp("2026-11-13"))


def signal_and_exec(h1):
    """Per New York date: the signal candle's mid close and the next H1 candle's bid/ask open.

    Signal candle = the one opening at 15:00 New York, else the last one that day opening before 15:00.
    """
    ny = h1.index.tz_convert(NY)
    date = ny.tz_localize(None).normalize()
    pos = np.arange(len(h1))
    df = pd.DataFrame({"date": date, "hour": ny.hour, "pos": pos})
    df = df[df["hour"] <= CLOSE_HOUR]
    sig_pos = df.groupby("date")["pos"].max()
    mid_c = ((h1["bid_c"] + h1["ask_c"]) / 2).values
    nxt = sig_pos.values + 1
    ok = nxt < len(h1)
    out = pd.DataFrame(index=sig_pos.index)
    out["close"] = mid_c[sig_pos.values]
    out["exec_bid"] = np.where(ok, h1["bid_o"].values[np.minimum(nxt, len(h1) - 1)], np.nan)
    out["exec_ask"] = np.where(ok, h1["ask_o"].values[np.minimum(nxt, len(h1) - 1)], np.nan)
    return out


def signals(close, use_filter=True):
    """(entry signal, exit signal) per day from mid closes (§2)."""
    sma200 = close.rolling(SMA_LONG, min_periods=SMA_LONG - 5).mean()
    low5 = close.rolling(LOW_DAYS, min_periods=LOW_DAYS).min()
    sma5 = close.rolling(SMA_SHORT, min_periods=SMA_SHORT).mean()
    entry = (close <= low5) & close.notna()
    if use_filter:
        entry &= close > sma200
    exit_ = close > sma5
    return entry.values, exit_.values, (close > sma200).values


def walk(entry, exit_, can_trade):
    """List of (entry index, exit index) under §2's rules.

    Entry on day i needs entry[i] and an execution price that day. In a trade, the exit fires on a
    later day when exit_ is true or MAX_HOLD days have passed; if that day has no execution price,
    the exit happens on the next day that does.
    """
    trades, i, n = [], 0, len(entry)
    while i < n:
        if entry[i] and can_trade[i]:
            e, j, want_out = i, i + 1, False
            while j < n:
                want_out = want_out or exit_[j] or (j - e >= MAX_HOLD)
                if want_out and can_trade[j]:
                    break
                j += 1
            if j >= n:
                break   # still open at the end of the data: not counted
            trades.append((e, j))
            i = j + 1
        else:
            i += 1
    return trades


class Instrument:
    """Arrays on NYSE days for one instrument, and the pricing of trades."""

    def __init__(self, inst, h1, gbpusd_bars, rates, days, span=DEV):
        self.inst, self.days = inst, days
        se = signal_and_exec(h1).reindex(days)
        self.close = se["close"]
        self.bid, self.ask = se["exec_bid"].values, se["exec_ask"].values
        self.mid = (self.bid + self.ask) / 2
        d = se[(se.index >= span[0]) & (se.index <= span[1])]
        self.slip = float(((d["exec_ask"] - d["exec_bid"]) / 2).median())   # half the median dev spread
        a = latest_before(atr(daily_bars(h1), 20), days)
        usd_gbp = 1 / latest_before(gbpusd_bars["c"], days)
        self.units = (RISK_GBP / (a * usd_gbp)).round(PRECISION).values
        self.rate = DIVIDEND[inst] - rate_on(rates, "USD", days).values - MARKUP
        self.fx_exit = (1 / on_or_before(gbpusd_bars["c"], days)).values
        # Rollover days charged from the first trading day up to (not including) each day.
        cal = pd.date_range(days[0], days[-1])
        charged = np.array([FIN_DAYS.get(d.weekday(), 0) for d in cal])
        cum = pd.Series(np.concatenate([[0], np.cumsum(charged)[:-1]]), index=cal)
        self.cum_fin = cum.reindex(days).values
        self.can_trade = ~np.isnan(self.mid) & ~np.isnan(self.units) & (np.nan_to_num(self.units) > 0)

    def trade_pnl(self, e, x):
        """GBP P&L parts for entries at day indices e and exits at x (arrays, any shape)."""
        e, x = np.asarray(e), np.asarray(x)
        u, fx = self.units[e], self.fx_exit[x]
        price = u * (self.mid[x] - self.mid[e]) * fx
        cost = -u * ((self.ask[e] - self.mid[e] + self.slip) + (self.mid[x] - self.bid[x] + self.slip)) * fx
        fin = u * self.mid[e] * self.rate[e] * (self.cum_fin[x] - self.cum_fin[e]) / 365 * fx
        return price, cost, fin


def build(include_holdout=False):
    end = HOLDOUT[1] if include_holdout else DEV[1]
    days = trading_days("2005-01-03", end + pd.Timedelta(days=20))
    gbpusd = daily_bars(load_candles("GBP_USD", "H1", include_holdout))
    rates = load_rates()
    return {i: Instrument(i, load_candles(i, "H1", include_holdout), gbpusd, rates, days, DEV) for i in INSTRUMENTS}, days
