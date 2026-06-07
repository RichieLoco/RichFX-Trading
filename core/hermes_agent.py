"""
RichFX Hermes Agent
===================
Conversational portfolio query agent built on CrewAI.
Surfaced through the Shikigami Telegram bot.

Hermes has a set of tools it can call to fetch live data,
then reasons over the results to answer natural language questions.

Tools:
    get_live_portfolio     — live positions + account from /live
    get_last_analysis      — latest crew decisions from /last-analysis
    get_performance        — 30-day closed trade stats from /performance
    get_horizon            — cross-pair signal rankings from /horizon/last
    get_news               — upcoming news events (extracted from last analysis)
    get_signal_state       — current QQE, MACD, trend for a symbol from /state

Actions (trigger crew endpoints):
    trigger_analysis       — POST /analyse for a symbol
    trigger_horizon        — GET /horizon (fresh run)
    trigger_scout          — GET /scout (pattern confidence)
"""

import os
import json
import requests
from datetime import datetime, timezone
from typing import Optional
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
CREW_API_URL = os.getenv("CREW_API_URL",    "http://localhost:8000")
TOOL_TIMEOUT = 8   # seconds per tool HTTP call

llm_hermes = LLM(
    model       = "ollama/qwen3-14b-8k",
    base_url    = OLLAMA_URL,
    temperature = 0.3,
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool("get_live_portfolio")
def get_live_portfolio() -> str:
    """
    Fetch current open positions and live account state.
    Returns balance, equity, open P&L, margin, and details of every
    open position including symbol, direction, volume, entry price,
    current price, and per-position P&L.
    Use this for any question about current trades, account balance,
    open P&L, or margin usage.
    """
    try:
        r = requests.get(f"{CREW_API_URL}/live", timeout=TOOL_TIMEOUT)
        if not r.ok:
            return f"UNAVAILABLE: /live returned HTTP {r.status_code}"
        data = r.json()
        if "error" in data:
            return f"UNAVAILABLE: {data['error']}"

        acc  = data.get("account", {})
        pos  = data.get("positions", [])
        lines = [
            f"Account: balance={acc.get('balance','?')} {acc.get('currency','USD')} "
            f"equity={acc.get('equity','?')} open_pnl={acc.get('profit','?')} "
            f"margin={acc.get('margin','?')} free_margin={acc.get('free_margin','?')} "
            f"demo={acc.get('is_demo', True)}",
        ]
        if pos:
            for p in pos:
                lines.append(
                    f"Position: {p['symbol']} {p['type'].upper()} {p['volume']}lots "
                    f"entry={p['open_price']} current={p['current_price']} "
                    f"pnl={p['profit']} swap={p['swap']} magic={p['magic']} "
                    f"opened={p.get('open_time','?')[:16]}"
                )
        else:
            lines.append("Positions: none open")
        return "\n".join(lines)
    except Exception as e:
        return f"UNAVAILABLE: {e}"


def _fetch_analysis(symbol: str) -> str:
    """Internal helper to fetch analysis for one symbol."""
    try:
        r = requests.get(
            f"{CREW_API_URL}/last-analysis",
            params={"symbol": symbol},
            timeout=TOOL_TIMEOUT,
        )
        if not r.ok:
            return f"UNAVAILABLE: /last-analysis returned HTTP {r.status_code}"
        a = r.json()
        if not a:
            return f"No analysis cached for {symbol} yet."

        lines = [f"Last analysis for {symbol}:"]
        if a.get("regime"):
            lines.append(f"Regime: {a['regime'].get('summary','')}")
        if a.get("risk"):
            lines.append(f"Risk: {a['risk'].get('summary','')}")
        if a.get("strategy"):
            lines.append(f"Strategy: {a['strategy'].get('summary','')}")
        if a.get("execution"):
            lines.append(f"Action: {a['execution'].get('summary','')}")
        if a.get("session"):
            lines.append(f"Session: {a['session'].get('summary','')} status={a['session'].get('status','')}")
        if a.get("drawdown"):
            lines.append(f"Drawdown: {a['drawdown'].get('summary','')} status={a['drawdown'].get('status','')}")
        if a.get("correlation"):
            lines.append(f"Correlation: {a['correlation'].get('summary','')}")
        if a.get("news"):
            ev = a["news"].get("events", [])
            lines.append(f"News: {a['news'].get('summary','')} events={ev}")
        if a.get("timeframe_alignment"):
            lines.append(f"HTF alignment: {a['timeframe_alignment'].get('summary','')}")
        if a.get("volatility"):
            v = a["volatility"]
            lines.append(
                f"Volatility: {v.get('summary','')} ratio={v.get('ratio','?')}x "
                f"status={v.get('status','')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"UNAVAILABLE: {e}"


@tool("get_last_analysis")
def get_last_analysis(symbol: str = "ALL") -> str:
    """
    Fetch the most recent crew analysis for a trading pair.
    Returns regime, risk, strategy, execution, session, drawdown,
    correlation warnings, news events, HTF alignment, and volatility.
    Use for questions about signals, news, regime, or agent decisions.
    Pass symbol="ALL" (or omit) to fetch both EURUSD and AUDUSD.
    Pass a specific symbol e.g. "EURUSD" or "AUDUSD" for one pair only.
    Always use symbol="ALL" for general questions like "any news?" or
    "how does the market look?" that don't specify a pair.
    """
    if symbol == "ALL" or not symbol:
        eurusd = _fetch_analysis("EURUSD")
        audusd = _fetch_analysis("AUDUSD")
        return eurusd + "\n\n" + audusd
    return _fetch_analysis(symbol)


@tool("get_performance")
def get_performance(days: int = 30) -> str:
    """
    Fetch closed trade performance statistics for the last N days.
    Returns win rate, total net P&L, average win, average loss,
    and per-symbol breakdown.
    Use this for questions about historical performance, win rate,
    profitability, or how the system has been doing recently.
    """
    try:
        r = requests.get(
            f"{CREW_API_URL}/performance",
            params={"days": days},
            timeout=TOOL_TIMEOUT,
        )
        if not r.ok:
            return f"UNAVAILABLE: /performance returned HTTP {r.status_code}"
        data = r.json()

        lines = [f"Performance last {days} days:"]
        for acct, stats in data.get("by_account", {}).items():
            lines.append(
                f"Account {acct}: {stats.get('total_trades',0)} trades "
                f"win_rate={stats.get('win_rate',0)}% "
                f"net={stats.get('total_net',0)} "
                f"avg_win={stats.get('avg_win',0)} avg_loss={stats.get('avg_loss',0)}"
            )
        for sym, stats in data.get("by_symbol", {}).items():
            lines.append(
                f"  {sym}: {stats.get('total_trades',0)} trades "
                f"win_rate={stats.get('win_rate',0)}% net={stats.get('total_net',0)}"
            )
        if not data.get("by_account"):
            lines.append("No closed trades in this period.")
        return "\n".join(lines)
    except Exception as e:
        return f"UNAVAILABLE: {e}"


@tool("get_horizon")
def get_horizon(timeframe: str = "H4") -> str:
    """
    Fetch the most recent Horizon cross-pair analysis.
    Returns signal quality rankings across all monitored pairs,
    recommendations for which pairs to promote to active EA trading,
    and which pairs to avoid.
    Use this for questions about which pairs look best, pair expansion,
    or cross-market outlook. Timeframe should be H4 or H1.
    """
    try:
        r = requests.get(
            f"{CREW_API_URL}/horizon/last",
            params={"timeframe": timeframe},
            timeout=TOOL_TIMEOUT,
        )
        if not r.ok:
            return f"UNAVAILABLE: /horizon/last returned HTTP {r.status_code}"
        data = r.json()
        if data.get("status") == "PENDING":
            return f"Horizon {timeframe}: not yet run — no data available."
        return (
            f"Horizon {timeframe}: status={data.get('status','?')} "
            f"summary={data.get('summary','')}\n"
            f"Narrative: {data.get('narrative','')[:400]}"
        )
    except Exception as e:
        return f"UNAVAILABLE: {e}"


@tool("get_signal_state")
def get_signal_state(symbol: str = "EURUSD", timeframe: str = "H4") -> str:
    """
    Fetch the current bar signal state for a specific trading pair.
    Returns QQE value, overbought/oversold status, QMP trend direction,
    buy/sell signals, MACD values, spread, and bar time.
    Use this for specific questions about QQE levels, current signals,
    or technical state of a particular pair.
    """
    try:
        r = requests.get(
            f"{CREW_API_URL}/state",
            params={"symbol": symbol, "timeframe": timeframe},
            timeout=TOOL_TIMEOUT,
        )
        if not r.ok:
            return f"UNAVAILABLE: /state returned HTTP {r.status_code}"
        data = r.json()
        sig  = data.get("signal", {})
        meta = data.get("meta", {})
        price = data.get("price", {})
        seq  = data.get("sequences", {})

        buy_seq  = seq.get("buy_sequence",  {})
        sell_seq = seq.get("sell_sequence", {})

        lines = [
            f"Signal state {symbol} {timeframe} bar={meta.get('last_bar_time','?')[:16]}:",
            f"QQE={sig.get('qqe_value','?')} above50={sig.get('qqe_above_50','?')} "
            f"OB={sig.get('qqe_overbought_triggered','?')} OS={sig.get('qqe_oversold_triggered','?')}",
            f"QMP trend={sig.get('qmp_trend','?')} buy_signal={sig.get('qmp_buy_signal','?')} "
            f"sell_signal={sig.get('qmp_sell_signal','?')}",
            f"MACD={sig.get('macd','?')} vs avg={sig.get('macd_avg','?')} "
            f"above_avg={sig.get('macd_above_avg','?')}",
            f"Spread={price.get('spread','?')}pts bid={price.get('bid','?')}",
        ]
        if buy_seq.get("active"):
            lines.append(
                f"Buy sequence: {buy_seq.get('trade_count')} trades "
                f"avg={buy_seq.get('avg_entry')} pnl={buy_seq.get('total_profit')}"
            )
        if sell_seq.get("active"):
            lines.append(
                f"Sell sequence: {sell_seq.get('trade_count')} trades "
                f"avg={sell_seq.get('avg_entry')} pnl={sell_seq.get('total_profit')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"UNAVAILABLE: {e}"


@tool("trigger_analysis")
def trigger_analysis(symbol: str = "EURUSD", timeframe: str = "H4") -> str:
    """
    Trigger a fresh full crew analysis for a trading pair.
    This runs the 4-agent LLM chain (Regime, Risk, Strategy, Execution)
    and takes approximately 4 minutes to complete.
    Use when the user explicitly asks to run or refresh analysis.
    Returns the execution recommendation when complete.
    """
    try:
        r = requests.post(
            f"{CREW_API_URL}/analyse",
            json={"symbol": symbol, "timeframe": timeframe},
            timeout=300,
        )
        if not r.ok:
            return f"Analysis failed: HTTP {r.status_code} — {r.text[:200]}"
        data = r.json()
        action = data.get("execution", {}).get("summary", "complete")
        return f"Analysis complete for {symbol}: {action}"
    except Exception as e:
        return f"Analysis failed: {e}"


@tool("trigger_horizon")
def trigger_horizon(timeframe: str = "H4") -> str:
    """
    Trigger a fresh Horizon cross-pair analysis.
    Analyses all monitored pairs and ranks signal quality.
    Takes approximately 30-60 seconds.
    Use when the user explicitly asks for a fresh Horizon run.
    """
    try:
        r = requests.get(
            f"{CREW_API_URL}/horizon",
            params={"timeframe": timeframe},
            timeout=120,
        )
        if not r.ok:
            return f"Horizon failed: HTTP {r.status_code}"
        data = r.json()
        return f"Horizon {timeframe} complete: {data.get('summary', 'done')}"
    except Exception as e:
        return f"Horizon failed: {e}"


@tool("trigger_scout")
def trigger_scout(symbol: str = "EURUSD", timeframe: str = "H4") -> str:
    """
    Run Backtest Scout pattern confidence analysis.
    Compares current signal against 500 bars of historical patterns
    and returns a confidence score 1-10.
    Use when the user asks about pattern confidence or historical signal quality.
    """
    try:
        r = requests.get(
            f"{CREW_API_URL}/scout",
            params={"symbol": symbol, "timeframe": timeframe},
            timeout=30,
        )
        if not r.ok:
            return f"Scout failed: HTTP {r.status_code}"
        data = r.json()
        return (
            f"Scout for {symbol}: confidence={data.get('confidence','?')}/10 "
            f"similar_bars={data.get('similar_count','?')} "
            f"summary={data.get('summary','')[:200]}"
        )
    except Exception as e:
        return f"Scout failed: {e}"


# ---------------------------------------------------------------------------
# Hermes Agent
# ---------------------------------------------------------------------------

HERMES_TOOLS = [
    get_live_portfolio,
    get_last_analysis,
    get_performance,
    get_horizon,
    get_signal_state,
    trigger_analysis,
    trigger_horizon,
    trigger_scout,
]


def create_hermes_agent() -> Agent:
    return Agent(
        role="Hermes — RichFX Portfolio Assistant",
        goal=(
            "Answer natural language questions about the RichFX trading portfolio. "
            "Use your tools to fetch live data, then give a concise plain-text answer. "
            "Always use the most relevant tool(s) for the question. "
            "When no symbol is specified, default to EURUSD and AUDUSD as the active pairs. "
            "If a tool returns UNAVAILABLE, say so and answer with what you have. "
            "Never give financial advice or recommend specific trades. "
            "Be concise — this is a Telegram message, not a report. "
            "3-4 sentences maximum unless the user asks for more detail."
        ),
        backstory=(
            "You are Hermes, the intelligent assistant embedded in the RichFX "
            "algorithmic trading system. You have access to live portfolio data, "
            "agent analysis results, performance statistics, and market signals "
            "via a set of tools. You answer in plain text — no markdown, no "
            "bullet symbols. You are honest about data gaps: if a tool is "
            "unavailable you say so rather than guessing."
        ),
        tools=HERMES_TOOLS,
        llm=llm_hermes,
        verbose=False,
        allow_delegation=False,
        max_iter=6,       # max tool calls per query
        max_retry_limit=2,
    )


def run_hermes_query(question: str, history: list = None) -> str:
    """
    Run a single Hermes query. Returns plain-text answer.

    Parameters
    ----------
    question : natural language question from the user
    history  : list of prior {"role": ..., "content": ...} turns (last 5 max)
    """
    history = history or []

    # Build conversation context string from history
    history_str = ""
    if history:
        history_str = "\n\nConversation so far:\n"
        for turn in history[-(5 * 2):]:
            role = "User" if turn["role"] == "user" else "Hermes"
            history_str += f"{role}: {turn['content']}\n"
        history_str += "\n"

    task_description = (
        f"{history_str}"
        f"User question: {question}\n\n"
        "IMPORTANT: You MUST call at least one tool before answering. "
        "Never answer from your own knowledge — always fetch live data first. "
        "For news questions, call get_last_analysis with symbol=ALL. "
        "For P&L questions, call get_live_portfolio. "
        "For signal/QQE questions, call get_signal_state. "
        "For performance questions, call get_performance. "
        "For pair rankings, call get_horizon. "
        "Use your tools to fetch the relevant data, then answer concisely. "
        "Plain text only. No markdown. 3-4 sentences unless more detail is requested. "
        "If any tool returns UNAVAILABLE, acknowledge it and answer with what you have."
    )

    agent = create_hermes_agent()
    task  = Task(
        description    = task_description,
        expected_output = "A concise plain-text answer to the user's question.",
        agent          = agent,
    )
    crew = Crew(
        agents  = [agent],
        tasks   = [task],
        process = Process.sequential,
        verbose = False,
    )

    try:
        result = crew.kickoff()
        # Extract plain text from result
        if hasattr(result, "raw"):
            return str(result.raw).strip()
        if hasattr(result, "tasks_output") and result.tasks_output:
            return str(result.tasks_output[-1].raw).strip()
        return str(result).strip()
    except Exception as e:
        return f"Hermes encountered an error: {e}"
