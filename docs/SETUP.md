# RichFX Setup Guide

Complete setup instructions for the RichFX trading system across all machines.

---

## Prerequisites

### Network
- All machines connected via **Tailscale** mesh VPN
- Tailscale IPs:
  - ubuntu-ai (Ubuntu): `100.127.251.110`
  - Win11 VM: `100.80.62.2`
  - NAS LXC (n8n): `100.110.69.69`
  - Raspberry Pi (tunnel): `100.88.68.108`

### Accounts Required
- Cloudflare account (for tunnel and Access)
- Telegram bot token (for Shikigami alerts)
- MetaTrader 5 account (demo or live)

---

## ubuntu-ai (Ubuntu)

### 1. Install Ollama

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

Configure Ollama to listen on all interfaces and keep models warm:

```bash
sudo systemctl edit ollama
```

Add:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_KEEP_ALIVE=60m"
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### 2. Pull and Create Models

```bash
# Pull base models
ollama pull qwen3:14b
ollama pull deepseek-r1:14b
ollama pull qwen2.5:14b
ollama pull qwen3-14b-nothink  # if available, or use qwen3:14b

# Create 8k context versions
cat > /tmp/qwen3_modelfile.txt << 'EOF'
FROM qwen3:14b
PARAMETER num_ctx 8192
EOF
ollama create qwen3-14b-8k -f /tmp/qwen3_modelfile.txt

cat > /tmp/deepseek_modelfile.txt << 'EOF'
FROM deepseek-r1:14b
PARAMETER num_ctx 8192
EOF
ollama create deepseek-r1-14b-8k -f /tmp/deepseek_modelfile.txt

cat > /tmp/qwen25_modelfile.txt << 'EOF'
FROM qwen2.5:14b
PARAMETER num_ctx 8192
EOF
ollama create qwen25-14b-8k -f /tmp/qwen25_modelfile.txt
```

Verify models are available:
```bash
ollama list
```

### 3. Clone Repository and Set Up Python Environment

```bash
git clone <repo-url> ~/trading_system
cd ~/trading_system

python3 -m venv venv
source venv/bin/activate

pip install fastapi uvicorn crewai httpx
```

### 4. Configure SSH Access to Win11 VM

```bash
# Generate SSH key if not already done
ssh-keygen -t ed25519 -C "richfx-bridge"

# Copy public key to VM
ssh-copy-id richi-rdp@100.80.62.2

# Test connection
ssh richi-rdp@100.80.62.2 "echo connected"
```

### 5. Configure Symbols

Edit `config/symbols.json`:
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
    },
    {
      "symbol": "AUDUSD",
      "timeframe": "H4",
      "magic": 100402,
      "account": "demo",
      "active": true,
      "label": "Australian Dollar / US Dollar"
    }
  ]
}
```

### 6. Install systemd Service

```bash
sudo cp systemd/richfx-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable richfx-api
sudo systemctl start richfx-api
sudo systemctl status richfx-api
```

Service file (`/etc/systemd/system/richfx-api.service`):
```ini
[Unit]
Description=RichFX Crew API
After=network-online.target

[Service]
Type=simple
User=richi
WorkingDirectory=/home/richi/trading_system
ExecStart=/home/richi/trading_system/venv/bin/uvicorn core.crew_api:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 7. Pre-warm Models

After service starts, warm up all four models:
```bash
ollama run qwen3-14b-8k "ready" --keepalive 60m &
ollama run deepseek-r1-14b-8k "ready" --keepalive 60m &
ollama run qwen3-14b-nothink-8k "ready" --keepalive 60m &
ollama run qwen25-14b-8k "ready" --keepalive 60m &
wait
ollama ps  # verify all four loaded
```

### 8. Verify Installation

```bash
# Health check
curl http://localhost:8000/health

# Symbols
curl http://localhost:8000/symbols

# State (requires VM to be running)
curl "http://localhost:8000/state?symbol=EURUSD&timeframe=H4"

# Dashboard
curl -I http://localhost:8000/ui/richfx_trading_floor.html
```

---

## Win11 VM

### 1. Install Python 3.12

Download from [python.org](https://python.org) and install with "Add to PATH" checked.

### 2. Install Dependencies

```cmd
pip install MetaTrader5 pandas numpy requests
```

### 3. Configure MT5

- Install MetaTrader 5
- Log in to your broker account (demo or live)
- Enable algorithmic trading: Tools → Options → Expert Advisors → Allow algorithmic trading
- Ensure the EA (`QQE_DCA.mq5`) is running on the relevant charts

### 4. Configure File Paths

In `core/mt5_bridge.py`, verify these paths match your installation:
```python
WATCHLIST_FILE = Path(r"C:\__RichStuff\FX\trading_system\core\watchlist.json")
STATE_DIR      = Path(r"C:\__RichStuff\FX\trading_system\data\signals")
```

In `core/vm_health.py`, verify:
```python
HISTORY_FILE = r"C:\__RichStuff\FX\trading_system\data\signals\history.json"
```

### 5. Configure Watchlist

Edit `core/watchlist.json`:
```json
{
  "pairs": [
    { "symbol": "EURUSD", "timeframe": "H4", "magic": 100401, "active": true },
    { "symbol": "AUDUSD", "timeframe": "H4", "magic": 100402, "active": true }
  ]
}
```

Magic numbers must match your EA's `Magic` input parameter.

### 6. Configure Telegram

In `core/telegram_alerter.py`, set:
```python
TELEGRAM_TOKEN   = "your-bot-token"
TELEGRAM_CHAT_ID = "your-chat-id"
```

**Note:** Never commit these values to git. Use environment variables or a separate secrets file excluded from version control.

### 7. Start Processes

Open three separate PowerShell windows:

```powershell
# Window 1 — MT5 Bridge
python C:\__RichStuff\FX\trading_system\core\mt5_bridge.py --loop

# Window 2 — VM Health Server
python C:\__RichStuff\FX\trading_system\core\vm_health.py

# Window 3 — Telegram Alerter
python C:\__RichStuff\FX\trading_system\core\telegram_alerter.py
```

Or start as hidden background processes:
```powershell
Start-Process python -ArgumentList "C:\__RichStuff\FX\trading_system\core\mt5_bridge.py --loop" -WindowStyle Hidden
Start-Process python -ArgumentList "C:\__RichStuff\FX\trading_system\core\vm_health.py" -WindowStyle Hidden
Start-Process python -ArgumentList "C:\__RichStuff\FX\trading_system\core\telegram_alerter.py" -WindowStyle Hidden
```

**Note:** These do not survive a VM reboot. See WinSW section below for persistent services.

### 8. Verify VM Installation

From the ubuntu-ai:
```bash
# Health check
curl http://100.80.62.2:8765/health

# History endpoint
curl "http://100.80.62.2:8765/history?magic=100401&days=30"

# State files exist
ssh richi-rdp@100.80.62.2 "dir C:\__RichStuff\FX\trading_system\data\signals\"
```

### 9. WinSW Services (Recommended)

For processes that survive reboots, install [WinSW](https://github.com/winsw/winsw).

Example service config (`vm_health_svc.xml`):
```xml
<service>
  <id>richfx-vm-health</id>
  <name>RichFX VM Health Server</name>
  <description>HTTP health and history server for RichFX</description>
  <executable>python</executable>
  <arguments>C:\__RichStuff\FX\trading_system\core\vm_health.py</arguments>
  <logmode>rotate</logmode>
</service>
```

Install:
```cmd
vm_health_svc.exe install
vm_health_svc.exe start
```

---

## n8n (NAS LXC)

### 1. Install n8n via Docker

```bash
docker run -d \
  --name n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  n8nio/n8n
```

### 2. Create EURUSD Workflow

Create a workflow with:

1. **Cron trigger** — every 5 minutes (or use a custom H4 bar detection)
2. **HTTP Request** — `GET http://100.80.62.2:8765/health` (preflight VM check)
3. **IF node** — check `status === "ok"`
4. **HTTP Request** — `POST http://100.127.251.110:8000/analyse`
   - Body: `{"symbol": "EURUSD", "timeframe": "H4", "magic": 100401}`
5. **Telegram node** — send analysis result summary

Duplicate the workflow for AUDUSD with `magic: 100402`.

### 3. Preflight Alert Workflow

Separate workflow that runs every 5 minutes:
1. HTTP Request to `/health` on ubuntu-ai
2. HTTP Request to VM `/health`
3. IF either fails → Telegram alert via Shikigami

---

## Cloudflare Tunnel

### 1. Install cloudflared on Raspberry Pi

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
```

### 2. Authenticate

```bash
cloudflared tunnel login
```

### 3. Create Tunnel

```bash
cloudflared tunnel create RICH-TUNNEL
```

### 4. Configure `/etc/cloudflared/config.yml`

```yaml
tunnel: RICH-TUNNEL
credentials-file: /home/richi/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: crew.richielo.co
    service: http://100.127.251.110:8000
    originRequest:
      connectTimeout: 30s
  - service: http_status:404
```

**Important:** Use only ASCII characters in comments. UTF-8 decorative characters (em-dashes etc.) cause cloudflared to silently drop ingress rules.

### 5. Install as Service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### 6. Configure Cloudflare Access

In Cloudflare Zero Trust dashboard:
1. Access → Applications → Add application → Self-hosted
2. Application domain: `crew.richielo.co`
3. Session duration: 24 hours
4. Policy: Allow — Emails — `your@email.com`

---

## Verification Checklist

After full setup, verify each component:

```bash
# From ubuntu-ai:

# 1. Ollama running with models loaded
ollama ps

# 2. API service running
sudo systemctl status richfx-api
curl http://localhost:8000/health

# 3. VM reachable
curl http://100.80.62.2:8765/health

# 4. State files being written
ssh richi-rdp@100.80.62.2 "dir C:\__RichStuff\FX\trading_system\data\signals\"

# 5. History endpoint returning data
curl "http://100.80.62.2:8765/history?magic=0&days=30"

# 6. Full analysis chain
curl -s --max-time 600 -X POST http://localhost:8000/analyse \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"EURUSD","timeframe":"H4","magic":100401}' \
  | python3 -m json.tool | grep '"summary"'

# 7. Dashboard accessible locally
curl -I http://localhost:8000/ui/richfx_trading_floor.html

# 8. Dashboard accessible publicly (requires Cloudflare Access login)
curl -I https://crew.richielo.co/ui/richfx_trading_floor.html
```

---

## Troubleshooting

### Analysis times out
- Check if another analysis is running: `sudo journalctl -u richfx-api -f`
- The single-worker executor processes one analysis at a time
- Wait for the current run to complete before retrying

### Agents greyed out after service restart
- Expected — `_last_analysis` cache is in-memory
- Wait for next n8n bar trigger to repopulate
- Or manually trigger: `curl -X POST http://localhost:8000/analyse ...`

### VM health 503
- Check MT5 bridge is running: `tasklist | findstr python` (from SSH)
- Check state files exist and are recent
- Verify SSH connectivity from ubuntu-ai to VM

### Cloudflare tunnel not routing new hostname
- Check for UTF-8 characters in `config.yml` comments: `cat -A /etc/cloudflared/config.yml`
- Remove decorative characters: `sudo sed -i 's/[─━]\+//g' /etc/cloudflared/config.yml`
- If using Zero Trust dashboard, add hostname there — local config may be overridden by remote

### Models returning empty responses
- Do not use `qwen3-14b-nothink` for CrewAI agents — incompatible with ReAct loop
- Use `qwen3-14b-8k` for Strategy Evaluator instead
- Verify `ollama ps` shows all four models loaded

### TabError on service start
```bash
sed -i 's/\t/    /g' ~/trading_system/core/crew_api.py
python3 -m py_compile ~/trading_system/core/crew_api.py && echo "OK"
sudo systemctl restart richfx-api
```
