# RichFX System Architecture

---

## System Overview

RichFX is a multi-machine algorithmic trading monitoring system. It connects a live MetaTrader 5 EA to a CrewAI multi-agent analysis pipeline, presenting results on a pixel-art trading floor dashboard.

The system is **advisory only** — the crew analyses market conditions and recommends actions, but all trade execution is handled exclusively by the MQL5 EA running in MT5. The crew has no ability to open, close, or modify trades.

---

## Machine Roles

### ubuntu-ai — Ubuntu (100.127.251.110)

The primary compute node. Runs all LLM inference and the FastAPI server.

| Component | Port | Description |
|-----------|------|-------------|
| Ollama | 11434 | Local LLM inference server |
| FastAPI (crew_api.py) | 8000 | Main HTTP API + static file server |

**Ollama models loaded:**
- `qwen3-14b-8k` — Regime Analyst + Strategy Evaluator
- `deepseek-r1-14b-8k` — Risk Governor
- `qwen25-14b-8k` — Execution Coordinator

All models configured with 8192 token context window and 60-minute keepalive to remain resident in the 128GB unified RAM pool.

---

### Win11 VM — Windows 11 (100.80.62.2)

Runs MetaTrader 5 and the bridge processes that connect it to the Ubuntu server.

| Component | Port | Description |
|-----------|------|-------------|
| MT5 Terminal | — | MetaTrader 5 with QQE_DCA EA |
| vm_health.py | 8765 | HTTP server — health + trade history |
| mt5_bridge.py | — | Writes state + history JSON files |
| telegram_alerter.py | — | Shikigami Telegram bot |

The VM is the only machine with a direct MT5 connection. All market data, positions, and account information flows from here.

---

### NAS LXC — (100.110.69.69)

Runs n8n for workflow automation and scheduling.

| Component | Port | Description |
|-----------|------|-------------|
| n8n Docker | 5678 | Workflow automation |

n8n is the **sole trigger for `/analyse`**. It fires on each H4 bar close, calls the crew API, and sends Telegram alerts. The dashboard never triggers analysis directly.

---

### Raspberry Pi — (100.88.68.108)

Lightweight always-on device running the Cloudflare tunnel.

| Component | Description |
|-----------|-------------|
| cloudflared | Cloudflare tunnel → crew.richielo.co |

---

## Data Flow

### Per-Bar Cycle (every 4 hours)

```
MT5 EA (Win11 VM)
    │ new H4 bar detected
    ▼
mt5_bridge.py
    ├── fetch OHLCV from MT5
    ├── calculate QQE + QMP signals
    ├── fetch open positions + account state
    ├── write state_EURUSD_H4.json
    ├── write state_AUDUSD_H4.json
    ├── write state.json (backward compat)
    └── write history.json (all closed deals, 90 days)

n8n (NAS LXC) — H4 bar schedule
    │
    ├── preflight: GET http://100.80.62.2:8765/health
    ├── preflight: GET http://100.127.251.110:8000/health
    │
    └── POST http://100.127.251.110:8000/analyse
            │   {symbol, timeframe, magic}
            ▼
        crew_api.py
            ├── SSH → read state_EURUSD_H4.json
            ├── run CrewAI chain:
            │     Regime Analyst (qwen3-14b-8k)
            │         → Risk Governor (deepseek-r1-14b-8k)
            │             → Strategy Evaluator (qwen3-14b-8k)
            │                 → Execution Coordinator (qwen25-14b-8k)
            ├── check_session()      — pure Python
            ├── check_drawdown()     — pure Python
            ├── check_correlation()  — reads correlations.json
            ├── check_news_calendar() — ForexFactory API
            ├── cache result in _last_analysis[symbol]
            └── return AnalyseResponse

n8n → send Telegram alert (Shikigami)
```

### Dashboard Poll (every 5 minutes)

```
Browser (dashboard)
    │
    ├── GET /state?symbol=EURUSD     → SSH read state JSON (cached 5 min)
    ├── GET /state?symbol=AUDUSD     → SSH read state JSON (cached 5 min)
    ├── GET /last-analysis?symbol=EURUSD  → in-memory cache (instant)
    ├── GET /last-analysis?symbol=AUDUSD  → in-memory cache (instant)
    └── GET /performance?days=30     → HTTP proxy to vm_health :8765
```

The dashboard performs **zero LLM inference**. All heavy computation is done by n8n-triggered analysis runs.

---

## API Architecture

### FastAPI (`crew_api.py`)

```
GET  /health              Liveness probe
GET  /symbols             Active pairs from config/symbols.json
GET  /state               Market state (SSH → VM, 5-min cache)
POST /analyse             Run 4-agent crew (single-worker executor)
GET  /last-analysis       Most recent cached crew result
GET  /history             Proxy → vm_health :8765/history
GET  /performance         Aggregated stats from /history
POST /performance/analyse LLM performance narrative (daily)
GET  /                    Redirect → /ui/richfx_trading_floor.html
Static /ui                Trading floor HTML
Static /sprites           Character and furniture GIFs/PNGs
```

### Concurrency Model

```
FastAPI event loop
    │
    ├── _analyse_executor (ThreadPoolExecutor, max_workers=1)
    │       └── run_crew_for_state() — blocks for ~4 minutes
    │           (only one analysis can run at a time)
    │
    └── Default thread pool
            └── fetch_state() — SSH to VM (seconds)
                (runs independently, never blocked by analysis lock)
```

The single-worker executor prevents concurrent LLM runs that would exhaust Ollama's context window. The lock gives an immediate error if analysis is already running rather than silently queuing.

---

## State File Schema

Written by `mt5_bridge.py`, read by `crew_api.py` via SSH.

```json
{
  "meta": {
    "symbol": "EURUSD",
    "timeframe": "H4",
    "last_bar_time": "2026-05-25T20:00:00+00:00",
    "generated_at": "2026-05-25T20:00:05+00:00",
    "bars_used": 500
  },
  "price": {
    "bid": 1.16421,
    "ask": 1.16425,
    "spread": 4.0
  },
  "signal": {
    "qqe_value": 58.37,
    "qqe_above_50": true,
    "qqe_overbought_triggered": false,
    "qqe_oversold_triggered": false,
    "qmp_trend": 1,
    "qmp_buy_signal": false,
    "qmp_sell_signal": false,
    "macd": 0.000312,
    "macd_avg": 0.000298,
    "macd_above_avg": true
  },
  "history": [
    {
      "bar": "2026-05-25T16:00:00+00:00",
      "qqe": 51.75,
      "trend": 1,
      "buy": false,
      "sell": false
    }
  ],
  "sequences": {
    "buy_sequence": {
      "active": true,
      "trade_count": 2,
      "total_lots": 0.04,
      "avg_entry": 1.16334,
      "total_profit": 4.24,
      "in_recovery": false
    },
    "sell_sequence": {
      "active": false,
      "trade_count": 0,
      "total_lots": 0,
      "avg_entry": 0,
      "total_profit": 0,
      "in_recovery": false
    }
  },
  "positions": [
    {
      "ticket": 46945815,
      "symbol": "EURUSD",
      "type": "buy",
      "volume": 0.01,
      "open_price": 1.16421,
      "current_price": 1.16502,
      "profit": 0.81,
      "swap": -0.02,
      "magic": 100401,
      "comment": "EURUSD-4H-6040-T1",
      "open_time": "2026-05-18T12:00:00+00:00"
    }
  ],
  "account": {
    "balance": 5000.0,
    "equity": 5000.81,
    "margin": 74.68,
    "free_margin": 4926.13,
    "profit": 0.81,
    "leverage": 30,
    "currency": "USD",
    "is_demo": true
  }
}
```

---

## Security Model

### Network Boundaries

```
Internet
    │
    ▼
Cloudflare Edge (HTTPS + Access auth)
    │  email OTP required
    ▼
Cloudflare Tunnel (encrypted)
    │
    ▼
Raspberry Pi (cloudflared)
    │  Tailscale
    ▼
ubuntu-ai :8000
    │  only /ui and /sprites served externally
    │  /analyse, /history, /state — Tailscale only
```

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Unauthorised dashboard access | Cloudflare Access — email OTP |
| API endpoint abuse from internet | All API routes require Tailscale network |
| Analysis spam (stacked LLM calls) | Single-worker executor + lock |
| Credential exposure | Telegram tokens in code — not committed to git |
| VM compromise | SSH key authentication only, no password auth |

### What Is Not Protected

- The Tailscale network itself — if a Tailscale device is compromised, all internal APIs are reachable
- The `/analyse` endpoint on Tailscale — any Tailscale device can trigger LLM inference
- Telegram bot token in source code — must be moved to environment variables before open-sourcing

---

## Dashboard Architecture

The trading floor is a single HTML file (`richfx_trading_floor.html`) served as a static file via FastAPI.

### Rendering

```
HTML Canvas (1280×400px)
    ├── drawRoom()          — walls, floor, carpet tiles
    ├── drawWhiteboard()    — PREFLIGHT status, LIVE/DEMO indicator
    ├── drawFurniture()     — PNG sprites (desk, partitions, etc.)
    ├── drawClock()         — UTC analogue clock
    ├── drawTVScreen()      — pair data, QQE, sequences, P&L
    ├── drawWindow()        — city view, weather, time-of-day sky
    └── drawNightOverlay()  — UTC-aware darkness overlay

HTML Div Layer (#agentLayer)
    └── 12 × .agent-wrapper divs
            ├── .agent-name  — label tag above sprite
            └── .agent-sprite <img>  — GIF animation
```

### Agent Positioning

Agents are positioned as CSS percentage values relative to the canvas wrapper, allowing the layout to scale to any viewport width while maintaining the 1280:400 aspect ratio.

### Z-ordering

Agents are sorted by Y position every 50ms. Higher Y (closer to camera) renders on top, giving a pseudo-3D depth effect as agents walk around the floor.

### Performance

- Canvas redraws at 60fps (requestAnimationFrame)
- Agent movement updates at 20fps (setInterval 50ms)
- Data polls at 5-minute intervals (setInterval 300000ms)
- All sprite assets cached by browser after first load (304 responses)
