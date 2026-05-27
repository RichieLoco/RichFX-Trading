# RichFX Trading Floor

An AI-powered algorithmic trading monitoring system built around a multi-agent CrewAI crew, a live MT5 bridge, and a pixel-art trading floor dashboard.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  ubuntu-ai (Ubuntu)          100.127.251.110                │
│  ├── Ollama (LLM inference)  :11434                         │
│  ├── FastAPI Crew API        :8000                          │
│  └── Trading Floor UI        /ui/richfx_trading_floor.html  │
├─────────────────────────────────────────────────────────────┤
│  Win11 VM                    100.80.62.2                    │
│  ├── MT5 Bridge              (writes state + history JSON)  │
│  ├── VM Health Server        :8765                          │
│  └── Telegram Alerter        (sends Shikigami alerts)       │
├─────────────────────────────────────────────────────────────┤
│  NAS LXC                     100.110.69.69                  │
│  └── n8n Docker              :5678                          │
├─────────────────────────────────────────────────────────────┤
│  Raspberry Pi                100.88.68.108                  │
│  └── Cloudflare Tunnel       → crew.richielo.co             │
└─────────────────────────────────────────────────────────────┘
```

All machines connected via **Tailscale** mesh VPN. Public dashboard access via **Cloudflare Access** at `https://crew.richielo.co`.

---

## Repository Structure

```
richfx/
│
├── README.md
│
├── ubuntu-ai/                          # Ubuntu server (ubuntu-ai)
│   ├── core/
│   │   ├── crew_api.py                 # FastAPI — main HTTP server
│   │   └── richfx_crew.py             # CrewAI agents + tasks
│   ├── config/
│   │   ├── symbols.json               # Active trading pairs config
│   │   └── correlations.json          # Pair correlation matrix
│   ├── ui/
│   │   └── richfx_trading_floor.html  # Trading floor dashboard
│   ├── sprites/
│   │   ├── characters/                # Agent GIF sprite sheets (112×112px)
│   │   │   ├── regime_*.gif
│   │   │   ├── risk_*.gif
│   │   │   ├── exec_*.gif
│   │   │   ├── strat_*.gif
│   │   │   ├── perf_*.gif
│   │   │   ├── sess_*.gif
│   │   │   ├── corr_*.gif
│   │   │   ├── dd_*.gif
│   │   │   ├── news_*.gif
│   │   │   ├── journ_*.gif
│   │   │   ├── scout_*.gif
│   │   │   └── meta_*.gif
│   │   └── furniture/                 # Office furniture PNG sprites
│   │       ├── desk.png
│   │       ├── coffee_machine.png
│   │       ├── partition1-4.png
│   │       └── ...
│   └── systemd/
│       └── richfx-api.service         # systemd service unit
│
├── win11-vm/                           # Windows 11 VM
│   └── core/
│       ├── mt5_bridge.py              # MT5 → JSON state writer
│       ├── vm_health.py               # HTTP health + history server (:8765)
│       ├── telegram_alerter.py        # Shikigami Telegram bot
│       ├── indicators.py              # QQE / QMP signal calculations
│       └── watchlist.json             # Active pairs for MT5 bridge
│
└── docs/
    ├── ARCHITECTURE.md                # System architecture detail
    ├── AGENTS.md                      # Agent descriptions and status
    ├── ROADMAP.md                     # Development roadmap
    └── SETUP.md                       # Installation and setup guide
```

---

## Agent Roster

The trading floor has 12 agents across two phases:

### Phase 1 — Core LLM Chain (always active)

| Agent | Role | Model |
|-------|------|-------|
| **Regime Analyst** | Classifies market regime — RANGING / TRENDING / MIXED | qwen3-14b-8k |
| **Risk Governor** | Enforces hard risk rules — spread, drawdown, sequence limits | deepseek-r1-14b-8k |
| **Strategy Evaluator** | Scores signal quality 1-10, returns PROCEED or HOLD | qwen3-14b-8k |
| **Execution Coordinator** | Produces final action instruction — open/add/close/no_action | qwen25-14b-8k |

### Phase 2 — Advisory Agents

| Agent | Role | Status |
|-------|------|--------|
| **Session** | Flags poor liquidity windows (off-peak hours) | ✅ Active |
| **Drawdown** | Monitors equity drawdown — warns at 2%, halts at 5% | ✅ Active |
| **Correlation** | Detects correlated USD exposure across pairs | ✅ Active |
| **News Watch** | ForexFactory calendar — blocks entries before high-impact events | ✅ Active |
| **Performance** | Tracks win rate and P&L from closed trade history | ✅ Active |
| **Journalist** | Plain-English post-mortems on completed sequences | ⏳ Pending |
| **Backtest Scout** | Compares signals to historical patterns | ⏳ Pending |
| **Meta-Supervisor** | Reviews crew decisions for systematic bias | ⏳ Pending |

---

## API Endpoints

All served by FastAPI on port 8000.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check |
| `/symbols` | GET | Active trading pairs from config |
| `/state` | GET | Current market state for a symbol (cached 5 min) |
| `/analyse` | POST | Run full 4-agent crew analysis |
| `/last-analysis` | GET | Most recent cached crew result |
| `/history` | GET | Closed trade history from MT5 |
| `/performance` | GET | Aggregated performance stats by symbol/account |
| `/ui` | Static | Trading floor dashboard HTML |
| `/sprites` | Static | Agent and furniture sprite files |

---

## Data Flow

```
MT5 Terminal (Win11 VM)
    │
    ├── mt5_bridge.py (every new H4 bar)
    │       ├── writes state_EURUSD_H4.json
    │       ├── writes state_AUDUSD_H4.json
    │       └── writes history.json (all closed deals)
    │
    └── vm_health.py (HTTP :8765)
            ├── GET /health
            └── GET /history  (reads history.json)

n8n (NAS LXC) — triggers on H4 bar schedule
    │
    └── POST /analyse → crew_api.py (ubuntu-ai)
            │
            ├── fetch state via SSH
            ├── run 4-agent CrewAI chain (Ollama)
            ├── run pure-Python checks (sess, dd, corr, news)
            ├── cache result in _last_analysis{}
            └── send Telegram alert (Shikigami)

Dashboard (browser)
    │
    ├── GET /state          (market data, TV screen)
    ├── GET /last-analysis  (agent results, no LLM trigger)
    └── GET /performance    (closed trade stats)
```

---

## Configuration Files

### `config/symbols.json`

Defines active trading pairs. Add a new entry and restart the service to activate a new pair.

```json
{
  "symbols": [
    {
      "symbol": "EURUSD",
      "timeframe": "H4",
      "magic": 100401,
      "account": "demo",
      "active": true,
      "label": "Euro / US Dollar"
    }
  ]
}
```

### `config/correlations.json`

Correlation matrix used by the Correlation agent. Updated at runtime — no restart required.

```json
{
  "pairs": [
    { "a": "EURUSD", "b": "AUDUSD", "coefficient": 0.7 }
  ]
}
```

---

## Setup

### ubuntu-ai (Ubuntu)

**Prerequisites:**
- Python 3.12
- Ollama with models: `qwen3-14b-8k`, `deepseek-r1-14b-8k`, `qwen3-14b-nothink-8k`, `qwen25-14b-8k`
- Tailscale

**Install:**
```bash
cd ~/trading_system
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn crewai httpx
```

**Ollama model setup:**
```bash
# Create 8k context versions
cat > /tmp/modelfile.txt << 'EOF'
FROM qwen3:14b
PARAMETER num_ctx 8192
EOF
ollama create qwen3-14b-8k -f /tmp/modelfile.txt
# Repeat for other models
```

**Run:**
```bash
# As a systemd service
sudo systemctl enable richfx-api
sudo systemctl start richfx-api

# Or directly
source venv/bin/activate
uvicorn core.crew_api:app --host 0.0.0.0 --port 8000
```

### Win11 VM

**Prerequisites:**
- Python 3.12
- MetaTrader5 Python package
- MT5 terminal installed and logged in

**Run:**
```bash
# MT5 Bridge (continuous loop)
python core\mt5_bridge.py --loop

# VM Health Server
python core\vm_health.py

# Telegram Alerter
python core\telegram_alerter.py
```

All three should be configured as WinSW services for auto-start on boot.

---

## Dashboard

The trading floor dashboard is a single HTML file served via FastAPI.

**Access:**
- Local: `http://localhost:8000/ui/richfx_trading_floor.html`
- Tailscale: `http://100.127.251.110:8000/ui/richfx_trading_floor.html`
- Public: `https://crew.richielo.co` (Cloudflare Access — email whitelist required)

**Features:**
- 12 pixel-art agents with walk/stand/action animations
- Live TV screen showing pair data, QQE, sequences, equity, P&L
- UTC clock, market open/closed indicator, weather system
- Night overlay (UTC-aware)
- Agent overlay panels with summary and full LLM output
- Automatic 5-minute data refresh

---

## Sprite Conventions

All character sprites are 112×112px GIFs with transparent backgrounds.

Each agent requires 12 animation files:
```
{agent}_stand_south.gif   {agent}_walk_south.gif
{agent}_stand_north.gif   {agent}_walk_north.gif
{agent}_stand_east.gif    {agent}_walk_east.gif
{agent}_stand_west.gif    {agent}_walk_west.gif
{agent}_cheer.gif
{agent}_cry.gif
{agent}_point.gif
{agent}_anxious.gif
```

Stand GIFs should have frame delay set to 15 (150ms):
```bash
gifsicle --batch --delay 15 {agent}_stand_*.gif
```

---

## Security

- **Tailscale** — all API endpoints accessible only within the Tailscale mesh
- **Cloudflare Access** — public dashboard requires email OTP authentication
- **Read-only dashboard** — no write operations exposed via the public URL
- **Analysis trigger** — `/analyse` is called exclusively by n8n, never by the dashboard

---

## Licence

Private repository — not for public distribution.
