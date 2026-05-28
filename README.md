# RichFX Trading Floor

An AI-powered algorithmic trading monitoring system built around a 12-agent CrewAI crew, a live MT5 bridge, and a pixel-art trading floor dashboard. Agents analyse H4 signals in a sequential chain, advisory agents monitor risk conditions, and a centralised SQLite database accumulates decision history for pattern analysis.

> 🔴 **Live demo:** [crew.richielo.co](https://crew.richielo.co) *(Cloudflare Access — request access via the repo)*

---

![RichFX Trading Floor](trading_floor_demo.gif)

## Hardware

All machines live in a **GeeekPi DeskPi RackMate Server T2 Cabinet**, with the trading floor dashboard displayed on a **GeeekPi 7.84" 1280×400 LCD Touchscreen** mounted in the rack — perfectly matching the canvas resolution of the dashboard.

| Machine | Device | RAM | Role |
|---------|--------|-----|------|
| **ubuntu-ai** | Minisforum MS-S1 MAX | 128GB unified | Ollama LLM inference, FastAPI crew API, dashboard |
| **NAS** | Minisforum AI N5 Pro | 96GB | n8n automation, Win11 VM host |
| **Win11 VM** | LXC on NAS | 8GB | MT5 terminal, bridge, health server, Telegram alerter |
| **dev-pc** | Minisforum AI X1 470 Pro | 128GB | EA development and MQL5 testing |
| **RPi4** | Raspberry Pi 4B| 8GB | Cloudflare tunnel → crew.richielo.co |

All machines connected via **Tailscale** mesh VPN.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  ubuntu-ai — Minisforum MS-S1 MAX (128GB)                       │
│  ├── Ollama (LLM inference)                 :11434              │
│  │     qwen3-14b-8k, deepseek-r1-14b-8k                         │
│  │     qwen25-14b-8k, gemma4-e4b-8k (~50GB loaded)              │
│  ├── FastAPI Crew API                       :8000               │
│  └── Trading Floor Dashboard               /ui/                 │
├─────────────────────────────────────────────────────────────────┤
│  NAS — Minisforum AI N5 Pro (96GB)                              │
│  ├── n8n Docker                             :5678               │
│  └── Win11 VM (LXC)                                             │
│        ├── MT5 Terminal + QQE_DCA EA                            │
│        ├── mt5_bridge.py    (state + DB writer)                 │
│        ├── vm_health.py     :8765                               │
│        ├── telegram_alerter.py                                  │
│        └── richfx.db        (centralised SQLite)                │
├─────────────────────────────────────────────────────────────────┤
│  Raspberry Pi                                                   │
│  └── Cloudflare Tunnel → crew.richielo.co                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agent Roster

The trading floor has 12 agents across two phases:

### Phase 1 — Core LLM Chain (runs every H4 bar)

| Agent | Role | Model |
|-------|------|-------|
| **Regime Analyst** | Classifies market regime — RANGING / TRENDING / MIXED | qwen3-14b-8k |
| **Risk Governor** | Enforces hard risk rules — spread, drawdown, sequence limits | deepseek-r1-14b-8k |
| **Strategy Evaluator** | Scores signal quality 1-10, returns PROCEED or HOLD | qwen3-14b-8k |
| **Execution Coordinator** | Produces final action — open/add/close/no_action | qwen25-14b-8k |

### Phase 2 — Advisory Agents

| Agent | Role | Status |
|-------|------|--------|
| **Session** | Flags poor liquidity windows (off-peak hours) | ✅ Active |
| **Drawdown** | Monitors equity drawdown — warns at 2%, halts at 5% | ✅ Active |
| **Correlation** | Detects correlated USD exposure across pairs | ✅ Active |
| **News Watch** | ForexFactory calendar — blocks before high-impact events | ✅ Active |
| **Performance** | Tracks win rate and P&L from closed trade history | ✅ Active |
| **Backtest Scout** | Compares signals to 500-bar historical pattern database | ✅ Active |
| **Journalist** | Plain-English post-mortems on completed sequences | ✅ Active |
| **Meta-Supervisor** | Reviews crew decisions for systematic bias (needs 10+ bars) | ⏳ Accumulating |

---

## The Trading Floor

![RichFX Trading Floor Demo](trading_floor_demo.gif)

## Agent Cast

| <sub>Regime Analyst</sub> | <sub>Risk Governor</sub> | <sub>Exec Coordinator</sub> | <sub>Strategy</sub> |
|:---:|:---:|:---:|:---:|
| ![](sprites/characters/regime_stand_south.gif) | ![](sprites/characters/risk_stand_south.gif) | ![](sprites/characters/exec_stand_south.gif) | ![](sprites/characters/strat_stand_south.gif) |

| <sub>Performance</sub> | <sub>Session</sub> | <sub>Correlation</sub> | <sub>Drawdown</sub> |
|:---:|:---:|:---:|:---:|
| ![](sprites/characters/perf_stand_south.gif) | ![](sprites/characters/sess_stand_south.gif) | ![](sprites/characters/corr_stand_south.gif) | ![](sprites/characters/dd_stand_south.gif) |

| <sub>News Watch</sub> | <sub>Journalist</sub> | <sub>Bktest Scout</sub> | <sub>Meta-Super</sub> |
|:---:|:---:|:---:|:---:|
| ![](sprites/characters/news_stand_south.gif) | ![](sprites/characters/journ_stand_south.gif) | ![](sprites/characters/scout_stand_south.gif) | ![](sprites/characters/meta_stand_south.gif) |

---

## API Endpoints

All served by FastAPI on port 8000.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check |
| `/symbols` | GET | Active trading pairs from config |
| `/state` | GET | Current market state for a symbol (5 min cache) |
| `/analyse` | POST | Run full crew analysis (n8n only — never dashboard) |
| `/last-analysis` | GET | Most recent cached crew result (persisted to disk) |
| `/history` | GET | Closed trade history from MT5 |
| `/performance` | GET | Aggregated performance stats by symbol/account |
| `/bars` | GET | Historical bar data with signals from SQLite |
| `/decisions` | GET | Crew decision log from SQLite |
| `/sequences` | GET | Completed sequence records from SQLite |
| `/journalist` | GET | Recent sequence narratives |
| `/scout` | GET | Run Backtest Scout analysis |
| `/meta` | GET | Run Meta-Supervisor analysis |
| `/animate` | POST | Queue alt gesture for a female agent |
| `/pending-animation` | GET | Dashboard polls for queued animations |

---

## Data Flow

```
MT5 EA (Win11 VM) — runs independently, manages trades
    │
    └── mt5_bridge.py (every H4 bar)
            ├── writes state_EURUSD_H4.json
            ├── writes state_AUDUSD_H4.json
            ├── writes history.json (all closed deals)
            ├── seeds richfx.db bars table (500 bars + backfill)
            ├── appends new bar on each close
            └── seeds bars for monitoring-only pairs (magic=0, no EA)
                GBPUSD, USDJPY, EURGBP, NZDUSD, USDCAD, GBPJPY, EURUSD H1

n8n (NAS) — sole trigger for analysis, fires on H4 bar schedule
    │
    └── POST /analyse → crew_api.py (ubuntu-ai)
            ├── SSH fetch state JSON from VM
            ├── 4-agent LLM chain (Ollama, ~4 min)
            ├── pure-Python checks (sess, dd, corr, news)
            ├── cache result in memory + persist to disk
            ├── write decision to richfx.db via SSH
            ├── detect sequence close → trigger Journalist
            └── send Telegram alert (Shikigami)

Dashboard (browser) — read-only, never triggers /analyse
    ├── GET /state           (market data, TV screen)
    ├── GET /last-analysis   (agent states, instant from cache)
    ├── GET /performance     (closed trade stats)
    ├── GET /scout           (pattern confidence score)
    ├── GET /journalist      (sequence narratives)
    └── GET /pending-animation (alt gesture queue, polls 3s)
```

---

## Database Schema (richfx.db on Win11 VM)

```sql
bars        — OHLCV + QQE/MACD signals, all pairs/timeframes (500 bars seeded)
decisions   — crew decision per bar (regime, risk, strategy, action, spread)
sequences   — completed sequence records with Journalist narratives
```

Adding a new pair to `symbols.json` automatically populates all three tables — no schema changes required.

---

## Configuration

### `config/symbols.json`
Defines active trading pairs. Add an entry and restart to activate.

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
Correlation matrix for the Correlation agent. Reloaded on every analysis — no restart required.

### `.env` (not committed)
```
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Dashboard Features

**Access:**
- Local: `http://localhost:8000`
- Tailscale: `http://<ubuntu-ai-tailscale-ip>:8000`
- Public: `https://crew.richielo.co` *(Cloudflare Access — email OTP)*

**Features:**
- 12 pixel-art agents with walk/stand/action/cheer/cry/anxious animations
- TV screen: QQE, sequence P&L, open P&L, closed P&L, equity, margin
- TV screen auto-rotates across active pairs every 30 seconds (pauses on manual click)
- UTC analogue clock, market open/closed indicator
- Weather system (sun/cloud/rain), night overlay (UTC-aware)
- Agent overlay panels with LLM summary and full output
- Alt gesture animation — female agents zoom from bottom on request
- Analysis cache persisted to disk — agents active instantly after restart
- Automatic 5-minute data refresh (n8n owns analysis trigger)

---

## Sprite Conventions

All character sprites are 112×112px GIFs with transparent backgrounds.

Each agent requires these animation files:
```
{agent}_stand_south/north/east/west.gif
{agent}_walk_south/north/east/west.gif
{agent}_cheer.gif   {agent}_cry.gif
{agent}_point.gif   {agent}_anxious.gif
```

---

## Security

- **Tailscale** — all API endpoints accessible only within the mesh
- **Cloudflare Access** — public dashboard requires email OTP
- **Read-only dashboard** — no write operations via public URL
- **n8n-only analysis** — `/analyse` never called by the dashboard
- **Credentials in `.env`** — never committed to repo

---

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture, data flow, security model
- [AGENTS.md](docs/AGENTS.md) — per-agent documentation, models, output formats
- [ROADMAP.md](docs/ROADMAP.md) — completed work, pending items, architecture decisions
- [SETUP.md](docs/SETUP.md) — installation guide for all machines

---

## Licence

MIT
