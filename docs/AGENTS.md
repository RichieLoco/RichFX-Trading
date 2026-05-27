# RichFX Agent Roster

Detailed reference for all 12 agents on the RichFX Trading Floor.

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

### 1. Market Regime Analyst (`regime`)

**Model:** `qwen3-14b-8k`  
**Position:** First in chain  
**Sprite:** `regime`

**Role:**  
Classifies the current market regime based on QQE momentum, QMP trend filter, and MACD. Sets the context for all downstream agents.

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

---

### 2. Risk Governor (`risk`)

**Model:** `deepseek-r1-14b-8k`  
**Position:** Second in chain  
**Sprite:** `risk`

**Role:**  
Enforces hard risk rules. Receives regime assessment as context. Approves or rejects based on quantitative limits. Cannot be overridden by other agents.

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

**Sprite state mapping:**
- `approved` → cheer
- `rejected` → cry

---

### 3. Strategy Evaluator (`strat`)

**Model:** `qwen3-14b-nothink-8k` (suppressed CoT)  
**Position:** Third in chain  
**Sprite:** `strat`

**Role:**  
Evaluates signal quality after Risk approves. If Risk rejects, immediately outputs HOLD without scoring. Prevents weak signals from reaching execution even when risk limits are technically within bounds.

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

**Sprite state mapping:**
- `PROCEED` → cheer
- `HOLD` → anxious

---

### 4. Execution Coordinator (`exec`)

**Model:** `qwen25-14b-8k`  
**Position:** Fourth in chain  
**Sprite:** `exec`

**Role:**  
Produces the final trading instruction. Receives all upstream decisions as context. If Strategy returned HOLD, outputs no_action. Otherwise produces a precise CSV instruction.

**Output format (strict CSV — no markdown):**
```
action,symbol,lots,seq_num,rationale
```

**Actions:**
- `open_buy` — new buy sequence
- `open_sell` — new sell sequence
- `add_to_buy` — DCA addition to existing buy
- `add_to_sell` — DCA addition to existing sell
- `close_buy` — close buy sequence
- `close_sell` — close sell sequence
- `no_action` — no trade this bar

**Sprite state mapping:**
- `no_action` → idle
- any trade action → point

---

## Phase 2 — Advisory Agents

### 5. Session Monitor (`sess`)

**Implementation:** Pure Python  
**Trigger:** Every `/analyse` call  
**Sprite:** `sess`

**Role:**  
Checks current UTC hour against forex session windows. Warns during off-peak hours when liquidity is poor and spreads are wide.

**Session windows (UTC):**
| Hours | Session | Quality |
|-------|---------|---------|
| 08:00–13:00 | London | GOOD |
| 13:00–17:00 | London/NY Overlap | HIGH |
| 17:00–22:00 | New York | GOOD |
| 22:00–05:00 | Off-peak | LOW |
| 05:00–08:00 | Pre-London | LOW |

**Sprite state mapping:**
- HIGH/GOOD → idle
- LOW → anxious

---

### 6. Drawdown Monitor (`dd`)

**Implementation:** Pure Python  
**Trigger:** Every `/analyse` call  
**Sprite:** `dd`

**Role:**  
Calculates current equity drawdown from balance. Provides an independent check from the Risk Governor's spread/sequence rules.

**Thresholds:**
- < 2% → `DD_OK` → idle
- 2–5% → `DD_WARN` → anxious
- ≥ 5% → `DD_HALT` → cry

**Calculation:**
```
drawdown_pct = max(0, (balance - equity) / balance) * 100
```

---

### 7. Correlation Agent (`corr`)

**Implementation:** Pure Python + `config/correlations.json`  
**Trigger:** Every `/analyse` call  
**Sprite:** `corr`

**Role:**  
Checks whether the proposed trade direction would create correlated USD exposure with positions already open on other pairs. Prevents doubling up on the same underlying move.

**Logic:**
- For USD-quote pairs (EURUSD, GBPUSD): same direction = correlated
- For USD-base pairs (USDCAD): opposite direction = correlated

**Correlation matrix:** Loaded from `config/correlations.json` — no restart required when file is updated.

**Sprite state mapping:**
- No conflicts → approved (cheer)
- Correlated exposure detected → anxious

---

### 8. News Watch (`news`)

**Implementation:** Pure Python — ForexFactory calendar API  
**Trigger:** Every `/analyse` call  
**Sprite:** `news`

**Role:**  
Fetches the current week's economic calendar and checks for high-impact events affecting the currencies in the current pair within a configurable window.

**Calendar source:** `https://nfs.faireconomy.media/ff_calendar_thisweek.json`

**Status levels:**
- No events within 24h → `NEWS_OK` → approved (cheer)
- High-impact event within 24h → `NEWS_WARN` → point
- High-impact event within 2h → `NEWS_BLOCK` → anxious

**Fail-safe:** If the calendar is unreachable, returns `NEWS_OK` and logs the error. Does not block trading due to connectivity issues.

---

### 9. Performance Analyst (`perf`)

**Implementation:** `/performance` endpoint + closed trade history  
**Trigger:** Every page load + 5-minute poll  
**Sprite:** `perf`

**Role:**  
Reads closed trade history from the VM and calculates rolling performance statistics. Groups by account type (demo/live) and by symbol for clear attribution.

**Data source:** `vm_health.py` → `history.json` (written by `mt5_bridge.py` on each new bar)

**Metrics:**
- Total trades, wins, losses
- Win rate (%)
- Total net P&L
- Average win / average loss

**Sprite state mapping:**
- Win rate ≥ 60% → approved (cheer)
- Win rate 40–60% → idle
- Win rate < 40% → anxious
- No closed trades → dormant

---

### 10. Journalist (`journ`)

**Status:** ⏳ Pending — requires closed sequence accumulation  
**Sprite:** `journ`

**Planned role:**  
Writes plain-English post-mortems when a sequence closes. Records entry conditions, crew decisions, duration, and final result. Output stored for Meta-Supervisor review.

**Trigger condition:**  
Sequence close detection — when a magic number that had open positions now has none.

**Sample output:**
```
AUDUSD BUY sequence closed — 2 trades, avg entry 0.71334,
closed at 0.71685. Net P&L: +$13.99. Duration: 7 days.
Risk Governor approved at 6/10. Strategy held 1 bar before entry.
Result: WIN.
```

---

### 11. Backtest Scout (`scout`)

**Status:** ⏳ Pending — requires historical pattern database  
**Sprite:** `scout`

**Planned role:**  
Compares current signal conditions against historical bar patterns to produce a confidence score. Answers: "How have similar setups performed historically?"

**Dependencies:**
- SQLite historical bar database (to be built)
- Pattern matching algorithm (QQE level + MACD state + regime)

---

### 12. Meta-Supervisor (`meta`)

**Status:** ⏳ Pending — requires weeks of decision log accumulation  
**Sprite:** `meta`

**Planned role:**  
Reviews crew decisions across many bars and flags systematic patterns. Examples:
- "Risk Governor has rejected 23 consecutive bars — is the spread threshold too tight?"
- "Strategy Evaluator returns HOLD 95% of the time — is signal quality genuinely poor or is the scoring too conservative?"
- "EURUSD sequences have a 40% win rate vs AUDUSD at 100% — consider pair weighting"

**Dependencies:**
- Decision log database (bar-by-bar crew output storage)
- Minimum ~2 weeks of data for meaningful pattern detection
