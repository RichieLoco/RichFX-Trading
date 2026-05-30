# RichFX Development Roadmap

---

## Current Status

**Version:** Phase 2 (Advisory Agents — all implemented)  
**Active agents:** 15 of 16 (Sequence Advisor pending 20+ sequences)  
**EA status:** Demo account, monitoring EURUSD H4 and AUDUSD H4  
**Monitoring pairs:** 35 pairs — 11 symbols across H1/H4/H8 timeframes

---

## Completed

### Phase 1 — Core Infrastructure
- [x] MT5 bridge writing state JSON on each H4 bar
- [x] FastAPI crew API with SSH state fetching
- [x] CrewAI 4-agent sequential chain (Regime → Risk → Strategy → Exec)
- [x] Pixel-art trading floor dashboard (HTML canvas + DOM agents)
- [x] 12 agent sprites with full animation sets
- [x] Agent overlay panels with LLM output
- [x] TV screen with pair data, QQE, sequences, equity, P&L
- [x] Dynamic symbol loading from `config/symbols.json`
- [x] Night overlay, weather system, UTC clock
- [x] n8n workflow triggering analysis on H4 bar schedule
- [x] Telegram alerts via Shikigami bot

### Phase 2 — Advisory Agents
- [x] Session Monitor (`sess`) — pure Python session quality check
- [x] Drawdown Monitor (`dd`) — equity drawdown thresholds
- [x] Correlation Agent (`corr`) — cross-pair USD exposure detection
- [x] News Watch (`news`) — ForexFactory economic calendar (1hr cache)
- [x] Performance Analyst (`perf`) — closed trade history stats
- [x] Strategy Evaluator (`strat`) — signal quality gate in LLM chain
- [x] Backtest Scout (`scout`) — 500 bars seeded, pattern confidence scoring
- [x] Journalist (`journ`) — gemma4-e4b-8k, triggers on sequence close
- [x] Meta-Supervisor (`meta`) — active, accumulating decisions
- [x] Horizon (`hori`) — gemma4-e4b-8k, H4+H1 rankings, per-timeframe cache
- [x] Timeframe Analyst (`tframe`) — H8 alignment check before H4 entries
- [x] Volatility Guard (`volat`) — ATR ratio vs 30-bar average

### Infrastructure
- [x] FastAPI serving UI and sprites
- [x] `/last-analysis` cache — persisted to disk, agents active on restart
- [x] Centralised SQLite DB (`richfx.db`) — bars, decisions, sequences tables
- [x] 500 bars seeded with backfilled QQE/MACD signals
- [x] 35 monitoring pairs — 11 symbols × H1/H4/H8 (XAUUSD, GBPAUD, EURJPY, EURCAD added)
- [x] Horizon per-timeframe cache — H4 and H1 stored separately
- [x] n8n Horizon H4 + H1 workflows — auto-refresh every bar
- [x] Dynamic dusk/dawn via SunCalc.js — accurate London sunrise/sunset
- [x] SunCalc cache bug fixed — date/time recalculation on day boundary
- [x] TV screen auto-rotates pairs every 30s (pauses on manual click)
- [x] Alt gesture animation for female agents (exec, corr, perf, journ, meta, news, hori, sqadv)
- [x] WinSW services for VM processes (mt5_bridge, vm_health, telegram_alerter)
- [x] n8n NAS health monitor — daily Telegram report with ZFS, temps, VMs, containers
- [x] GitHub repo public at https://github.com/RichieLoco/RichFX
- [x] Agent click hit areas tightened — 50×60% zone prevents overlap misfires
- [x] 16 agents on trading floor — tframe and sqadv at rear, 14 at front

---

## Upcoming

### Near Term

#### Sequence Advisor (`sqadv`)
- [ ] Build after 20+ completed sequences
- [ ] Analyse open sequences — age, trade count, P&L, signal alignment
- [ ] Recommend HEALTHY / MONITOR / CLOSE_EARLY
- [ ] Model: qwen3-14b-8k
- [ ] Female agent, has alt gesture

#### Scout Outcome Tagging
- [ ] Link closed sequences to historical bar patterns
- [ ] Confidence scoring incorporates actual win rates
- [ ] Scout becomes statistically meaningful rather than signal-consistency only

#### Multi-pair Crew Analysis
- [ ] `/analyse` running for each active pair independently
- [ ] Agent states keyed by pair, not global

---

### Medium Term

#### Live Account Transition
- [ ] Add live account symbol entries to `symbols.json`
- [ ] Live/demo badge on TV screen (`[L]` suffix already implemented)
- [ ] Separate performance tracking by account type
- [ ] Drawdown thresholds tightened for live account

#### Intervention Layer (advisory → active)
- [ ] New endpoint on `vm_health.py` accepting trade commands
- [ ] MT5 `OrderSend()` / `PositionClose()` via Python
- [ ] Risk Governor triggering close on `DD_HALT`
- [ ] Tframe blocking counter-trend entries at execution level
- [ ] Volat blocking entries during extreme ATR spikes
- [ ] Safeguards: confirmation window, max position size limits
- [ ] **Note:** Requires 2+ months of advisory data before implementation

#### Journalist Weekly Digest
- [ ] Batch weekly sequence narratives
- [ ] n8n workflow triggers weekly summary via Telegram

#### Cloudflare Hardening
- [ ] Rate limiting on API endpoints
- [ ] Cloudflare WAF rules

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Post-quantum SSH warning on VM connections | Cosmetic | Server needs OpenSSH upgrade |
| Scout outcome data missing | Low | Sequences need to close before win rates are meaningful |
| tframe shows TFRAME_NA until directional trade | Expected | Correct — no action to check alignment on |
| Volat ratio low on weekend/off-hours | Expected | Partial bars — correct during active sessions |

---

## Architecture Decisions

### Why n8n triggers `/analyse` (not the dashboard)
The dashboard previously called `/analyse` on every 5-minute poll. With multiple browser tabs this would stack queued analysis calls on the single-worker executor, eventually starving the event loop. Moving the trigger to n8n means analysis runs exactly once per bar regardless of how many dashboard sessions are open.

### Why `/last-analysis` instead of `/state` for agent data
`/state` contains raw MT5 data (price, positions, account). Agent decisions (regime classification, risk approval, signal score) are separate and only produced by the LLM chain. `/last-analysis` caches the last crew result in memory so the dashboard can read it without re-running inference.

### Why `deepseek-r1` for Risk Governor
The Risk Governor needs to reason through multiple hard rules simultaneously and produce a structured approval/rejection with explicit reasoning. DeepSeek R1's chain-of-thought produces more reliable structured output for rule-based tasks than pure instruction-following models.

### Why `qwen3-14b-nothink` is NOT used for Strategy Evaluator
The `-nothink` variant suppresses internal reasoning tokens that CrewAI's ReAct agent loop depends on for formatting its `Thought:` / `Final Answer:` cycle. Using it causes `ValueError: Invalid response from LLM call - None or empty` on every Strategy task. Use `qwen3-14b-8k` instead.

### Why correlations.json has no restart requirement
Correlation coefficients between pairs change over time and may need updating. Loading the file fresh on every `/analyse` call (rather than once at startup) means traders can update coefficients during a live session without interrupting the running service.
