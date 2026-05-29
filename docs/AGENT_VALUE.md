# RichFX Agent Value Reference

What each agent contributes — both as an advisory system and as a future intervention layer.

---

## How to Read This Document

Each agent is assessed across two dimensions:

- **Advisory value** — what insight does it provide right now, as a read-only system?
- **Intervention value** — what would it do if given the ability to act on MT5 directly?

A third dimension is noted where relevant: **value over time** — some agents are low-value today but compound in usefulness as data accumulates.

---

## Phase 1 — Core LLM Chain

---

### Market Regime Analyst

| | |
|:---:|:---|
| ![](sprites/characters/regime_stand_south.gif) | **Model:** qwen3-14b-8k<br>**Runs:** Every H4 bar via n8n<br>**Output:** RANGING / TRENDING\_UP / TRENDING\_DOWN / MIXED |

**Advisory value:** Tells you what kind of market you are in before any trade decision is made. All downstream agents receive this context. A MIXED classification at confidence 6/10 carries very different implications than RANGING at 9/10 — the crew uses this to calibrate everything else.

**Intervention value:** Prevents new sequences from opening in TRENDING markets. This is the single most important guard in a DCA mean-reversion system. A BUY sequence opened into a strong downtrend will keep absorbing DCA additions as price falls further — the Regime Analyst blocks this at source.

**Verdict:** High value now and always. The biggest losses in DCA systems come from trading against a sustained trend. ✅✅

---

### Risk Governor

| | |
|:---:|:---|
| ![](sprites/characters/risk_stand_south.gif) | **Model:** deepseek-r1-14b-8k<br>**Runs:** Every H4 bar via n8n<br>**Output:** APPROVED / REJECTED with score and warnings |

**Advisory value:** Hard quantitative guardrails — spread, drawdown, sequence count, regime compatibility. Shows exactly which rule caused a rejection and why. Makes the system auditable.

**Intervention value:** Already partially intervening — n8n reads the approval and only sends trade signals when approved. In a full intervention layer, REJECTED stops any `OrderSend()` call from firing. DD_HALT would also trigger emergency close of all open positions.

**Verdict:** High value now. The belt-and-braces safety layer. DeepSeek R1's chain-of-thought reasoning makes rejections transparent and trustworthy. ✅✅

---

### Strategy Evaluator

| | |
|:---:|:---|
| ![](sprites/characters/strat_stand_south.gif) | **Model:** qwen3-14b-8k<br>**Runs:** Every H4 bar via n8n<br>**Output:** PROCEED / HOLD with confidence 1-10 |

**Advisory value:** Quality gate between risk approval and execution. Risk approves many bars that have acceptable spreads and drawdown — Strategy then asks whether the signal itself is worth acting on. A 3/10 signal in a Risk-approved environment is still a bad trade.

**Intervention value:** The 100% HOLD rate currently seen in mixed EURUSD conditions is correct behaviour — the crew is correctly refusing to enter weak setups. When a genuinely strong signal appears (QQE crossing 50 decisively, MACD aligned, consistent trend across 5 bars), Strategy will PROCEED and allow execution.

**Verdict:** High value — actively preventing bad entries in weak conditions. The conservative bias is a feature, not a bug. ✅✅

---

### Execution Coordinator

| | |
|:---:|:---|
| ![](sprites/characters/exec_stand_south.gif) | **Model:** qwen25-14b-8k<br>**Runs:** Every H4 bar via n8n<br>**Output:** CSV instruction — action, symbol, lots, seq_num, rationale |

**Advisory value:** Translates the upstream decisions into a precise, machine-readable instruction. The structured CSV format means the instruction can be parsed and acted upon programmatically without ambiguity.

**Intervention value:** The CSV output format was designed for this. `open_buy,EURUSD,0.01,1,QQE crossed 50 with MACD aligned` maps directly to an MT5 `OrderSend()` call. This is the intervention endpoint — when you build the execution layer, this is the output it reads.

**Verdict:** Infrastructure value today, critical value in intervention. The most important agent for the future. ✅✅

---

## Phase 2 — Advisory Agents

---

### Session Monitor

| | |
|:---:|:---|
| ![](sprites/characters/sess_stand_south.gif) | **Implementation:** Pure Python<br>**Runs:** Every `/analyse` call<br>**Output:** SESSION\_OK / SESSION\_WARN |

**Advisory value:** Flags when the analysis is running during poor liquidity windows. A 3:00 UTC spread of 18pts on EURUSD vs a 10:00 UTC spread of 4pts tells a very different story about entry quality.

**Intervention value:** Would block new sequence entries during off-peak hours. Spreads are 3-4x wider at 03:00 UTC. Entering a DCA sequence at that time means you start immediately underwater by the spread cost, and any DCA additions compound that problem.

**Verdict:** Medium-high value. Simple but effective protection against a known bad entry condition. ✅

---

### Drawdown Monitor

| | |
|:---:|:---|
| ![](sprites/characters/dd_stand_south.gif) | **Implementation:** Pure Python<br>**Runs:** Every `/analyse` call<br>**Output:** DD\_OK / DD\_WARN / DD\_HALT |

**Advisory value:** Independent, always-visible equity drawdown percentage. Separate from the Risk Governor's sequence-level checks — this looks at the total portfolio picture.

**Intervention value:** DD_HALT at 5% stops all new entries regardless of what Risk Governor says. In a full intervention layer, DD_HALT would also close all open sequences to stop the bleeding. An independent safety net that cannot be overridden by other agents.

**Verdict:** High value as a safety net. Belt and braces alongside Risk Governor. ✅✅

---

### Correlation Agent

| | |
|:---:|:---|
| ![](sprites/characters/corr_stand_south.gif) | **Implementation:** Pure Python + correlations.json<br>**Runs:** Every `/analyse` call<br>**Output:** CORR\_OK / CORR\_WARN |

**Advisory value:** Shows when opening a new position would double up exposure to the same underlying currency move. With 2 pairs this fires rarely — with 6+ pairs it becomes essential.

**Intervention value:** Would block entries that create correlated exposure. If EURUSD BUY and GBPUSD BUY are both open, you effectively have 2x long USD exposure. One adverse USD move hits both sequences simultaneously — this prevents that.

**Value over time:** Low value today with 2 pairs. Becomes critical at 5+ pairs. The correlation matrix in `correlations.json` updates without restart — recalibrate it as you add pairs. ✅ (grows to ✅✅)

---

### News Watch

| | |
|:---:|:---|
| ![](sprites/characters/news_stand_south.gif) | **Implementation:** Pure Python — ForexFactory API<br>**Runs:** Every `/analyse` call<br>**Output:** NEWS\_OK / NEWS\_WARN / NEWS\_BLOCK |

**Advisory value:** Economic calendar awareness. Know before the crew runs whether a high-impact event is imminent. NFP, FOMC, CPI releases all cause volatility spikes that make DCA entry points unreliable.

**Intervention value:** NEWS_BLOCK prevents entries in the 2 hours around high-impact events. Entering a DCA sequence just before NFP can result in an immediate 50-80 pip move against you — the sequence then requires multiple DCA additions just to recover the initial news spike. Blocking the entry entirely is better.

**Verdict:** High value. News events are one of the most predictable causes of bad DCA entries. ✅✅

---

### Performance Analyst

| | |
|:---:|:---|
| ![](sprites/characters/perf_stand_south.gif) | **Implementation:** /performance endpoint + history.json<br>**Runs:** Every page load + 5-minute poll<br>**Output:** Win rate, net P&L, stats by symbol/account |

**Advisory value:** Shows whether the system is actually working. 80% win rate on 5 trades is statistically meaningless — but at 50+ trades it becomes a genuine performance signal. Tracks demo vs live accounts separately.

**Intervention value:** Could trigger automatic risk reduction if win rate drops below a threshold over a meaningful sample. "Win rate has dropped from 75% to 45% over the last 20 trades — reduce lot sizes until conditions improve."

**Value over time:** Low value today at 5 trades. Significant value at 50+ trades. The data is accumulating now. ✅ (grows to ✅✅)

---

### Backtest Scout

| | |
|:---:|:---|
| ![](sprites/characters/scout_stand_south.gif) | **Model:** qwen3-14b-8k<br>**Runs:** Every 5 minutes (dashboard poll)<br>**Output:** Confidence score 1-10, similar bar count |

**Advisory value:** Historical pattern matching — "53 similar setups found where QQE was ~52 with a downtrend. Signals were consistent in 7/10 cases." Currently based on signal consistency only (no outcome data yet).

**Intervention value:** Could weight lot size recommendations. High Scout confidence (8+) = use standard lots. Low confidence (3 or below) = use minimum lots or skip entry. Essentially a position sizing advisor.

**Value over time:** Grows significantly as sequences close and outcomes get tagged to historical bars. Currently providing signal consistency analysis — will provide actual win rate data from similar setups once outcome tagging is built. ✅ (grows to ✅✅)

---

### Journalist

| | |
|:---:|:---|
| ![](sprites/characters/journ_stand_south.gif) | **Model:** gemma4-e4b-8k<br>**Runs:** On sequence close detection<br>**Output:** Plain-English post-mortem narrative |

**Advisory value:** Converts raw trade data into readable post-mortems. Instead of reading "ticket 47365041, sell, 0.01 lots, profit $0.12, swap -$0.54, net -$0.42" you get a clear narrative explaining what happened, why, and what the outcome means.

**Intervention value:** Limited direct intervention value — this is primarily a learning and reporting tool. However the narratives feed Meta-Supervisor which does have intervention potential. Also enables weekly digest summaries via n8n.

**Verdict:** Low direct trading value, high insight and learning value. Helps you understand system behaviour over time. ✅

---

### Meta-Supervisor

| | |
|:---:|:---|
| ![](sprites/characters/meta_stand_south.gif) | **Model:** qwen3-14b-8k<br>**Runs:** Every 15 minutes (dashboard poll)<br>**Output:** STATUS OK/REVIEW/ALERT with findings and recommendation |

**Advisory value:** System health monitor — detects when the crew itself is behaving poorly. Systematic over-rejection, 100% HOLD streaks, regime misclassification patterns, spread threshold issues. Catches problems that individual agents can't see about themselves.

**Intervention value:** Could trigger automatic parameter adjustments — "Spread threshold appears too tight for current market conditions, recommend raising from 30pts to 40pts." Could also alert you to review specific agent configurations before losses accumulate.

**Value over time:** Requires 10+ decisions to activate, meaningful at 50+, very useful at 200+. Currently running with 10 decisions. ✅ (grows to ✅✅)

---

### Horizon

| | |
|:---:|:---|
| ![](sprites/characters/hori_stand_south.gif) | **Model:** gemma4-e4b-8k<br>**Runs:** Every H4 bar via n8n (~30 seconds)<br>**Output:** Rankings, PROMOTE/MONITOR/CAUTION per pair |

**Advisory value:** Data-driven pair expansion. Instead of guessing which pairs to add to the EA, Horizon analyses signal quality across all monitored pairs and ranks them. USDCAD scoring 9/10 for signal consistency is a much better basis for expansion than intuition.

**Intervention value:** Could automatically add highly-ranked pairs to `symbols.json` when confidence exceeds a threshold — though human review is recommended before any automatic promotion. Could also flag deteriorating signal quality on active pairs ("AUDUSD signal consistency has dropped — consider reducing position size").

**Verdict:** High strategic value. Prevents random pair selection and provides a structured, evidence-based approach to system expansion. ✅✅

---

## Proposed New Agents

---

### Timeframe Analyst (`tframe`) — *Planned*

| | |
|:---:|:---|
| *(sprite pending)* | **Implementation:** Pure Python initially, optional LLM narrative<br>**Planned trigger:** Every `/analyse` call<br>**Output:** ALIGNED / NEUTRAL / OPPOSING |

**Advisory value:** Higher timeframe context before every entry decision. If trading H4, checks D1 trend direction. If H1, checks both H4 and D1. Answers: "Is this trade with or against the dominant trend?"

**Intervention value:** This addresses **the single biggest risk in DCA mean-reversion systems** — counter-trend entries. A BUY sequence on H4 while D1 is in a strong downtrend will absorb DCA additions as price continues falling. The sequence requires progressively larger capital to recover. Blocking counter-trend entries entirely, or significantly reducing lot sizes, would have a major positive impact on drawdown.

Example: "H4 signal says BUY. D1 QQE is at 32 (below 50, downtrend). OPPOSING — this trade is counter to the daily trend. Recommend HOLD or minimum lots only."

**Verdict:** Very high value. Addresses the root cause of the worst DCA outcomes. Pure Python makes it fast and reliable. ✅✅ (top priority new agent)

---

### Volatility Guard (`volat`) — *Planned*

| | |
|:---:|:---|
| *(sprite pending)* | **Implementation:** Pure Python — ATR calculation from bars DB<br>**Planned trigger:** Every `/analyse` call<br>**Output:** NORMAL / ELEVATED / EXTREME |

**Advisory value:** ATR-based volatility monitoring. Calculates current ATR against a rolling historical average. Flags when volatility is significantly above normal — flash crashes, post-news spikes, open-of-week gaps. Shows you when market conditions are genuinely abnormal.

**Intervention value:** Blocks entries during volatility spikes. When ATR is 3x the normal range, spreads are wide and price movement is erratic — exactly the wrong conditions for a DCA entry. A sequence entered during a volatility spike often goes immediately and deeply underwater. Blocking the entry prevents this specific category of bad trade.

Example: "Current ATR: 45 pips. 30-day average ATR: 12 pips. Volatility is 3.75x normal. EXTREME — entry blocked."

**Verdict:** High value. Addresses abnormal entry conditions — the second biggest risk after counter-trend entries. Pure Python means it's fast and adds no inference overhead. ✅✅

---

### Sequence Advisor (`sqadv`) — *Planned*

| | |
|:---:|:---|
| *(sprite pending)* | **Model:** qwen3-14b-8k<br>**Planned trigger:** Every `/analyse` call<br>**Output:** HEALTHY / MONITOR / CLOSE\_EARLY recommendation |

**Advisory value:** Intelligent monitoring of open sequences. Currently the EA manages DCA additions mechanically — this agent adds reasoning. Looks at sequence age, number of trades, current P&L, signal direction vs sequence direction, and Scout's historical pattern data.

Example: "EURUSD BUY sequence: 4 trades, open 14 days, currently -$18. QQE has now crossed below 50 — signal has flipped bearish. Historical similar sequences at this stage (4 trades, 14+ days, signal reversal) closed at a loss in 8 of 10 cases. Recommend early close at -$18 rather than risk absorbing a 5th DCA addition."

**Intervention value:** The highest-value intervention candidate after Timeframe Analyst. Triggering early sequence closes when conditions deteriorate significantly — closing at -$18 is always better than waiting for the EA's mechanical close at -$45. Requires careful calibration to avoid closing sequences that would have recovered, but the potential to reduce maximum drawdown per sequence is significant.

**Caution:** Needs substantial sequence history before the LLM's recommendations can be trusted. Build after 20+ completed sequences.

**Verdict:** High long-term value. Most complex to implement correctly, most impactful when working well. ✅✅ (build after Timeframe Analyst and Volatility Guard)

---

## Summary Table

| Agent | Advisory Value | Intervention Value | Value Over Time |
|-------|---------------|-------------------|-----------------|
| Regime Analyst | ✅✅ High | ✅✅ Blocks trending entries | Stable |
| Risk Governor | ✅✅ High | ✅✅ Hard stops, DD halt | Stable |
| Strategy Evaluator | ✅✅ High | ✅✅ Quality gate | Stable |
| Execution Coordinator | ✅ Infrastructure | ✅✅ MT5 execution endpoint | Grows with intervention |
| Session Monitor | ✅ Medium | ✅ Blocks off-peak entries | Stable |
| Drawdown Monitor | ✅✅ High | ✅✅ Portfolio halt | Stable |
| Correlation Agent | ✅ Low now | ✅✅ Blocks correlated exposure | Grows with pairs |
| News Watch | ✅✅ High | ✅✅ Blocks pre-news entries | Stable |
| Performance Analyst | ✅ Low now | ✅ Risk reduction trigger | Grows with trades |
| Backtest Scout | ✅ Medium | ✅ Position sizing weight | Grows with outcomes |
| Journalist | ✅ Insight | ✗ Reporting only | Grows with sequences |
| Meta-Supervisor | ✅ Medium | ✅ Parameter adjustment | Grows with decisions |
| Horizon | ✅✅ Strategic | ✅ Pair promotion/demotion | Grows with monitoring data |
| *Timeframe Analyst* | ✅✅ High | ✅✅✅ Highest impact | Stable from day one |
| *Volatility Guard* | ✅✅ High | ✅✅ Blocks abnormal entries | Stable from day one |
| *Sequence Advisor* | ✅✅ High | ✅✅ Early close trigger | Grows with sequence history |
