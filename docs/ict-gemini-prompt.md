# Prompt for Gemini: extract ICT rules from videos

Paste everything below the line into Gemini with the video(s) attached. Run it once per video,
or attach several at once and keep the "Source" field accurate.

---

You are helping turn a trading strategy taught in video into exact rules that a computer can backtest.
The strategy is ICT-style: **liquidity sweep → change of character (CHoCH) → fair value gap (FVG) entry**.

Watch the whole video, including what's drawn on the charts, not just what's said.

## Ground rules
1. **Report only what the video teaches. Do not fill gaps with general ICT knowledge.** If the video doesn't cover
   something, write `NOT COVERED`. A gap is a useful answer.
2. For every answer give: the **timestamp** (mm:ss), a **short verbatim quote** where something is said, and
   how it was given: `STATED` (said explicitly), `SHOWN` (only visible on a chart example), or `IMPLIED`
   (you inferred it; explain why).
3. If the teacher is vague ("a strong move", "clean highs"), quote the vague words and then describe what the
   chart examples actually show (how many candles, how big compared to nearby candles, wick or close).
4. If the video contradicts itself, or a chart example breaks a stated rule, report both versions.
5. Where possible use numbers: candle counts, pips, ratios, times of day (give the time zone).

## Questions to answer

### A. Markets and timeframes
- A1. Which pairs or instruments does the teacher use or recommend? Any to avoid?
- A2. Which timeframe is used to (a) find the bias, (b) mark liquidity, (c) find the CHoCH, (d) find the FVG and enter?
- A3. Are there allowed trading hours or sessions ("killzones")? Give exact times and time zone.
- A4. Are any days or events excluded (news, Mondays, Fridays, holidays)?

### B. Bias (if any)
- B1. Is a higher-timeframe bias required before taking a trade? How exactly is it decided?
- B2. Is "premium/discount" (price above/below the 50% of a range) used? Which range, from which swing to which swing?
- B3. Can trades be taken against the bias?

### C. Liquidity: which levels count
- C1. What counts as a liquidity level? (swing high/low, equal highs/lows, previous day/week high/low,
  session high/low such as Asian range, other)
- C2. How is a **swing high/low** defined? (e.g. a candle with N lower highs on each side: what is N?)
- C3. If equal highs/lows are used, how close must they be (pips or %) and how many touches?
- C4. How old can a level be? Does a level stop counting once it has been swept, or after some time?
- C5. If several levels are nearby, which one matters?

### D. The sweep
- D1. What exactly is a sweep: price trades beyond the level by a wick, or a candle closes beyond it?
- D2. If a candle closes beyond the level, is it still a sweep, or a breakout that cancels the setup?
- D3. Minimum or maximum distance beyond the level?
- D4. Must price come back inside the level, and within how many candles?
- D5. On which timeframe is the sweep judged?

### E. Change of character (CHoCH)
- E1. After the sweep, which swing must be broken for a CHoCH? (the last swing low before the sweep high, the
  swing that made the sweep, other). How is that swing defined (same as C2?)
- E2. Does the break need a candle **close** beyond the swing, or is a wick enough?
- E3. Must the break be a "displacement" (large candles)? If so, how is that judged? Is an FVG required in it?
- E4. How many candles after the sweep may the CHoCH take? What cancels the setup before the CHoCH happens?
- E5. Does the teacher distinguish CHoCH from BOS (break of structure)? Exact difference?
- E6. Is a market structure shift (MSS) treated as the same as a CHoCH?

### F. Fair value gap (FVG)
- F1. Definition: three candles where candle 1's high is below candle 3's low (bullish) or candle 1's low is above
  candle 3's high (bearish)? Confirm or correct. Are wicks or bodies used?
- F2. Which FVG is used: one created by the CHoCH move, the first one after it, the largest, the one nearest the
  sweep, other?
- F3. Minimum FVG size (pips, or compared to something)?
- F4. Must the FVG be inside premium/discount or overlap anything else (order block, OTE 62–79% zone)?
- F5. If price never returns to the FVG, when is the setup cancelled (time, price going too far, new structure)?
- F6. Is an FVG that has been partly filled still valid?

### G. Entry
- G1. Entry price: the FVG's near edge, its 50% ("consequent encroachment"), its far edge, or wait for confirmation?
- G2. Limit order placed in advance, or enter at market after some confirmation candle? Describe the confirmation.
- G3. Can more than one entry be taken from the same setup (scaling in)?

### H. Stop loss
- H1. Where is the stop: beyond the sweep's extreme wick, beyond the FVG, beyond the CHoCH swing, other?
- H2. Is a buffer added (pips, spread)?
- H3. Is there a maximum stop size? Are trades skipped if the stop is too big or too small?

### I. Targets and trade management
- I1. Target: opposite liquidity (which level exactly?), a fixed risk:reward (e.g. 2R, 3R), or other?
- I2. Minimum risk:reward to take the trade?
- I3. Partial profits? At what level and what percentage?
- I4. Move stop to break-even? When exactly?
- I5. Trailing stop? How?
- I6. Exit at a time of day or end of session if neither stop nor target is hit?

### J. Risk and limits
- J1. Risk per trade (% of account)?
- J2. Maximum trades per day/session/week? Stop after a loss or a win?
- J3. Can trades on different pairs be open at the same time?

### K. Claimed results
- K1. Any win rate, average R, or profit figures claimed? Over what period, and is there evidence (account
  statement, backtest) or only assertion?

## Worked examples
For **every** trade example shown in the video, give one row:

| Timestamp | Pair | Timeframe(s) | Date/time of setup if visible | Direction | Liquidity level & price | Sweep candle | CHoCH swing & price | FVG high/low | Entry | Stop | Target | Outcome | Does it follow the stated rules? |

Read prices off the chart axis when they aren't said. Write `?` where they can't be read.

## Output
1. Answers to A–K, in the order above, using the format from ground rule 2.
2. The worked-examples table.
3. A final list titled **"Unclear or missing"**: every rule a programmer would still need decided, most important first.

Source: <video title, channel, URL>
