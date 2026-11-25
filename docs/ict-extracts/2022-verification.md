# 2022 Mentorship: Claude's check of the Gemini extract

Checked 2026-11-25 against YouTube auto-captions (yt-dlp) for Episodes 2 (tmeCWULSTHc), 4 (L-ReMHiavPM) and
6 (Bkt8B3kLATQ). Timestamps are 20-second caption buckets. Auto-captions scramble word order a little; quotes are
lightly cleaned. Chart frames were not checked yet, so what is drawn (as opposed to said) is unverified.

## Confirmed

| Rule | Evidence |
|---|---|
| Liquidity = old highs (buy stops) / old lows (sell stops); relative equal highs | Ep2 17:20–17:40; Ep4 06:20 |
| Hunt starts at 08:30 New York; sweet spot 08:30–11:00 | Ep4 03:00 "8:30 in the morning, that starts the hunt"; Ep2 42:40 |
| Sequence: run above old high → break of a short-term low → FVG → sell when price trades back into it | Ep2 29:00–30:20, 43:20–43:40; Ep4 03:00–03:40, 07:00–07:40 |
| An FVG is required; without one there is no trade | Ep6 22:00 "if there is no fair value gap in here, guess what, you don't have a trade"; Ep6 24:20; Ep4 06:40 (no FVG → "wouldn't even meet the criteria") |
| The FVG must sit in the displacement range: from the sweep extreme to the end of the leg that broke the short-term swing | Ep6 20:00–21:00, 24:00 |
| FVG = 3 candles; bearish gap is candle 1's low to candle 3's high (wicks) | Ep6 12:00–12:20; Ep2 18:00 |
| Displacement must be energetic; a wick is "not all that convincing"; judged by the candle close beyond the short-term low | Ep6 19:00–19:40, 21:00 "how do you know it's displacement, how it closes down here below that low"; Ep6 23:40 "takes out a short-term high and it closes above it with an energetic displacement" |
| Entry: limit order at the FVG's near edge (bearish: candle 3's high), optionally one tick inside | Ep6 35:40–36:00 "go one tick above … removes all the doubt"; 36:20 "you had a limit order right there" |
| Use 2m/5m; if no FVG on that timeframe, drop to 1m | Ep4 03:00, 06:00; Ep2 29:20 |
| Target: an old low / imbalance below the range's 50% (equilibrium) | Ep4 02:00–02:40, 07:40 |
| Don't trail the stop early | Ep6 40:20, 41:00–41:20 |

## Gemini was wrong or incomplete

| Item | Gemini said | The video says |
|---|---|---|
| C2 swing definition | NOT COVERED | **3-candle swing**: Ep2 43:20 "a candle higher to the left … the low of this candle, and the next candle's higher low … so you have a swing low formed" (one candle each side) |
| E2 close vs wick | Ep6 close; Ep4 trade-through | Both confirmed, and they conflict: Ep4 07:20 "as soon as this candle trades through it, you now have an opportunity"; Ep6 wants a close. **Owner must rule.** |
| H1 stop | above the sweep extreme, or candle 1 | **Above candle 2's high, or above candle 1 "which creates a swing high", whichever you can afford** (Ep6 36:00); Ep6 27:20 "above this candle or the swing high". The sweep extreme isn't named in these passages. |
| Alternative entry | (not mentioned) | Ep2 43:40: a sell-stop order inside the candle, stop at that candle's high |
| I3 partials / I4 stop moves | NOT COVERED | Ep6 41:00–41:20: a first partial is taken; the stop stays put until a significant intermediate-term low is taken, then it can be rolled. No percentages. |
| J2 "max 4 trades/day (Ep 4 notes)" | STATED | **Not in Ep 4.** The whole transcript was read; it has no trade limit. Source unknown; treat as unsupported. |

## Episodes 3, 5, 7, 8, 9, 10 (checked 2026-11-25, captions only)
Videos: Ep3 nQfHZ2DEJ8c, Ep5 N29ZJ-o31xs, Ep7 G8-z91acgG4, Ep8 7rbV8aWkcqY, Ep9 iZLXnNiZm_s, Ep10 S9ORTYmXwdE.

### Confirmed
| Rule | Evidence |
|---|---|
| Swing = 3 candles (higher low on each side for a swing low); **candle closes don't matter** | Ep5 14:40 "it's three candles, it does not matter what the close of the candles are"; Ep10 18:20 "you do not need a Williams fractal" |
| Liquidity for the morning = the **first** swing high/low before 08:30, no need to go back days; afternoon = swings formed before 13:30 | Ep5 14:40, 32:00–32:20 |
| Morning trades 08:30 to noon, preferably positioned before 11:00 | Ep5 10:40–11:00 |
| Noon–13:00 New York is a no-trade period | Ep5 10:00, 22:20 |
| Afternoon trading from 13:30 at the earliest | Ep5 32:20, 36:20 |
| "If you win in the morning, don't trade in the afternoon" | Ep5 45:00 |
| Big overnight run (about 02:00–05:00): skip the New York morning, wait until after lunch (equities only) | Ep9 12:40, 15:20 |
| 16:30 is "like the close for me" | Ep9 08:40 |
| Asian range not used for indices | Ep5 01:40 |
| Futures: stop trading the expiring contract; roll to the one with larger open interest about a week before the 3rd Friday | Ep5 03:20–05:20 |
| Risk: max 4.5% per trade, usually 3–3.5% | Ep3 49:00; Ep7 10:20; Ep3 42:20 (3.25% on that trade) |
| SMT: Dow refusing to make a lower low while NQ/ES do = accumulation (bullish) | Ep7 22:40–24:40 |
| −1 standard deviation (fib) projection used as a target | Ep8 09:00 |
| Dislikes yen pairs ("double return to a specific level") | Ep8 02:00 |
| Bias: daily dealing range, premium/discount, daily FVGs, and the draw to old highs/lows; bias = whether the day expands above or below its opening price | Ep7 03:40–09:40; Ep10 04:00–07:00, 18:00, 20:00–23:00 |
| With a bearish bias you don't look for bullish FVGs; counter-trend trades only with smaller size | Ep10 60:00; Ep7 04:00 |

### Wrong or different from Gemini
| Item | Gemini said | The video says |
|---|---|---|
| Two FVGs | "Ep 7 / Ep 9: let it trade down to the lower one" | It's **Ep3 42:00**, and the mechanism differs: "let it trade down to the lower one, sacrifice the better entry, and if it trades into it and comes back up into the [higher] box, I'll enter". Nothing on this in Ep7 or Ep9. One live example. |
| Afternoon session "13:00–16:30" | STATED | 13:30 start is stated; 16:30 is only "like the close for me". No forced exit time. |
| Ep10 quote "counter-trend … permissible, require more experience" | STATED | Not found in the Ep10 captions. Ep7 04:00 says counter-trend means leverage "dialed back". |
| Forex hunting window | (not mentioned) | Ep8 11:40: 07:00–10:00 New York for forex |
| Order-block limit "+3 pips for spread" | (not mentioned) | Ep3 15:20 |

### Still discretionary (needs a ruling to backtest)
Daily bias has no mechanical rule: it combines the daily range's premium/discount, daily FVGs and the nearest old
high/low. Displacement is judged by eye apart from "closes beyond the low" and "leaves an FVG".
