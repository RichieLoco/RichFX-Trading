# RichFX Agent Roster

Detailed reference for all 13 agents on the RichFX Trading Floor — including their role, implementation, and value as both an advisory system and a future intervention layer.

---

## Overview

Agents are divided into two phases:

- **Phase 1** — Core LLM chain. Run on every H4 bar via n8n. Sequential pipeline where each agent receives context from the previous.
- **Phase 2** — Advisory agents. Some run alongside the LLM chain (pure Python), others require accumulated data before activation.

Agent states on the trading floor:

| State | Sprite | Meaning |
|-------|--------|---------|
| `dormant` | Greyscale, slow walk | Not yet implemented or no data |
| `idle` | Slightly muted, normal walk | Active but no notable condition |
| `approved` | Full colour, cheering | Positive result |
| `rejected` | Full colour, crying | Hard rejection triggered |
| `action` | Full colour, pointing | Action recommended |
| `anxious` | Full colour, anxious | Warning condition |
| `thinking` | Full colour, normal walk | Processing |

---

## Phase 1 — Core LLM Chain

---

### 1. Market Regime Analyst (`regime`)

| | |
|:---:|:---|
| ![](sprites/characters/regime_stand_south.gif) | **Model:** `qwen3-14b-8k`<br>**Position:** First in chain<br>**Runs:** Every H4 bar via n8n<br>**Output:** RANGING / TRENDING\_UP / TRENDING\_DOWN / MIXED |

**Role:** Classifies the current market regime based on QQE momentum, QMP trend filter, and MACD. Sets the context for all downstream agents.

**Output format:**
```
Regime: RANGING|TRENDING_UP|TRENDING_DOWN|MIXED
Confidence: 1-10
New Sequences Allowed: Yes|No
Recommended Max Trades: N
Reasoning: [2 sentences]
```

**Regime types:**
- `RANGING` — Price oscillating, mean-reversion favoured
- `TRENDING_UP` — Clear upward momentum, buy bias
- `TRENDING_DOWN` — Clear downward momentum, sell bias
- `MIXED` — Conflicting signals, caution warranted

**Sprite state mapping:** approved → cheer, rejected → cry

**Advisory value:** Tells you what kind of market you are in before any trade decision is made. A MIXED classification at 6/10 carries very different implications than RANGING at 9/10.

**Intervention value:** Prevents new sequences opening in TRENDING markets. This is the single most important guard in a DCA mean-reversion system — the biggest losses come from trading against a sustained trend. ✅✅

---

### 2. Risk Governor (`risk`)

| | |
|:---:|:---|
| ![](sprites/characters/risk_stand_south.gif) | **Model:** `deepseek-r1-14b-8k`<br>**Position:** Second in chain<br>**Runs:** Every H4 bar via n8n<br>**Output:** APPROVED / REJECTED with score and warnings |

**Role:** Enforces hard risk rules. Receives regime assessment as context. Approves or rejects based on quantitative limits. Cannot be overridden by other agents.

**Hard rules:**
- Spread ≤ 30pts (hard reject above)
- Drawdown < 5% of balance (halt above)
- Sequences < 6 trades
- Regime must permit new sequences

**Output format:**
```
Approved: Yes|No
Risk Score: 1-10
Warnings: [list]
Max Lots: N
Reasoning: [explanation]
```

**Sprite state mapping:** approved → cheer, rejected → cry

**Advisory value:** Hard quantitative guardrails — shows exactly which rule caused a rejection and why. Makes the system auditable.

**Intervention value:** Already partially intervening — n8n reads the approval and only sends trade signals when approved. DD_HALT would trigger emergency close of all open positions. DeepSeek R1's chain-of-thought makes rejections transparent and trustworthy. ✅✅

---

### 3. Strategy Evaluator (`strat`)

| | |
|:---:|:---|
| ![](sprites/characters/strat_stand_south.gif) | **Model:** `qwen3-14b-8k`<br>**Position:** Third in chain<br>**Runs:** Every H4 bar via n8n<br>**Output:** PROCEED / HOLD with confidence 1-10 |

**Role:** Evaluates signal quality after Risk approves. If Risk rejects, immediately outputs HOLD without scoring. Prevents weak signals from reaching execution even when risk limits are technically within bounds.

**Scoring criteria:**
- QQE momentum strength and distance from 50
- MACD alignment with proposed direction
- Trend consistency across last 5 bars
- Spread acceptability for entry
- New sequence vs DCA addition context

**Output format (strict — no markdown):**
```
PROCEED or HOLD
confidence,N
[one reason, max 20 words]
```

**Sprite state mapping:** PROCEED → cheer, HOLD → anxious

**Advisory value:** Quality gate between risk approval and execution. A 3/10 signal in a Risk-approved environment is still a bad trade.

**Intervention value:** The 100% HOLD rate in mixed conditions is correct behaviour — the crew correctly refuses to enter weak setups. When a genuinely strong signal appears the crew will PROCEED. ✅✅

---

### 4. Execution Coordinator (`exec`)

| | |
|:---:|:---|
| ![](sprites/characters/exec_stand_south.gif) | **Model:** `qwen25-14b-8k`<br>**Position:** Fourth in chain<br>**Runs:** Every H4 bar via n8n<br>**Output:** CSV instruction — action, symbol, lots, seq\_num, rationale |

**Role:** Produces the final trading instruction. Receives all upstream decisions as context. If Strategy returned HOLD, outputs no_action. Otherwise produces a precise CSV instruction.

**Output format (strict CSV — no markdown):**
```
action,symbol,lots,seq_num,rationale
```

**Actions:** `open_buy`, `open_sell`, `add_to_buy`, `add_to_sell`, `close_buy`, `close_sell`, `no_action`

**Sprite state mapping:** no_action → idle, any trade action → point

**Advisory value:** Translates upstream decisions into a precise machine-readable instruction.

**Intervention value:** The CSV format maps directly to MT5 `OrderSend()`. This is the intervention endpoint — when the execution layer is built, this is the output it reads. ✅✅

---

## Phase 2 — Advisory Agents

---

### 5. Session Monitor (`sess`)

| | |
|:---:|:---|
| ![](sprites/characters/sess_stand_south.gif) | **Implementation:** Pure Python<br>**Trigger:** Every `/analyse` call<br>**Output:** SESSION\_OK / SESSION\_WARN |

**Role:** Checks current UTC hour against forex session windows. Warns during off-peak hours when liquidity is poor and spreads are wide.

**Session windows (UTC):**
| Hours | Session | Quality |
|-------|---------|---------|
| 08:00–13:00 | London | GOOD |
| 13:00–17:00 | London/NY Overlap | HIGH |
| 17:00–22:00 | New York | GOOD |
| 22:00–05:00 | Off-peak | LOW |
| 05:00–08:00 | Pre-London | LOW |

**Sprite state mapping:** HIGH/GOOD → idle, LOW → anxious

**Advisory value:** Flags when the analysis is running during poor liquidity windows. A 3:00 UTC spread of 18pts vs a 10:00 UTC spread of 4pts tells a very different entry quality story.

**Intervention value:** Would block new sequence entries during off-peak hours. Spreads are 3-4x wider at 03:00 UTC — entering a DCA sequence then means starting immediately underwater by the spread cost. ✅

---

### 6. Drawdown Monitor (`dd`)

| | |
|:---:|:---|
| ![](sprites/characters/dd_stand_south.gif) | **Implementation:** Pure Python<br>**Trigger:** Every `/analyse` call<br>**Output:** DD\_OK / DD\_WARN / DD\_HALT |

**Role:** Calculates current equity drawdown from balance. Provides an independent check from the Risk Governor's spread/sequence rules.

**Thresholds:**
- < 2% → `DD_OK` → idle
- 2–5% → `DD_WARN` → anxious
- ≥ 5% → `DD_HALT` → cry

**Calculation:**
```
drawdown_pct = max(0, (balance - equity) / balance) * 100
```

**Advisory value:** Independent, always-visible equity drawdown percentage. Separate from the Risk Governor's sequence-level checks — looks at the total portfolio picture.

**Intervention value:** DD_HALT at 5% stops all new entries regardless of what Risk Governor says. In a full intervention layer, would also close all open sequences. An independent safety net that cannot be overridden. ✅✅

---

### 7. Correlation Agent (`corr`)

| | |
|:---:|:---|
| ![](sprites/characters/corr_stand_south.gif) | **Implementation:** Pure Python + `config/correlations.json`<br>**Trigger:** Every `/analyse` call<br>**Output:** CORR\_OK / CORR\_WARN |

**Role:** Checks whether the proposed trade direction would create correlated USD exposure with positions already open on other pairs. Prevents doubling up on the same underlying move.

**Logic:**
- For USD-quote pairs (EURUSD, GBPUSD): same direction = correlated
- For USD-base pairs (USDCAD): opposite direction = correlated

**Correlation matrix:** Loaded from `config/correlations.json` — no restart required when file is updated.

**Sprite state mapping:** No conflicts → approved (cheer), Correlated exposure → anxious

**Advisory value:** Shows when opening a new position would double up exposure to the same underlying currency move.

**Intervention value:** Would block entries that create correlated exposure. Low value today with 2 pairs — becomes critical at 5+ pairs. ✅ (grows to ✅✅ with more pairs)

---

### 8. News Watch (`news`)

| | |
|:---:|:---|
| ![](sprites/characters/news_stand_south.gif) | **Implementation:** Pure Python — ForexFactory API<br>**Trigger:** Every `/analyse` call<br>**Output:** NEWS\_OK / NEWS\_WARN / NEWS\_BLOCK |

**Role:** Fetches the current week's economic calendar and checks for high-impact events affecting the currencies in the current pair within a configurable window.

**Calendar source:** `https://nfs.faireconomy.media/ff_calendar_thisweek.json`

**Status levels:**
- No events within 24h → `NEWS_OK` → approved (cheer)
- High-impact event within 24h → `NEWS_WARN` → point
- High-impact event within 2h → `NEWS_BLOCK` → anxious

**Fail-safe:** If the calendar is unreachable, returns `NEWS_OK` and logs the error. Does not block trading due to connectivity issues.

**Advisory value:** Economic calendar awareness before every entry. NFP, FOMC, CPI releases all cause volatility spikes that make DCA entry points unreliable.

**Intervention value:** NEWS_BLOCK prevents entries in the 2 hours around high-impact events. Entering just before NFP can result in an immediate 50-80 pip move against you. ✅✅

---

### 9. Performance Analyst (`perf`)

| | |
|:---:|:---|
| ![](sprites/characters/perf_stand_south.gif) | **Implementation:** `/performance` endpoint + closed trade history<br>**Trigger:** Every page load + 5-minute poll<br>**Output:** Win rate, net P&L, stats by symbol/account |

**Role:** Reads closed trade history from the VM and calculates rolling performance statistics. Groups by account type (demo/live) and by symbol for clear attribution.

**Metrics:** Total trades, wins, losses, win rate (%), total net P&L, average win/loss

**Sprite state mapping:** Win rate ≥ 60% → cheer, 40-60% → idle, < 40% → anxious, no trades → dormant

**Advisory value:** Shows whether the system is actually working. Meaningful at 50+ trades — the data is accumulating now.

**Intervention value:** Could trigger automatic risk reduction if win rate drops below threshold over a meaningful sample. ✅ (grows to ✅✅ at 50+ trades)

---

### 10. Journalist (`journ`)

| | |
|:---:|:---|
| ![](sprites/characters/journ_stand_south.gif) | **Status:** ✅ Active — triggers on sequence close<br>**Model:** `gemma4-e4b-8k`<br>**Trigger:** Sequence close detection<br>**Output:** Plain-English post-mortem narrative |

**Role:** Writes plain-English post-mortems when a sequence closes. Records entry conditions, duration, and final result. Narrative stored in `sequences` table for Meta-Supervisor review and displayed in agent overlay.

**Trigger condition:** Sequence close detection — when a magic number that had open positions now has none. Fires 15 seconds after close to allow mt5_bridge to write the deal.

**Sample output:**
```
AUDUSD BUY sequence closed — 2 trades, avg entry 0.71334,
closed at 0.71685. Net P&L: +$13.99. Duration: 7 days.
Result: WIN.
```

**Note:** Gemma 4 thinking tokens are stripped from output automatically.

**Advisory value:** Converts raw trade data into readable post-mortems. Helps you understand system behaviour over time.

**Intervention value:** Limited direct intervention — primarily a learning and reporting tool. Feeds Meta-Supervisor which does have intervention potential. ✅

---

### 11. Backtest Scout (`scout`)

| | |
|:---:|:---|
| ![](sprites/characters/scout_stand_south.gif) | **Status:** ✅ Active — 500 bars seeded<br>**Model:** `qwen3-14b-8k`<br>**Trigger:** Every 5 minutes (dashboard poll) + on-demand POST<br>**Output:** Confidence score 1-10 (live) / optimisation plan or results analysis (backtest) |

Scout operates in two modes:

---

#### Mode 1 — Live Signal Pattern Matching (existing)

Compares current signal conditions against historical bar patterns to produce a confidence score. Finds similar QQE range (±5) and trend direction bars from the last 500 bars and assesses signal consistency.

**Output format:**
```
confidence,N
[one sentence explanation]
```

**Sprite state mapping:** confidence ≥ 7 → cheer, 4-6 → idle, 1-3 → anxious

**Advisory value:** Historical pattern matching — "53 similar setups found, signals consistent in 7/10 cases." Currently based on signal consistency only (no outcome data yet). Grows significantly as sequences close and outcomes get tagged to bars.

---

#### Mode 2a — Backtest Parameter Recommendation (`POST /scout/recommend`)

Supply a `.set` file and receive a structured optimisation plan. Scout identifies:
- **Tier 1 parameters** — highest impact, sweep first (BB period/deviation, breakeven buffer, trailing settings, max trades)
- **Tier 2 parameters** — secondary pass after Tier 1 (QQE settings, entry distance)
- **Lock fixed** — parameters to keep constant and why (enabling flags, visualisation settings)
- **Estimated combination count** — so you know what you're in for before starting
- **Recommended mode** — Fast genetic for first pass, Slow complete for final verification only

Scout is pair-aware — EURJPY parameters get scaled for JPY pip values, commodity pairs get appropriate volatility context.

**EA set files** should be stored in `config/backtest_sets/` (not committed to repo).

```bash
curl -s -X POST "http://localhost:8000/scout/recommend" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "symbol=EURJPY" \
  --data-urlencode "timeframe=H4" \
  --data-urlencode "set_content=$(cat config/backtest_sets/EURJPY_H4.set)"
```

---

#### Mode 2b — Backtest Results Analysis (`POST /scout/analyse`)

Supply MT5 optimisation results (tab-separated export from Strategy Tester) and receive:
- **Confirmed parameters** — constant across all top results = true signal
- **Noise parameters** — vary without affecting outcome = can be set to simple defaults
- **Anomaly flags** — genetic convergence (all results identical), overfitting risk, too few trades
- **Recommended forward-test set** — minimal parameter set using only confirmed signal params
- **Next sweep suggestion** — zoom-in range for a second pass, or "Ready for forward testing"

**Results files** should be stored in `config/backtest_results/` (not committed to repo).

```bash
curl -s -X POST "http://localhost:8000/scout/analyse" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "symbol=USDCAD" \
  --data-urlencode "timeframe=H4" \
  --data-urlencode "csv_content=$(cat config/backtest_results/USDCAD_H4_opt.csv)"
```

**Response status values:** `ANALYSED` | `REVIEW` (anomalies detected) | `READY` (ready for forward testing)

**Advisory value:** Turns manual backtest interpretation into a structured workflow. Identifies what the optimiser actually found vs noise, and produces a clean forward-test set. ✅✅

---

### 12. Meta-Supervisor (`meta`)

| | |
|:---:|:---|
| ![](sprites/characters/meta_stand_south.gif) | **Status:** ✅ Active — accumulating decisions<br>**Model:** `qwen3-14b-8k`<br>**Trigger:** Every 15 minutes (dashboard poll)<br>**Output:** STATUS OK/REVIEW/ALERT with findings |

**Role:** Reviews crew decisions across many bars and flags systematic patterns. Examples:
- "Risk Governor has rejected 23 consecutive bars — is the spread threshold too tight?"
- "Strategy Evaluator returns HOLD 95% of the time — is signal quality genuinely poor or is the scoring too conservative?"
- "EURUSD sequences have a 40% win rate vs AUDUSD at 100% — consider pair weighting"

**Data source:** `decisions` table in `richfx.db` — one row per `/analyse` call.

**Output format:**
```
STATUS: OK | REVIEW | ALERT
FINDINGS: [2-3 bullet points]
RECOMMENDATION: [one action item]
```

**Activation threshold:** 10 decisions minimum (~2 days at 6 bars/day).

**Sprite state mapping:** OK → cheer, REVIEW → point, ALERT → anxious

**Advisory value:** System health monitor — detects when the crew itself is behaving poorly in ways individual agents can't see.

**Intervention value:** Could trigger automatic parameter adjustments. Requires 50+ decisions to be meaningful. ✅ (grows to ✅✅ over months)

---

### 13. Horizon (`hori`)

| | |
|:---:|:---|
| ![](sprites/characters/hori_stand_south.gif) | **Status:** ✅ Active — refreshes every H4 bar<br>**Model:** `gemma4-e4b-8k`<br>**Trigger:** Every H4 bar via n8n (~30 seconds)<br>**Output:** Rankings, PROMOTE/MONITOR/CAUTION per pair |

**Role:** Analyses signal quality across all monitored pairs and ranks them. Recommends which monitoring pairs are worth promoting to full EA trading. Discovers pairs dynamically from the bars DB — no hardcoded list.

**Monitored pairs (current):** EURUSD H4, AUDUSD H4, GBPUSD H4, USDJPY H4, EURGBP H4, NZDUSD H4, USDCAD H4, GBPJPY H4, EURUSD H1

**Output format:**
```
SUMMARY: one sentence
RANKINGS: each pair scored 1-10 with reason
PROMOTE: pairs ready for EA trading
MONITOR: pairs to keep watching
CAUTION: pairs to avoid and why
NOTE: sample size caveat
```

**Sprite state mapping:** OPPORTUNITY → cheer, CAUTION → anxious, MONITORING → idle

**Advisory value:** Data-driven pair expansion. Instead of guessing which pairs to add, Horizon analyses actual signal quality from 500 bars of history.

**Intervention value:** Could automatically add highly-ranked pairs to `symbols.json` when confidence exceeds a threshold — human review recommended before any automatic promotion. ✅✅

---

## Proposed New Agents

---

### Timeframe Analyst (`tframe`) — *Planned*

| | |
|:---:|:---|
| ![](sprites/characters/tframe_stand_south.gif) | **Implementation:** Pure Python initially<br>**Planned trigger:** Every `/analyse` call<br>**Output:** ALIGNED / NEUTRAL / OPPOSING |

**Role:** Checks the higher timeframe trend before every entry decision. If trading H4, checks D1. If H1, checks H4 and D1.

**Advisory value:** "H4 signal says BUY. D1 QQE is at 32 (below 50, downtrend). OPPOSING — this trade is counter to the daily trend."

**Intervention value:** Addresses the single biggest risk in DCA mean-reversion — counter-trend entries. A BUY sequence on H4 while D1 is in a strong downtrend will absorb DCA additions as price falls further. Blocking counter-trend entries or reducing lot sizes would have major positive impact on drawdown. ✅✅ (top priority new agent)

---

### Volatility Guard (`volat`) — *Planned*

| | |
|:---:|:---|
| ![](sprites/characters/volat_stand_south.gif) | **Implementation:** Pure Python — ATR from bars DB<br>**Planned trigger:** Every `/analyse` call<br>**Output:** NORMAL / ELEVATED / EXTREME |

**Role:** Calculates current ATR against a rolling historical average. Flags when volatility is significantly above normal — flash crashes, post-news spikes, open-of-week gaps.

**Advisory value:** "Current ATR: 45 pips. 30-day average: 12 pips. Volatility is 3.75x normal. EXTREME."

**Intervention value:** Blocks entries during volatility spikes. When ATR is 3x the normal range, spreads are wide and price movement is erratic — exactly the wrong conditions for a DCA entry. ✅✅

---

### Sequence Advisor (`sqadv`) — *Planned*

| | |
|:---:|:---|
| ![](sprites/characters/sqadv_stand_south.gif) | **Model:** `qwen3-14b-8k`<br>**Planned trigger:** Every `/analyse` call<br>**Output:** HEALTHY / MONITOR / CLOSE\_EARLY |

**Role:** Analyses open sequences and advises whether to add to them, hold, or close early. Looks at sequence age, number of trades, current P&L, signal direction vs sequence direction, and Scout's historical pattern data.

**Advisory value:** "EURUSD BUY sequence: 4 trades, open 14 days, currently -$18. QQE has flipped bearish. Recommend early close at -$18 rather than risk a 5th DCA addition."

**Intervention value:** Triggering early sequence closes when conditions deteriorate. Closing at -$18 is always better than waiting for the EA to close at -$45. Build after 20+ completed sequences. ✅✅ (highest long-term value)

---

## Summary Table

| Agent | Status | Advisory | Intervention | Value Over Time |
|-------|--------|----------|-------------|-----------------|
| Regime Analyst | ✅ Active | High | Blocks trending entries | Stable |
| Risk Governor | ✅ Active | High | Hard stops, DD halt | Stable |
| Strategy Evaluator | ✅ Active | High | Quality gate | Stable |
| Execution Coordinator | ✅ Active | Infrastructure | MT5 execution endpoint | Grows with intervention |
| Session Monitor | ✅ Active | Medium | Blocks off-peak entries | Stable |
| Drawdown Monitor | ✅ Active | High | Portfolio halt | Stable |
| Correlation Agent | ✅ Active | Low now | Blocks correlated exposure | Grows with pairs |
| News Watch | ✅ Active | High | Blocks pre-news entries | Stable |
| Performance Analyst | ✅ Active | Low now | Risk reduction trigger | Grows with trades |
| Backtest Scout | ✅ Active | Medium | Position sizing weight | Grows with outcomes |
| Journalist | ✅ Active | Insight | Reporting only | Grows with sequences |
| Meta-Supervisor | ✅ Active | Medium | Parameter adjustment | Grows with decisions |
| Horizon | ✅ Active | High strategic | Pair promotion/demotion | Grows with monitoring |
| *Timeframe Analyst* | 🔲 Planned | High | Highest impact — blocks counter-trend | Stable from day one |
| *Volatility Guard* | 🔲 Planned | High | Blocks abnormal entries | Stable from day one |
| *Sequence Advisor* | 🔲 Planned | High | Early close trigger | Grows with sequence history |
