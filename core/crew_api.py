from typing import Optional
"""
RichFX Crew API
===============
FastAPI wrapper around richfx_crew.py.
Exposes the CrewAI trading crew as an HTTP endpoint
so n8n can trigger it on a schedule.

Run with:
    source ~/trading_system/venv/bin/activate
    uvicorn crew_api:app --host 0.0.0.0 --port 8000
"""

import subprocess
import json
import os
import sys
import time
import asyncio
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.getenv("BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPRITES_DIR = os.path.join(BASE_DIR, "sprites")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "symbols.json")


load_dotenv(os.path.join(BASE_DIR, ".env"))

VM_HOST       = os.getenv("VM_HOST", "100.80.62.2")
VM_USER       = os.getenv("VM_USER", "richi-rdp")
VM_BASE_PATH  = os.getenv("VM_BASE_PATH", "C:/__RichStuff/FX")
VM_DB_WRITE_PATH = os.getenv("VM_DB_WRITE_PATH", "C:/Users/richi-rdp/db_write.py")
VM_HEALTH_URL = f"http://{VM_HOST}:8765"
STATE_DIR     = f"{VM_BASE_PATH}/trading_system/data/signals"

# Fallback if config file is missing — change to False when going live
ACCOUNT_IS_DEMO_DEFAULT = True

# Cache for last crew analysis result — keyed by symbol
_last_analysis: dict = {}

_last_horizon: dict = {}

_prev_positions: dict = {}  # {symbol_magic: set of ticket IDs} for sequence close detection

_calendar_cache: dict = {"data": None, "fetched_at": 0}

_animation_queue: list = []
FEMALE_AGENTS = {"exec", "corr", "perf", "journ", "meta", "news", "hori", "sqadv"}

_calendar_cache: dict = {"data": None, "fetched_at": 0}

_animation_queue: list = []

# ── Analysis cache persistence ────────────────────────────────────────────────
CACHE_FILE = os.path.join(BASE_DIR, "data", "last_analysis_cache.json")

def save_analysis_cache():
    """Persist _last_analysis to disk after every successful analysis."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(_last_analysis, f, default=str)
    except Exception as e:
        print(f"[Cache] Save failed: {e}", flush=True)

def load_analysis_cache():
    """Load _last_analysis from disk on startup."""
    global _last_analysis
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE) as f:
                _last_analysis = json.load(f)
            print(f"[Cache] Loaded {len(_last_analysis)} symbols from disk", flush=True)
    except Exception as e:
        print(f"[Cache] Load failed: {e}", flush=True)


# ── Horizon cache persistence ────────────────────────────────────────────────
HORIZON_CACHE_FILE = os.path.join(BASE_DIR, "data", "last_horizon_cache.json")

def save_horizon_cache():
    try:
        with open(HORIZON_CACHE_FILE, "w") as f:
            json.dump(_last_horizon, f, default=str)
    except Exception as e:
        print(f"[Horizon] Cache save failed: {e}", flush=True)

def load_horizon_cache():
    global _last_horizon
    try:
        if os.path.exists(HORIZON_CACHE_FILE):
            with open(HORIZON_CACHE_FILE) as f:
                _last_horizon = json.load(f)
            print(f"[Horizon] Cache loaded from disk", flush=True)
    except Exception as e:
        print(f"[Horizon] Cache load failed: {e}", flush=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="RichFX Crew API", version="1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/sprites", StaticFiles(directory=SPRITES_DIR), name="sprites")
app.mount("/ui", StaticFiles(directory=BASE_DIR), name="ui")  

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class AnalyseRequest(BaseModel):
    symbol:    str = "EURUSD"
    timeframe: str = "H4"
    magic:     int = 100401

class AgentResult(BaseModel):
    agent:   str
    summary: str
    raw:     str
        
class SessionResult(BaseModel):
    status:  str   # SESSION_OK | SESSION_WARN
    session: str   # e.g. "London/NY Overlap"
    quality: str   # HIGH | GOOD | LOW
    summary: str

class DrawdownResult(BaseModel):
    status:       str    # DD_OK | DD_WARN | DD_HALT
    drawdown_pct: float
    summary:      str

class CorrelationResult(BaseModel):
    status:   str        # CORR_OK | CORR_WARN
    conflicts: list[str] # e.g. ["AUDUSD also has BUY open (corr=0.7)"]
    summary:  str

class NewsResult(BaseModel):
    status:   str        # NEWS_OK | NEWS_WARN | NEWS_BLOCK
    events:   list[str]  # e.g. ["NFP in 2h (HIGH)", "FOMC in 4h (HIGH)"]
    summary:  str
    window_hours: int    # how many hours ahead we're checking

class AnalyseResponse(BaseModel):
    symbol:        str
    timeframe:     str
    bar_time:      str
    generated_at:  str
    qqe_value:     float
    qmp_trend:     int
    spread:        float
    regime:        AgentResult
    risk:          AgentResult
    strategy:      AgentResult
    execution:     AgentResult
    telegram_sent: bool
    session:       SessionResult
    drawdown:      DrawdownResult
    correlation:   CorrelationResult
    news:          NewsResult
    timeframe_alignment: Optional[dict] = None
    volatility:          Optional[dict] = None

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_symbols_config() -> list:
    """Load active symbols from config/symbols.json."""
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        return [s for s in config.get("symbols", []) if s.get("active", True)]
    except Exception:
        return []

def get_symbol_is_demo(symbol: str) -> bool:
    """Return True if the given symbol is configured on a demo account."""
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        for s in config.get("symbols", []):
            if s["symbol"] == symbol:
                return s.get("account", "demo") == "demo"
    except Exception:
        pass
    return ACCOUNT_IS_DEMO_DEFAULT

CORRELATIONS_PATH = os.path.join(BASE_DIR, "config", "correlations.json")

def load_correlations() -> dict:
    """
    Load correlation matrix from config/correlations.json.
    Returns dict keyed by frozenset pair for easy lookup.
    Read fresh on each call — no restart needed when file changes.
    """
    try:
        with open(CORRELATIONS_PATH) as f:
            data = json.load(f)
        return {
            (entry["a"], entry["b"]): entry["coefficient"]
            for entry in data.get("pairs", [])
        }
    except Exception:
        return {}  # safe fallback — correlation check skipped if file missing

def check_session(symbol: str) -> SessionResult:
    """Pure logic session quality check — no LLM needed."""
    h = datetime.now(timezone.utc).hour
    if   13 <= h < 17: quality, session = 'HIGH', 'London/NY Overlap'
    elif  8 <= h < 13: quality, session = 'GOOD', 'London'
    elif 17 <= h < 22: quality, session = 'GOOD', 'New York'
    elif 22 <= h or h < 5: quality, session = 'LOW', 'Off-peak'
    else:              quality, session = 'LOW',  'Pre-London'
    return SessionResult(
        status  = 'SESSION_OK' if quality in ('HIGH', 'GOOD') else 'SESSION_WARN',
        session = session,
        quality = quality,
        summary = f"{session} ({quality})",
    )


def check_drawdown(account: dict,
                   warn_pct: float = 0.02,
                   halt_pct: float = 0.05) -> DrawdownResult:
    """Pure arithmetic drawdown check — no LLM needed."""
    balance = account.get('balance', 0)
    equity  = account.get('equity',  0)
    dd_pct  = max(0.0, (balance - equity) / balance) if balance > 0 else 0.0
    if   dd_pct >= halt_pct: status = 'DD_HALT'
    elif dd_pct >= warn_pct: status = 'DD_WARN'
    else:                    status = 'DD_OK'
    return DrawdownResult(
        status       = status,
        drawdown_pct = round(dd_pct * 100, 2),
        summary      = f"DD {dd_pct:.2%} — {status}",
    )

def check_correlation(
    current_symbol:     str,
    proposed_direction: str,
    other_states:       dict,
) -> CorrelationResult:
    if not proposed_direction or proposed_direction in ('NO ACT', '--', 'FLAT'):
        return CorrelationResult(
            status    = 'CORR_OK',
            conflicts = [],
            summary   = 'No new position — correlation not applicable',
        )

    correlation_pairs = load_correlations()  # ← reads from file, not hardcoded
    conflicts = []

    for (sym_a, sym_b), coef in correlation_pairs.items():
        other_sym = None
        if   current_symbol == sym_a: other_sym = sym_b
        elif current_symbol == sym_b: other_sym = sym_a
        else: continue

        if other_sym not in other_states:
            continue

        positions = other_states[other_sym].get('positions', [])
        if not positions:
            continue

        buys      = sum(1 for p in positions if p.get('type') == 'buy')
        sells     = sum(1 for p in positions if p.get('type') == 'sell')
        other_dir = 'BUY' if buys >= sells else 'SELL'
        usd_base  = current_symbol.startswith('USD') or other_sym.startswith('USD')
        correlated = (
            (not usd_base and other_dir == proposed_direction) or
            (usd_base     and other_dir != proposed_direction)
        )
        if correlated:
            pnl = sum(p.get('profit', 0) for p in positions)
            conflicts.append(
                f"{other_sym} has {len(positions)}x {other_dir} open "
                f"(P&L ${pnl:+.2f}, corr={coef})"
            )

    if conflicts:
        return CorrelationResult(
            status    = 'CORR_WARN',
            conflicts = conflicts,
            summary   = 'Correlated: ' + ' | '.join(conflicts),
        )
    return CorrelationResult(
        status    = 'CORR_OK',
        conflicts = [],
        summary   = f'No correlated exposure for {proposed_direction} on {current_symbol}',
    )

def check_news_calendar(symbol: str, window_hours: int = 24) -> NewsResult:
    """
    Check ForexFactory calendar for high-impact events affecting this symbol.
    Extracts the two currency codes from the symbol (e.g. EURUSD → EUR, USD).
    Returns NEWS_BLOCK if a high-impact event is within 2 hours,
            NEWS_WARN  if within window_hours,
            NEWS_OK    otherwise.
    Calendar response is cached for 1 hour to avoid rate limiting.
    """
    import urllib.request
    import time as _time
    from datetime import timedelta

    sym = symbol.upper()
    currencies = {sym[:3], sym[3:6]} if len(sym) >= 6 else set()
    now = datetime.now(timezone.utc)

    # Use cached calendar if fetched within the last hour
    if _calendar_cache["data"] is not None and _time.time() - _calendar_cache["fetched_at"] < 3600:
        raw = _calendar_cache["data"]
    else:
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode())
            _calendar_cache["data"]       = raw
            _calendar_cache["fetched_at"] = _time.time()
        except Exception as e:
            return NewsResult(
                status       = "NEWS_OK",
                events       = [],
                summary      = f"Calendar unavailable ({e}) — proceeding",
                window_hours = window_hours,
            )

    block_events = []
    warn_events  = []

    for item in raw:
        if item.get("impact", "").upper() != "HIGH":
            continue
        if item.get("country", "").upper() not in currencies:
            continue
        try:
            event_time = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
        except Exception:
            continue

        delta      = event_time - now
        hours_away = delta.total_seconds() / 3600

        if hours_away < -1:
            continue

        label = (
            f"{item.get('country','')} {item.get('title','')} in {int(hours_away)}h (HIGH)"
            if hours_away >= 0 else
            f"{item.get('country','')} {item.get('title','')} just released (HIGH)"
        )

        if abs(hours_away) <= 2:
            block_events.append(label)
        elif hours_away <= window_hours:
            warn_events.append(label)

    all_events = block_events + warn_events

    if block_events:
        return NewsResult(
            status       = "NEWS_BLOCK",
            events       = all_events,
            summary      = f"BLOCK — high-impact event within 2h: {block_events[0]}",
            window_hours = window_hours,
        )
    elif warn_events:
        return NewsResult(
            status       = "NEWS_WARN",
            events       = all_events,
            summary      = f"WARN — {len(warn_events)} high-impact event(s) within {window_hours}h",
            window_hours = window_hours,
        )
    return NewsResult(
        status       = "NEWS_OK",
        events       = [],
        summary      = f"No high-impact events for {symbol} in next {window_hours}h",
        window_hours = window_hours,
    )

async def check_timeframe_alignment(symbol: str, timeframe: str, proposed_action: str) -> dict:
    """
    Check higher timeframe trend alignment before entry.
    H1 → checks H4 and H8
    H4 → checks H8
    H8 → no higher TF available
    """
    # Determine trade direction
    action_lower = proposed_action.lower()
    if 'buy' in action_lower:
        direction = 'BUY'
    elif 'sell' in action_lower:
        direction = 'SELL'
    else:
        return {
            "status":  "TFRAME_NA",
            "summary": "No directional trade — alignment check skipped",
            "direction": "NONE",
            "timeframes_checked": [],
        }

    # Higher timeframe map
    tf_map = {"H1": ["H4", "H8"], "H4": ["H8"], "H8": []}
    higher_tfs = tf_map.get(timeframe, [])

    if not higher_tfs:
        return {
            "status":  "TFRAME_NA",
            "summary": f"No higher timeframe configured for {timeframe}",
            "direction": direction,
            "timeframes_checked": [],
        }

    results = []
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=10.0) as client:
        for htf in higher_tfs:
            try:
                resp = await client.get(
                    f"{VM_HEALTH_URL}/bars",
                    params={"symbol": symbol, "timeframe": htf, "limit": 5}
                )
                bars = resp.json().get("bars", [])
                if not bars:
                    results.append({"tf": htf, "alignment": "NO_DATA", "trend": 0, "qqe": None})
                    continue

                latest = bars[0]
                trend  = latest.get("qmp_trend", 0) or 0
                qqe    = latest.get("qqe", 50)    or 50

                if direction == 'BUY':
                    if trend == 1 and qqe > 50:   alignment = "ALIGNED"
                    elif trend == -1 or qqe < 45:  alignment = "OPPOSING"
                    else:                          alignment = "NEUTRAL"
                else:  # SELL
                    if trend == -1 and qqe < 50:  alignment = "ALIGNED"
                    elif trend == 1 or qqe > 55:  alignment = "OPPOSING"
                    else:                         alignment = "NEUTRAL"

                results.append({"tf": htf, "alignment": alignment,
                                 "trend": trend, "qqe": round(qqe, 2)})
            except Exception:
                results.append({"tf": htf, "alignment": "ERROR", "trend": 0, "qqe": None})

    # Overall status — any OPPOSING = block
    alignments = [r["alignment"] for r in results
                  if r["alignment"] not in ("NO_DATA", "ERROR")]

    if not alignments:
        overall = "TFRAME_NA"
    elif "OPPOSING" in alignments:
        overall = "TFRAME_OPPOSING"
    elif all(a == "ALIGNED" for a in alignments):
        overall = "TFRAME_ALIGNED"
    else:
        overall = "TFRAME_NEUTRAL"

    detail  = " | ".join(
        f"{r['tf']}: {r['alignment']} (QQE {r['qqe']}, trend {r['trend']})"
        for r in results
    )
    summary = f"{direction} entry — {overall} | {detail}"

    return {
        "status":             overall,
        "summary":            summary,
        "direction":          direction,
        "timeframes_checked": results,
    }

async def check_volatility(symbol: str, timeframe: str) -> dict:
    """
    ATR-based volatility spike detection.
    Compares current ATR against 30-bar rolling average.
    NORMAL < 1.5x | ELEVATED 1.5-2.5x | EXTREME > 2.5x
    """
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/bars",
                params={"symbol": symbol, "timeframe": timeframe, "limit": 50}
            )
            bars = resp.json().get("bars", [])
    except Exception as e:
        return {
            "status":       "VOLAT_NA",
            "summary":      f"Volatility check unavailable: {e}",
            "current_atr":  None,
            "avg_atr":      None,
            "ratio":        None,
        }

    if len(bars) < 10:
        return {
            "status":       "VOLAT_NA",
            "summary":      "Insufficient bars for ATR calculation",
            "current_atr":  None,
            "avg_atr":      None,
            "ratio":        None,
        }

    # Calculate ATR (high - low) for each bar
    atrs = []
    for b in bars:
        try:
            high = float(b.get("high", 0))
            low  = float(b.get("low",  0))
            if high > 0 and low > 0:
                atrs.append(round(high - low, 5))
        except Exception:
            continue

    if len(atrs) < 5:
        return {
            "status":       "VOLAT_NA",
            "summary":      "Insufficient OHLC data for ATR",
            "current_atr":  None,
            "avg_atr":      None,
            "ratio":        None,
        }

    current_atr = atrs[1] if len(atrs) > 1 else atrs[0]  # last CLOSED bar
    avg_atr     = round(sum(atrs[2:32]) / min(len(atrs) - 2, 30), 5)  # 30-bar average
    ratio       = round(current_atr / avg_atr, 2) if avg_atr > 0 else 1.0

    # Convert to pips for display (approximate)
    pip_scale   = 100 if "JPY" in symbol or "XAU" in symbol else 10000
    current_pts = round(current_atr * pip_scale, 1)
    avg_pts     = round(avg_atr     * pip_scale, 1)

    if ratio >= 2.5:
        status  = "VOLAT_EXTREME"
        verdict = f"EXTREME spike — {ratio}x normal ({current_pts}pts vs avg {avg_pts}pts)"
    elif ratio >= 1.5:
        status  = "VOLAT_ELEVATED"
        verdict = f"ELEVATED volatility — {ratio}x normal ({current_pts}pts vs avg {avg_pts}pts)"
    else:
        status  = "VOLAT_NORMAL"
        verdict = f"Normal volatility — {ratio}x avg ({current_pts}pts vs avg {avg_pts}pts)"

    return {
        "status":       status,
        "summary":      f"{symbol} {timeframe}: {verdict}",
        "current_atr":  current_atr,
        "avg_atr":      avg_atr,
        "ratio":        ratio,
        "current_pts":  current_pts,
        "avg_pts":      avg_pts,
    }

def write_decision_to_db(decision: dict):
    """
    Write crew decision to VM SQLite DB via SSH.
    Pipes JSON to db_write.py on the VM.
    """
    try:
        payload = json.dumps({"table": "decisions", "data": decision})
        result = subprocess.run(
            ["ssh", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5",
             "-o", "BatchMode=yes", f"{VM_USER}@{VM_HOST}",
             f"python {VM_DB_WRITE_PATH}"],
            input=payload.encode(),
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"[DB] Decision write failed (rc={result.returncode}): {result.stderr.decode()}", flush=True)
            print(f"[DB] stdout: {result.stdout.decode()}", flush=True)
        else:
            print(result.stdout.decode().strip(), flush=True)
    except Exception as e:
        print(f"[DB] Decision write error: {e}", flush=True)


def detect_sequence_close(symbol: str, current_positions: list,
                          magic: int) -> bool:
    """Returns True if all positions for this magic just closed."""
    global _prev_positions
    key = f"{symbol}_{magic}"
    current_tickets = {
        p["ticket"] for p in current_positions
        if p.get("magic") == magic
    }
    prev_tickets = _prev_positions.get(key, None)
    _prev_positions[key] = current_tickets
    if prev_tickets is None:
        return False
    return len(prev_tickets) > 0 and len(current_tickets) == 0

# ---------------------------------------------------------------------------
# State fetching + cache
# ---------------------------------------------------------------------------
_state_cache: dict = {}
_STATE_CACHE_TTL   = 300  # 5 minutes — H4 bar only changes every 4 hours

def fetch_state(symbol: str, timeframe: str) -> dict:
    """SSH into the VM and read the state JSON file for this symbol."""
    candidates = [
        f"{STATE_DIR}/state_{symbol}_{timeframe}.json",
        f"{STATE_DIR}/state.json",
    ]
    for state_file in candidates:
        cmd = f"powershell -Command \"Get-Content '{state_file}'\""
        result = subprocess.run(
            ["ssh", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10",
             "-o", "BatchMode=yes", f"{VM_USER}@{VM_HOST}", cmd],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    raise RuntimeError(f"Could not fetch state for {symbol} {timeframe}")

def fetch_state_cached(symbol: str, timeframe: str) -> dict:
    """Return cached state if fresh, otherwise SSH to fetch."""
    key = (symbol, timeframe)
    now = time.time()
    if key in _state_cache:
        ts, data = _state_cache[key]
        if now - ts < _STATE_CACHE_TTL:
            return data
    data = fetch_state(symbol, timeframe)
    _state_cache[key] = (now, data)
    return data

# ---------------------------------------------------------------------------
# Crew execution
# ---------------------------------------------------------------------------
_analyse_executor = ThreadPoolExecutor(max_workers=1)
_analyse_lock     = threading.Lock()

def run_crew_for_state(state: dict) -> tuple:
    """Run the 3-agent crew. Raises RuntimeError if already running."""
    if not _analyse_lock.acquire(blocking=False):
        raise RuntimeError("Analysis already in progress — skipping")
    try:
        from richfx_crew import (
            state_to_context, create_regime_agent, create_risk_governor,
            create_strategy_agent,
            create_execution_agent, create_tasks, extract_compact,
            send_telegram, format_output,
)
        from crewai import Crew, Process

        ctx   = state_to_context(state)
        ra    = create_regime_agent()
        rg    = create_risk_governor()
        sa    = create_strategy_agent()
        ea    = create_execution_agent()
        tasks = create_tasks(ra, rg, sa, ea, ctx)
        crew  = Crew(
            agents=[ra, rg, sa, ea],tasks=tasks,
                      process=Process.sequential, verbose=False)
        result = crew.kickoff()

        outputs = []
        if hasattr(result, "tasks_output") and result.tasks_output:
            for t in result.tasks_output:
                outputs.append({
                    "agent":  getattr(t, "agent", "Unknown"),
                    "output": getattr(t, "raw", str(t)),
                })
        return result, outputs, extract_compact
    finally:
        _analyse_lock.release()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
from fastapi.responses import RedirectResponse, FileResponse

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/ui/richfx_trading_floor.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(BASE_DIR, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    raise HTTPException(status_code=404)

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/symbols")
def get_symbols():
    """
    Return the active symbol list from config/symbols.json.
    The dashboard fetches this on load to build the TV pair list dynamically.
    Add a new entry to symbols.json and restart the service to pick it up.
    """
    symbols = load_symbols_config()
    if not symbols:
        raise HTTPException(
            status_code=503,
            detail=f"No symbols found. Check {CONFIG_PATH} exists and has active entries.",
        )
    return {"symbols": symbols}


@app.get("/history")
async def get_history(magic: int = 100401, days: int = 30):
    """Proxy closed trade history from VM health server."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/history",
                params={"magic": magic, "days": days},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VM history unavailable: {e}")

@app.get("/bars")
async def get_bars(symbol: str = "EURUSD", timeframe: str = "H4",
                   limit: int = 100, offset: int = 0):
    """Proxy historical bar data from VM SQLite DB."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/bars",
                params={"symbol": symbol, "timeframe": timeframe,
                        "limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VM bars unavailable: {e}")


@app.get("/decisions")
async def get_decisions(symbol: str = "EURUSD", timeframe: str = "H4",
                        limit: int = 50):
    """Proxy decision log from VM SQLite DB."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/decisions",
                params={"symbol": symbol, "timeframe": timeframe,
                        "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VM decisions unavailable: {e}")


@app.get("/sequences")
async def get_sequences(symbol: str = None, limit: int = 20):
    """Proxy completed sequence log from VM SQLite DB."""
    import httpx
    try:
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{VM_HEALTH_URL}/sequences", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VM sequences unavailable: {e}")


@app.get("/journalist")
async def get_journalist(symbol: str = None, last: int = 5):
    """Return recent completed sequence summaries with Journalist narratives."""
    import httpx
    try:
        params = {"limit": last}
        if symbol:
            params["symbol"] = symbol
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{VM_HEALTH_URL}/sequences", params=params)
            resp.raise_for_status()
            data = resp.json()
        sequences = data.get("sequences", [])
        if not sequences:
            return {"message": "No completed sequences yet.", "sequences": []}
        return {"count": len(sequences), "sequences": sequences}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Sequences unavailable: {e}")

@app.post("/animate")
async def trigger_animation(agent: str, gesture: str = "alt"):
    """Queue an animation for a female agent."""
    if agent not in FEMALE_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent}' not eligible. Female agents: {sorted(FEMALE_AGENTS)}"
        )
    _animation_queue.append({"agent": agent, "gesture": gesture})
    return {"queued": True, "agent": agent, "gesture": gesture}


@app.get("/pending-animation")
async def get_pending_animation():
    """Dashboard polls this to check if an animation has been requested."""
    if _animation_queue:
        return _animation_queue.pop(0)
    return {"agent": None, "gesture": None}
        
@app.get("/performance")
async def get_performance(days: int = 30):
    """
    Aggregate closed trade performance across all active symbols.
    Groups by account type (demo/live) for clear distinction.
    Reads from /history which is populated by mt5_bridge.py.
    """
    import httpx
    symbols = load_symbols_config()

    # Fetch all deals unfiltered
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/history",
                params={"magic": 0, "days": days},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"History unavailable: {e}")

    all_trades = raw.get("trades", [])

    # Build magic → symbol config lookup
    magic_map = {s["magic"]: s for s in symbols}

    # Group trades by account type and by symbol
    by_account: dict = {"demo": [], "live": []}
    by_symbol:  dict = {}

    for trade in all_trades:
        magic = trade.get("magic", 0)
        if magic not in magic_map:
            continue  # trade from unknown/unconfigured EA — skip
        cfg     = magic_map[magic]
        account = cfg.get("account", "demo")
        symbol  = cfg["symbol"]

        by_account[account].append(trade)

        if symbol not in by_symbol:
            by_symbol[symbol] = {
                "symbol":  symbol,
                "account": account,
                "magic":   magic,
                "trades":  [],
            }
        by_symbol[symbol]["trades"].append(trade)

    def calc_stats(trades: list) -> dict:
        if not trades:
            return {"total_trades": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "total_net": 0.0,
                    "avg_win": 0.0, "avg_loss": 0.0}
        wins   = [t for t in trades if t["net"] > 0]
        losses = [t for t in trades if t["net"] <= 0]
        return {
            "total_trades": len(trades),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     round(len(wins) / len(trades) * 100, 1),
            "total_net":    round(sum(t["net"] for t in trades), 2),
            "avg_win":      round(sum(t["net"] for t in wins)   / len(wins),   2) if wins   else 0.0,
            "avg_loss":     round(sum(t["net"] for t in losses) / len(losses), 2) if losses else 0.0,
        }

    return {
        "days":         days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_time":   raw.get("cache_time", "unknown"),
        "by_account": {
            account: calc_stats(trades)
            for account, trades in by_account.items()
            if trades  # only include accounts that have trades
        },
        "by_symbol": {
            sym: {
                **calc_stats(data["trades"]),
                "account": data["account"],
                "magic":   data["magic"],
            }
            for sym, data in by_symbol.items()
        },
    }

@app.post("/performance/analyse")
async def analyse_performance(days: int = 30):
    """
    Run the Performance agent LLM analysis on closed trade history.
    Called by n8n on a daily schedule — not part of the per-bar crew chain.
    """
    loop = asyncio.get_running_loop()

    # Get performance data first
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/history",
                params={"magic": 0, "days": days},
            )
            raw = resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"History unavailable: {e}")

    # Build perf_data using same logic as /performance
    symbols   = load_symbols_config()
    magic_map = {s["magic"]: s for s in symbols}
    # ... (reuse the grouping logic from /performance)
    # For brevity — call the performance endpoint internally:
    perf_data = (await get_performance(days=days))

    try:
        from richfx_crew import run_performance_analysis
        narrative = await loop.run_in_executor(
            None, run_performance_analysis, perf_data
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Performance analysis failed: {e}")

    return {
        "days":      days,
        "narrative": narrative,
        "stats":     perf_data,
    }

@app.get("/scout")
async def run_scout(symbol: str = "EURUSD", timeframe: str = "H4"):
    """
    Run Backtest Scout — finds similar historical bar patterns and scores confidence.
    Called by dashboard on demand, not in the main crew chain.
    """
    loop = asyncio.get_running_loop()

    # Get current signal from cached state
    try:
        state = await loop.run_in_executor(None, fetch_state_cached, symbol, timeframe)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"State unavailable: {e}")

    sig = state["signal"]

    # Find similar bars from DB — match on QQE range and trend direction
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{VM_HEALTH_URL}/bars",
                params={"symbol": symbol, "timeframe": timeframe, "limit": 500},
            )
            resp.raise_for_status()
            all_bars = resp.json().get("bars", [])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Bars unavailable: {e}")

    # Filter bars with similar QQE (within ±5) and same trend direction
    current_qqe   = sig.get("qqe_value", 50)
    current_trend = sig.get("qmp_trend", 0)
    similar = [
        b for b in all_bars
        if b.get("qqe") is not None
        and abs(b["qqe"] - current_qqe) <= 5
        and b.get("qmp_trend") == current_trend
    ]

    if len(similar) < 3:
        return {
            "symbol":       symbol,
            "timeframe":    timeframe,
            "summary":      f"SCOUT: Only {len(similar)} similar bars found — insufficient data",
            "confidence":   0,
            "similar_count": len(similar),
        }

    # Trim to 20 bars for context window — pass signal values only, no outcome
    similar_trimmed = [
        {
            "bar_time":  b["bar_time"],
            "qqe":       b["qqe"],
            "qmp_trend": b["qmp_trend"],
            "macd":      b["macd"],
            "macd_avg":  b["macd_avg"],
        }
        for b in similar[:20]
    ]

    # Run Scout LLM analysis
    try:
        from richfx_crew import run_scout_analysis
        narrative = await loop.run_in_executor(
            None, run_scout_analysis, sig, similar_trimmed
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scout analysis failed: {e}")

    # Parse confidence score — first number after "confidence"
    import re
    conf = 0
    for line in narrative.split('\n'):
        m = re.search(r'confidence[,:\s]+(\d+)', line, re.IGNORECASE)
        if m:
            conf = int(m.group(1))
            break

    return {
        "symbol":        symbol,
        "timeframe":     timeframe,
        "summary":       narrative,
        "confidence":    conf,
        "similar_count": len(similar),
        "current_qqe":   current_qqe,
        "current_trend": current_trend,
    }

@app.get("/meta")
async def run_meta(symbol: str = "EURUSD", timeframe: str = "H4",
                   limit: int = 50):
    """
    Run Meta-Supervisor — reviews recent crew decisions for systematic patterns.
    Requires at least 10 decisions to produce meaningful output.
    """
    loop = asyncio.get_running_loop()

    # Get decisions from DB
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            dec_resp  = await client.get(
                f"{VM_HEALTH_URL}/decisions",
                params={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            )
            dec_resp.raise_for_status()
            decisions = dec_resp.json().get("decisions", [])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Decisions unavailable: {e}")

    if len(decisions) < 10:
        return {
            "symbol":    symbol,
            "timeframe": timeframe,
            "status":    "PENDING",
            "summary":   f"Only {len(decisions)} decisions logged — need at least 10.",
            "narrative": "",
        }

    # Get performance data
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            perf_resp = await client.get(
                f"{VM_HEALTH_URL}/history",
                params={"magic": 0, "days": 30},
            )
            perf_data = perf_resp.json()
    except Exception:
        perf_data = {}

    # Build performance summary
    symbols    = load_symbols_config()
    magic_map  = {s["magic"]: s for s in symbols}
    by_account = {}
    for trade in perf_data.get("trades", []):
        magic = trade.get("magic", 0)
        if magic not in magic_map:
            continue
        acct = magic_map[magic].get("account", "demo")
        if acct not in by_account:
            by_account[acct] = {"total_trades": 0, "wins": 0,
                                "total_net": 0.0, "win_rate": 0.0}
        by_account[acct]["total_trades"] += 1
        if trade["net"] > 0:
            by_account[acct]["wins"] += 1
        by_account[acct]["total_net"] = round(
            by_account[acct]["total_net"] + trade["net"], 2)
    for acct in by_account:
        t = by_account[acct]["total_trades"]
        w = by_account[acct]["wins"]
        by_account[acct]["win_rate"] = round(w / t * 100, 1) if t > 0 else 0.0

    perf_summary = {"by_account": by_account}

    # Run Meta LLM analysis
    try:
        from richfx_crew import run_meta_analysis
        narrative = await loop.run_in_executor(
            None, run_meta_analysis, decisions, perf_summary
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Meta analysis failed: {e}")

    # Parse status
    status = "OK"
    for line in narrative.split('\n'):
        if "STATUS:" in line.upper():
            if "ALERT" in line.upper():
                status = "ALERT"
            elif "REVIEW" in line.upper():
                status = "REVIEW"
            break

    return {
        "symbol":          symbol,
        "timeframe":       timeframe,
        "status":          status,
        "decisions_count": len(decisions),
        "summary":         narrative.split('\n')[0] if narrative else "",
        "narrative":       narrative,
    }

@app.get("/bars/symbols")
async def get_bars_symbols():
    """Return all symbol/timeframe pairs available in the bars DB."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{VM_HEALTH_URL}/bars/symbols")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VM bars/symbols unavailable: {e}")

@app.get("/horizon")
async def run_horizon(timeframe: str = "H4"):
    """
    Run Horizon agent — analyses signal quality across all monitoring pairs
    and recommends which pairs are worth promoting to full EA trading.
    Requires bars data for monitoring pairs in richfx.db.
    """
    loop = asyncio.get_running_loop()

    import httpx
    import re

    # Get all pairs that have bars — both active and monitoring
    symbols    = load_symbols_config()
    active_sym = {s["symbol"] for s in symbols}

    # Discover all pairs in the bars DB dynamically — no hardcoding
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Get all unique symbol/timeframe combinations from bars table
            resp = await client.get(
                f"{VM_HEALTH_URL}/bars/symbols",
            )
            all_pairs = resp.json().get("pairs", [])
    except Exception:
        # Fallback — use active symbols from config
        all_pairs = [
            {"symbol": s["symbol"], "timeframe": s.get("timeframe", "H4")}
            for s in symbols
        ]

    pair_data = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for pair in all_pairs:
            sym = pair["symbol"]
            pair_tf = pair.get("timeframe", timeframe)
            if pair_tf != timeframe:
                continue
            try:
                resp = await client.get(
                    f"{VM_HEALTH_URL}/bars",
                    params={"symbol": sym, "timeframe": timeframe, "limit": 500},
                )
                bars = resp.json().get("bars", [])
                if len(bars) < 10:
                    continue

                # Calculate signal stats from bars with QQE data
                signal_bars = [b for b in bars if b.get("qqe") is not None]
                if len(signal_bars) < 5:
                    continue

                qqe_vals   = [b["qqe"] for b in signal_bars]
                trend_vals = [b["qmp_trend"] for b in signal_bars if b.get("qmp_trend") is not None]
                macd_vals  = [b["macd"] for b in signal_bars if b.get("macd") is not None]

                # Signal consistency metrics
                avg_qqe      = round(sum(qqe_vals) / len(qqe_vals), 2)
                qqe_range    = round(max(qqe_vals) - min(qqe_vals), 2)
                trend_consistency = round(
                    max(trend_vals.count(1), trend_vals.count(-1)) / len(trend_vals) * 100
                    if trend_vals else 0, 1
                )
                # How often QQE is decisively above or below 50 (not hovering)
                decisive = sum(1 for q in qqe_vals if q > 55 or q < 45)
                decisive_pct = round(decisive / len(qqe_vals) * 100, 1)

                pair_data[sym] = {
                    "symbol":             sym,
                    "timeframe":          timeframe,
                    "bar_count":          len(signal_bars),
                    "avg_qqe":            avg_qqe,
                    "qqe_range":          qqe_range,
                    "trend_consistency":  trend_consistency,
                    "decisive_pct":       decisive_pct,
                    "is_active":          sym in active_sym,
                }
            except Exception:
                continue

    if not pair_data:
        return {
            "status":    "PENDING",
            "summary":   "Insufficient bar data — monitoring pairs still seeding.",
            "narrative": "",
            "pairs":     {},
        }

    # Run Horizon LLM analysis
    try:
        from richfx_crew import run_horizon_analysis
        narrative = await loop.run_in_executor(
            None, run_horizon_analysis, pair_data, list(active_sym)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Horizon analysis failed: {e}")

    # Parse recommendation
    status = "MONITORING"
    if "PROMOTE" in narrative.upper():
        status = "OPPORTUNITY"
    elif "CAUTION" in narrative.upper() or "AVOID" in narrative.upper():
        status = "CAUTION"

    summary_line = narrative.split('\n')[0] if narrative else ""

    _last_horizon["result"] = {
        "status":       status,
        "summary":      summary_line,
        "narrative":    narrative,
        "pairs":        pair_data,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_horizon_cache()
    return _last_horizon["result"]

@app.get("/horizon/last")
async def get_last_horizon():
    """Return cached Horizon analysis — instant, no LLM."""
    if not _last_horizon:
        return {
            "status":    "PENDING",
            "summary":   "No Horizon analysis run yet.",
            "narrative": "",
            "pairs":     {},
        }
    return _last_horizon["result"]

@app.get("/state")
async def get_state(symbol: str = "EURUSD", timeframe: str = "H4"):
    """
    Return raw market state JSON for a given symbol/timeframe.
    Injects is_demo flag from config so the dashboard whiteboard is accurate.
    """
    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(
            None, fetch_state_cached, symbol, timeframe
        )
        if isinstance(state, dict) and "account" in state:
            state["account"]["is_demo"] = get_symbol_is_demo(symbol)
        return state
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.on_event("startup")
async def startup():
    load_analysis_cache()
    load_horizon_cache()
    print(f"[Startup] Cache loaded: {list(_last_analysis.keys())}")

@app.post("/analyse", response_model=AnalyseResponse)
async def analyse(req: AnalyseRequest):
    """
    Run the full 3-agent crew analysis for a given symbol/timeframe/magic.
    Executes in a thread pool so the event loop stays responsive for
    static file serving (sprites) during the Ollama/CrewAI call.
    """
    loop = asyncio.get_running_loop()

    try:
        state = await loop.run_in_executor(
            None, fetch_state, req.symbol, req.timeframe
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not fetch state: {e}")

    try:
        result, outputs, extract_compact = await loop.run_in_executor(
            _analyse_executor, run_crew_for_state, state
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Crew failed: {e}")

    sig = state["signal"]

    def make_agent_result(role: str) -> AgentResult:
        for t in outputs:
            if t["agent"] == role:
                return AgentResult(
                    agent   = role,
                    summary = extract_compact(t["output"], role),
                    raw     = t["output"][:500],
                )
        return AgentResult(agent=role, summary="No output", raw="")

    # Session and drawdown checks — pure Python, no LLM, run directly
    sess_result = check_session(req.symbol)
    dd_result   = check_drawdown(state.get("account", {}))
    
    # Correlation check — needs the execution result to know proposed direction,
    # and cached states for other active pairs
    exec_summary = make_agent_result("Execution Coordinator").summary
    proposed_dir = (
        'BUY'  if any(x in exec_summary.upper() for x in ('BUY', 'OPEN_BUY'))  else
        'SELL' if any(x in exec_summary.upper() for x in ('SELL', 'OPEN_SELL')) else
        None
    )
    # Fetch other pairs from cache — no SSH needed if dashboard polled recently
    active_syms   = load_symbols_config()
    other_states  = {}
    for sym_cfg in active_syms:
        sym = sym_cfg['symbol']
        if sym == req.symbol:
            continue
        try:
            other_states[sym] = fetch_state_cached(sym, sym_cfg['timeframe'])
        except Exception:
            pass  # if a pair isn't cached yet just skip it
    corr_result = check_correlation(req.symbol, proposed_dir, other_states)
    news_result  = check_news_calendar(req.symbol)
    # Timeframe alignment check
    tframe_result = await check_timeframe_alignment(
        req.symbol, req.timeframe,
        exec_summary
    )
    # Volatility check
    volat_result = await check_volatility(req.symbol, req.timeframe)

    # Cache the result for /last-analysis endpoint
    _last_analysis[req.symbol] = {
        "symbol":        state["meta"]["symbol"],
        "timeframe":     state["meta"]["timeframe"],
        "bar_time":      state["meta"]["last_bar_time"],
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "qqe_value":     sig["qqe_value"],
        "qmp_trend":     sig["qmp_trend"],
        "spread":        state["price"]["spread"],
        "regime":        make_agent_result("Market Regime Analyst").model_dump(),
        "risk":          make_agent_result("Risk Governor").model_dump(),
        "strategy":      make_agent_result("Strategy Evaluator").model_dump(),
        "execution":     make_agent_result("Execution Coordinator").model_dump(),
        "telegram_sent": True,
        "session":       sess_result.model_dump(),
        "drawdown":      dd_result.model_dump(),
        "correlation":   corr_result.model_dump(),
        "news":          news_result.model_dump(),
        "timeframe_alignment": tframe_result,
        "volatility": volat_result,
    }
    save_analysis_cache()

    # ── Decision logging — write to VM DB in background thread ───────────────
    import threading
    regime_summary = make_agent_result("Market Regime Analyst").summary
    risk_summary   = make_agent_result("Risk Governor").summary
    strat_summary  = make_agent_result("Strategy Evaluator").summary
    exec_summary_str = make_agent_result("Execution Coordinator").summary

    def _parse_score(summary):
        try:
            return int(summary.split('/')[0].split('(')[-1])
        except Exception:
            return 0

    threading.Thread(target=write_decision_to_db, args=({
        "symbol":       state["meta"]["symbol"],
        "timeframe":    state["meta"]["timeframe"],
        "bar_time":     state["meta"]["last_bar_time"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime":       regime_summary.split('|')[0].strip(),
        "regime_conf":  _parse_score(regime_summary),
        "risk_status":  "APPROVED" if "APPROVED" in risk_summary else "REJECTED",
        "risk_score":   _parse_score(risk_summary),
        "strategy":     "PROCEED" if "PROCEED" in strat_summary else "HOLD",
        "strat_score":  _parse_score(strat_summary),
        "action":       exec_summary_str.split('|')[0].strip(),
        "session":      sess_result.session,
        "spread":       state["price"]["spread"],
    },), daemon=True).start()

    # ── Sequence close detection — trigger Journalist ─────────────────────────
    current_positions = state.get("positions", [])
    if detect_sequence_close(req.symbol, current_positions, req.magic):
        print(f"[Journalist] Sequence close detected for {req.symbol}", flush=True)

        async def _run_journalist():
            try:
                # Wait a few seconds for mt5_bridge to write the closed deal to history
                await asyncio.sleep(15)

                # Fetch the most recently closed sequence from history
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    hist_resp = await client.get(
                        f"{VM_HEALTH_URL}/history",
                        params={"magic": req.magic, "days": 30},
                    )
                    hist_data = hist_resp.json()

                trades = hist_data.get("trades", [])
                if not trades:
                    print(f"[Journalist] No trades found for magic {req.magic}", flush=True)
                    return

                # Group most recent closed trades into a sequence
                # Trades are sorted newest first — find the cluster that just closed
                from datetime import timedelta
                now_utc   = datetime.now(timezone.utc)
                recent    = [
                    t for t in trades
                    if (now_utc - datetime.fromisoformat(t["time"])).total_seconds() < 3600
                ]

                if not recent:
                    # Fall back to most recent trade if nothing in last hour
                    recent = trades[:1]

                if not recent:
                    return

                # Build sequence summary from recent trades
                direction   = "BUY"  if recent[0]["type"] == "sell" else "SELL"
                # Closing a BUY sequence = sell deals, closing SELL = buy deals
                net_profit  = round(sum(t["net"] for t in recent), 2)
                trade_count = len(recent)
                avg_entry   = round(sum(t["price"] for t in recent) / trade_count, 5)
                close_price = recent[0]["price"]
                open_time   = min(t["time"] for t in recent)
                close_time  = max(t["time"] for t in recent)

                try:
                    open_dt  = datetime.fromisoformat(open_time)
                    close_dt = datetime.fromisoformat(close_time)
                    duration_hrs = max(1, int((close_dt - open_dt).total_seconds() / 3600))
                except Exception:
                    duration_hrs = 0

                sequence = {
                    "symbol":      req.symbol,
                    "timeframe":   req.timeframe,
                    "direction":   direction,
                    "magic":       req.magic,
                    "open_time":   open_time,
                    "close_time":  close_time,
                    "trade_count": trade_count,
                    "total_lots":  round(sum(t["volume"] for t in recent), 2),
                    "avg_entry":   avg_entry,
                    "close_price": close_price,
                    "gross_profit": round(sum(t["profit"] for t in recent), 2),
                    "net_profit":  net_profit,
                    "duration_hrs": duration_hrs,
                    "result":      "WIN" if net_profit > 0 else "LOSS",
                }

                print(f"[Journalist] Generating narrative for {req.symbol} {direction} "
                      f"net ${net_profit:+.2f}", flush=True)

                # Run Journalist LLM in thread pool
                loop = asyncio.get_running_loop()
                from richfx_crew import run_journalist_narrative
                narrative = await loop.run_in_executor(
                    None, run_journalist_narrative, sequence
                )
                sequence["narrative"] = narrative
                print(f"[Journalist] Narrative: {narrative[:100]}...", flush=True)

                # Write sequence to VM DB
                payload = json.dumps({"table": "sequences", "data": sequence})
                result  = subprocess.run(
                    ["ssh", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5",
                     "-o", "BatchMode=yes", f"{VM_USER}@{VM_HOST}",
                     f"python {VM_DB_WRITE_PATH}"],
                    input=payload.encode(),
                    capture_output=True, timeout=15,
                )
                if result.returncode == 0:
                    print(f"[Journalist] Sequence written to DB", flush=True)
                else:
                    print(f"[Journalist] DB write failed: {result.stderr.decode()}", flush=True)

            except Exception as e:
                print(f"[Journalist] Error: {e}", flush=True)

        # Run as background task — don't block the /analyse response
        asyncio.create_task(_run_journalist())
    
    return AnalyseResponse(
        symbol        = state["meta"]["symbol"],
        timeframe     = state["meta"]["timeframe"],
        bar_time      = state["meta"]["last_bar_time"],
        generated_at  = datetime.now(timezone.utc).isoformat(),
        qqe_value     = sig["qqe_value"],
        qmp_trend     = sig["qmp_trend"],
        spread        = state["price"]["spread"],
        regime        = make_agent_result("Market Regime Analyst"),
        risk          = make_agent_result("Risk Governor"),
        strategy      = make_agent_result("Strategy Evaluator"),
        execution     = make_agent_result("Execution Coordinator"),
        telegram_sent = True,
        session       = sess_result,
        drawdown      = dd_result,
        correlation   = corr_result,
        news          = news_result,
        timeframe_alignment = tframe_result,
        volatility          = volat_result,
    )

@app.get("/last-analysis")
async def get_last_analysis(symbol: str = "EURUSD"):
    """
    Return the most recent crew analysis result for a symbol.
    The dashboard uses this instead of triggering a new /analyse call.
    Analysis is triggered exclusively by n8n on the bar schedule.
    """
    if symbol not in _last_analysis:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis cached for {symbol} yet — waiting for first n8n run."
        )
    return _last_analysis[symbol]