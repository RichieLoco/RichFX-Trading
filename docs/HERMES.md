# Hermes — RichFX Conversational Portfolio Assistant

Hermes is a CrewAI agent that answers natural language questions about the RichFX
trading portfolio via the Shikigami Telegram bot. Send any message to the bot and
Hermes fetches the relevant live data and responds in plain text.

---

## Architecture

```
You (Telegram)
    │ inbound message
    ▼
telegram_alerter.py
    ├── UID check — only TELEGRAM_ALLOWED_UID is permitted
    ├── Send "Hermes is thinking..." acknowledgement
    └── POST /ask → crew_api.py
            │
            ▼
        hermes_agent.py — CrewAI Agent (qwen3-14b-8k)
            │  ReAct loop: think → call tool → observe → answer
            │
            ├── get_live_portfolio()   → GET /live
            ├── get_last_analysis()    → GET /last-analysis
            ├── get_performance()      → GET /performance
            ├── get_horizon()          → GET /horizon/last
            ├── get_signal_state()     → GET /state
            ├── trigger_analysis()     → POST /analyse  (~4 min)
            ├── trigger_horizon()      → GET /horizon   (~60s)
            └── trigger_scout()        → GET /scout     (~30s)
            │
            ▼
        Plain-text answer → crew_api.py → telegram_alerter.py
    │
    ▼
Shikigami bot reply
```

---

## Configuration

All credentials live in `.env` — never in source code.

| Key | Where needed | Description |
|-----|-------------|-------------|
| `TELEGRAM_TOKEN` | Both machines | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Win11 VM | Outbound alert target |
| `TELEGRAM_ALLOWED_UID` | Win11 VM | Your Telegram user ID — locks Hermes to you |
| `CREW_API_URL` | Win11 VM | URL of crew API on ubuntu-ai |
| `OLLAMA_BASE_URL` | ubuntu-ai | Ollama inference server |

Get `TELEGRAM_ALLOWED_UID` from @userinfobot in Telegram (the "Id" field).

---

## Tools

| Tool | Data source | What it returns |
|------|-------------|----------------|
| `get_live_portfolio` | `/live` | Balance, equity, open P&L, all open positions with per-position P&L |
| `get_last_analysis` | `/last-analysis` | Latest regime, risk, strategy, execution, session, drawdown, news, HTF, volatility |
| `get_performance` | `/performance` | Win rate, net P&L, avg win/loss — last 30 days, per symbol |
| `get_horizon` | `/horizon/last` | Cross-pair signal rankings and narrative |
| `get_signal_state` | `/state` | QQE value, OB/OS status, QMP trend, buy/sell signals, MACD, spread |
| `trigger_analysis` | `POST /analyse` | Runs full 4-agent crew chain — takes ~4 minutes |
| `trigger_horizon` | `GET /horizon` | Fresh Horizon cross-pair scan — takes ~60 seconds |
| `trigger_scout` | `GET /scout` | Pattern confidence score from 500-bar historical database |

---

## Behaviour

**Model:** `qwen3-14b-8k` — same model as Regime Analyst and Strategy Evaluator.
Already resident in the MS-S1 MAX's 128GB unified RAM pool.

**Tool selection:** Hermes uses a ReAct loop — it reasons about which tools to call
based on your question rather than calling all tools every time. A question about
P&L triggers `get_live_portfolio`. A question about news triggers `get_last_analysis`.
A question about pair rankings triggers `get_horizon`.

**Max tool calls:** 6 per query. Sufficient for multi-step questions
("what is my P&L and what does the crew think about EURUSD?" = 2 tools).

**Conversation memory:** Rolling window of the last 5 turns (10 messages).
Follow-up questions work naturally:
- "What is my AUDUSD P&L?" → Hermes fetches live positions
- "And how has it been performing this month?" → Hermes knows you mean AUDUSD

Memory resets when `telegram_alerter.py` restarts.

**Tool failures:** If a tool returns an error (VM offline, API timeout), Hermes
states which tool was unavailable and answers with whatever data it could fetch.
It never silently ignores failures.

**Concurrency:** Hermes runs on its own `ThreadPoolExecutor` and lock, separate
from the main analysis chain. Queries run concurrently with H4 analysis — they
do not block each other. If analysis is running when a query arrives, Hermes
notes this in its reply (e.g. "analysis for EURUSD was running for 47s when
your query arrived — some data may be from the previous bar") but still answers.

**Response time:** Typically 10-30 seconds for data-only queries. Longer if
Hermes calls `trigger_analysis` (~4 min) or `trigger_horizon` (~60s).

---

## Example Queries

**Portfolio state:**
- "What is my open P&L?"
- "What positions do I have open?"
- "How much margin am I using?"
- "What is my account equity?"

**Signal and analysis:**
- "What is the QQE on EURUSD right now?"
- "What did the crew recommend on the last bar?"
- "Is AUDUSD overbought or oversold?"
- "What is the market regime on EURUSD?"

**News and risk:**
- "Are there any upcoming news events that could affect EURUSD?"
- "What is the volatility like right now?"
- "Is the drawdown within limits?"
- "Any correlation warnings?"

**Performance:**
- "What is my win rate this month?"
- "How much have I made on EURUSD in the last 30 days?"
- "How is the system performing overall?"

**Pair expansion:**
- "Which pairs does Horizon rank highest?"
- "What pairs should I consider adding to the EA?"

**Actions:**
- "Run analysis on EURUSD"
- "Run a fresh Horizon scan"
- "What is the pattern confidence on AUDUSD?"

---

## Security

Hermes only responds to messages from the Telegram user ID set in
`TELEGRAM_ALLOWED_UID`. Messages from any other user ID are silently ignored
and logged to the alerter console. There is no way to authenticate as another
user — the check is against the Telegram-assigned numeric user ID, not a username.

No credentials are stored in source code. All sensitive values live in `.env`
which is gitignored.

---

## Files

| File | Machine | Purpose |
|------|---------|---------|
| `core/hermes_agent.py` | ubuntu-ai | CrewAI agent + all tool definitions |
| `core/crew_api.py` | ubuntu-ai | `POST /ask` endpoint, concurrency management |
| `win11-vm/core/telegram_alerter.py` | Win11 VM | Inbound polling, UID check, `hermes_ask()` relay |
| `.env` | Both | Credentials and config (gitignored) |
| `.env.example` | Both (repo) | Template — copy to `.env` and fill in values |

---

## Deployment

```bash
# ubuntu-ai
cp core/hermes_agent.py ~/trading_system/core/
cp core/crew_api.py ~/trading_system/core/
sudo systemctl restart richfx-api

# Win11 VM (from ubuntu-ai)
scp win11-vm/core/telegram_alerter.py \
    "richi-rdp@<vm-ip>:C:/__RichStuff/FX/trading_system/core/telegram_alerter.py"
ssh richi-rdp@<vm-ip> "powershell -Command \"Restart-Service RichFX-Alerter\""

# Verify .env exists on both machines
cat ~/trading_system/.env
ssh richi-rdp@<vm-ip> \
    "powershell -Command \"Get-Content 'C:\\__RichStuff\\FX\\trading_system\\.env'\""
```

---

## Troubleshooting

### Hermes not responding to Telegram messages

**Check for conflicting bot pollers.** Telegram's `getUpdates` API only delivers each message to one poller. If another process is consuming updates, Shikigami never sees them.

Common culprits:

1. **hermes-gateway systemd service** — Claude Code ships a Telegram plugin that runs as a user service. If it was ever activated with your bot token it will intercept all messages.
   ```bash
   # Check if running
   systemctl --user status hermes-gateway
   # Disable permanently
   systemctl --user stop hermes-gateway
   systemctl --user disable hermes-gateway
   ```

2. **Stale bot polling process** — a previous run left a zombie process holding the token.
   ```bash
   # Find it
   ps aux | grep -i "hermes\|telegram_alerter" | grep -v grep
   # Kill strays — keep only the WinSW-managed one
   ```

3. **Invalid or revoked bot token** — if the token was exposed and revoked, all polling silently fails.
   ```bash
   TOKEN=$(grep TELEGRAM_TOKEN ~/trading_system/.env | cut -d= -f2)
   curl -s "https://api.telegram.org/bot${TOKEN}/getMe" | python3 -m json.tool | grep ok
   # Should return "ok": true
   ```

4. **Multiple `telegram_alerter.py` processes** — crash-loop restarts can leave orphan processes all racing for messages. Stop the WinSW service, kill all python processes running the alerter, then start cleanly.

### Hermes answers from its own knowledge instead of calling tools

This is a model behaviour issue with `qwen3-14b-8k`. The task description in `run_hermes_query()` explicitly instructs the agent to call tools first — if it regresses, check that `hermes_agent.py` contains the `IMPORTANT: You MUST call at least one tool` instruction.

### Hermes asks for a symbol on general questions

`get_last_analysis` defaults to `symbol="ALL"` which fetches both EURUSD and AUDUSD. If Hermes is asking you to specify a pair, the agent may be using a cached version of `hermes_agent.py` — restart the API: `sudo systemctl restart richfx-api`.

---

## Roadmap

- **Session persistence** — save conversation history to SQLite so memory survives restarts
- **Proactive alerts** — Hermes pushes unsolicited insights when conditions change significantly
- **Voice of the floor** — Hermes narrates what the trading floor agents are seeing, unprompted, at key moments (new bar, sequence open/close, drawdown threshold)
