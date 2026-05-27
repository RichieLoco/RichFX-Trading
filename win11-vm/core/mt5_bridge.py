"""
RichFX MT5 Bridge
=================
Runs on the Windows VM. Connects to MT5, fetches OHLCV data,
calculates QQE/QMP signals via indicators.py, and writes a
JSON state file that Hermes agents read over SSH.

Usage:
    python mt5_bridge.py                    # single run, print state
    python mt5_bridge.py --loop             # continuous loop (every new bar)
    python mt5_bridge.py --symbol GBPUSD    # override symbol
    python mt5_bridge.py --tf H1            # override timeframe

State file: C:\\__RichStuff\\FX\\trading_system\\data\\signals\\state.json
"""

import json
import time
import argparse
import sys
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# Add the core directory to path so we can import indicators
sys.path.insert(0, str(Path(__file__).parent))
from indicators import get_signal_state, qmp_filter, qqe_adv

# ---------------------------------------------------------------------------
# Config — loaded from watchlist.json
# ---------------------------------------------------------------------------
WATCHLIST_FILE = Path(r"C:\__RichStuff\FX\trading_system\core\watchlist.json")
STATE_DIR      = Path(r"C:\__RichStuff\FX\trading_system\data\signals")
STATE_FILE     = STATE_DIR / "state.json"   # default single-symbol file

DEFAULT_BARS   = 500
QQE_OVERBOUGHT = 60
QQE_OVERSOLD   = 40


def load_watchlist():
    """Load active pairs from central watchlist.json."""
    try:
        with open(WATCHLIST_FILE) as f:
            cfg = json.load(f)
        return [p for p in cfg.get("pairs", []) if p.get("active", True)]
    except Exception as e:
        print(f"[WARN] Could not load watchlist.json: {e} — using EURUSD H4 default")
        return [{"symbol": "EURUSD", "timeframe": "H4", "magic": 100401}]


def get_state_file(symbol: str, timeframe: str) -> Path:
    """Return per-symbol state file path. Falls back to state.json for single pair."""
    pairs = load_watchlist()
    if len(pairs) <= 1:
        return STATE_FILE   # single pair — use default filename
    return STATE_DIR / f"state_{symbol}_{timeframe}.json"


DEFAULT_SYMBOL = load_watchlist()[0].get("symbol", "EURUSD")
DEFAULT_TF     = load_watchlist()[0].get("timeframe", "H4")

TIMEFRAME_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "H8":  mt5.TIMEFRAME_H8,
    "D1":  mt5.TIMEFRAME_D1,
}

# ---------------------------------------------------------------------------
# MT5 connection
# ---------------------------------------------------------------------------
def connect_mt5() -> bool:
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
        return False
    info = mt5.terminal_info()
    print(f"[OK] Connected to MT5: {info.name} build {info.build}")
    return True


def disconnect_mt5():
    mt5.shutdown()


# ---------------------------------------------------------------------------
# Fetch OHLCV from MT5
# ---------------------------------------------------------------------------
def fetch_ohlcv(symbol: str, tf_str: str, bars: int) -> pd.DataFrame:
    tf = TIMEFRAME_MAP.get(tf_str.upper())
    if tf is None:
        raise ValueError(f"Unknown timeframe: {tf_str}. Use: {list(TIMEFRAME_MAP)}")

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Failed to fetch {symbol} {tf_str}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"open": "open", "high": "high",
                             "low": "low", "close": "close",
                             "tick_volume": "volume"})
    return df.sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Get account and position state from MT5
# ---------------------------------------------------------------------------
def get_account_state() -> dict:
    info = mt5.account_info()
    if info is None:
        return {}
    return {
        "balance":     round(info.balance, 2),
        "equity":      round(info.equity, 2),
        "margin":      round(info.margin, 2),
        "free_margin": round(info.margin_free, 2),
        "profit":      round(info.profit, 2),
        "leverage":    info.leverage,
        "currency":    info.currency,
    }


def get_open_positions(symbol: str = None) -> list:
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        result.append({
            "ticket":      p.ticket,
            "symbol":      p.symbol,
            "type":        "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
            "volume":      p.volume,
            "open_price":  p.price_open,
            "current_price": p.price_current,
            "profit":      round(p.profit, 2),
            "swap":        round(p.swap, 2),
            "magic":       p.magic,
            "comment":     p.comment,
            "open_time":   datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
        })
    return result


# ---------------------------------------------------------------------------
# Calculate sequence state from open positions
# ---------------------------------------------------------------------------
def calculate_sequence_state(positions: list, magic: int = 100401) -> dict:
    """
    Derives the EA sequence state from open positions.
    Groups positions by direction and calculates avg entry, profit, trade count.
    """
    buys  = [p for p in positions if p["type"] == "buy"  and p["magic"] == magic]
    sells = [p for p in positions if p["type"] == "sell" and p["magic"] == magic]

    def summarise(trades):
        if not trades:
            return {"active": False, "trade_count": 0, "total_lots": 0,
                    "avg_entry": 0, "total_profit": 0, "in_recovery": False}
        total_lots  = sum(t["volume"] for t in trades)
        total_cost  = sum(t["volume"] * t["open_price"] for t in trades)
        avg_entry   = total_cost / total_lots if total_lots > 0 else 0
        total_profit = sum(t["profit"] + t["swap"] for t in trades)
        return {
            "active":       True,
            "trade_count":  len(trades),
            "total_lots":   round(total_lots, 2),
            "avg_entry":    round(avg_entry, 5),
            "total_profit": round(total_profit, 2),
            "in_recovery":  False,   # EA tracks this internally; shown as flag
        }

    return {
        "buy_sequence":  summarise(buys),
        "sell_sequence": summarise(sells),
    }


# ---------------------------------------------------------------------------
# Build full state dict
# ---------------------------------------------------------------------------
def build_state(symbol: str, tf: str, bars: int = DEFAULT_BARS,
                magic: int = 100401) -> dict:

    # 1. Fetch price data
    df = fetch_ohlcv(symbol, tf, bars)
    last_bar_time = df["time"].iloc[-2].isoformat()   # last CLOSED bar

    # 2. Signal state (last closed bar = -2 in chronological array)
    signal = get_signal_state(
        close = df["close"].values,
        high  = df["high"].values,
        low   = df["low"].values,
        qqe_overbought = QQE_OVERBOUGHT,
        qqe_oversold   = QQE_OVERSOLD,
    )

    # 3. Recent signal history (last 5 closed bars)
    qmp = qmp_filter(df["close"].values, df["high"].values, df["low"].values)
    history = []
    for i in range(-6, -1):   # bars -6 to -2
        history.append({
            "bar":       df["time"].iloc[i].isoformat(),
            "buy":       bool(qmp["buy"].iloc[i]),
            "sell":      bool(qmp["sell"].iloc[i]),
            "qqe":       round(qmp["rsi_ma"].iloc[i], 4),
            "trend":     int(qmp["trend"].iloc[i]),
        })

    # 4. Current price
    tick = mt5.symbol_info_tick(symbol)
    current_price = {
        "bid":    tick.bid if tick else 0,
        "ask":    tick.ask if tick else 0,
        "spread": round((tick.ask - tick.bid) * (10 ** 5 if "JPY" not in symbol else 10 ** 3), 1) if tick else 0,
    }

    # 5. Account and positions
    account   = get_account_state()
    positions = get_open_positions(symbol)
    sequences = calculate_sequence_state(positions, magic=magic)

    # 6. Write bars to centralised DB — df and signal available here
    write_bars(symbol, tf, df, signal)

    # 7. Backfill signals for historical bars (only updates NULL rows — safe to call always)
    backfill_signals(symbol, tf, df)

    return {
        "meta": {
            "symbol":        symbol,
            "timeframe":     tf,
            "last_bar_time": last_bar_time,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "bars_used":     bars,
        },
        "price":     current_price,
        "signal":    signal,
        "history":   history,
        "sequences": sequences,
        "positions": positions,
        "account":   account,
    }


# ---------------------------------------------------------------------------
# Write state file
# ---------------------------------------------------------------------------
def write_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Use per-symbol file if multiple pairs, else default
    sym = state["meta"]["symbol"]
    tf  = state["meta"]["timeframe"]
    target = get_state_file(sym, tf)
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(target)
    # Always also write the default state.json for backward compat
    if target != STATE_FILE:
        tmp2 = STATE_FILE.with_suffix(".tmp")
        with open(tmp2, "w") as f:
            json.dump(state, f, indent=2, default=str)
        tmp2.replace(STATE_FILE)


HISTORY_FILE = STATE_DIR / "history.json"

def write_history(days: int = 90):
    """Write ALL closed trade history — unfiltered by magic. mt5_bridge owns this file."""
    from datetime import timedelta
    date_from = datetime.now(timezone.utc) - timedelta(days=days)
    deals = mt5.history_deals_get(date_from, datetime.now(timezone.utc))

    all_trades = []
    if deals:
        SKIP_TYPES = {2, 3, 4, 5, 6, 7, 8, 9}
        for d in deals:
            if d.type in SKIP_TYPES:
                continue
            if d.entry != 1:
                continue
            all_trades.append({
                "ticket":     d.ticket,
                "time":       datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
                "symbol":     d.symbol,
                "type":       "buy" if d.type == 0 else "sell",
                "volume":     d.volume,
                "price":      d.price,
                "profit":     round(d.profit,     2),
                "commission": round(d.commission, 2),
                "swap":       round(d.swap,       2),
                "net":        round(d.profit + d.commission + d.swap, 2),
                "magic":      d.magic,
                "comment":    d.comment,
            })

    all_trades.sort(key=lambda x: x["time"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days":         days,
        "trades":       all_trades,
    }
    tmp = HISTORY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    tmp.replace(HISTORY_FILE)

DB_FILE        = STATE_DIR / "richfx.db"

def get_db() -> sqlite3.Connection:
    """Get SQLite connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bars (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                timeframe   TEXT    NOT NULL,
                bar_time    TEXT    NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                volume      INTEGER NOT NULL,
                qqe         REAL,
                qmp_trend   INTEGER,
                macd        REAL,
                macd_avg    REAL,
                UNIQUE(symbol, timeframe, bar_time)
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT    NOT NULL,
                timeframe    TEXT    NOT NULL,
                bar_time     TEXT    NOT NULL,
                generated_at TEXT    NOT NULL,
                regime       TEXT,
                regime_conf  INTEGER,
                risk_status  TEXT,
                risk_score   INTEGER,
                strategy     TEXT,
                strat_score  INTEGER,
                action       TEXT,
                session      TEXT,
                spread       REAL,
                UNIQUE(symbol, timeframe, bar_time)
            );

            CREATE TABLE IF NOT EXISTS sequences (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT    NOT NULL,
                timeframe    TEXT    NOT NULL,
                direction    TEXT    NOT NULL,
                magic        INTEGER NOT NULL,
                open_time    TEXT    NOT NULL,
                close_time   TEXT    NOT NULL,
                trade_count  INTEGER NOT NULL,
                total_lots   REAL    NOT NULL,
                avg_entry    REAL    NOT NULL,
                close_price  REAL    NOT NULL,
                gross_profit REAL    NOT NULL,
                net_profit   REAL    NOT NULL,
                duration_hrs INTEGER NOT NULL,
                result       TEXT    NOT NULL,
                narrative    TEXT,
                UNIQUE(symbol, direction, open_time)
            );

            CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf
                ON bars(symbol, timeframe, bar_time);
            CREATE INDEX IF NOT EXISTS idx_decisions_symbol
                ON decisions(symbol, timeframe, bar_time);
            CREATE INDEX IF NOT EXISTS idx_sequences_symbol
                ON sequences(symbol, close_time);
        """)


def write_bars(symbol: str, tf: str, df: pd.DataFrame, signals: dict):
    """
    Write/update bar data with signal values into the centralised DB.
    Uses INSERT OR IGNORE so existing bars are never overwritten.
    Only the most recent bar gets signal values — historical bars get
    NULL signals since we don't recalculate the full indicator series.
    """
    with get_db() as conn:
        rows = []
        for i, row in df.iterrows():
            bar_time = row['time'].isoformat()
            # Only attach signals to the last closed bar
            is_last = (i == len(df) - 2)
            rows.append((
                symbol,
                tf,
                bar_time,
                row['open'],
                row['high'],
                row['low'],
                row['close'],
                int(row['volume']),
                signals.get('qqe_value')    if is_last else None,
                signals.get('qmp_trend')    if is_last else None,
                signals.get('macd')         if is_last else None,
                signals.get('avg')     if is_last else None,
            ))

        conn.executemany("""
            INSERT OR IGNORE INTO bars
                (symbol, timeframe, bar_time, open, high, low, close,
                 volume, qqe, qmp_trend, macd, macd_avg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        print(f"[DB] {symbol} {tf} — {conn.total_changes} new bars written")

def backfill_signals(symbol: str, tf: str, df: pd.DataFrame):
    """
    Backfill QQE, QMP trend, and MACD signals for all historical bars.
    Only updates rows where qqe IS NULL — safe to call on every build_state.
    """
    print(f"[DB] Backfilling signals for {symbol} {tf}...")

    qmp = qmp_filter(df["close"].values, df["high"].values, df["low"].values)

    updates = []
    for i in range(len(df) - 1):  # exclude current open bar
        bar_time = df["time"].iloc[i].isoformat()
        updates.append((
            round(float(qmp["rsi_ma"].iloc[i]), 4),
            int(qmp["trend"].iloc[i]),
            round(float(qmp["macd"].iloc[i]), 6),
            round(float(qmp["avg"].iloc[i]), 6),    # ← avg not macd_avg
            symbol,
            tf,
            bar_time,
        ))

    with get_db() as conn:
        conn.executemany("""
            UPDATE bars
            SET qqe = ?, qmp_trend = ?, macd = ?, macd_avg = ?
            WHERE symbol = ? AND timeframe = ? AND bar_time = ?
            AND qqe IS NULL
        """, updates)
        changed = conn.total_changes
        print(f"[DB] Backfilled {changed} bars for {symbol} {tf}")

def print_state_summary(state: dict):
    s = state["signal"]
    seq = state["sequences"]
    acc = state["account"]
    meta = state["meta"]

    print(f"\n{'='*60}")
    print(f"  {meta['symbol']} {meta['timeframe']}  |  {meta['last_bar_time']}")
    print(f"{'='*60}")
    print(f"  QQE Value : {s['qqe_value']:.4f}  "
          f"({'OVERBOUGHT' if s['qqe_overbought_triggered'] else 'OVERSOLD' if s['qqe_oversold_triggered'] else 'NEUTRAL'})")
    print(f"  QQE vs 50 : {'ABOVE' if s['qqe_above_50'] else 'BELOW'}")
    print(f"  QMP Trend : {'+1 (UP)' if s['qmp_trend'] == 1 else '-1 (DOWN)' if s['qmp_trend'] == -1 else '0 (FLAT)'}")
    print(f"  Buy Signal: {s['qmp_buy_signal']}  |  Sell Signal: {s['qmp_sell_signal']}")
    print(f"  MACD      : {s['macd']:.6f}  Avg: {s['macd_avg']:.6f}")
    print()
    if seq["buy_sequence"]["active"]:
        b = seq["buy_sequence"]
        print(f"  BUY SEQ   : {b['trade_count']} trades | "
              f"{b['total_lots']} lots | avg {b['avg_entry']} | "
              f"P&L ${b['total_profit']:.2f}")
    if seq["sell_sequence"]["active"]:
        s2 = seq["sell_sequence"]
        print(f"  SELL SEQ  : {s2['trade_count']} trades | "
              f"{s2['total_lots']} lots | avg {s2['avg_entry']} | "
              f"P&L ${s2['total_profit']:.2f}")
    if not seq["buy_sequence"]["active"] and not seq["sell_sequence"]["active"]:
        print(f"  SEQUENCES : None active")
    print()
    if acc:
        print(f"  Balance   : {acc['currency']} {acc['balance']:,.2f}  "
              f"Equity: {acc['equity']:,.2f}  "
              f"P&L: {acc['profit']:+.2f}")
    print(f"  State file: {STATE_FILE}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="RichFX MT5 Bridge")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--tf",     default=DEFAULT_TF)
    parser.add_argument("--bars",   type=int, default=DEFAULT_BARS)
    parser.add_argument("--magic",  type=int, default=100401)
    parser.add_argument("--loop",   action="store_true",
                        help="Run continuously, updating on each new bar")
    parser.add_argument("--interval", type=int, default=10,
                        help="Poll interval in seconds when --loop is set")
    args = parser.parse_args()

    if not connect_mt5():
        sys.exit(1)
    
    init_db()   # ← add this
    print(f"[DB] Database initialised at {DB_FILE}")

    # Load all active pairs from watchlist
    pairs = load_watchlist()
    print(f"[Bridge] Monitoring {len(pairs)} pair(s): " +
          ", ".join(f"{p.get('symbol','?')} {p.get('timeframe','H4')}" for p in pairs))

    last_bar_times = {}   # per-symbol tracking

    try:
        while True:
            try:
                any_new_bar = False
                for pair in pairs:
                    sym    = pair.get("symbol",    args.symbol)
                    tf     = pair.get("timeframe", args.tf)
                    magic  = pair.get("magic",     args.magic)
                    key    = f"{sym}_{tf}"

                    state = build_state(sym, tf, args.bars, magic)
                    current_bar = state["meta"]["last_bar_time"]

                    if current_bar != last_bar_times.get(key):
                        write_state(state)
                        print_state_summary(state)
                        last_bar_times[key] = current_bar
                        any_new_bar = True
                    elif not args.loop:
                        write_state(state)
                        print_state_summary(state)

                # Write combined history once per bar cycle — outside pair loop
                if any_new_bar or not args.loop:
                    write_history(days=90)

            except Exception as e:
                print(f"[ERROR] {e}")
                if not args.loop:
                    raise

            if not args.loop:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user")
    finally:
        disconnect_mt5()


if __name__ == "__main__":
    main()
