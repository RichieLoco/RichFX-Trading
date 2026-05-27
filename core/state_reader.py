"""
RichFX State Reader
===================
Runs on MS-S1 MAX (Ubuntu). SSHes to the Windows VM and reads
the state.json written by mt5_bridge.py.

Used by Hermes agents to get current market/signal/position state
without needing a direct MT5 connection.

Usage:
    from state_reader import get_state, watch_state

    # One-shot read
    state = get_state()
    print(state["signal"]["qqe_value"])

    # Watch for new bars, call handler on each update
    watch_state(on_new_bar=my_handler)
"""

import json
import time
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Config — matches your Tailscale + SSH setup
# ---------------------------------------------------------------------------
VM_HOST      = "100.80.62.2"
VM_USER      = "richi-rdp"
STATE_FILE   = "C:/__RichStuff/FX/trading_system/data/signals/state.json"
SSH_TIMEOUT  = 10   # seconds


# ---------------------------------------------------------------------------
# Core read
# ---------------------------------------------------------------------------
def get_state() -> dict:
    """
    Fetch and return the current state dict from the VM.
    Raises RuntimeError if SSH or JSON parsing fails.
    """
    result = subprocess.run(
        ["ssh",
         "-o", "ConnectTimeout=10",
         "-o", "BatchMode=yes",
         "-o", "LogLevel=ERROR",
         f"{VM_USER}@{VM_HOST}",
         f"powershell -Command \"Get-Content '{STATE_FILE}'\""],
        capture_output=True, text=True, timeout=SSH_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"SSH failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def get_state_safe() -> Optional[dict]:
    """Like get_state() but returns None on any error instead of raising."""
    try:
        return get_state()
    except Exception as e:
        print(f"[state_reader] Warning: {e}")
        return None


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------
def get_signal(state: dict = None) -> dict:
    """Return just the signal block. Fetches fresh state if none passed."""
    return (state or get_state())["signal"]


def get_sequences(state: dict = None) -> dict:
    """Return buy/sell sequence state."""
    return (state or get_state())["sequences"]


def get_account(state: dict = None) -> dict:
    """Return account state (balance, equity, etc.)."""
    return (state or get_state())["account"]


def get_positions(state: dict = None) -> list:
    """Return list of open positions."""
    return (state or get_state())["positions"]


def is_new_bar(state: dict, last_bar_time: str) -> bool:
    """True if state contains a bar we haven't seen yet."""
    return state["meta"]["last_bar_time"] != last_bar_time


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------
def watch_state(
    on_new_bar: Callable[[dict], None],
    poll_interval: int = 15,
    on_error: Optional[Callable[[Exception], None]] = None,
):
    """
    Poll the VM every `poll_interval` seconds.
    Calls `on_new_bar(state)` whenever a new bar is detected.

    Example:
        def handle(state):
            sig = state["signal"]
            print(f"New bar: QQE={sig['qqe_value']:.2f}")

        watch_state(on_new_bar=handle)
    """
    print(f"[state_reader] Watching {VM_HOST}:{STATE_FILE}")
    print(f"[state_reader] Poll interval: {poll_interval}s  |  Ctrl-C to stop\n")

    last_bar_time = None

    while True:
        try:
            state = get_state()
            current_bar = state["meta"]["last_bar_time"]

            if current_bar != last_bar_time:
                if last_bar_time is not None:   # skip the very first read
                    print(f"[state_reader] New bar: {current_bar}")
                    on_new_bar(state)
                else:
                    print(f"[state_reader] Connected. Current bar: {current_bar}")
                    _print_summary(state)
                last_bar_time = current_bar

        except KeyboardInterrupt:
            print("\n[state_reader] Stopped.")
            break
        except Exception as e:
            if on_error:
                on_error(e)
            else:
                print(f"[state_reader] Error: {e}")

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------
def _print_summary(state: dict):
    sig  = state["signal"]
    seq  = state["sequences"]
    acc  = state["account"]
    meta = state["meta"]
    price = state["price"]

    print(f"{'─'*56}")
    print(f"  {meta['symbol']} {meta['timeframe']}  |  {meta['last_bar_time'][:19]}Z")
    print(f"  Bid: {price['bid']}  Ask: {price['ask']}  Spread: {price['spread']} pts")
    print(f"{'─'*56}")

    qqe_val = sig["qqe_value"]
    if qqe_val >= 60:
        ob_label = "  🔴 OVERBOUGHT"
    elif qqe_val <= 40:
        ob_label = "  🟢 OVERSOLD"
    else:
        ob_label = "  NEUTRAL"
    side = "ABOVE 50" if sig["qqe_above_50"] else "BELOW 50"
    print(f"  QQE   : {qqe_val:.4f}  {side}{ob_label}")

    trend_str = {1: "↑ UP", -1: "↓ DOWN", 0: "─ FLAT"}.get(sig["qmp_trend"], "?")
    print(f"  Trend : {trend_str}")
    print(f"  Signal: Buy={sig['qmp_buy_signal']}  Sell={sig['qmp_sell_signal']}")
    print(f"  MACD  : {sig['macd']:.6f}  Avg: {sig['macd_avg']:.6f}")

    print()
    b = seq["buy_sequence"]
    s = seq["sell_sequence"]
    if b["active"]:
        print(f"  BUY   : {b['trade_count']} trades | {b['total_lots']} lots | "
              f"avg {b['avg_entry']} | P&L ${b['total_profit']:+.2f}")
    if s["active"]:
        print(f"  SELL  : {s['trade_count']} trades | {s['total_lots']} lots | "
              f"avg {s['avg_entry']} | P&L ${s['total_profit']:+.2f}")
    if not b["active"] and not s["active"]:
        print(f"  Sequences: none active")

    if acc:
        print(f"\n  {acc['currency']} {acc['balance']:>10,.2f} balance  "
              f"|  {acc['equity']:>10,.2f} equity  "
              f"|  {acc['profit']:>+8.2f} open P&L")
    print(f"{'─'*56}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RichFX State Reader")
    parser.add_argument("--watch", action="store_true",
                        help="Watch for new bars continuously")
    parser.add_argument("--interval", type=int, default=15,
                        help="Poll interval in seconds (default: 15)")
    args = parser.parse_args()

    if args.watch:
        def on_bar(state):
            _print_summary(state)

        watch_state(on_new_bar=on_bar, poll_interval=args.interval)
    else:
        state = get_state()
        _print_summary(state)
