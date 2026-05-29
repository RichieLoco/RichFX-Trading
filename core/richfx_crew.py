"""RichFX Trading Crew - complete clean version"""
import json, time, re, argparse, subprocess, requests
from datetime import datetime, timezone
from crewai import Agent, Task, Crew, Process, LLM
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
VM_HOST          = os.getenv("VM_HOST", "100.80.62.2")
VM_USER          = os.getenv("VM_USER", "richi-rdp")
VM_BASE_PATH     = os.getenv("VM_BASE_PATH", "C:/__RichStuff/FX")
STATE_FILE       = f"{VM_BASE_PATH}/trading_system/data/signals/state.json"
OLLAMA_URL       = "http://localhost:11434"
POLL_INTERVAL    = 30
POLL_INTERVAL = 30

llm_regime     = LLM(model="ollama/qwen3-14b-8k",       base_url=OLLAMA_URL, temperature=0.1)
llm_risk       = LLM(model="ollama/deepseek-r1-14b-8k", base_url=OLLAMA_URL, temperature=0.1)
llm_executor   = LLM(model="ollama/qwen25-14b-8k",      base_url=OLLAMA_URL, temperature=0.1)
llm_strategy   = LLM(model="ollama/qwen3-14b-8k",       base_url=OLLAMA_URL, temperature=0.1)
llm_perf       = LLM(model="ollama/qwen3-14b-8k",       base_url=OLLAMA_URL, temperature=0.2)
llm_scout      = LLM(model="ollama/qwen3-14b-8k",       base_url=OLLAMA_URL, temperature=0.1)
llm_journalist = LLM(model="ollama/gemma4-e4b-8k",      base_url=OLLAMA_URL, temperature=0.3)
llm_horizon    = LLM(model="ollama/gemma4-e4b-8k",      base_url=OLLAMA_URL, temperature=0.2)


def fetch_state():
    cmd = "powershell -Command \"Get-Content '" + STATE_FILE + "'\"" 
    result = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10",
         "-o", "BatchMode=yes", VM_USER + "@" + VM_HOST, cmd],
        capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError("SSH failed: " + result.stderr.strip())
    return json.loads(result.stdout)


def state_to_context(state):
    sig  = state["signal"]
    seq  = state["sequences"]
    acc  = state["account"]
    meta = state["meta"]
    hist = state.get("history", [])
    pos  = state.get("positions", [])
    b    = seq["buy_sequence"]
    s    = seq["sell_sequence"]

    parts = [
        "Symbol: " + meta["symbol"] + " " + meta["timeframe"] + "  Bar: " + meta["last_bar_time"],
        "Spread: " + str(state["price"]["spread"]) + " pts",
        "",
        "=== SIGNAL ===",
        "QQE: " + str(sig["qqe_value"]) + " (ABOVE 50: " + str(sig["qqe_above_50"]) + ")",
        "OB triggered: " + str(sig["qqe_overbought_triggered"]) + "  OS triggered: " + str(sig["qqe_oversold_triggered"]),
        "QMP Trend: " + str(sig["qmp_trend"]) + "  Buy signal: " + str(sig["qmp_buy_signal"]) + "  Sell signal: " + str(sig["qmp_sell_signal"]),
        "MACD: " + str(round(sig["macd"], 6)) + " vs Signal: " + str(round(sig["macd_avg"], 6)) + " (above avg: " + str(sig["macd_above_avg"]) + ")",
        "",
        "=== SIGNAL HISTORY (last 5 bars) ===",
    ]

    for h in hist[-5:]:
        parts.append(
            "  " + h["bar"][:16] +
            ": QQE=" + str(round(h["qqe"], 2)) +
            " trend=" + str(h["trend"]) +
            " buy=" + str(h["buy"]) +
            " sell=" + str(h["sell"])
        )

    parts.append("")
    parts.append("=== OPEN POSITIONS ===")
    if pos:
        for p in pos:
            pnl_str = ("+" if p.get("profit", 0) >= 0 else "") + str(round(p.get("profit", 0), 2))
            parts.append(
                "  " + p.get("symbol", "?") +
                " " + p.get("type", "?").upper() +
                " " + str(p.get("volume", 0)) + " lots" +
                " | entry: " + str(p.get("open_price", 0)) +
                " | now: " + str(p.get("current_price", 0)) +
                " | P&L: $" + pnl_str +
                " | opened: " + str(p.get("open_time", "?"))[:16]
            )
    else:
        parts.append("  None")

    parts.append("")
    parts.append("=== SEQUENCES ===")
    if b["active"]:
        recovery = " [IN RECOVERY]" if b.get("in_recovery") else ""
        parts.append(
            "  BUY: " + str(b["trade_count"]) + " trades" +
            " | avg entry: " + str(b["avg_entry"]) +
            " | lots: " + str(b["total_lots"]) +
            " | P&L: $" + str(round(b["total_profit"], 2)) +
            recovery
        )
    else:
        parts.append("  BUY: none")

    if s["active"]:
        recovery = " [IN RECOVERY]" if s.get("in_recovery") else ""
        parts.append(
            "  SELL: " + str(s["trade_count"]) + " trades" +
            " | avg entry: " + str(s["avg_entry"]) +
            " | lots: " + str(s["total_lots"]) +
            " | P&L: $" + str(round(s["total_profit"], 2)) +
            recovery
        )
    else:
        parts.append("  SELL: none")

    cur = acc.get("currency", "USD")
    dd  = round(max(0, acc.get("balance", 0) - acc.get("equity", 0)), 2)
    dd_pct = round(dd / acc.get("balance", 1) * 100, 2) if acc.get("balance", 0) > 0 else 0

    parts.append("")
    parts.append("=== ACCOUNT ===")
    parts.append("  Currency: " + cur)
    parts.append("  Balance:  $" + str(round(acc.get("balance", 0), 2)))
    parts.append("  Equity:   $" + str(round(acc.get("equity", 0), 2)))
    parts.append("  Open P&L: $" + str(round(acc.get("profit", 0), 2)))
    parts.append("  Margin:   $" + str(round(acc.get("margin", 0), 2)))
    parts.append("  Free margin: $" + str(round(acc.get("free_margin", 0), 2)))
    parts.append("  Drawdown: $" + str(dd) + " (" + str(dd_pct) + "%)")
    parts.append("  Leverage: " + str(acc.get("leverage", "?")))

    return "\n".join(parts)


def create_regime_agent():
    return Agent(
        role="Market Regime Analyst",
        goal="Classify market regime as RANGING, TRENDING_UP, TRENDING_DOWN, or MIXED. State whether new sequences are allowed and recommended max trades.",
        backstory="Expert in forex market structure. DCA mean-reversion works in ranging markets, fails in trending. Conservative: uncertain means MIXED.",
        llm=llm_regime, verbose=False, allow_delegation=False)

def create_strategy_agent():
    return Agent(
        role="Strategy Evaluator",
        goal=(
            "Evaluate whether the current signal is strong enough to act on. "
            "Run after Risk Governor approves. Score signal quality 1-10 and "
            "return PROCEED or HOLD. You assess signal strength, not safety — "
            "Risk Governor handles safety."
        ),
        backstory=(
            "Expert in QQE momentum and MACD confluence for DCA mean-reversion. "
            "Strong signals: QQE crossing 50 with trend and MACD aligned, consistent "
            "across multiple bars. Weak signals: QQE drifting near 50 with no "
            "directional conviction, MACD diverging, or single-bar spikes. "
            "When in doubt, HOLD — weak entries damage sequence P&L."
        ),
        llm=llm_strategy, verbose=False, allow_delegation=False,
    )

def create_performance_agent():
    return Agent(
        role="Performance Analyst",
        goal=(
            "Analyse closed trade history and produce a concise performance report. "
            "Calculate win rate, P&L trends, and flag any concerning patterns. "
            "Distinguish clearly between demo and live account performance."
        ),
        backstory=(
            "Expert in trading system performance analysis. Focuses on statistical "
            "significance, drawdown patterns, and whether the system is performing "
            "as expected. Knows that 2 trades is not enough to draw conclusions — "
            "always contextualises sample size. Clear, honest, no spin."
        ),
        llm=llm_perf, verbose=False, allow_delegation=False,
    )

def create_risk_governor():
    return Agent(
        role="Risk Governor",
        goal="Enforce hard risk limits. Approve or reject trading. You are the final authority.",
        backstory="Hard rules: max 5pct equity DD triggers halt, max 6 trades per sequence, max 30pt spread for entries, no new sequences in TRENDING markets.",
        llm=llm_risk, verbose=False, allow_delegation=False)


def create_execution_agent():
    return Agent(
        role="Execution Coordinator",
        goal="Translate the approved risk decision into one precise execution instruction.",
        backstory="Rules: QQE<40 + QMP buy dot = new buy seq. QQE>60 + QMP sell dot = new sell seq. DCA additions need QQE correct side of 50. Lots: 0.01,0.03,0.05,0.08,0.13,0.13.",
        llm=llm_executor, verbose=False, allow_delegation=False)


def create_tasks(ra, rg, sa, ea, ctx):   # ← sa added
    t1 = Task(
        description="Analyse this market state and classify the regime:\n\n" + ctx + "\n\nOutput: regime classification, confidence 1-10, New Sequences Allowed: Yes/No, Recommended Max Trades: N, 2-sentence reasoning.",
        expected_output="Regime (RANGING/TRENDING_UP/TRENDING_DOWN/MIXED), confidence 1-10, New Sequences Allowed, Recommended Max Trades, reasoning.",
        agent=ra,
    )
    t2 = Task(
        description="Using the regime assessment above and this state:\n\n" + ctx + "\n\nCheck: DD within 5pct? Sequences under 6 trades? Spread under 30pts? Regime permits new sequences? Output: Approved Yes/No, Risk Score 1-10, warnings.",
        expected_output="Approved Yes/No, Risk Score 1-10, warnings, max lots, reasoning.",
        agent=rg, context=[t1],
    )
    t3 = Task(
        description=(
            "Risk Governor has assessed the above. Now evaluate signal quality:\n\n" + ctx + "\n\n"
            "Score the signal 1-10 based on:\n"
            "- QQE momentum strength and distance from 50\n"
            "- MACD alignment with the proposed direction\n"
            "- Trend consistency across last 5 bars in signal history\n"
            "- Whether spread is acceptable for entry\n"
            "- Whether this would be a new sequence or DCA addition\n\n"
            "If Risk Governor REJECTED, output HOLD immediately — do not override risk decisions.\n\n"
            "FIRST LINE MUST BE: PROCEED or HOLD\n"
            "SECOND LINE MUST BE: confidence,N (e.g. confidence,7)\n"
            "THIRD LINE: one reason, max 20 words.\n"
            "No preamble. No markdown. No extra lines."
        ),
        expected_output="PROCEED or HOLD, confidence score, one-line reason.",
        agent=sa, context=[t1, t2],
    )
    t4 = Task(
        description=(
            "Using regime, risk, and strategy decisions above and this state:\n\n" + ctx + "\n\n"
            "If Strategy Evaluator returned HOLD, output: no_action,symbol,0,0,strategy hold\n"
            "Otherwise output one precise instruction: action, symbol, lots, sequence_trade_number, rationale."
        ),
        expected_output="FIRST LINE MUST BE CSV: action,symbol,lots,seq_num,rationale (action is one of: open_buy/open_sell/add_to_buy/add_to_sell/close_buy/close_sell/no_action). No preamble. No markdown. Just the CSV line.",
        agent=ea, context=[t1, t2, t3],
    )
    return [t1, t2, t3, t4]

def run_performance_analysis(perf_data: dict) -> str:
    """
    Run performance agent standalone — not part of the main crew chain.
    Called by the /performance/analyse endpoint on a daily schedule.
    """
    pa = create_performance_agent()

    # Build context string from performance data
    lines = [
        f"Performance Report — Last {perf_data['days']} days",
        f"Generated: {perf_data['generated_at']}",
        "",
    ]

    for account, stats in perf_data.get("by_account", {}).items():
        lines.append(f"=== {account.upper()} ACCOUNT ===")
        lines.append(f"  Total trades: {stats['total_trades']}")
        lines.append(f"  Win rate:     {stats['win_rate']}%  ({stats['wins']}W / {stats['losses']}L)")
        lines.append(f"  Total net:    ${stats['total_net']}")
        lines.append(f"  Avg win:      ${stats['avg_win']}")
        lines.append(f"  Avg loss:     ${stats['avg_loss']}")
        lines.append("")

    for symbol, stats in perf_data.get("by_symbol", {}).items():
        lines.append(f"  {symbol} ({stats['account']}): "
                     f"{stats['total_trades']} trades | "
                     f"WR {stats['win_rate']}% | "
                     f"Net ${stats['total_net']}")

    ctx = "\n".join(lines)

    task = Task(
        description=(
            f"Analyse this trading performance data and write a concise report:\n\n{ctx}\n\n"
            "Include: overall assessment, win rate commentary, P&L trend, "
            "any concerns or positives worth noting. "
            "Note the sample size — be appropriately cautious with small samples. "
            "Max 150 words. Plain text, no markdown."
        ),
        expected_output="Concise performance assessment, max 150 words, plain text.",
        agent=pa,
    )

    from crewai import Crew, Process
    crew  = Crew(agents=[pa], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    return getattr(result, "raw", str(result))

# ─── Journalist Agent ─────────────────────────────────────────────────────────

def create_journalist_agent():
    return Agent(
        role="Trading Journalist",
        goal=(
            "Write clear, concise post-mortems for completed trading sequences. "
            "Explain what happened, why, and what the outcome was. "
            "Plain English, no jargon, max 100 words."
        ),
        backstory=(
            "Experienced financial journalist covering algorithmic trading. "
            "Writes for an audience that understands trading but wants clarity. "
            "Never sensationalises. Always contextualises with sample size."
        ),
        llm=llm_journalist, verbose=False, allow_delegation=False,
    )


def run_journalist_narrative(sequence: dict) -> str:
    """Generate a plain-English post-mortem for a completed sequence."""
    agent     = create_journalist_agent()
    direction = sequence.get("direction", "?")
    symbol    = sequence.get("symbol", "?")
    trades    = sequence.get("trade_count", 0)
    avg_entry = sequence.get("avg_entry", 0)
    close_px  = sequence.get("close_price", 0)
    net       = sequence.get("net_profit", 0)
    duration  = sequence.get("duration_hrs", 0)
    result    = sequence.get("result", "?")

    ctx = (
        f"Completed sequence: {symbol} {direction}\n"
        f"Trades in sequence: {trades}\n"
        f"Average entry: {avg_entry}\n"
        f"Close price: {close_px}\n"
        f"Net P&L: ${net:+.2f}\n"
        f"Duration: {duration} hours\n"
        f"Result: {result}\n"
    )
    task = Task(
        description=(
            f"Write a post-mortem for this completed trading sequence:\n\n{ctx}\n\n"
            "Cover: what the sequence was, how it progressed, the final result, "
            "and one observation. Max 100 words. Plain text. No markdown."
        ),
        expected_output="Plain text post-mortem, max 100 words.",
        agent=agent,
    )
    from crewai import Crew, Process
    result_obj = Crew(agents=[agent], tasks=[task],
                      process=Process.sequential, verbose=False).kickoff()
    raw = getattr(result_obj, "raw", str(result_obj)).strip()
    # Strip Gemma 4 thinking tokens
    raw = re.sub(r"Thinking\.\.\..*?\.\.\.done thinking\.", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"Thinking Process:.*?(?=\n[A-Z]|\Z)", "", raw, flags=re.DOTALL).strip()
    return raw


# ─── Backtest Scout Agent ─────────────────────────────────────────────────────

def create_scout_agent():
    return Agent(
        role="Backtest Scout",
        goal=(
            "Compare current signal conditions to historical bar patterns. "
            "Find similar QQE, MACD, and regime setups and report how they performed. "
            "Produce a confidence score 1-10."
        ),
        backstory=(
            "Quantitative analyst specialising in pattern recognition. "
            "Knows that past performance is not a guarantee but statistical "
            "base rates from large samples are meaningful. "
            "Always reports sample size. Never overfits to small samples."
        ),
        llm=llm_scout, verbose=False, allow_delegation=False,
    )

def run_scout_analysis(current_signal: dict, similar_bars: list) -> str:
    """Analyse historical similar bars and produce a confidence score."""
    if not similar_bars:
        return "confidence,1\nInsufficient historical data for pattern matching."

    agent  = create_scout_agent()
    total  = len(similar_bars)

    ctx = (
        f"Current signal:\n"
        f"  QQE: {current_signal.get('qqe_value', '?')}\n"
        f"  QMP Trend: {current_signal.get('qmp_trend', '?')}\n"
        f"  MACD: {current_signal.get('macd', '?')}\n"
        f"  MACD avg: {current_signal.get('macd_avg', '?')}\n"
        f"  MACD above avg: {current_signal.get('macd_above_avg', '?')}\n\n"
        f"Similar historical bars found: {total} (same QQE range ±5, same trend)\n"
        f"Sample (most recent 10):\n"
    )
    for b in similar_bars[:10]:
        ctx += (
            f"  {str(b.get('bar_time','?'))[:16]} — "
            f"QQE={b.get('qqe','?')} "
            f"trend={b.get('qmp_trend','?')} "
            f"macd={b.get('macd','?')}\n"
        )

    task = Task(
        description=(
            f"Analyse these historical similar signal setups:\n\n{ctx}\n\n"
            "Note: outcome data is not yet available. Score confidence based on:\n"
            "- Signal consistency across the similar bars\n"
            "- Whether QQE, trend, and MACD are all aligned\n"
            "- Sample size (more similar bars = more reliable pattern)\n\n"
            "FIRST LINE MUST BE EXACTLY: confidence,N (where N is 1-10)\n"
            "SECOND LINE: one sentence explanation, max 20 words.\n"
            "No other text. No preamble. No markdown."
        ),
        expected_output="confidence,N then one-line explanation.",
        agent=agent,
    )
    from crewai import Crew, Process
    result_obj = Crew(agents=[agent], tasks=[task],
                      process=Process.sequential, verbose=False).kickoff()
    return getattr(result_obj, "raw", str(result_obj)).strip()

# ─── Horizon Agent ────────────────────────────────────────────────────────────
def create_horizon_agent():
    return Agent(
        role="Horizon Analyst",
        goal=(
            "Analyse signal quality across multiple forex pairs and timeframes. "
            "Identify which pairs show the most consistent, tradeable signals. "
            "Recommend which monitoring pairs are worth promoting to full EA trading."
        ),
        backstory=(
            "Quantitative analyst specialising in cross-market opportunity identification. "
            "Evaluates signal consistency, trend clarity, and QQE behaviour across pairs. "
            "Knows that consistent, decisive signals matter more than occasional spikes. "
            "Conservative — only recommends promotion when evidence is strong. "
            "Always contextualises recommendations with sample size caveats."
        ),
        llm=llm_horizon, verbose=False, allow_delegation=False,
    )


def run_horizon_analysis(pair_data: dict, active_pairs: list) -> str:
    """
    Analyse signal quality across monitoring pairs and recommend expansion candidates.
    pair_data: dict of {symbol: {bar_count, avg_qqe, qqe_range, trend_consistency, decisive_pct}}
    active_pairs: list of currently active EA pairs
    """
    if not pair_data:
        return "HORIZON: Insufficient data — monitoring pairs still seeding."

    agent = create_horizon_agent()

    ctx = (
        f"Currently active EA pairs: {', '.join(active_pairs)}\n\n"
        f"Monitoring pair signal analysis ({list(pair_data.values())[0]['timeframe']}):\n\n"
    )

    for sym, d in pair_data.items():
        status = "ACTIVE" if d["is_active"] else "monitoring"
        ctx += (
            f"  {sym} [{status}]\n"
            f"    Bars analysed:      {d['bar_count']}\n"
            f"    Avg QQE:            {d['avg_qqe']} (50 = neutral)\n"
            f"    QQE range:          {d['qqe_range']} (higher = more volatile signals)\n"
            f"    Trend consistency:  {d['trend_consistency']}% (% bars with clear direction)\n"
            f"    Decisive signals:   {d['decisive_pct']}% (QQE clearly above 55 or below 45)\n\n"
        )

    task = Task(
        description=(
            f"Analyse this cross-pair signal quality data and provide expansion recommendations:\n\n{ctx}\n\n"
            "The trading system uses a DCA mean-reversion strategy on H4 bars.\n"
            "Best candidates have: high trend consistency, high decisive signal %, "
            "moderate QQE range (not too chaotic), sufficient bar history.\n\n"
            "Output format (plain text, no markdown):\n"
            "SUMMARY: one sentence overview\n\n"
            "RANKINGS: list each pair with a score 1-10 and one reason\n\n"
            "PROMOTE: list any pairs ready for EA trading (or 'None yet')\n\n"
            "MONITOR: list pairs to keep watching\n\n"
            "CAUTION: list any pairs to avoid and why\n\n"
            "NOTE: always mention sample size — more bars = more reliable assessment.\n"
            "No preamble. Plain text only."
        ),
        expected_output="SUMMARY, RANKINGS, PROMOTE, MONITOR, CAUTION sections in plain text.",
        agent=agent,
    )

    from crewai import Crew, Process
    result_obj = Crew(
        agents=[agent], tasks=[task],
        process=Process.sequential, verbose=False
    ).kickoff()

    raw = getattr(result_obj, "raw", str(result_obj)).strip()
    # Strip Gemma 4 thinking tokens
    raw = re.sub(r"Thinking\.\.\..*?\.\.\.done thinking\.", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"Thinking Process:.*?(?=SUMMARY|$)", "", raw, flags=re.DOTALL).strip()
    return raw

# ─── Meta-Supervisor Agent ────────────────────────────────────────────────────
llm_meta = LLM(model="ollama/qwen3-14b-8k", base_url=OLLAMA_URL, temperature=0.2)

def create_meta_agent():
    return Agent(
        role="Meta-Supervisor",
        goal=(
            "Review crew decision patterns across many bars. "
            "Identify systematic biases, avoidance patterns, or calibration issues. "
            "Flag when the crew is behaving in ways that warrant human review."
        ),
        backstory=(
            "Senior risk manager reviewing algorithmic decision systems. "
            "Looks for patterns humans miss: systematic over-rejection, "
            "HOLD rate too high, regime misclassification streaks. "
            "Constructive and specific — always suggests what to investigate."
        ),
        llm=llm_meta, verbose=False, allow_delegation=False,
    )


def run_meta_analysis(decisions: list, performance: dict) -> str:
    """Analyse recent crew decisions for systematic patterns."""
    if len(decisions) < 10:
        return f"META: Only {len(decisions)} decisions logged — need at least 10 for meaningful analysis."

    agent    = create_meta_agent()
    total    = len(decisions)
    approved = sum(1 for d in decisions if d.get("risk_status") == "APPROVED")
    holds    = sum(1 for d in decisions if d.get("strategy") == "HOLD")
    actions  = sum(1 for d in decisions if d.get("action") not in ("no_action", None, ""))
    avg_spread = round(sum(d.get("spread", 0) for d in decisions) / total, 1)

    regimes = {}
    for d in decisions:
        r = (d.get("regime") or "UNKNOWN").split("(")[0].strip()
        regimes[r] = regimes.get(r, 0) + 1

    ctx = (
        f"Decision log — last {total} bars:\n"
        f"  Risk approvals:     {approved}/{total} ({round(approved/total*100,1)}%)\n"
        f"  Strategy HOLD rate: {holds}/{total} ({round(holds/total*100,1)}%)\n"
        f"  Actual actions:     {actions}/{total} ({round(actions/total*100,1)}%)\n"
        f"  Average spread:     {avg_spread}pts\n"
        f"  Regime distribution: {regimes}\n\n"
        f"Performance (last 30 days):\n"
    )
    for acct, stats in performance.get("by_account", {}).items():
        ctx += (
            f"  {acct.upper()}: {stats['total_trades']} trades, "
            f"WR {stats['win_rate']}%, Net ${stats['total_net']}\n"
        )

    task = Task(
        description=(
            f"Review this crew decision pattern and flag any concerns:\n\n{ctx}\n\n"
            "Look for: systematic over-rejection, HOLD rate too high/low, "
            "spread threshold issues, regime classification streaks.\n\n"
            "Output exactly:\n"
            "STATUS: OK | REVIEW | ALERT\n"
            "FINDINGS: [2-3 bullet points]\n"
            "RECOMMENDATION: [one action item]\n"
            "No preamble. Plain text."
        ),
        expected_output="STATUS, FINDINGS, RECOMMENDATION in plain text.",
        agent=agent,
    )
    from crewai import Crew, Process
    result_obj = Crew(agents=[agent], tasks=[task],
                      process=Process.sequential, verbose=False).kickoff()
    return getattr(result_obj, "raw", str(result_obj)).strip()

AGENT_LABELS = {
    "Market Regime Analyst": ("\U0001f310", "REGIME"),
    "Risk Governor":          ("\U0001f6e1", "RISK"),
    "Strategy Evaluator":     ("\U0001f4c8", "STRAT"), 
    "Execution Coordinator":  ("\u26a1", "ACTION"),
}


def extract_compact(text, role):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"[*`]|```\w*", "", text).strip()

    if role == "Market Regime Analyst":
        r = re.search(r"(?i)(?:regime|classification)[:\s]+([A-Z_]+)", text)
        c = re.search(r"(?i)confidence[:\s]+(\d+)", text)
        a = re.search(r"(?i)new sequences allowed[:\s]+(\w+)", text)
        m = re.search(r"(?i)(?:recommended )?max trades?[:\s]+(\d+)", text)
        regime = r.group(1) if r else "UNKNOWN"
        conf   = c.group(1) if c else "?"
        allow  = a.group(1).upper() if a else "?"
        maxt   = m.group(1) if m else "?"
        return regime + " (" + conf + "/10) | New seqs: " + allow + " | Max: " + maxt

    elif role == "Risk Governor":
        ap = re.search(r"(?i)approved[?\s:\*]+(yes|no|approved|reject\w*)", text)
        sc = re.search(r"(?i)risk\s*score[:\s]+(\d+)", text)
        status = "APPROVED" if ap and ap.group(1).lower() in ("yes", "approved") else "REJECTED"
        score  = sc.group(1) if sc else "?"

        # Try multiple warning patterns
        warn = ''
        for pattern in [
            r"(?i)\*{0,2}warnings?\*{0,2}[:\s\-]+([^\n]{10,200})",
            r"(?i)warning[^:\n]*[:\s]+([^\n]{10,200})",
            r"(?i)(?:caution|concern|note)[:\s]+([^\n]{10,200})",
        ]:
            m = re.search(pattern, text)
            if m:
                warn = m.group(1).strip().lstrip('*- ').strip()[:200]
                break

        # If still no warning, take first substantive line after the decision
        if not warn:
            for line in text.split('\n'):
                line = line.strip().lstrip('*- ').strip()
                if len(line) > 20 and 'approved' not in line.lower() and 'risk score' not in line.lower():
                    warn = line[:200]
                    break

        return f"{status} ({score}/10) | {warn or 'see details'}"
    
    elif role == "Strategy Evaluator":
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        decision = 'UNKNOWN'
        score    = '?'
        reason   = ''
        for line in lines:
            u = line.upper()
            if 'PROCEED' in u and decision == 'UNKNOWN':
                decision = 'PROCEED'
            elif 'HOLD' in u and decision == 'UNKNOWN':
                decision = 'HOLD'
            m = re.search(r'confidence[,:\s]+(\d+)', line, re.IGNORECASE)
            if m:
                score = m.group(1)
            elif not reason:  # ← removed the score==? condition, capture first non-decision/non-confidence line
                if 'PROCEED' not in u and 'HOLD' not in u and 'confidence' not in u.lower():
                    reason = line[:100]
        return f"{decision} ({score}/10) | {reason}"

    elif role == "Execution Coordinator":
        import re as _re
        KNOWN = {
            "no_action", "open_buy", "open_sell", "opensell", "openbuy",
            "add_to_buy", "add_to_sell", "close_buy", "close_sell"
        }
        # Normalise escaped newlines
        t = text.replace("\\n", "\n").replace("\\t", " ")
        # Strip markdown bold markers
        t = t.replace("**", "")
        action = None
        lots = "0.01"
        reason = ""
        # Scan every line for first known action keyword
        for ln in t.split("\n"):
            ln = ln.strip().lstrip("-• ").strip()
            if not ln:
                continue
            # CSV style: "open_buy, EURUSD, 0.01, 1, rationale..."
            parts = [p.strip().strip('"').strip("'") for p in ln.split(",")]
            first = parts[0].lower()
            if first in KNOWN:
                action = first.upper()
                if len(parts) >= 3:
                    import re as __re
                    if __re.match(r"[\d.]+", parts[2]):
                        lots = parts[2]
                if len(parts) >= 4:
                    reason = ", ".join(parts[3:]).strip().lstrip(",").strip()
                break
            # Key-value style: "action: open_buy" or "action open_buy"
            import re as __re
            kv = __re.match(r"(?i)action[:\s]+([\w_]+)", ln)
            if kv and kv.group(1).lower() in KNOWN:
                action = kv.group(1).upper()
                lo = __re.search(r"(?i)lots[:\s]+([\d.]+)", t)
                if lo:
                    lots = lo.group(1)
                ra = __re.search(r"(?i)rationale[:\s]+(.+?)(?:\n|$)", t)
                if ra:
                    reason = ra.group(1).strip()
                break
        action = action or "UNKNOWN"
        if "NO_ACTION" in action or action == "NO":
            return "NO ACTION | " + reason[:300]
        return action + " " + lots + " lots | " + reason[:300]
    else:
        for line in text.split("\n"):
            if len(line.strip()) > 15:
                return line.strip()[:400]
        return text[:120]


def format_output(result, state, task_outputs=None):
    meta  = state["meta"]
    sig   = state["signal"]
    seq   = state["sequences"]
    acc   = state["account"]
    price = state["price"]
    bt    = datetime.fromisoformat(meta["last_bar_time"]).strftime("Bar: %a %d %b %H:%M UTC")
    qqe   = sig["qqe_value"]
    qlbl  = "OB" if qqe >= 60 else "OS" if qqe <= 40 else "NEUTRAL"
    tlbl  = {1: "UP", -1: "DOWN", 0: "FLAT"}.get(sig["qmp_trend"], "?")
    b = seq["buy_sequence"]
    s = seq["sell_sequence"]
    if b["active"] and s["active"]:
        sq = "BUY " + str(b["trade_count"]) + "T $" + str(round(b["total_profit"])) + " | SELL " + str(s["trade_count"]) + "T $" + str(round(s["total_profit"]))
    elif b["active"]:
        sq = "BUY " + str(b["trade_count"]) + " trades avg " + str(b["avg_entry"]) + " $" + str(round(b["total_profit"]))
    elif s["active"]:
        sq = "SELL " + str(s["trade_count"]) + " trades avg " + str(s["avg_entry"]) + " $" + str(round(s["total_profit"]))
    else:
        sq = "No open sequences"
    cur = acc.get("currency", "GBP")
    eq  = acc.get("equity", 0)
    trend_icon = {1: "\u2191", -1: "\u2193", 0: "\u2014"}.get(sig["qmp_trend"], "?")
    lines = [
        "\U0001f916 <b>RichFX Crew Analysis</b>",
        "\U0001f4ca <b>" + meta["symbol"] + " " + meta["timeframe"] + "</b>  |  Last Bar Close: " + bt,
        "QQE: " + str(round(qqe,2)) + " | Trend: " + trend_icon + " " + tlbl,
        "Spread " + str(round(price["spread"])) + "pts | " + sq,
        cur + " " + str(round(eq)) + " equity",
        "\u2500" * 19,
    ]
    if task_outputs:
        for t in task_outputs:
            em, lb = AGENT_LABELS.get(t["agent"], ("\U0001f916", t["agent"][:8].upper()))
            lines.append(em + " <b>" + lb + ":</b> " + extract_compact(t["output"], t["agent"]))
    else:
        raw = str(result.raw)[:300] if hasattr(result, "raw") else str(result)[:300]
        lines.append(raw)
    return "\n".join(lines)


def send_telegram(message):
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n[TELEGRAM]\n" + message + "\n")
        return True
    try:
        r = requests.post(
            "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("[ERROR] Telegram: " + str(e))
        return False


def run_crew(state):
    ctx = state_to_context(state)
    ra = create_regime_agent()
    rg = create_risk_governor()
    sa  = create_strategy_agent() 
    ea = create_execution_agent()
    tasks = create_tasks(ra, rg, sa, ea, ctx)
    crew = Crew(agents=[ra, rg, sa, ea], tasks=tasks, process=Process.sequential, verbose=False)
    sym = state["meta"]["symbol"]
    tf  = state["meta"]["timeframe"]
    print("[Crew] " + sym + " " + tf + "...")
    print("  Regime: " + llm_regime.model + " | Risk: " + llm_risk.model +
          " | Strategy: " + llm_strategy.model + " | Exec: " + llm_executor.model)
    result = crew.kickoff()
    outputs = []
    if hasattr(result, "tasks_output") and result.tasks_output:
        for t in result.tasks_output:
            outputs.append({
                "agent":  getattr(t, "agent", "Unknown"),
                "output": getattr(t, "raw", str(t)),
            })
    return result, outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    last_bar = None
    print("=== RichFX CrewAI Trading Crew ===")
    print("Models: " + llm_regime.model + " | " + llm_risk.model + " | " + llm_executor.model)
    print("Mode: " + ("watch" if args.watch else "single run") + "\n")
    while True:
        try:
            state = fetch_state()
            bar = state["meta"]["last_bar_time"]
            if bar != last_bar:
                last_bar = bar
                print("[" + datetime.now().strftime("%H:%M:%S") + "] New bar: " + bar)
                result, outputs = run_crew(state)
                send_telegram(format_output(result, state, outputs))
                print("[Done] Sent to Telegram")
            elif not args.watch:
                result, outputs = run_crew(state)
                send_telegram(format_output(result, state, outputs))
                break
        except KeyboardInterrupt:
            print("\n[Crew] Stopped.")
            break
        except Exception as e:
            print("[ERROR] " + str(e))
            if not args.watch:
                raise
        if not args.watch:
            break
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
