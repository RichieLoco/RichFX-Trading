# -*- coding: utf-8 -*-
"""
RichFX Telegram Alerter
========================
Lightweight always-on alerter running on the Windows VM.
No LLM required -- pure threshold checks.
Runs alongside mt5_bridge.py, survives MS-S1 MAX being off.

Features:
- Broker server time used throughout (no DST/timezone issues)
- Market open/close detection based on broker clock
- Multi-symbol support
- Sequence depth alerts
- Drawdown alerts
- Friday close reminder with open sequences (fires once per Friday window)
- Sunday market open summary
- Telegram delivery via bot

Setup:
1. Create a Telegram bot via @BotFather -> get token
2. Get your chat ID via @userinfobot
3. Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID below
4. python telegram_alerter.py

Run as scheduled task on VM (starts with Windows, no delay needed):
  schtasks /create /tn RichFX-Alerter /tr "python C:\\path\\telegram_alerter.py"
  /sc onlogon /ru richi-rdp /f
"""

import json
import time
import requests
import MetaTrader5 as mt5
from datetime import datetime, timezone
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).parent))
from indicators import get_signal_state

# ---------------------------------------------------------------------------
# CONFIG -- loaded from .env (never hardcode credentials in source)
# Required .env keys:
#   TELEGRAM_TOKEN       -- bot token from @BotFather
#   TELEGRAM_CHAT_ID     -- your chat ID from @userinfobot
#   TELEGRAM_ALLOWED_UID -- your Telegram user ID (locks Hermes to you only)
#   CREW_API_URL         -- e.g. http://<ubuntu-ai-tailscale-ip>:8000
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

TELEGRAM_TOKEN       = os.getenv("TELEGRAM_TOKEN",       "")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID",     "")
TELEGRAM_ALLOWED_UID = int(os.getenv("TELEGRAM_ALLOWED_UID", "0"))
CREW_API_URL         = os.getenv("CREW_API_URL",         "http://localhost:8000")

# ---------------------------------------------------------------------------
# Config loaded from watchlist.json -- edit that file to add new pairs
# ---------------------------------------------------------------------------
WATCHLIST_FILE = Path(r"C:\__RichStuff\FX\trading_system\core\watchlist.json")
STATE_DIR      = Path(r"C:\__RichStuff\FX\trading_system\data\signals")

def load_config():
    """Load watchlist and settings from central config file."""
    try:
        with open(WATCHLIST_FILE) as f:
            cfg = json.load(f)
        pairs    = [p for p in cfg.get("pairs", []) if p.get("active", True)]
        settings = cfg.get("settings", {})
        return pairs, settings
    except Exception as e:
        print(f"[WARN] Could not load watchlist.json: {e} -- using defaults")
        return [{"symbol": "EURUSD", "tf": "H4", "magic": 100401}], {}

_pairs, _settings = load_config()
WATCH_LIST             = _pairs
MAX_SEQUENCE_TRADES    = _settings.get("max_sequence_trades",    4)
MAX_EQUITY_DD_PCT      = _settings.get("max_equity_dd_pct",      3.0)
FRIDAY_CLOSE_WARN_HOUR = _settings.get("friday_close_warn_hour", 22)
POLL_INTERVAL_SECS     = _settings.get("poll_interval_secs",     60)

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(message: str, silent: bool = False) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_TOKEN:  # not configured -- print locally instead
        print(f"[TELEGRAM] {message}".encode("ascii", errors="replace").decode("ascii"))
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":               TELEGRAM_CHAT_ID,
        "text":                  message,
        "parse_mode":            "HTML",
        "disable_notification":  silent,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Hermes -- conversational query handler (CrewAI agent via /ask endpoint)
# ---------------------------------------------------------------------------
# Rolling conversation history per session (resets on alerter restart)
_hermes_history  = []
_HERMES_MAX_TURNS = 5


def hermes_ask(user_message: str) -> str:
    """Send a query to the Hermes CrewAI agent via crew_api /ask."""
    global _hermes_history
    try:
        r = requests.post(
            f"{CREW_API_URL}/ask",
            json={
                "message": user_message,
                "history": _hermes_history[-(_HERMES_MAX_TURNS * 2):],
            },
            timeout=120,
        )
        if r.ok:
            reply = r.json().get("reply", "No response from Hermes.")
        else:
            reply = f"Hermes API error: HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        reply = "Hermes timed out -- the agent may still be running. Try again shortly."
    except Exception as e:
        reply = f"Could not reach Hermes: {e}"

    # Update rolling history
    _hermes_history.append({"role": "user",      "content": user_message})
    _hermes_history.append({"role": "assistant", "content": reply})
    if len(_hermes_history) > _HERMES_MAX_TURNS * 2:
        _hermes_history = _hermes_history[-(_HERMES_MAX_TURNS * 2):]

    return reply


def get_telegram_updates(offset: int = 0) -> list:
    """Poll Telegram for new inbound messages."""
    if not TELEGRAM_TOKEN:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 10, "allowed_updates": ["message"]},
            timeout=15,
        )
        if r.ok:
            return r.json().get("result", [])
    except Exception:
        pass
    return []


def handle_inbound_messages(offset: int) -> int:
    """Process inbound Telegram messages. Returns updated offset."""
    updates = get_telegram_updates(offset)
    for update in updates:
        offset = update["update_id"] + 1
        msg    = update.get("message", {})
        uid    = msg.get("from", {}).get("id", 0)
        text   = msg.get("text", "").strip()

        if not text:
            continue

        # Security: only respond to the authorised user
        if TELEGRAM_ALLOWED_UID and uid != TELEGRAM_ALLOWED_UID:
            print(f"[Hermes] Ignored message from unauthorised UID {uid}")
            continue

        print(f"[Hermes] Query: {text[:80]}")

        # Acknowledge immediately so the user knows Hermes received it
        send_telegram("Hermes is thinking...")

        # All messages go straight to the CrewAI agent.
        # The agent decides which tools to call based on the question --
        # no keyword routing needed here.
        reply = hermes_ask(text)
        send_telegram(reply)

    return offset


# ---------------------------------------------------------------------------
# Broker time helpers
# ---------------------------------------------------------------------------
def get_broker_time() -> datetime:
    """
    Get current broker server time directly from MT5.
    This is always correct regardless of DST, timezone offset,
    or whether you're in UTC+2 or UTC+3.
    """
    tick = mt5.symbol_info_tick("EURUSD")
    if tick:
        return datetime.fromtimestamp(tick.time, tz=timezone.utc)
    # Fallback to terminal time
    info = mt5.terminal_info()
    if info:
        return datetime.fromtimestamp(info.time, tz=timezone.utc)
    return datetime.now(timezone.utc)


def is_market_open() -> bool:
    """
    Detect if forex market is open using broker server time.
    Pepperstone: closes ~23:59 Friday broker time,
                 reopens ~00:01 Sunday broker time.
    """
    bt = get_broker_time()
    dow  = bt.weekday()   # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    hour = bt.hour

    if dow == 5:                          # Saturday -- always closed
        return False
    if dow == 4 and hour >= 23:           # Friday from 23:00 broker time
        return False
    if dow == 6 and hour < 23:            # Sunday before 23:00 broker time
        return False
    return True


def is_approaching_friday_close() -> bool:
    """True in the hour before Friday close (22:00-22:59 broker time)."""
    bt = get_broker_time()
    return bt.weekday() == 4 and bt.hour == FRIDAY_CLOSE_WARN_HOUR


def is_sunday_open() -> bool:
    """True in the first few minutes after Sunday market open."""
    bt = get_broker_time()
    return bt.weekday() == 6 and bt.hour == 23 and bt.minute < 10


def broker_time_str() -> str:
    return get_broker_time().strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# MT5 data helpers
# ---------------------------------------------------------------------------
def connect_mt5() -> bool:
    try:
        if not mt5.initialize():
            print(f"[ERROR] MT5 init failed: {mt5.last_error()}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[ERROR] MT5 exception: {e}", flush=True)
        return False


def get_account() -> dict:
    info = mt5.account_info()
    if not info:
        return {}
    return {
        "balance":  info.balance,
        "equity":   info.equity,
        "profit":   info.profit,
        "currency": info.currency,
    }


def get_sequences(symbol: str, magic: int) -> dict:
    """Get current buy/sell sequence state for a symbol/magic combo."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return {"buy": None, "sell": None}

    buys  = [p for p in positions if p.type == mt5.ORDER_TYPE_BUY  and p.magic == magic]
    sells = [p for p in positions if p.type == mt5.ORDER_TYPE_SELL and p.magic == magic]

    def summarise(trades, direction):
        if not trades:
            return None
        total_lots   = sum(t.volume for t in trades)
        total_cost   = sum(t.volume * t.price_open for t in trades)
        avg_entry    = total_cost / total_lots
        total_profit = sum(t.profit + t.swap for t in trades)
        return {
            "direction":     direction,
            "trade_count":   len(trades),
            "total_lots":    round(total_lots, 2),
            "avg_entry":     round(avg_entry, 5),
            "profit":        round(total_profit, 2),
            "oldest_ticket": min(t.ticket for t in trades),
        }

    return {
        "buy":  summarise(buys,  "BUY"),
        "sell": summarise(sells, "SELL"),
    }


def get_ohlcv(symbol: str, tf_str: str, bars: int = 300):
    """Fetch OHLCV data from MT5."""
    import pandas as pd
    tf_map = {
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1, "M15": mt5.TIMEFRAME_M15,
    }
    tf = tf_map.get(tf_str, mt5.TIMEFRAME_H4)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Alert checks
# ---------------------------------------------------------------------------
def check_sequence_depth(symbol: str, magic: int) -> list:
    """
    Return (stable_key, message) tuples for sequences at/beyond depth threshold.

    The dedup key is deliberately built from (symbol, direction, trade_count)
    only -- NOT from the message text. The message body embeds live P&L,
    which changes every poll; hashing/truncating that text (as the old
    alert[:80] approach did) produces a different key almost every cycle,
    which defeats dedup entirely (same root cause as the Friday-close bug).
    A new key is only produced when the sequence actually gets deeper,
    which is the behaviour we want (escalate on depth change, not on P&L wobble).
    """
    results = []
    seqs = get_sequences(symbol, magic)
    for seq in seqs.values():
        if seq and seq["trade_count"] >= MAX_SEQUENCE_TRADES:
            direction = seq["direction"]  # "BUY"/"SELL" -- not the lowercase dict key
            emoji = "🟡" if seq["trade_count"] < 5 else "🔴"
            stable_key = f"SEQUENCE_DEPTH:{symbol}:{direction}:{seq['trade_count']}"
            message = (
                f"{emoji} <b>{symbol} {direction} SEQUENCE DEPTH {seq['trade_count']}</b>\n"
                f"Lots: {seq['total_lots']} | Avg: {seq['avg_entry']} | "
                f"P&amp;L: {seq['profit']:+.2f}"
            )
            results.append((stable_key, message))
    return results


def check_drawdown(account: dict, session_high: float) -> list:
    """
    Return (stable_key, message) tuples if equity drawdown exceeds threshold.

    Key is rounded to the nearest whole percentage point rather than derived
    from the message text, so it doesn't flap every poll as equity wobbles
    by cents (same fix pattern as check_sequence_depth).
    """
    results = []
    if not account or session_high <= 0:
        return results
    equity = account["equity"]
    dd_pct = (session_high - equity) / session_high * 100
    if dd_pct >= MAX_EQUITY_DD_PCT:
        emoji = "🔴" if dd_pct >= 5.0 else "🟡"
        stable_key = f"DRAWDOWN:{int(dd_pct)}"
        message = (
            f"{emoji} <b>DRAWDOWN ALERT: {dd_pct:.1f}%</b>\n"
            f"Session high: {account['currency']} {session_high:,.2f} | "
            f"Current equity: {equity:,.2f}"
        )
        results.append((stable_key, message))
    return results


def check_friday_close(symbol: str, magic: int) -> list:
    """Warn if Friday close approaching with open sequences."""
    if not is_approaching_friday_close():
        return []
    seqs = get_sequences(symbol, magic)
    open_seqs = [s for s in seqs.values() if s is not None]
    if not open_seqs:
        return []
    lines = [f"⚠️ <b>FRIDAY CLOSE IN ~1HR -- OPEN SEQUENCES:</b>"]
    for seq in open_seqs:
        lines.append(
            f"  {seq['direction']}: {seq['trade_count']} trades | "
            f"P&amp;L {seq['profit']:+.2f}"
        )
    lines.append("Consider closing manually or ensure Friday EOD is enabled in EA.")
    return ["\n".join(lines)]


def build_sunday_summary() -> str:
    """Build a market-open summary for Sunday."""
    account = get_account()
    lines = [
        f"🟢 <b>MARKET OPEN -- RichFX System Status</b>",
        f"📅 {broker_time_str()}",
        f"",
    ]
    if account:
        lines.append(
            f"💰 Balance: {account['currency']} {account['balance']:,.2f} | "
            f"Equity: {account['equity']:,.2f}"
        )
    for item in WATCH_LIST:
        seqs = get_sequences(item["symbol"], item["magic"])
        open_count = sum(1 for s in seqs.values() if s)
        if open_count > 0:
            lines.append(f"⚠️ {item['symbol']}: {open_count} open sequence(s) from last week")
        else:
            lines.append(f"✅ {item['symbol']}: No open sequences, clean start")
    lines.append(f"\n<i>EA is active. Monitoring every {POLL_INTERVAL_SECS}s.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def _hermes_poll_loop():
    """Background thread -- polls Telegram for Hermes queries independently of MT5."""
    offset = 0
    print("[Hermes] Inbound poll thread started", flush=True)
    while True:
        try:
            offset = handle_inbound_messages(offset)
        except Exception as e:
            print(f"[Hermes] Poll error: {e}")
        time.sleep(2)  # poll every 2s for responsiveness


def main():
    print("[RichFX Alerter] Starting...", flush=True)

    # Start Hermes inbound polling immediately -- does not require MT5
    import threading
    hermes_thread = threading.Thread(target=_hermes_poll_loop, daemon=True)
    hermes_thread.start()

    while not connect_mt5():
        print("[RichFX Alerter] Waiting for MT5... retrying in 30s", flush=True)
        time.sleep(30)

    # Track state to avoid duplicate alerts
    last_alerts       = set()
    session_high      = 0.0
    sunday_sent       = False
    friday_sent       = False   # True once the Friday close alert has been sent this week
    last_market_state = None

    send_telegram(
        f"🟢 <b>RichFX Alerter started</b>\n"
        f"Monitoring: {', '.join(w['symbol'] for w in WATCH_LIST)}\n"
        f"Broker time: {broker_time_str()}"
    )

    while True:
        try:
            market_open = is_market_open()

            # Market state change notification
            if last_market_state is not None and market_open != last_market_state:
                if market_open:
                    send_telegram(build_sunday_summary())
                    sunday_sent = True
                    friday_sent = False   # reset for next week
                else:
                    send_telegram(
                        f"🔴 <b>MARKET CLOSED</b>\n"
                        f"Broker time: {broker_time_str()}\n"
                        f"Reopens Sunday ~23:00 broker time."
                    )
            last_market_state = market_open

            # Sunday open summary (if not already sent)
            if is_sunday_open() and not sunday_sent:
                send_telegram(build_sunday_summary())
                sunday_sent = True
            elif not is_sunday_open():
                sunday_sent = False

            if not market_open:
                time.sleep(POLL_INTERVAL_SECS)
                continue

            # Update session high watermark
            account = get_account()
            if account:
                equity = account["equity"]
                if equity > session_high:
                    session_high = equity

            # Reset friday_sent once we leave the warning window so it's
            # ready to fire again next Friday
            if not is_approaching_friday_close():
                friday_sent = False

            # Sequence depth alerts -- stable (symbol, direction, depth) key,
            # NOT derived from the message text (which embeds live P&L).
            for item in WATCH_LIST:
                for stable_key, alert in check_sequence_depth(item["symbol"], item["magic"]):
                    if stable_key not in last_alerts:
                        send_telegram(alert)
                        last_alerts.add(stable_key)
                        print(f"[ALERT] {alert[:100]}")

            # Drawdown alerts -- stable key rounded to whole % rather than
            # derived from the message text (which embeds live equity/pct).
            for stable_key, alert in check_drawdown(account, session_high):
                if stable_key not in last_alerts:
                    send_telegram(alert)
                    last_alerts.add(stable_key)
                    print(f"[ALERT] {alert[:100]}")

            # Friday close alerts -- handled separately with a stable dedup key
            # per symbol so that floating P&L in the message body doesn't cause
            # the key to differ on every poll and bypass deduplication.
            if not friday_sent:
                for item in WATCH_LIST:
                    for alert in check_friday_close(item["symbol"], item["magic"]):
                        stable_key = f"FRIDAY_CLOSE:{item['symbol']}"
                        if stable_key not in last_alerts:
                            send_telegram(alert)
                            last_alerts.add(stable_key)
                            print(f"[ALERT] {alert[:100]}")
                            friday_sent = True

            # Clear old alerts every hour so they can re-trigger if still active.
            # Skip the clear during the Friday close warning window so the stable
            # dedup key can't be wiped and allow a re-fire within the same hour.
            bt = get_broker_time()
            if bt.minute == 0 and not is_approaching_friday_close():
                last_alerts.clear()

        except KeyboardInterrupt:
            print("\n[Alerter] Stopped.", flush=True)
            send_telegram("⚪ <b>RichFX Alerter stopped</b>")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            # Don't spam on repeated errors
            time.sleep(POLL_INTERVAL_SECS * 5)
            continue

        time.sleep(POLL_INTERVAL_SECS)

    mt5.shutdown()


if __name__ == "__main__":
    main()