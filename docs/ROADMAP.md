# RichFX Development Roadmap

---

## Current Status

**Version:** Phase 2 (Advisory Agents — all implemented)  
**Active agents:** 11 of 12 (Meta accumulating decisions)  
**EA status:** Demo account, monitoring EURUSD H4 and AUDUSD H4  
**Monitoring pairs:** 7 additional pairs seeding bar data (no EA)

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
- [x] Journalist (`journ`) — gemma4-e4b-8k, triggers on sequence close, thinking tokens stripped
- [x] Meta-Supervisor (`meta`) — accumulating decisions, activates at 10+

### Infrastructure
- [x] FastAPI serving UI and sprites (eliminates file:// CORS)
- [x] `/last-analysis` cache — dashboard never triggers `/analyse`
- [x] Analysis cache persisted to disk — agents active instantly after restart
- [x] `vm_health.py` HTTP server with `/history`, `/bars`, `/decisions`, `/sequences` endpoints
- [x] `mt5_bridge.py` writing `history.json` and `richfx.db` on each bar
- [x] Centralised SQLite DB (`richfx.db`) — bars, decisions, sequences tables
- [x] 500 bars seeded with backfilled QQE/MACD signals
- [x] 7 monitoring-only pairs seeding bar data (GBPUSD, USDJPY, EURGBP, NZDUSD, USDCAD, GBPJPY, EURUSD H1)
- [x] Cloudflare tunnel at `crew.richielo.co`
- [x] Cloudflare Access email whitelist authentication
- [x] `window.location.origin` API base — Tailscale compatible
- [x] Ollama `KEEP_ALIVE=60m` — models pre-warmed in memory
- [x] Gemma 4 e4b pulled and assigned to Journalist
- [x] Decision logging to DB after every `/analyse`
- [x] Sequence close detection and Journalist trigger
- [x] Favicon (RichieLoco logo)
- [x] `white-space: pre-wrap` on overlay output divs
- [x] Closed P&L on TV screen row 2
- [x] TV screen auto-rotates pairs every 30s (pauses on manual click)
- [x] Alt gesture animation for female agents
- [x] WinSW services for VM processes (mt5_bridge, vm_health, telegram_alerter)
- [x] n8n NAS health monitor workflow with Telegram alerts
- [x] GitHub repo at https://github.com/RichieLoco/RichFX

---

## Upcoming

### Near Term

#### Horizon Agent (Pair Expansion Analyst)
- [ ] Analyse signal quality across all monitoring pairs in bars DB
- [ ] Identify pairs where QQE/MACD signals are most consistent
- [ ] Recommend which pairs to promote to full EA trading
- [ ] Run weekly via n8n
- [ ] Model: gemma4-26b-8k (when pulled)

#### Dynamic Dusk/Dawn
- [ ] Replace hardcoded UTC hour checks with SunCalc algorithm
- [ ] 1.5-hour transition windows either side of actual sunrise/sunset
- [ ] Location-configurable (default: London)

#### Multi-pair Crew Analysis
- [ ] `/analyse` running for each active pair independently
- [ ] Agent states keyed by pair, not global
- [ ] TV screen overlay showing per-pair crew decisions

---

### Medium Term

#### Scout Outcome Tagging
- [ ] Link closed sequences to historical bar patterns
- [ ] Confidence scoring incorporates actual win rates
- [ ] Scout becomes statistically meaningful rather than signal-consistency only

#### Journalist Weekly Digest
- [ ] Batch weekly sequence narratives
- [ ] n8n workflow triggers weekly summary via Telegram
- [ ] Ad-hoc: `GET /journalist?last=5` already available

#### Dashboard Enhancements
- [ ] Dynamic dusk/dawn using SunCalc (location-aware)
- [ ] Agent gesture animations in zoomed overlay mode for all gestures

---

### Long Term

#### Meta-Supervisor (`meta`)
- [ ] Decision log database (bar-by-bar crew output storage)
- [ ] Pattern detection queries (systematic avoidance, win rate by pair)
- [ ] Weekly Telegram meta-analysis report
- [ ] Threshold adjustment recommendations

#### Live Account Transition
- [ ] Add live account symbol entries to `symbols.json`
- [ ] Live/demo badge on TV screen (`[L]` suffix)
- [ ] Separate performance tracking by account type
- [ ] Drawdown thresholds tightened for live account

#### Intervention Layer (advisory → active)
- [ ] New endpoint on `vm_health.py` accepting trade commands
- [ ] MT5 `OrderSend()` / `PositionClose()` via Python
- [ ] Risk Governor triggering close on `DD_HALT`
- [ ] Safeguards: confirmation window, max position size limits
- [ ] **Note:** Significant work — requires careful safety design before implementation

#### Cloudflare Hardening
- [ ] Rate limiting on API endpoints
- [ ] Cloudflare WAF rules
- [ ] Access logging to R2

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Post-quantum SSH warning on VM connections | Cosmetic | Server needs OpenSSH upgrade |
| Scout outcome data missing | Low | Sequences need to close before win rates are meaningful |
| Meta needs 10+ decisions | Low | Accumulating — activates automatically |
| `write_history` only called on new bar | Low | Single-run mode doesn't write history |

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
