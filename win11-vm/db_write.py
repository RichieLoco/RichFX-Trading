"""
RichFX DB Writer
================
Accepts a JSON payload via stdin and writes it to richfx.db.
Called by crew_api.py via SSH.

Usage:
    echo '{"table": "decisions", "data": {...}}' | python db_write.py
"""
import sys
import json
import sqlite3
from pathlib import Path

DB_FILE = Path(r"C:\__RichStuff\FX\trading_system\data\signals\richfx.db")

def write_decision(data: dict):
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("""
        INSERT OR REPLACE INTO decisions
        (symbol, timeframe, bar_time, generated_at, regime, regime_conf,
         risk_status, risk_score, strategy, strat_score, action, session, spread)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["symbol"],
        data["timeframe"],
        data["bar_time"],
        data["generated_at"],
        data.get("regime", ""),
        data.get("regime_conf", 0),
        data.get("risk_status", ""),
        data.get("risk_score", 0),
        data.get("strategy", ""),
        data.get("strat_score", 0),
        data.get("action", ""),
        data.get("session", ""),
        data.get("spread", 0),
    ))
    conn.commit()
    conn.close()
    print(f"[DB] Decision written: {data['symbol']} {data['bar_time']}")


def write_sequence(data: dict):
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("""
        INSERT OR IGNORE INTO sequences
        (symbol, timeframe, direction, magic, open_time, close_time,
         trade_count, total_lots, avg_entry, close_price, gross_profit,
         net_profit, duration_hrs, result, narrative)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["symbol"],
        data["timeframe"],
        data["direction"],
        data["magic"],
        data["open_time"],
        data["close_time"],
        data["trade_count"],
        data["total_lots"],
        data["avg_entry"],
        data["close_price"],
        data["gross_profit"],
        data["net_profit"],
        data["duration_hrs"],
        data["result"],
        data.get("narrative", None),
    ))
    conn.commit()
    conn.close()
    print(f"[DB] Sequence written: {data['symbol']} {data['direction']}")


def update_narrative(data: dict):
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("""
        UPDATE sequences SET narrative = ?
        WHERE id = ?
    """, (data["narrative"], data["sequence_id"]))
    conn.commit()
    conn.close()
    print(f"[DB] Narrative updated for sequence {data['sequence_id']}")


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        table   = payload.get("table")
        data    = payload.get("data", {})

        if table == "decisions":
            write_decision(data)
        elif table == "sequences":
            write_sequence(data)
        elif table == "narrative":
            update_narrative(data)
        else:
            print(f"[DB] Unknown table: {table}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"[DB] Error: {e}", file=sys.stderr)
        sys.exit(1)
