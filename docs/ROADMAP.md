# RichFX Development Roadmap

---

## Current Status

**Version:** Phase 2 (Advisory Agents)  
**Active agents:** 9 of 12  
**EA status:** Demo account, monitoring EURUSD H4 and AUDUSD H4

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
- [x] News Watch (`news`) — ForexFactory economic calendar
- [x] Performance Analyst (`perf`) — closed trade history stats
- [x] Strategy Evaluator (`strat`) — signal quality gate in LLM chain

### Infrastructure
- [x] FastAPI serving UI and sprites (eliminates file:// CORS)
- [x] `/last-analysis` cache — dashboard never triggers `/analyse`
- [x] `vm_health.py` HTTP server with `/history` endpoint
- [x] `mt5_bridge.py` writing `history.json` on each bar
- [x] Cloudflare tunnel at `crew.richielo.co`
- [x] Cloudflare Access email whitelist authentication
- [x] `window.location.origin` API base — Tailscale compatible
- [x] Ollama `KEEP_ALIVE=60m` — models pre-warmed in memory

---

## In Progress

### Journalist Agent (`journ`)
- [ ] Sequence close detection (compare positions bar-over-bar)
- [ ] Post-mortem template generation
- [ ] Telegram delivery of sequence summaries
- [ ] Storage in decision log for Meta-Supervisor

**Trigger:** When a magic number that had open positions closes all of them  
**Blocked by:** Need a few more completed sequences

---

## Upcoming

### Near Term

#### VM Process Management
- [ ] Convert `mt5_bridge.py` to WinSW service
- [ ] Convert `vm_health.py` to WinSW service
- [ ] Convert `telegram_alerter.py` to WinSW service
- [ ] Auto-restart on failure, survive VM reboots

#### Closed P&L on TV Screen
- [ ] Add `closed_pnl` cell to TV screen row 2
- [ ] Populate from `/performance` endpoint
- [ ] Green/red colour coding

#### Performance Agent LLM Narrative
- [ ] `POST /performance/analyse` endpoint
- [ ] Daily Telegram performance summary via n8n
- [ ] `run_performance_analysis()` in `richfx_crew.py`

#### Multi-pair Analysis
- [ ] `/analyse` running for each active pair (currently first pair only)
- [ ] Agent states keyed by pair, not global
- [ ] TV screen agent overlay showing per-pair crew decisions

---

### Medium Term

#### Backtest Scout (`scout`)
- [ ] SQLite historical bar database schema design
- [ ] Historical data ingestion from MT5
- [ ] Pattern matching: QQE level + MACD state + regime
- [ ] Confidence score integration into crew chain

#### Journalist Agent (`journ`)
- [ ] Sequence post-mortem LLM narrative
- [ ] `journ` sprite activation on sequence close
- [ ] Weekly digest Telegram message

#### Dashboard Enhancements
- [ ] Favicon (eliminates 404 in logs)
- [ ] `white-space: pre-wrap` on all overlay FULL OUTPUT divs
- [ ] Closed P&L cell populated from performance endpoint
- [ ] Agent state persistence across service restarts (Redis or SQLite)

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
| VM processes not WinSW services | Medium | Will not survive VM reboot |
| `_last_analysis` cache lost on restart | Low | Repopulated on next n8n bar trigger |
| Post-quantum SSH warning on VM connections | Cosmetic | Server needs OpenSSH upgrade |
| `write_history` only called on new bar | Low | Single-run mode doesn't write history |
| No favicon served | Cosmetic | 404 in logs, harmless |

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
