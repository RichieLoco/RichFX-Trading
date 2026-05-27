"""
RichFX Signal Indicators — Python port of MT5 source code
=========================================================
Exact ports of:
  - QQE Adv.mq5      (contactchristinali@gmail.com, v1.00)
  - MACD_Platinum.mq5 (contactchristinali@gmail.com, v1.2, ZeroLag=True)
  - QMP_Filter.mq5   (contactchristinali@gmail.com, v1.4)

All arrays are chronological (index 0 = oldest bar), matching the MQL5
internal calculation order (not the "as series" display order).

Usage:
    import pandas as pd
    from indicators import qqe_adv, macd_platinum, qmp_filter

    # df must have a 'close', 'high', 'low' column, chronological order
    qqe   = qqe_adv(df['close'], sf=7, rsi_period=14, wp=1)
    macd  = macd_platinum(df['close'], fast=12, slow=26, smooth=9)
    qmp   = qmp_filter(df['close'], df['high'], df['low'],
                       fast=12, slow=26, smooth=9, sf=1, rsi_period=8, wp=3)

    # Latest closed bar signal (index -2 = bar[1] in MT5 "as series")
    buy_signal  = qmp['buy'].iloc[-2]
    sell_signal = qmp['sell'].iloc[-2]
    qqe_value   = qqe['rsi_ma'].iloc[-2]
"""

import numpy as np
import pandas as pd
from typing import Union


# ---------------------------------------------------------------------------
# Helper: EMA
# ---------------------------------------------------------------------------
def _ema(series: np.ndarray, period: int, start: int = 1) -> np.ndarray:
    """Standard EMA computed chronologically from index `start`."""
    coeff = 2.0 / (1 + period)
    out = np.zeros(len(series))
    out[:start] = series[:start]   # seed
    for i in range(start, len(series)):
        out[i] = coeff * series[i] + (1 - coeff) * out[i - 1]
    return out


# ---------------------------------------------------------------------------
# QQE Adv
# ---------------------------------------------------------------------------
def qqe_adv(
    close: Union[pd.Series, np.ndarray],
    sf: int = 7,
    rsi_period: int = 14,
    wp: int = 1,
) -> pd.DataFrame:
    """
    QQE Advanced indicator — exact port of QQE Adv.mq5

    Parameters
    ----------
    close       : close prices, chronological (oldest first)
    sf          : smoothing factor (EMA applied to RSI when sf > 1)
    rsi_period  : RSI period
    wp          : Wilder's period multiplier for ATR smoothing

    Returns
    -------
    DataFrame with columns:
        rsi_ma          : smoothed RSI line (RsiMa / buffer 0)
        tr_level_slow   : trailing stop line (TrLevelSlow / buffer 1)
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    wilders_period = rsi_period * 2 - 1

    # --- RSI (Wilder's smoothing = EMA with period rsi_period) ---
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    rsi_vals = np.full(n, 50.0)
    # Seed with simple average for first period
    avg_gain = np.mean(gain[1: rsi_period + 1])
    avg_loss = np.mean(loss[1: rsi_period + 1])
    for i in range(rsi_period, n):
        avg_gain = (avg_gain * (rsi_period - 1) + gain[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + loss[i]) / rsi_period
        rs = avg_gain / avg_loss if avg_loss != 0 else 1e10
        rsi_vals[i] = 100.0 - 100.0 / (1 + rs)

    # --- RsiMa: EMA(RSI, SF) if SF > 1, else RSI ---
    if sf > 1:
        rsi_ma = _ema(rsi_vals, sf, start=rsi_period)
    else:
        rsi_ma = rsi_vals.copy()

    # --- AtrRsi = abs(RsiMa[i-1] - RsiMa[i])  (i-1 is previous bar) ---
    atr_rsi = np.zeros(n)
    for i in range(1, n):
        atr_rsi[i] = abs(rsi_ma[i - 1] - rsi_ma[i])

    # --- MaAtrRsi = EMA(AtrRsi, WildersP) ---
    start_ma = rsi_period + sf + 1
    ma_atr_rsi = _ema(atr_rsi, wilders_period, start=start_ma)

    # --- MaMaAtrRsi = EMA(MaAtrRsi, WildersP) ---
    start_mama = start_ma + wilders_period
    ma_ma_atr_rsi = _ema(ma_atr_rsi, wilders_period, start=start_mama)

    # --- TrLevelSlow trailing stop ---
    tr_level = np.zeros(n)
    tr = rsi_ma[0]
    for i in range(1, n):
        dar = ma_ma_atr_rsi[i] * wp
        prev_tr = tr
        if rsi_ma[i] < tr:
            new_tr = rsi_ma[i] + dar
            # Don't snap if previous was also below and new would jump above
            if not (rsi_ma[i - 1] < prev_tr and new_tr > prev_tr):
                tr = new_tr
        elif rsi_ma[i] > tr:
            new_tr = rsi_ma[i] - dar
            if not (rsi_ma[i - 1] > prev_tr and new_tr < prev_tr):
                tr = new_tr
        tr_level[i] = tr

    return pd.DataFrame({
        "rsi_ma":        rsi_ma,
        "tr_level_slow": tr_level,
    })


# ---------------------------------------------------------------------------
# MACD Platinum (Zero-Lag MACD)
# ---------------------------------------------------------------------------
def macd_platinum(
    close: Union[pd.Series, np.ndarray],
    fast: int = 12,
    slow: int = 26,
    smooth: int = 9,
    zero_lag: bool = True,
    multiplier: int = 10,
) -> pd.DataFrame:
    """
    MACD Platinum — exact port of MACD_Platinum.mq5

    Parameters
    ----------
    close       : close prices, chronological
    fast        : fast EMA period
    slow        : slow EMA period
    smooth      : signal line EMA period
    zero_lag    : use zero-lag correction (True matches EA default)
    multiplier  : internal price multiplier (10, matches MQL5 source)

    Returns
    -------
    DataFrame with columns:
        macd   : MACD line (blue / buffer 0)
        avg    : signal line (orange / buffer 1)
        cross_up   : True where Macd crossed above Avg on that bar
        cross_down : True where Macd crossed below Avg on that bar
    """
    close = np.asarray(close, dtype=float)
    n = len(close)

    fast_coeff   = 2.0 / (1 + fast)
    slow_coeff   = 2.0 / (1 + slow)
    smooth_coeff = 2.0 / (1 + smooth)

    fast_ema     = np.zeros(n)
    slow_ema     = np.zeros(n)
    fast_ema_ema = np.zeros(n)
    slow_ema_ema = np.zeros(n)
    avg_ema      = np.zeros(n)
    avg_ema_ema  = np.zeros(n)
    macd_arr     = np.zeros(n)
    avg_arr      = np.zeros(n)

    for i in range(1, n):
        fast_ema[i] = fast_coeff * multiplier * close[i] + (1 - fast_coeff) * fast_ema[i - 1]
        slow_ema[i] = slow_coeff * multiplier * close[i] + (1 - slow_coeff) * slow_ema[i - 1]

        if not zero_lag:
            macd_arr[i] = fast_ema[i] - slow_ema[i]
            avg_arr[i]  = smooth_coeff * macd_arr[i] + (1 - smooth_coeff) * avg_arr[i - 1]
        else:
            fast_ema_ema[i] = fast_coeff * fast_ema[i] + (1 - fast_coeff) * fast_ema_ema[i - 1]
            slow_ema_ema[i] = slow_coeff * slow_ema[i] + (1 - slow_coeff) * slow_ema_ema[i - 1]
            # Zero-lag correction: 2*EMA - EMA(EMA)
            macd_arr[i] = (2 * fast_ema[i] - fast_ema_ema[i]) - (2 * slow_ema[i] - slow_ema_ema[i])
            avg_ema[i]     = smooth_coeff * macd_arr[i]  + (1 - smooth_coeff) * avg_ema[i - 1]
            avg_ema_ema[i] = smooth_coeff * avg_ema[i]   + (1 - smooth_coeff) * avg_ema_ema[i - 1]
            avg_arr[i]     = 2 * avg_ema[i] - avg_ema_ema[i]

    # Crosses
    cross_up   = np.zeros(n, dtype=bool)
    cross_down = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if macd_arr[i] > avg_arr[i] and macd_arr[i - 1] <= avg_arr[i - 1]:
            cross_up[i] = True
        elif macd_arr[i] < avg_arr[i] and macd_arr[i - 1] >= avg_arr[i - 1]:
            cross_down[i] = True

    return pd.DataFrame({
        "macd":       macd_arr,
        "avg":        avg_arr,
        "cross_up":   cross_up,
        "cross_down": cross_down,
    })


# ---------------------------------------------------------------------------
# QMP Filter
# ---------------------------------------------------------------------------
def qmp_filter(
    close: Union[pd.Series, np.ndarray],
    high:  Union[pd.Series, np.ndarray],
    low:   Union[pd.Series, np.ndarray],
    # MACD params
    fast:   int  = 12,
    slow:   int  = 26,
    smooth: int  = 9,
    # QQE params
    sf:         int = 1,
    rsi_period: int = 8,
    wp:         int = 3,
) -> pd.DataFrame:
    """
    QMP Filter — exact port of QMP_Filter.mq5

    Signal logic (QMP Filter source, OnCalculate):
        UP   when MACD >= Avg  AND  RsiMa >= TrLevelSlow  (trend changed to UP)
        DOWN when MACD <  Avg  AND  RsiMa <  TrLevelSlow  (trend changed to DOWN)

    A dot is plotted only on the bar where the trend CHANGES direction.
    Consecutive bars in the same direction do NOT re-fire.

    Parameters
    ----------
    close, high, low : OHLC arrays, chronological (oldest first)
    fast, slow, smooth : MACD Platinum parameters
    sf, rsi_period, wp : QQE Adv parameters

    Returns
    -------
    DataFrame with columns:
        macd        : MACD line value
        avg         : signal line value
        rsi_ma      : QQE RSI-MA line value
        tr_level    : QQE trailing stop value
        trend       : +1 (up), -1 (down), 0 (flat) — persistent
        buy         : True on the bar trend changes to UP   (dot fires)
        sell        : True on the bar trend changes to DOWN (dot fires)
        buy_price   : low - 2*point  (dot position on chart)
        sell_price  : high + 2*point (dot position on chart)
    """
    close = np.asarray(close, dtype=float)
    high  = np.asarray(high,  dtype=float)
    low   = np.asarray(low,   dtype=float)
    n = len(close)

    # Point size (approximate — for dot placement only)
    price_range = np.max(high) - np.min(low)
    digits = 5 if price_range < 10 else 2
    point = 10 ** (-digits) * (10 if digits in (3, 5) else 1)

    # Build sub-indicators
    macd_df = macd_platinum(close, fast=fast, slow=slow, smooth=smooth)
    qqe_df  = qqe_adv(close, sf=sf, rsi_period=rsi_period, wp=wp)

    macd_line  = macd_df["macd"].values
    avg_line   = macd_df["avg"].values
    rsi_ma     = qqe_df["rsi_ma"].values
    tr_level   = qqe_df["tr_level_slow"].values

    UP_TREND   =  1
    DOWN_TREND = -1

    trend      = np.zeros(n, dtype=int)
    buy        = np.zeros(n, dtype=bool)
    sell       = np.zeros(n, dtype=bool)
    buy_price  = np.full(n, np.nan)
    sell_price = np.full(n, np.nan)

    for i in range(1, n):
        trend[i] = trend[i - 1]   # carry forward

        macd_up = macd_line[i] >= avg_line[i]
        qqe_up  = rsi_ma[i]   >= tr_level[i]

        if trend[i] != UP_TREND and macd_up and qqe_up:
            trend[i]     = UP_TREND
            buy[i]       = True
            buy_price[i] = low[i] - 2 * point

        elif trend[i] != DOWN_TREND and (not macd_up) and (not qqe_up):
            trend[i]      = DOWN_TREND
            sell[i]       = True
            sell_price[i] = high[i] + 2 * point

    return pd.DataFrame({
        "macd":       macd_line,
        "avg":        avg_line,
        "rsi_ma":     rsi_ma,
        "tr_level":   tr_level,
        "trend":      trend,
        "buy":        buy,
        "sell":       sell,
        "buy_price":  buy_price,
        "sell_price": sell_price,
    })


# ---------------------------------------------------------------------------
# Convenience: full signal state for a single symbol
# ---------------------------------------------------------------------------
def get_signal_state(
    close: Union[pd.Series, np.ndarray],
    high:  Union[pd.Series, np.ndarray],
    low:   Union[pd.Series, np.ndarray],
    qqe_overbought: int = 60,
    qqe_oversold:   int = 40,
    # QMP (EA default)
    fast: int = 12, slow: int = 26, smooth: int = 9,
    sf:   int = 1,  rsi_period: int = 8, wp: int = 3,
    # QQE main (EA default for overbought/oversold detection)
    qqe_sf: int = 7, qqe_rsi: int = 14, qqe_wp: int = 1,
) -> dict:
    """
    Returns current signal state dict matching the EA's internal state.
    Uses bar[-2] (last closed bar, equivalent to MT5 bar[1] in series).

    The QQE used for overbought/oversold detection (qqe_sf=7, qqe_rsi=14)
    is separate from the QQE inside QMP Filter (sf=1, rsi_period=8).
    Both are used by the EA simultaneously.
    """
    close = np.asarray(close, dtype=float)
    high  = np.asarray(high,  dtype=float)
    low   = np.asarray(low,   dtype=float)

    # Main QQE (for OB/OS levels shown on chart)
    main_qqe = qqe_adv(close, sf=qqe_sf, rsi_period=qqe_rsi, wp=qqe_wp)

    # QMP Filter (uses its own internal QQE with sf=1, rsi=8)
    qmp = qmp_filter(close, high, low,
                     fast=fast, slow=slow, smooth=smooth,
                     sf=sf, rsi_period=rsi_period, wp=wp)

    # Bar indices: -1 = current forming bar, -2 = last closed bar
    b = -2   # last confirmed closed bar

    qqe_val = main_qqe["rsi_ma"].iloc[b]

    return {
        # QQE state (main indicator)
        "qqe_value":               round(qqe_val, 4),
        "qqe_overbought_triggered": qqe_val >= qqe_overbought,
        "qqe_oversold_triggered":   qqe_val <= qqe_oversold,
        "qqe_above_50":            qqe_val > 50.0,
        "qqe_below_50":            qqe_val < 50.0,

        # QMP signal on last closed bar
        "qmp_buy_signal":          bool(qmp["buy"].iloc[b]),
        "qmp_sell_signal":         bool(qmp["sell"].iloc[b]),
        "qmp_trend":               int(qmp["trend"].iloc[b]),

        # MACD state
        "macd":                    round(qmp["macd"].iloc[b], 6),
        "macd_avg":                round(qmp["avg"].iloc[b], 6),
        "macd_above_avg":          qmp["macd"].iloc[b] >= qmp["avg"].iloc[b],
    }
