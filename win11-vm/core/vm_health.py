"""
RichFX VM Health Check + History Server
========================================
HTTP server on port 8765.

Endpoints:
  GET /health                         — liveness check
  GET /history?magic=100401&days=30  — closed trade history from MT5
"""
import json
import threading
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta

_cache = {}
_cache_lock = threading.Lock()

LIVE_FILE = r"C:\__RichStuff\FX\trading_system\data\signals\live.json"

# ---------------------------------------------------------------------------
# Live data — written every ~10s by mt5_bridge.py, read here on demand
# ---------------------------------------------------------------------------
def get_live_data() -> dict:
    """Read live account + position snapshot written by mt5_bridge."""
    try:
        with open(LIVE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "live.json not found — mt5_bridge may not be running"}
    except Exception as e:
        return {"error": f"Could not read live.json: {e}"}

HISTORY_FILE = r"C:\__RichStuff\FX\trading_system\data\signals\history.json"
DB_PATH     = r"C:\__RichStuff\FX\trading_system\data\signals\richfx.db"

def refresh_cache():
    """Read history from file written by mt5_bridge.py."""
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        all_trades = data.get("trades", [])
        with _cache_lock:
            _cache["trades"]     = all_trades
            _cache["updated_at"] = data.get("generated_at", "unknown")
    except Exception as e:
        print(f"[VM Health] Could not read history file: {e}")

def get_summary_stats(trades):
    if not trades:
        return {"total_trades":0,"wins":0,"losses":0,
                "win_rate":0.0,"total_net":0.0,"avg_win":0.0,"avg_loss":0.0}
    wins   = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    return {
        "total_trades": len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate":     round(len(wins)/len(trades)*100, 1),
        "total_net":    round(sum(t["net"] for t in trades), 2),
        "avg_win":      round(sum(t["net"] for t in wins)  /len(wins),   2) if wins   else 0.0,
        "avg_loss":     round(sum(t["net"] for t in losses)/len(losses), 2) if losses else 0.0,
    }

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._respond(200, {"status": "ok"})
        elif parsed.path == "/history":
            params = parse_qs(parsed.query)
            magic  = int(params.get("magic", ["0"])[0])
            days   = min(int(params.get("days", ["30"])[0]), 365)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            with _cache_lock:
                all_trades = _cache.get("trades", [])
            trades = [
                t for t in all_trades
                if (magic == 0 or t["magic"] == magic) and t["time"] >= cutoff
            ]
            self._respond(200, {
                "magic":      magic,
                "days":       days,
                "summary":    get_summary_stats(trades),
                "trades":     trades,
                "cache_time": _cache.get("updated_at", "never"),
            })
        elif parsed.path == "/bars":
            params    = parse_qs(parsed.query)
            symbol    = params.get("symbol",    ["EURUSD"])[0]
            tf        = params.get("timeframe", ["H4"])[0]
            limit     = min(int(params.get("limit",  ["100"])[0]), 500)
            offset    = int(params.get("offset", ["0"])[0])
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT bar_time, open, high, low, close, volume,
                           qqe, qmp_trend, macd, macd_avg
                    FROM bars
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY bar_time DESC
                    LIMIT ? OFFSET ?
                """, (symbol, tf, limit, offset)).fetchall()
                conn.close()
                self._respond(200, {
                    "symbol":    symbol,
                    "timeframe": tf,
                    "count":     len(rows),
                    "bars":      [dict(r) for r in rows],
                })
            except Exception as e:
                self._respond(500, {"error": str(e)})
        elif parsed.path == "/bars/symbols":
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT DISTINCT symbol, timeframe, COUNT(*) as bar_count
                    FROM bars
                    GROUP BY symbol, timeframe
                    ORDER BY symbol, timeframe
                """).fetchall()
                conn.close()
                self._respond(200, {
                    "pairs": [dict(r) for r in rows]
                })
            except Exception as e:
                self._respond(500, {"error": str(e)})
        elif parsed.path == "/decisions":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol",    ["EURUSD"])[0]
            tf     = params.get("timeframe", ["H4"])[0]
            limit  = min(int(params.get("limit", ["50"])[0]), 200)
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT bar_time, generated_at, regime, regime_conf,
                           risk_status, risk_score, strategy, strat_score,
                           action, session, spread
                    FROM decisions
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY bar_time DESC
                    LIMIT ?
                """, (symbol, tf, limit)).fetchall()
                conn.close()
                self._respond(200, {
                    "symbol":    symbol,
                    "timeframe": tf,
                    "count":     len(rows),
                    "decisions": [dict(r) for r in rows],
                })
            except Exception as e:
                self._respond(500, {"error": str(e)})

        elif parsed.path == "/sequences":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", [None])[0]
            limit  = min(int(params.get("limit", ["20"])[0]), 100)
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                if symbol:
                    rows = conn.execute("""
                        SELECT * FROM sequences
                        WHERE symbol = ?
                        ORDER BY close_time DESC LIMIT ?
                    """, (symbol, limit)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT * FROM sequences
                        ORDER BY close_time DESC LIMIT ?
                    """, (limit,)).fetchall()
                conn.close()
                self._respond(200, {
                    "count":     len(rows),
                    "sequences": [dict(r) for r in rows],
                })
            except Exception as e:
                self._respond(500, {"error": str(e)})
        elif parsed.path == "/live":
            data = get_live_data()
            code = 500 if "error" in data else 200
            self._respond(code, data)
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    import time
    refresh_cache()
    print(f"[VM Health] Cache loaded — {len(_cache.get('trades', []))} closed trades")
    def cache_loop():
        while True:
            time.sleep(30)
            refresh_cache()
    threading.Thread(target=cache_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", 8765), HealthHandler)
    print("[VM Health] Listening on port 8765 — /health + /history + /live")
    server.serve_forever()