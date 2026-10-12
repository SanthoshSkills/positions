#!/usr/bin/env python3
"""
Heikin Ashi chart with Supertrend, MACD and RSI

Used automatically by nse_screener.py (screener report, stock lookup) and portfolio.py (holding charts)
when CHART_STYLE = "heikin" in nse_screener.py. Keep this file in the same folder.

For each of daily, weekly and monthly (side by side) the chart has three panels:
  1. Price panel: Heikin Ashi candles, the Supertrend line (green under price = uptrend, red over price =
     downtrend; a triangle marks each flip), support/resistance bands, and on the daily panel your buy-above,
     stop-loss and target lines.
  2. MACD panel: MACD line, signal line and histogram. A triangle marks every crossover
     (green up = MACD crossed ABOVE the signal, red down = crossed BELOW).
  3. RSI panel: RSI with the 30 / 50 / 70 lines (shaded above 70 and below 30).
SWING CALLOUTS (daily and weekly): on the last SWING_FLIPS_SHOWN flips the confirming Heikin Ashi candles are numbered 1 to 3 above
(green flip) or below (red flip) the candles, the 2nd and 3rd carry a BUY / SELL tag (the method's entry candles), the angle label gets a steep
check mark or cross (STEEP_ANGLE), and a box at the bottom left of the panel says in words where the latest flip stands.
Right after every Supertrend flip the price panel shows the angle of the line over the next FLIP_ANGLE_BARS (3) candles (it is
shown from the first candle after the flip). Right after every crossover the MACD panel shows the angle of the MACD line over the next MACD_ANGLE_BARS (3) candles
(in ATRs, same scale as the Supertrend angle; 'wait' = no candle after the crossover yet).
The panel titles say in words what the indicator is doing now and how many candles ago it last changed.
The Supertrend title and a small label at the end of the line also show the line's ANGLE in degrees: its slope over
the last ANGLE_BARS (10) candles since the latest flip, measured in ATRs (45 degrees = the line moves one ATR per
candle), so it compares fairly between stocks and timeframes. + = rising, - = falling, near 0 = flat.

Test it on its own (needs yfinance):   python ha_chart.py TCS INFY      (writes ha_<SYMBOL>.png)

How the numbers are worked out
  * Heikin Ashi candles are smoothed candles built from the real ones: close = average of open, high, low,
    close; open = middle of the previous Heikin Ashi candle. Weekly and monthly candles are made from the real
    weekly and monthly bars first, then turned into Heikin Ashi.
  * Supertrend uses Wilder's ATR. Period and multiplier (default 10, 2) come from hub_settings.json, which you
    change in the hub: python trading_hub.py settings supertrend 10 2   or the web page (6. Chart settings).
  * MACD 12, 26, 9 and RSI 14 (Wilder), the usual settings.
  * INDICATORS_ON = "heikin" works the indicators out on the Heikin Ashi candles, which is what you see
    when you switch a TradingView chart to Heikin Ashi. Set "real" to use the real prices instead.

Honest limits
  * A Heikin Ashi candle's open and close are NOT real prices that traded. Read price levels (buy-above, stop,
    latest price) from the dotted lines and the reports, not from the candle bodies.
  * Heikin Ashi, Supertrend and MACD all lag: they confirm a move after it has started and give false flips in
    sideways markets. The monthly panel has only about 5 years of bars, so its MACD is rough early on.
  * The still-forming weekly or monthly candle is drawn lighter; its values can change before it closes.
  * Candle-formation names (engulfing, evening star...) and the exit alerts still use REAL candles, because
    those formations are defined on real prices.
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ----------------------------- SETTINGS (edit me) -----------------------------
SUPERTREND_PERIOD = 10            # starting values only: the real ones are read from hub_settings.json (see hub_settings.py)
SUPERTREND_MULT = 2.0             # change them in the hub (python trading_hub.py settings, or the web page), not here
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_PERIOD = 14
RSI_HIGH, RSI_LOW = 70, 30
INDICATORS_ON = "heikin"          # "heikin" = on Heikin Ashi candles (like TradingView); "real" = on real prices
ANGLE_BARS = 10                   # the Supertrend angle is measured over the last this many candles (never across a flip)
MACD_ANGLE_BARS = 3                # the MACD angle after a crossover is measured over up to this many candles after it (at least 1)
FLIP_ANGLE_BARS = 3                # the angle right after a Supertrend flip is measured over up to this many candles after it (at least 1)
FLIP_ANGLE_LABELS = 3              # label the angle on this many of the most recent flips in each price panel
SWING_CALLOUTS = True              # mark the Heikin Ashi + Supertrend swing set-up (see swing_setup) on the daily and weekly price panels
SWING_TFS = ("D", "W")             # the method is taught on daily or weekly charts
SETUP_CANDLES = 3                  # 1 to 3 confirming candles after a flip; the entry is on the 2nd or 3rd (the flip candle counts as the 1st)
STEEP_ANGLE = 15.0                 # a flip counts as "steep" at this many degrees or more (in ATRs). A placeholder: the method says "steep" without a number, tune it on your charts
MIN_PRIOR_TREND = 5                # the trend before the flip should have lasted at least this many candles
SWING_FLIPS_SHOWN = 2              # draw the numbered candles for this many of the most recent flips per panel
STALE_AFTER = 3                    # a flip older than SETUP_CANDLES + this many candles is called "not fresh" in the box
MACD_ANGLE_LABELS = 4              # label the angle on this many of the most recent crossovers in each MACD panel
BARS = {"D": 150, "W": 104, "M": 60}   # candles drawn per timeframe (about 7 months, 2 years, 5 years)
# ------------------------------------------------------------------------------

GREEN, RED, BLUE, ORANGE, PURPLE = "#1b7f3b", "#c0392b", "#1f4e9c", "#e08a00", "#7b3fa0"
TF_NAME = {"D": "Daily", "W": "Weekly", "M": "Monthly"}
UNIT = {"D": "day", "W": "week", "M": "month"}
TICK_FMT = {"D": "%d %b", "W": "%d %b %y", "M": "%b %y"}
AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


# ------------------------------- INDICATORS -----------------------------------
def resample(df, kind):
    rules = {"W": ["W-FRI"], "M": ["ME", "M"]}[kind]  # 'ME' on new pandas, 'M' on old
    for rule in rules:
        try:
            return df.resample(rule).agg(AGG).dropna()
        except ValueError:
            continue
    raise RuntimeError("Could not resample prices")


def heikin_ashi(df):
    """Heikin Ashi candles from OHLC bars (same index)."""
    o, h, l, c = (df[k].astype(float).values for k in ("Open", "High", "Low", "Close"))
    ha_c = (o + h + l + c) / 4
    ha_o = np.empty_like(ha_c)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(ha_c)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return pd.DataFrame({"Open": ha_o, "High": np.maximum.reduce([h, ha_o, ha_c]),
                         "Low": np.minimum.reduce([l, ha_o, ha_c]), "Close": ha_c,
                         "Volume": df["Volume"].values}, index=df.index)


def _atr(df, period):
    """Wilder's average true range."""
    h, l, c = (df[k].astype(float) for k in ("High", "Low", "Close"))
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()   # Wilder's smoothing


def supertrend(df, period=None, mult=None):
    """-> (line, direction). direction is +1 in an uptrend (line under price), -1 in a downtrend, 0 before it starts."""
    period = SUPERTREND_PERIOD if period is None else period
    mult = SUPERTREND_MULT if mult is None else mult
    h, l, c = (df[k].astype(float) for k in ("High", "Low", "Close"))
    atr = _atr(df, period)
    hl2 = ((h + l) / 2).values
    ub, lb, cl = (hl2 + mult * atr).values, (hl2 - mult * atr).values, c.values
    n = len(df)
    fu, fl = ub.copy(), lb.copy()
    st, dr = np.full(n, np.nan), np.zeros(n, dtype=int)
    valid = np.where(~np.isnan(ub))[0]
    if len(valid) == 0:
        return pd.Series(st, index=df.index), pd.Series(dr, index=df.index)
    s = valid[0]
    dr[s], st[s] = 1, fl[s]
    for i in range(s + 1, n):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or cl[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or cl[i - 1] < fl[i - 1]) else fl[i - 1]
        if dr[i - 1] == 1:
            dr[i] = -1 if cl[i] < fl[i] else 1
        else:
            dr[i] = 1 if cl[i] > fu[i] else -1
        st[i] = fl[i] if dr[i] == 1 else fu[i]
    return pd.Series(st, index=df.index), pd.Series(dr, index=df.index)


def line_angle(line, direction, atr, bars=None):
    """Angle of the Supertrend line in degrees, or None when it cannot be measured yet.
    The slope (line change per candle over the last `bars` candles, only counting candles since the latest
    flip) is divided by the ATR, so 45 degrees = the line moves one ATR per candle. Because it is measured in
    ATRs, the number is comparable between stocks and between daily, weekly and monthly charts.
    Positive = line rising, negative = line falling, near 0 = flat."""
    bars = ANGLE_BARS if bars is None else bars
    d = np.asarray(direction)
    if len(d) < 3 or d[-1] == 0 or not np.isfinite(atr) or atr <= 0:
        return None
    flips = np.where(np.diff(d) != 0)[0]
    first = (flips[-1] + 1) if len(flips) else 0          # first candle of the current trend
    start = max(first, len(d) - 1 - bars)
    steps = len(d) - 1 - start
    if steps < 1:
        return None                                       # the flip candle itself: no candle after it yet
    v0, v1 = float(line[start]), float(line[-1])
    if not (np.isfinite(v0) and np.isfinite(v1)):
        return None
    return float(np.degrees(np.arctan(((v1 - v0) / steps) / atr)))


def cross_angle(line, k, atr, bars=None):
    """Angle (degrees) of the MACD line over the candles right AFTER a crossover at index k, or None if there is no
    candle after it yet. Slope per candle over up to `bars` candles after the crossover, divided by the ATR, so
    45 degrees = the MACD line moves one ATR per candle (same scale as the Supertrend angle). + = rising, - = falling.
    Returns (angle, candles used)."""
    bars = MACD_ANGLE_BARS if bars is None else bars
    steps = min(bars, len(line) - 1 - k)
    if steps < 1:
        return None, 0
    v0, v1, a = float(line[k]), float(line[k + steps]), float(atr[k + steps])
    if not (np.isfinite(v0) and np.isfinite(v1) and np.isfinite(a)) or a <= 0:
        return None, steps
    return float(np.degrees(np.arctan(((v1 - v0) / steps) / a))), steps


def flip_angle(line, direction, k, atr, bars=None):
    """Angle (degrees) of the Supertrend line over the candles right AFTER a flip at index k (stays inside the new trend),
    or None when there is no candle after the flip yet. In ATRs, same scale as line_angle. Returns (angle, candles used)."""
    bars = FLIP_ANGLE_BARS if bars is None else bars
    d = np.asarray(direction)
    later = np.where(np.diff(d[k:]) != 0)[0]
    end_limit = (k + later[0]) if len(later) else len(d) - 1      # last candle before the next flip
    steps = min(bars, end_limit - k)
    if steps < 1:
        return None, 0
    v0, v1, a = float(line[k]), float(line[k + steps]), float(atr[k + steps])
    if not (np.isfinite(v0) and np.isfinite(v1)) or not np.isfinite(a) or a <= 0:
        return None, steps
    return float(np.degrees(np.arctan(((v1 - v0) / steps) / a))), steps


def swing_setup(ha, st, dr, k, atr):
    """The Heikin Ashi + Supertrend swing set-up for the flip at index k.
    LONG (flip to green): a downtrend (red line) was in place, the line turned green, and 1 to SETUP_CANDLES bullish Heikin Ashi
    candles formed ABOVE the line; the entry is taken on the 2nd or 3rd of them, and the line should have turned up steeply.
    EXIT is the mirror: green line turns red, 1 to 3 bearish candles BELOW the line, entry (sell) on the 2nd or 3rd, steep fall.
    The flip candle counts as candle 1. Returns a dict (state is ENTRY / WAIT / FAILED; for a red flip ENTRY means an exit signal)."""
    d = int(dr[k])
    o, c = ha["Open"].values, ha["Close"].values
    seq, broke = [], None
    for j in range(k, len(dr)):
        if dr[j] != d:
            broke = "the Supertrend flipped back"
            break
        good = (c[j] > o[j] and c[j] > st[j]) if d == 1 else (c[j] < o[j] and c[j] < st[j])
        if not good:
            broke = f"candle {len(seq) + 1} was not {'bullish above' if d == 1 else 'bearish below'} the line"
            break
        seq.append(j)
        if len(seq) == SETUP_CANDLES:
            break
    run, p = 0, k - 1
    while p >= 0 and dr[p] == -d:
        run, p = run + 1, p - 1
    ang, steps = flip_angle(st, dr, k, atr)
    steep = ang is not None and ((ang >= STEEP_ANGLE) if d == 1 else (ang <= -STEEP_ANGLE))
    state = "ENTRY" if len(seq) >= 2 else "WAIT" if broke is None else "FAILED"
    return {"d": d, "k": k, "seq": seq, "broke": broke, "state": state, "prior": run, "angle": ang, "steep": steep,
            "age": len(dr) - 1 - k}


def swing_text(sw, unit, forming):
    """Short plain-English status lines for the box on the price panel."""
    up = sw["d"] == 1
    word = "buy" if up else "sell / exit"
    n, last = len(sw["seq"]), sw["age"] + sw["k"]
    head = f"Swing {word} set-up: {'UP' if up else 'DOWN'} flip {ago(sw['age'], unit)}"
    if sw["age"] > SETUP_CANDLES + STALE_AFTER:
        return (f"No fresh swing set-up. Last flip: {'UP' if up else 'DOWN'}, {ago(sw['age'], unit)}.\n"
                f"A new one starts when the Supertrend line flips colour.")
    if sw["state"] == "FAILED":
        body = f"Failed: {sw['broke']}. No {word} signal from this flip."
    elif sw["state"] == "WAIT":
        body = (f"Waiting: {n} of 2 candles confirmed. The {word} is taken on the 2nd or 3rd "
                f"{'bullish' if up else 'bearish'} candle {'above' if up else 'below'} the line.")
    else:
        pos = sw["seq"].index(last) + 1 if last in sw["seq"] else None
        if pos and pos >= 2:
            body = (f"{word.upper()} ZONE: this is candle {pos} of 3" + (" (still forming: wait for the close)" if forming else "") + ".")
        elif pos == 1:
            body = "Waiting: candle 1 only."
        else:
            body = f"The {word} window (candle 2 or 3) closed {last - sw['seq'][-1]} candle(s) ago."
    if sw["angle"] is None:
        ang = "Angle: wait for the next candle."
    else:
        need = f"{'+' if up else '-'}{STEEP_ANGLE:.0f}"
        ang = f"Angle {sw['angle']:+.0f}\u00b0 " + (f"steep \u2713 (needs {need}\u00b0)" if sw["steep"] else f"shallow \u2717 (needs {need}\u00b0)")
    prior = (f"{'down' if up else 'up'}trend before it: {sw['prior']} candles"
             + (" \u2713" if sw["prior"] >= MIN_PRIOR_TREND else f" (short, under {MIN_PRIOR_TREND})"))
    return f"{head}\n{body}\n{ang} | {prior}"


def macd(close):
    """-> (macd line, signal line, histogram)."""
    line = (close.ewm(span=MACD_FAST, adjust=False, min_periods=MACD_FAST).mean()
            - close.ewm(span=MACD_SLOW, adjust=False, min_periods=MACD_SLOW).mean())
    sig = line.ewm(span=MACD_SIGNAL, adjust=False, min_periods=MACD_SIGNAL).mean()
    return line, sig, line - sig


def rsi(close, period=RSI_PERIOD):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def compute(bars):
    """All indicators for one timeframe (on the full history, so they are warmed up)."""
    ha = heikin_ashi(bars)
    src = ha if INDICATORS_ON == "heikin" else bars
    st, dr = supertrend(src)
    atr = _atr(src, SUPERTREND_PERIOD)
    m, s, hist = macd(src["Close"])
    up = (m > s) & (m.shift(1) <= s.shift(1))
    down = (m < s) & (m.shift(1) >= s.shift(1))
    return {"ha": ha, "st": st, "dr": dr, "atr": atr, "macd": m, "sig": s, "hist": hist,
            "cross_up": up.fillna(False).values, "cross_down": down.fillna(False).values,
            "rsi": rsi(src["Close"])}


def ago(n, unit):
    return "on the latest candle" if n == 0 else f"{n} {unit}{'' if n == 1 else 's'} ago"


# ------------------------------- DRAWING --------------------------------------
def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def draw_timeframe(axes, tf, bars, forming, res, extra_levels, font):
    ax_p, ax_m, ax_r = axes
    ind = compute(bars)
    n = min(BARS[tf], len(bars))
    sl = slice(len(bars) - n, None)
    d = ind["ha"].iloc[sl]
    x = np.arange(n)
    o, h, lo, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    up_c = c >= o
    cols = np.where(up_c, GREEN, RED)
    alphas = np.ones(n)
    if forming:
        alphas[-1] = 0.45

    # ---------------- price panel ----------------
    last_real = res["last_close"]
    stop, buy, target = _num(res.get("stop_loss")), _num(res.get("buy_above")), _num(res.get("target_price"))
    levels = []
    if tf == "D":
        if buy:
            levels.append((buy, GREEN, "Buy above"))
        if stop:
            levels.append((stop, RED, "Stop-loss"))
        if target:
            levels.append((target, PURPLE, "Target"))
        levels += list(extra_levels or [])
    y_lo, y_hi = float(lo.min()), float(h.max())
    if tf == "D":                                   # make sure YOUR stop and cost are visible
        for v, _, name in levels:
            if name in ("Stop-loss", "Your average cost") and v:
                y_lo, y_hi = min(y_lo, v * 0.97), max(y_hi, v * 1.03)
    pad = (y_hi - y_lo) * 0.06
    lim = (y_lo - pad, y_hi + (y_hi - y_lo) * 0.24)   # head-room for the legend

    shown = set()
    for b in res["_bands"]:
        if b["high"] < lim[0] or b["low"] > lim[1]:
            continue
        color, name = (("tab:green", "Support") if b["high"] <= last_real else
                       ("tab:red", "Resistance") if b["low"] >= last_real else
                       ("tab:gray", "Price inside band"))
        ax_p.axhspan(b["low"], b["high"], color=color, alpha=min(0.10 + 0.05 * b["touches"], 0.40),
                     label=None if name in shown else name)
        shown.add(name)

    for v, color, name in levels:
        if v and lim[0] <= v <= lim[1]:
            ax_p.axhline(v, color=color, linestyle=":", linewidth=2.2, label=name)
    if lim[0] <= last_real <= lim[1]:
        ax_p.axhline(last_real, color="black", linestyle="--", linewidth=1.3, label="Latest price")

    for k in range(n):                               # candles
        ax_p.plot([k, k], [lo[k], h[k]], color=cols[k], linewidth=1.5, alpha=alphas[k], zorder=3)
    body = np.maximum(np.abs(c - o), (y_hi - y_lo) * 0.004)
    ax_p.bar(x, body, bottom=np.minimum(o, c), width=0.7, color=cols, edgecolor=cols, zorder=4)
    if forming:                                      # lighter last candle
        ax_p.patches[-1].set_alpha(0.45)

    st, dr = ind["st"].iloc[sl].values, ind["dr"].iloc[sl].values
    ax_p.plot(x, np.where(dr == 1, st, np.nan), color=GREEN, linewidth=2.6, zorder=5,
              label=f"Supertrend up ({SUPERTREND_PERIOD}, {SUPERTREND_MULT:g})")
    ax_p.plot(x, np.where(dr == -1, st, np.nan), color=RED, linewidth=2.6, zorder=5,
              label=f"Supertrend down")
    prev = np.concatenate([[0], dr[:-1]])
    f_up = np.where((dr == 1) & (prev == -1))[0]
    f_dn = np.where((dr == -1) & (prev == 1))[0]
    ax_p.scatter(f_up, st[f_up], marker="^", s=170, color=GREEN, edgecolor="black", zorder=7)
    ax_p.scatter(f_dn, st[f_dn], marker="v", s=170, color=RED, edgecolor="black", zorder=7)
    off_p = len(bars) - n
    for j, is_up in sorted([(int(q), True) for q in f_up] + [(int(q), False) for q in f_dn])[-FLIP_ANGLE_LABELS:]:
        fa, _ = flip_angle(ind["st"].values, ind["dr"].values, j + off_p, ind["atr"].values)
        fc = GREEN if is_up else RED
        mark = "" if (fa is None or not (SWING_CALLOUTS and tf in SWING_TFS)) else \
            (" \u2713" if ((fa >= STEEP_ANGLE) if is_up else (fa <= -STEEP_ANGLE)) else " \u2717")
        ax_p.annotate("wait" if fa is None else f"{fa:+.0f}\u00b0{mark}", xy=(j, st[j]),
                      xytext=(0, -36 if is_up else 36), textcoords="offset points", ha="center", va="center",
                      fontsize=font - 3, fontweight="bold", color=fc, zorder=8, annotation_clip=False,
                      bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=fc, lw=1.8),
                      arrowprops=dict(arrowstyle="-", color=fc, lw=1.4))
    if SWING_CALLOUTS and tf in SWING_TFS:
        fl_all = np.where(np.diff(ind["dr"].values) != 0)[0] + 1
        shown_sw = [swing_setup(ind["ha"], ind["st"].values, ind["dr"].values, int(k), ind["atr"].values)
                    for k in fl_all[-SWING_FLIPS_SHOWN:]]
        for sw in shown_sw:
            up_s = sw["d"] == 1
            sc = GREEN if up_s else RED
            for t, jj in enumerate(sw["seq"]):
                xx = jj - off_p
                if xx < 0:
                    continue
                q = "?" if (forming and jj == len(bars) - 1) else ""
                y0, sgn = (h[xx], 1) if up_s else (lo[xx], -1)
                ax_p.annotate(f"{t + 1}{q}", xy=(xx, y0), xytext=(0, 15 * sgn), textcoords="offset points",
                              ha="center", va="center", fontsize=font - 4, fontweight="bold", color=sc, zorder=9,
                              annotation_clip=False, bbox=dict(boxstyle="circle,pad=0.2", fc="white", ec=sc, lw=1.5))
                if t >= 1:
                    ax_p.annotate(("BUY" if up_s else "SELL") + f" {t + 1}", xy=(xx, y0), xytext=(0, (42 if t == 1 else 70) * sgn),
                                  textcoords="offset points", ha="center", va="center", fontsize=font - 4,
                                  fontweight="bold", color="white", zorder=9, annotation_clip=False,
                                  bbox=dict(boxstyle="round,pad=0.25", fc=sc, ec=sc),
                                  arrowprops=dict(arrowstyle="-", color=sc, lw=1.2))
        if shown_sw:
            sw = shown_sw[-1]
            ax_p.text(0.01, 0.015, swing_text(sw, UNIT[tf], forming), transform=ax_p.transAxes, fontsize=font - 5,
                      va="bottom", ha="left", zorder=10, linespacing=1.35,
                      bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=GREEN if sw["d"] == 1 else RED, lw=2, alpha=0.92))

    ax_p.set_xlim(-1, n)
    ax_p.set_ylim(*lim)
    ax_p.grid(alpha=0.25)
    ax_p.legend(loc="upper left", ncol=2, frameon=True, framealpha=0.85, fontsize=font - 4)
    full_dr = ind["dr"].values
    angle = line_angle(ind["st"].values, full_dr, float(ind["atr"].values[-1]))
    angle_txt = "angle wait" if angle is None else f"angle {angle:+.0f}\u00b0"
    if full_dr[-1] != 0:
        flips = np.where(np.diff(full_dr) != 0)[0]
        since = (len(full_dr) - 1 - (flips[-1] + 1)) if len(flips) else None
        state = ("UP" if full_dr[-1] == 1 else "DOWN") + f" {ind['st'].values[-1]:,.2f}, {angle_txt}"
        if since is not None:
            state += f", flipped {ago(since, UNIT[tf])}"
            fang, _ = flip_angle(ind["st"].values, full_dr, flips[-1] + 1, ind["atr"].values)
            state += " (" + ("wait" if fang is None else f"{fang:+.0f}\u00b0") + " at flip)"
        if angle is not None:                          # label next to the end of the line
            lc = GREEN if full_dr[-1] == 1 else RED
            ax_p.annotate(f"{angle:+.0f}\u00b0", xy=(n - 1, st[-1]),
                          xytext=(-12, -34 if full_dr[-1] == 1 else 34), textcoords="offset points",
                          ha="right", va="center", fontsize=font - 2, fontweight="bold", color=lc,
                          zorder=8, annotation_clip=False,
                          bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=lc, lw=2),
                          arrowprops=dict(arrowstyle="-", color=lc, lw=1.6))
    else:
        state = "not enough history"
    forming_txt = "   (last candle still forming)" if forming else ""
    ax_p.set_title(f"{TF_NAME[tf]} Heikin Ashi{forming_txt}\nSupertrend {state}", loc="left", fontsize=font - 2)

    # ---------------- MACD panel ----------------
    mv, sv, hv = (ind[k].iloc[sl].values for k in ("macd", "sig", "hist"))
    ax_m.bar(x, np.nan_to_num(hv), width=0.7, color=np.where(np.nan_to_num(hv) >= 0, GREEN, RED), alpha=0.5)
    ax_m.plot(x, mv, color=BLUE, linewidth=2.2, label=f"MACD ({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL})")
    ax_m.plot(x, sv, color=ORANGE, linewidth=2.2, label="Signal")
    ax_m.axhline(0, color="black", linewidth=1)
    cu, cd = ind["cross_up"][sl], ind["cross_down"][sl]
    iu, idn = np.where(cu)[0], np.where(cd)[0]
    ax_m.scatter(iu, mv[iu], marker="^", s=150, color=GREEN, edgecolor="black", zorder=6, label="Crossed above")
    ax_m.scatter(idn, mv[idn], marker="v", s=150, color=RED, edgecolor="black", zorder=6, label="Crossed below")
    offset = len(bars) - n
    mfull, atrfull = ind["macd"].values, ind["atr"].values
    for j, is_up in sorted([(int(q), True) for q in iu] + [(int(q), False) for q in idn])[-MACD_ANGLE_LABELS:]:
        ang, _ = cross_angle(mfull, j + offset, atrfull)
        col = GREEN if is_up else RED
        ax_m.annotate("wait" if ang is None else f"{ang:+.0f}\u00b0", xy=(j, mv[j]),
                      xytext=(0, 30 if is_up else -30), textcoords="offset points", ha="center", va="center",
                      fontsize=font - 4, fontweight="bold", color=col, zorder=8, annotation_clip=False,
                      bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col, lw=1.8),
                      arrowprops=dict(arrowstyle="-", color=col, lw=1.4))
    ax_m.legend(loc="upper left", ncol=2, frameon=True, framealpha=0.85, fontsize=font - 6)
    allx = np.where(ind["cross_up"] | ind["cross_down"])[0]
    last_m, last_s = ind["macd"].values[-1], ind["sig"].values[-1]
    if np.isnan(last_m) or np.isnan(last_s):
        mtxt = "not enough history"
    else:
        mtxt = "above signal" if last_m > last_s else "below signal"
        if len(allx):
            kind = "up" if ind["cross_up"][allx[-1]] else "down"
            mtxt += f", crossed {kind} {ago(len(bars) - 1 - allx[-1], UNIT[tf])}".replace("on the latest candle", "now")
            cang, csteps = cross_angle(ind["macd"].values, allx[-1], ind["atr"].values)
            mtxt += ", angle wait" if cang is None else f", angle {cang:+.0f}\u00b0"
    ax_m.set_title(f"MACD {mtxt}", loc="left", fontsize=font - 3, pad=3)
    ax_m.grid(alpha=0.25)

    # ---------------- RSI panel ----------------
    rv = ind["rsi"].iloc[sl].values
    ax_r.plot(x, rv, color=PURPLE, linewidth=2.2)
    ax_r.axhline(RSI_HIGH, color=RED, linestyle="--", linewidth=1.3)
    ax_r.axhline(RSI_LOW, color=GREEN, linestyle="--", linewidth=1.3)
    ax_r.axhline(50, color="gray", linestyle=":", linewidth=1)
    ax_r.fill_between(x, RSI_HIGH, 100, color=RED, alpha=0.07)
    ax_r.fill_between(x, 0, RSI_LOW, color=GREEN, alpha=0.07)
    ax_r.set_ylim(0, 100)
    ax_r.set_yticks([RSI_LOW, 50, RSI_HIGH])
    last_r = ind["rsi"].values[-1]
    if np.isnan(last_r):
        rtxt = "not enough history"
    else:
        zone = "overbought" if last_r > RSI_HIGH else "oversold" if last_r < RSI_LOW else \
            "above 50 (buyers ahead)" if last_r >= 50 else "below 50 (sellers ahead)"
        rtxt = f"{last_r:.0f}, {zone}"
    ax_r.set_title(f"RSI ({RSI_PERIOD}): {rtxt}", loc="left", fontsize=font - 1, pad=3)
    ax_r.grid(alpha=0.25)

    ticks = np.linspace(0, n - 1, 5).astype(int)
    ax_r.set_xticks(ticks)
    ax_r.set_xticklabels([d.index[t].strftime(TICK_FMT[tf]) for t in ticks])
    ax_p.tick_params(labelbottom=False)
    ax_m.tick_params(labelbottom=False)


def make_chart(symbol, df, res, out_path, font=16, layout="side", extra_levels=None, title=None):
    """Draw the chart for one stock and save it as a PNG.
    res: the dict from the screener's analyze (needs last_close, status, _bands and optionally
    buy_above, stop_loss, target_price). extra_levels: [(price, colour, label)] drawn on the daily panel."""
    global SUPERTREND_PERIOD, SUPERTREND_MULT
    try:
        import hub_settings
        SUPERTREND_PERIOD, SUPERTREND_MULT = hub_settings.supertrend()   # the hub's current values
    except ImportError:
        pass
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).dropna(subset=["Close"])
    if getattr(df.index, "tz", None) is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    last_day = df.index[-1]
    series = {"D": df, "W": resample(df, "W"), "M": resample(df, "M")}
    forming = {"D": False,
               "W": (series["W"].index[-1] - last_day).days >= 2,
               "M": (series["M"].index[-1] - last_day).days > 3}
    rc = {"font.size": font, "axes.titlesize": font + 1, "axes.titleweight": "bold",
          "legend.fontsize": font - 4, "xtick.labelsize": font, "ytick.labelsize": font - 2,
          "figure.titlesize": font + 4, "figure.titleweight": "bold"}
    with plt.rc_context(rc):
        if layout == "side":
            fig = plt.figure(figsize=(27, 15.5))
            gs = fig.add_gridspec(3, 3, height_ratios=[5, 1.6, 1.6], left=0.04, right=0.992, top=0.92,
                                  bottom=0.05, hspace=0.30, wspace=0.15)
            slots = {tf: [(r, k) for r in range(3)] for k, tf in enumerate(("D", "W", "M"))}
            dpi = 110
        else:
            fig = plt.figure(figsize=(11, 32))
            ratios = [5, 1.6, 1.6, 1.4, 5, 1.6, 1.6, 1.4, 5, 1.6, 1.6]
            gs = fig.add_gridspec(11, 1, height_ratios=ratios, left=0.1, right=0.98, top=0.97,
                                  bottom=0.03, hspace=0.45)
            slots = {tf: [(4 * k + r, 0) for r in range(3)] for k, tf in enumerate(("D", "W", "M"))}
            dpi = 110
        for tf in ("D", "W", "M"):
            first = fig.add_subplot(gs[slots[tf][0]])
            axes = [first] + [fig.add_subplot(gs[s], sharex=first) for s in slots[tf][1:]]
            draw_timeframe(axes, tf, series[tf], forming[tf], res, extra_levels, font)
        if title is None:
            buy, stop, tgt = _num(res.get("buy_above")), _num(res.get("stop_loss")), _num(res.get("target_price"))
            plan = (f"buy above {buy:,.2f} / stop {stop:,.2f}" + (f" / target {tgt:,.2f}" if tgt else "")
                    if buy and stop else "extended - no entry now")
            title = f"{symbol}  -  {res['status']}   |   latest price {res['last_close']:,.2f}   |   {plan}"
        fig.suptitle(title, y=0.985 if layout == "side" else 0.995)
        fig.savefig(out_path, dpi=dpi)
        plt.close(fig)


# ------------------------------- STAND-ALONE TEST -----------------------------
def main():
    import nse_screener as ns
    import stock_lookup as sl

    names = [a.upper() for a in sys.argv[1:]] or ["TCS"]
    prices = sl.download(names)
    bench = ns.download_benchmark()
    for sym, df in prices.items():
        res = sl.analyze_any(df, bench)
        make_chart(sym, df, res, f"ha_{sym}.png", font=ns.CHART_FONT_SIZE, layout=ns.CHART_LAYOUT)
        print(f"Wrote ha_{sym}.png")


if __name__ == "__main__":
    main()
