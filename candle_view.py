#!/usr/bin/env python3
"""
Candlestick view with call-outs and "what to watch next" notes

What it does
  Takes the stocks that nse_screener.py finalized (the same ones in its report.html:
  newest results.csv, WEAK businesses hidden, top TOP_N) and for each one:
    1. Draws real candlesticks on three timeframes side by side: daily, weekly, monthly.
    2. Finds classic candle formations (hammer, engulfing, doji, morning star ...) and
       points at them with an arrow and a label. Multi-candle patterns get a shaded band.
    3. Writes what to watch in the NEXT candle: the positive signs and the warning signs,
       with exact prices taken from the chart, your buy-above price and your stop-loss.
  Output goes into the same dated folder as the screener:
    output/<date>/candle_report.html     (large type, opens in your browser)
    output/<date>/candles/SYMBOL.png
    output/<date>/candle_signals.csv     (every formation found, for your own sorting)

Run (after nse_screener.py, with the other scripts in the same folder):
    python candle_view.py                 all finalized stocks
    python candle_view.py TCS INFY        only these (they must be in the latest results.csv)

Honest limits
  * Candle formations are weak signals on their own. They matter most when they appear at a
    support or resistance band, or at the breakout price, with higher-than-usual volume.
  * "Positive" and "negative" below are conditions to watch, not predictions.
  * The weekly and monthly candle that is still forming is drawn in a lighter shade and is
    never used to name a formation. Judge it only after it closes.
  * If you run it before about 4 pm India time on a trading day, today's unfinished daily
    candle is ignored (this assumes your PC clock shows India time).
"""

import datetime as dt
import sys
import webbrowser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

import nse_screener as ns

try:
    import chart_patterns as cp
except ImportError:  # the candlestick view still works without it
    cp = None

# ----------------------------- SETTINGS (edit me) -----------------------------
FONT = ns.CHART_FONT_SIZE     # chart text size (follows nse_screener.py; raise it for bigger text)
LAYOUT = ns.CHART_LAYOUT      # "side" = daily, weekly, monthly next to each other; "stacked" = one under another
BARS = {"D": 60, "W": 52, "M": 36}      # candles drawn per timeframe
RECENT = {"D": 12, "W": 8, "M": 5}      # only label formations that ended within this many candles
MAX_CALLOUTS = 4              # labels per panel (the most recent ones win)
MAX_POINTS = 6                # bullets per list in the "what to watch" boxes
OPEN_REPORT = True
REPORT_LINK = "report.html"       # the page the candlestick report links back to (the portfolio sets its own)
REPORT_TARGET = "screener_view"   # window name of that page
REPORT_LABEL = "the screener report (report.html)"
REPORT_NAME = "screener report"       # text of the per-stock link back to that page
# ------------------------------------------------------------------------------

GREEN, RED, GREY = "#1b7f3b", "#c0392b", "#555555"
UNIT = {"D": "day", "W": "week", "M": "month"}
TF_NAME = {"D": "Daily", "W": "Weekly", "M": "Monthly"}
NEXT_NAME = {"D": "Next day's candle", "W": "Next week's candle", "M": "Next month's candle"}
DATE_FMT = {"D": "%d %b %Y", "W": "week ending %d %b %Y", "M": "%b %Y"}
TICK_FMT = {"D": "%d %b", "W": "%d %b %y", "M": "%b %y"}
MA_SPEC = {"D": (50, "50-day avg"), "W": (30, "30-week avg"), "M": (10, "10-month avg")}
VOL_WINDOW = {"D": 50, "W": 20, "M": 12}

# name: (bias, priority, plain meaning). bias +1 bullish, -1 bearish, 0 neutral.
PATTERNS = {
    "Hammer": (1, 1, "Sellers pushed the price down but buyers took it back up and it closed near the high. "
                     "Often marks the end of a dip."),
    "Inverted hammer": (1, 1, "Buyers tried to push up after a fall. Weak on its own: it needs a strong "
                              "candle after it."),
    "Dragonfly doji": (1, 1, "Price fell sharply, then recovered to close where it opened. Buyers rejected "
                             "the low."),
    "Bullish marubozu": (1, 1, "A strong up candle with almost no wicks: buyers were in control all period."),
    "Bullish engulfing": (1, 2, "An up candle that fully covers the previous down candle's body. "
                                "Buyers have taken over."),
    "Piercing line": (1, 2, "After a down candle, price opened lower but closed above its midpoint. "
                            "Buyers fought back."),
    "Bullish harami": (1, 2, "A small up candle inside the previous big down candle. Selling is losing force."),
    "Tweezer bottom": (1, 2, "Two candles with the same low: buyers defended that level twice."),
    "Morning star": (1, 3, "Down candle, small indecisive candle, then a strong up candle. "
                           "A classic bottoming sequence."),
    "Three white soldiers": (1, 3, "Three strong up candles in a row, each closing higher: steady buying."),
    "Hanging man": (-1, 1, "Hammer shape but after a rise. Sellers showed up; a weak warning that needs "
                           "a down candle after it."),
    "Shooting star": (-1, 1, "Price shot up but sellers pushed it back down to close near the low. "
                             "Buyers were rejected."),
    "Gravestone doji": (-1, 1, "Price rose, then fell back to where it opened. Buyers were rejected at "
                               "the high."),
    "Bearish marubozu": (-1, 1, "A strong down candle with almost no wicks: sellers were in control all period."),
    "Bearish engulfing": (-1, 2, "A down candle that fully covers the previous up candle's body. "
                                 "Sellers have taken over."),
    "Dark cloud cover": (-1, 2, "After an up candle, price opened higher but closed below its midpoint. "
                                "Sellers fought back."),
    "Bearish harami": (-1, 2, "A small down candle inside the previous big up candle. Buying is losing force."),
    "Tweezer top": (-1, 2, "Two candles with the same high: sellers defended that level twice."),
    "Evening star": (-1, 3, "Up candle, small indecisive candle, then a strong down candle. "
                            "A classic topping sequence."),
    "Three black crows": (-1, 3, "Three strong down candles in a row, each closing lower: steady selling."),
    "Doji": (0, 1, "Open and close almost equal: indecision. The next candle decides who wins."),
    "Inside bar": (0, 0, "This candle's range sits inside the previous candle's range: price is coiling. "
                         "A break of either end of that earlier candle usually sets the next move."),
}


# ------------------------------- PATTERN FINDER -------------------------------
def detect(df):
    """Return a list of formations found on completed candles in df (OHLCV bars, oldest first)."""
    o, h = df["Open"].values.astype(float), df["High"].values.astype(float)
    lo, c = df["Low"].values.astype(float), df["Close"].values.astype(float)
    n = len(df)
    rng, body = h - lo, np.abs(c - o)
    top, bot = np.maximum(o, c), np.minimum(o, c)
    up_w, lo_w = h - top, bot - lo
    found = []

    def add(i, first, name):
        found.append({"i": i, "first": first, "name": name, "bias": PATTERNS[name][0],
                      "high": float(h[first:i + 1].max()), "low": float(lo[first:i + 1].min())})

    for i in range(15, n):
        ar, ab = rng[i - 14:i].mean(), body[i - 14:i].mean()   # typical range and body just before
        if ar <= 0 or ab <= 0:
            continue

        def trend(j):
            """Direction of the 5 candles before candle j, measured in typical candle ranges."""
            ch = (c[j - 1] - c[j - 6]) / ar
            return "down" if ch < -1.0 else "up" if ch > 1.0 else "flat"

        t = trend(i)
        small_up, small_lo = up_w[i] <= 0.15 * rng[i], lo_w[i] <= 0.15 * rng[i]

        # --- one candle -------------------------------------------------------
        if rng[i] >= 0.5 * ar:
            if body[i] <= 0.1 * rng[i]:
                if lo_w[i] >= 0.6 * rng[i] and up_w[i] <= 0.1 * rng[i] and t == "down":
                    add(i, i, "Dragonfly doji")
                elif up_w[i] >= 0.6 * rng[i] and lo_w[i] <= 0.1 * rng[i] and t == "up":
                    add(i, i, "Gravestone doji")
                else:
                    add(i, i, "Doji")
            elif body[i] >= 0.9 * rng[i] and body[i] >= 1.2 * ab:
                add(i, i, "Bullish marubozu" if c[i] > o[i] else "Bearish marubozu")
            elif lo_w[i] >= 2 * body[i] and small_up:
                if t == "down":
                    add(i, i, "Hammer")
                elif t == "up":
                    add(i, i, "Hanging man")
            elif up_w[i] >= 2 * body[i] and small_lo:
                if t == "up":
                    add(i, i, "Shooting star")
                elif t == "down":
                    add(i, i, "Inverted hammer")

        # --- two candles ------------------------------------------------------
        p = i - 1
        tp = trend(p) if p >= 6 else "flat"
        bull_i, bear_i = c[i] > o[i], c[i] < o[i]
        bull_p, bear_p = c[p] > o[p], c[p] < o[p]
        big_p = body[p] >= 0.8 * ab
        mid_p = (o[p] + c[p]) / 2
        if bear_p and bull_i and o[i] <= c[p] and c[i] >= o[p] and body[i] > body[p] and tp != "up":
            add(i, p, "Bullish engulfing")
        elif bull_p and bear_i and o[i] >= c[p] and c[i] <= o[p] and body[i] > body[p] and tp != "down":
            add(i, p, "Bearish engulfing")
        elif bear_p and big_p and bull_i and o[i] < c[p] and mid_p < c[i] < o[p] and tp == "down":
            add(i, p, "Piercing line")
        elif bull_p and big_p and bear_i and o[i] > c[p] and o[p] < c[i] < mid_p and tp == "up":
            add(i, p, "Dark cloud cover")
        elif bear_p and big_p and bull_i and o[i] > c[p] and c[i] < o[p] and body[i] < 0.6 * body[p] and tp == "down":
            add(i, p, "Bullish harami")
        elif bull_p and big_p and bear_i and o[i] < c[p] and c[i] > o[p] and body[i] < 0.6 * body[p] and tp == "up":
            add(i, p, "Bearish harami")
        if tp == "up" and bull_p and bear_i and abs(h[i] - h[p]) <= 0.1 * ar:
            add(i, p, "Tweezer top")
        if tp == "down" and bear_p and bull_i and abs(lo[i] - lo[p]) <= 0.1 * ar:
            add(i, p, "Tweezer bottom")
        if h[i] < h[p] and lo[i] > lo[p]:
            add(i, p, "Inside bar")

        # --- three candles ----------------------------------------------------
        a, b = i - 2, i - 1
        ta = trend(a) if a >= 6 else "flat"
        if (c[a] < o[a] and body[a] >= 0.8 * ab and body[b] <= 0.5 * body[a] and top[b] <= c[a]
                and bull_i and c[i] > (o[a] + c[a]) / 2 and ta == "down"):
            add(i, a, "Morning star")
        if (c[a] > o[a] and body[a] >= 0.8 * ab and body[b] <= 0.5 * body[a] and bot[b] >= c[a]
                and bear_i and c[i] < (o[a] + c[a]) / 2 and ta == "up"):
            add(i, a, "Evening star")
        if (all(c[k] > o[k] and body[k] >= 0.6 * ab and up_w[k] <= 0.35 * body[k] for k in (a, b, i))
                and c[a] < c[b] < c[i] and o[a] <= o[b] <= c[a] and o[b] <= o[i] <= c[b]):
            add(i, a, "Three white soldiers")
        if (all(c[k] < o[k] and body[k] >= 0.6 * ab and lo_w[k] <= 0.35 * body[k] for k in (a, b, i))
                and c[a] > c[b] > c[i] and c[a] <= o[b] <= o[a] and c[b] <= o[i] <= o[b]):
            add(i, a, "Three black crows")
    return found


def best_per_candle(found, n_done, recent):
    """One formation per candle (bigger, non-neutral ones win) within the recent window."""
    chosen = {}
    for p in found:
        if p["i"] < n_done - recent:
            continue
        key = (p["bias"] != 0, PATTERNS[p["name"]][1])
        if p["i"] not in chosen or key > chosen[p["i"]][0]:
            chosen[p["i"]] = (key, p)
    out = []
    for p in sorted((v[1] for v in chosen.values()), key=lambda p: p["i"], reverse=True):
        # a 3-candle pattern can re-fire on the next candle: keep only the latest one
        if any(q["name"] == p["name"] and q["i"] - p["i"] <= 3 for q in out):
            continue
        out.append(p)
    return out


def mark(p):
    return "\u25B2" if p["bias"] > 0 else "\u25BC" if p["bias"] < 0 else "\u25CF"


# ------------------------------- NEXT CANDLE NOTES ----------------------------
def money(x):
    return f"{x:,.2f}"


def shares(v):
    if v >= 1e7:
        return f"{v / 1e7:.1f} crore shares"
    if v >= 1e5:
        return f"{v / 1e5:.1f} lakh shares"
    return f"{v:,.0f} shares"


def make_plan(tf, bars, done, forming, found, levels):
    """Plain-English notes on what the next candle needs to do. Prices come from the chart."""
    unit = UNIT[tf]
    last = done.iloc[-1]
    o, h, lo, c = (float(last[k]) for k in ("Open", "High", "Low", "Close"))
    rng = max(h - lo, 1e-9)
    pos_in = (c - lo) / rng
    kind = "bullish" if c > o else "bearish" if c < o else "flat"
    where = "upper third" if pos_in >= 0.67 else "lower third" if pos_in <= 0.33 else "middle"
    when = done.index[-1].strftime(DATE_FMT[tf])
    n = len(done)
    latest = [p for p in found if p["i"] == n - 1]
    latest.sort(key=lambda p: (p["bias"] != 0, PATTERNS[p["name"]][1]), reverse=True)

    read = (f"The last completed {unit} ({when}) was {kind} and closed in the {where} of its range "
            f"({money(lo)} to {money(h)}).")
    if latest:
        read += " Formation on it: " + ", ".join(f"{mark(p)} {p['name']}" for p in latest) + "."
    else:
        read += " No classic formation on it."

    forming_note = ""
    if forming:
        cur = float(bars["Close"].iloc[-1])
        rel = "above" if cur > h else "below" if cur < lo else "inside"
        close_on = bars.index[-1].strftime("%d %b")
        forming_note = (f"This {unit}'s candle is still forming: price so far is {money(cur)}, {rel} the last "
                        f"{unit}'s range. It closes on {close_on}, so judge it only then.")

    win = VOL_WINDOW[tf]
    avg_vol = float(done["Volume"].iloc[-win - 1:-1].mean())
    avg_range = float((done["High"] - done["Low"]).iloc[-14:].mean())
    pos, neg = [], []

    if latest:
        for p in latest:
            if p["bias"] > 0:
                pos.append(f"Confirms the {p['name']}: a close above {money(p['high'])}.")
                neg.append(f"The {p['name']} fails if price closes below {money(p['low'])}.")
            elif p["bias"] < 0:
                neg.append(f"Confirms the {p['name']}: a close below {money(p['low'])}.")
                pos.append(f"Cancels the {p['name']} warning: a close above {money(p['high'])}.")
            else:
                pos.append(f"Upside resolution of the {p['name']}: a close above {money(p['high'])}.")
                neg.append(f"Downside resolution of the {p['name']}: a close below {money(p['low'])}.")
    else:
        pos.append(f"Follow-through: a close above {money(h)} (the last candle's high) shows buyers still in control.")
        neg.append(f"Follow-through selling: a close below {money(lo)} (the last candle's low).")

    buy, stop, pivot = levels.get("buy"), levels.get("stop"), levels.get("pivot")
    px = levels.get("last") or c      # latest price (close of the newest daily candle)
    if tf == "D":
        if buy and c < pivot:
            pos.append(f"Breakout: a close above {money(buy)} ({(buy / px - 1) * 100:+.1f}% from the latest price "
                       f"{money(px)}) on volume of at least {shares(1.2 * avg_vol)} "
                       f"confirms the breakout (the screener's buy trigger).")
        elif buy:
            pos.append(f"Healthy retest: a dip toward {money(pivot)} (latest price {money(px)}) that closes back above it keeps the "
                       f"breakout valid.")
            neg.append(f"Failed breakout: a close back below {money(pivot)} puts the breakout in doubt.")
        elif not levels.get("holding"):
            neg.append("The screener marks this stock as extended (no buy price). A close below the last "
                       "candle's low could start the pullback it is waiting for.")

    res_b, sup_b = levels.get("res"), levels.get("sup")
    if res_b and (res_b["low"] - c) / c <= 0.10:
        pos.append(f"A close above the resistance band {res_b['low']:.0f}-{res_b['high']:.0f} "
                   f"({res_b['touches']} touches) would be a clean upside break.")
        neg.append(f"A long upper wick inside {res_b['low']:.0f}-{res_b['high']:.0f} would show sellers "
                   f"defending that resistance.")
    if sup_b and (c - sup_b["high"]) / c <= 0.10:
        neg.append(f"A close below the support band {sup_b['low']:.0f}-{sup_b['high']:.0f} would break a level "
                   f"buyers defended {sup_b['touches']} times.")
        pos.append(f"A dip into {sup_b['low']:.0f}-{sup_b['high']:.0f} that recovers and closes higher "
                   f"would show buyers defending support.")

    if stop and stop < px and tf in ("D", "W"):
        neg.append(f"Your stop-loss {money(stop)} is {(px - stop) / px * 100:.1f}% below the latest price {money(px)}. "
                   f"A trade through it ends the plan, whatever the candle looks like.")

    target = levels.get("target")
    if target and buy and tf in ("D", "W"):
        pos.append(f"Your target {money(target)} (profit {(target / buy - 1) * 100:.1f}% from the buy price) is "
                   f"{(target / px - 1) * 100:+.1f}% from the latest price {money(px)}. The plan is to hold toward it, "
                   f"not to sell on one candle.")
    pos.append(f"An up candle on volume above {shares(avg_vol)} (its recent average) gives any rise more weight.")
    neg.append(f"A down candle on volume above {shares(1.5 * avg_vol)} would suggest big holders are selling.")
    neg.append(f"Price trading above {money(h)} but closing back below it is a failed attempt, a warning sign.")

    note = (f"A typical {unit}'s range is about {money(avg_range)} ({avg_range / c * 100:.1f}% of price). "
            f"Moves much bigger than that deserve extra attention.")
    return {"read": read, "forming": forming_note, "latest": latest, "pos": pos[:MAX_POINTS],
            "neg": neg[:MAX_POINTS], "note": note}


# ------------------------------- DATA HELPERS ---------------------------------
def naive(df):
    if getattr(df.index, "tz", None) is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df


def drop_partial(df):
    """Remove today's unfinished daily candle (assumes the PC clock is India time)."""
    now = dt.datetime.now()
    if df.index[-1].date() == now.date() and now.time() < dt.time(16, 0):
        return df.iloc[:-1], True
    return df, False


def is_forming(tf, bars, last_day):
    if tf == "D":
        return False
    gap = (bars.index[-1] - last_day).days
    return gap >= 2 if tf == "W" else gap > 3


def sr_levels(daily, last):
    wdf = ns.resample_ohlc(daily, "W").iloc[-260:]
    bands = ns.sr_bands(wdf, last)
    sup = sorted([b for b in bands if b["high"] <= last], key=lambda b: b["high"], reverse=True)
    res = sorted([b for b in bands if b["low"] >= last], key=lambda b: b["low"])
    return bands, (sup[0] if sup else None), (res[0] if res else None)


# ------------------------------- CHART ----------------------------------------
def draw_panel(ax, tf, info, row, bands, levels, last_close):
    bars, n_bars = info["bars"], BARS[tf]
    d = bars.iloc[-n_bars:]
    start = len(bars) - len(d)
    x = np.arange(len(d))
    o, h = d["Open"].values, d["High"].values
    lo, c = d["Low"].values, d["Close"].values
    y_lo, y_hi = float(lo.min()), float(h.max())
    pad = (y_hi - y_lo) * 0.22          # head-room so labels have space
    lim = (y_lo - pad, y_hi + pad)

    # support / resistance bands (same colours as the screener charts)
    shown = set()
    for b in bands:
        if b["high"] < lim[0] or b["low"] > lim[1]:
            continue
        color, name = (("tab:green", "Support") if b["high"] <= last_close else
                       ("tab:red", "Resistance") if b["low"] >= last_close else
                       ("tab:gray", "Price inside band"))
        ax.axhspan(b["low"], b["high"], color=color, alpha=min(0.10 + 0.05 * b["touches"], 0.40),
                   label=None if name in shown else name)
        shown.add(name)

    # moving average for context
    m, label = MA_SPEC[tf]
    ma = bars["Close"].rolling(m).mean().iloc[-n_bars:]
    ax.plot(x, ma.values, color="tab:blue", linewidth=1.8, label=label)

    # buy-above and stop-loss lines
    for key, color, name in (("buy", GREEN, "Buy above"), ("stop", RED, "Stop-loss"), ("target", "tab:purple", "Target")):
        v = levels.get(key)
        if v and lim[0] <= v <= lim[1]:
            ax.axhline(v, color=color, linestyle=":", linewidth=2, label=name)

    lp = levels.get("last")
    if lp and lim[0] <= lp <= lim[1]:
        ax.axhline(lp, color="black", linestyle="--", linewidth=1.3, label="Latest price")

    # candles
    width = 0.62
    for k in range(len(d)):
        up = c[k] >= o[k]
        col = GREEN if up else RED
        alpha = 0.45 if (info["forming"] and k == len(d) - 1) else 1.0
        ax.plot([k, k], [lo[k], h[k]], color=col, linewidth=1.6, alpha=alpha, zorder=3)
        b_lo, b_h = min(o[k], c[k]), max(abs(c[k] - o[k]), (y_hi - y_lo) * 0.004)
        ax.add_patch(Rectangle((k - width / 2, b_lo), width, b_h, facecolor=col, edgecolor=col,
                               alpha=alpha, zorder=4))

    # call-outs
    chosen = best_per_candle(info["found"], len(info["done"]), RECENT[tf])[:MAX_CALLOUTS]
    for rank, p in enumerate(chosen):
        j, f0 = p["i"] - start, p["first"] - start
        if j < 0:
            continue
        bull, bear = p["bias"] > 0, p["bias"] < 0
        color = GREEN if bull else RED if bear else GREY
        if j > f0:
            ax.axvspan(f0 - 0.5, j + 0.5, color=color, alpha=0.12, zorder=1)
        dy = (38 if rank % 2 == 0 else 80) * (-1 if bull else 1)
        dx = -55 if j > 0.8 * len(d) else 55 if j < 0.2 * len(d) else 0
        ax.annotate(f"{mark(p)} {p['name']}", xy=(j, lo[j] if bull else h[j]), xytext=(dx, dy),
                    textcoords="offset points", ha="center", va="top" if bull else "bottom",
                    fontsize=FONT - 2, fontweight="bold", color=color, zorder=6, annotation_clip=False,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=color, lw=2),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=2))

    for rank, pt in enumerate((info.get("cpat") or [])[:2]):        # classical chart patterns (chart_patterns.py)
        pc = GREEN if pt["bias"] > 0 else RED if pt["bias"] < 0 else "tab:purple"
        for xs, ys in pt["lines"]:
            ax.plot([v - start for v in xs], ys, color=pc, linestyle="-.", linewidth=2.4, zorder=5)
        pp = [(i - start, y) for i, y in pt.get("pts", []) if i - start >= 0]
        if pp:
            ax.scatter([a for a, _ in pp], [b for _, b in pp], s=70, color="white", edgecolor=pc, linewidth=2, zorder=6)
        word = "broke out" if pt["state"] == "breakout" else "forming"
        ax.text(max(pt["i0"] - start, 0), 0.975 - 0.085 * rank, f"{pt['name']} ({word})", transform=ax.get_xaxis_transform(),
                fontsize=FONT - 3, fontweight="bold", color=pc, va="top", zorder=7,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=pc, lw=1.8, alpha=0.92))
    ax.set_xlim(-1, len(d))
    ax.set_ylim(*lim)
    ticks = np.linspace(0, len(d) - 1, 5).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([d.index[t].strftime(TICK_FMT[tf]) for t in ticks])
    latest = info["plan"]["latest"]
    last_txt = ", ".join(f"{mark(p)} {p['name']}" for p in latest) if latest else "no formation on last candle"
    forming = "   (last candle still forming)" if info["forming"] else ""
    ax.set_title(f"{TF_NAME[tf]} candles{forming}\nLast: {last_txt}", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09 if LAYOUT == "side" else -0.12),
              ncol=2, frameon=False)
    ax.grid(alpha=0.25)


def make_chart(symbol, row, tfs, bands, levels, last_close, out_path):
    plt.rcParams.update({"font.size": FONT, "axes.titlesize": FONT + 2, "axes.titleweight": "bold",
                         "legend.fontsize": FONT, "xtick.labelsize": FONT, "ytick.labelsize": FONT,
                         "figure.titlesize": FONT + 4, "figure.titleweight": "bold"})
    if LAYOUT == "side":
        fig, axes = plt.subplots(1, 3, figsize=(27, 10.5), constrained_layout=True)
        dpi = 130      # high resolution so the zoom stays sharp
    else:
        fig, axes = plt.subplots(3, 1, figsize=(11, 20), constrained_layout=True)
        dpi = 120
    for ax, tf in zip(axes, ("D", "W", "M")):
        draw_panel(ax, tf, tfs[tf], row, bands, levels, last_close)
    buy, stop = levels.get("buy"), levels.get("stop")
    tgt = levels.get("target")
    plan = (f"buy above {buy:.2f} / stop {stop:.2f} / target {tgt:.2f}" if buy and stop
            else ("your holding" + (f" / your stop {stop:.2f}" if stop else "")) if levels.get("holding")
            else "extended - no entry now")
    fig.suptitle(f"{symbol}  -  {row['status']}   |   latest price {last_close:,.2f}   |   {plan}")
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)


# ------------------------------- REPORT ---------------------------------------

PAGE_HEAD = """<html><head><meta charset='utf-8'><title>Candles __DATE__</title>
<style>
body{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5;max-width:1500px}
html{scroll-behavior:smooth}
table{border-collapse:collapse;font-size:19px}
td,th{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}
img.chart{width:100%;height:auto;cursor:zoom-in}
.q{padding:3px 12px;border-radius:6px;color:white;font-size:19px}
.STRONG{background:#1b7f3b} .CHECK{background:#b8860b} .WEAK{background:#b22222} .NOTCHECKED{background:#777}
.pos{color:#1b7f3b} .neg{color:#b22222} .neu{color:#555}
.cols{display:flex;gap:20px} .cols>div{flex:1;padding:6px 18px}
.good{background:#e6f4ea;border-left:8px solid #1b7f3b} .bad{background:#fdecea;border-left:8px solid #b22222}
.warn{background:#fff3cd;border-left:8px solid #b8860b;padding:12px 18px}
.small{font-size:17px;color:#444}
a.nav,a.lv{font-size:19px;font-weight:normal}
#top-btn{position:fixed;right:24px;bottom:24px;background:#1f4e9c;color:white;text-decoration:none;
 font-size:22px;font-weight:bold;padding:14px 20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.4);z-index:10}
#lb{position:fixed;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.92);display:none;z-index:50;overflow:auto}
#lb img{display:block;margin:auto;flex:none;max-width:none;cursor:grab;user-select:none;-webkit-user-drag:none}
#lbbar{position:fixed;right:24px;top:14px;z-index:60;display:none}
#lbbar button{font-size:24px;padding:8px 16px;margin-left:8px;border-radius:8px;border:0;cursor:pointer}
#lbhint{position:fixed;left:20px;top:20px;z-index:60;color:#eee;font-size:19px;display:none;
 background:rgba(0,0,0,.55);padding:4px 10px;border-radius:6px}
</style></head><body id='top'>
<script>window.name='candle_view';</script>
<h1>Candlestick view - __DATE__</h1>
<p><a href='__REPORT_LINK__' target='__REPORT_TARGET__'>&larr; Back to __REPORT_LABEL__</a></p>
<p>Latest completed daily candle: __LAST__. Charts show green = closed up, red = closed down.
Arrows point at formations: &#9650; bullish, &#9660; bearish, &#9679; indecision.
<b>Click any chart to zoom</b> (mouse wheel to zoom, drag to move, double-click to zoom in or out, Esc to close).</p>
<div class='warn'><b>How to use this.</b> A candle formation is a hint, not a signal. It counts for more at a
support or resistance band or at your buy-above price, and when volume is above average. The lists below are
conditions to watch for, not predictions. The unfinished weekly and monthly candles are drawn lighter and are
not used to name formations.</div>
<table><tr>__HEAD__</tr>__ROWS__</table>
__BODY__
<a id='top-btn' href='#top'>&uarr; Top</a>
<div id='lbhint'>Wheel = zoom &nbsp; Drag = move &nbsp; Double-click = zoom in/out &nbsp; Esc = close</div>
<div id='lbbar'><button id='zin'>+</button><button id='zout'>&minus;</button><button id='zfit'>Fit</button><button id='z100'>100%</button><button id='zx'>Close</button></div>
<div id='lb' tabindex='-1'><img id='lbimg' alt='' draggable='false'></div>
<script>
(function(){
var lb=document.getElementById('lb'), im=document.getElementById('lbimg');
var bar=document.getElementById('lbbar'), hint=document.getElementById('lbhint');
var s=1, fit=1, MAXS=3, dragging=false, sx=0, sy=0, sl=0, st=0;
function setScale(ns, cx, cy){
  ns=Math.min(Math.max(ns, fit), Math.max(MAXS, fit));
  if(Math.abs(ns-s)<1e-6) return;
  var r=im.getBoundingClientRect(), ox=cx-r.left, oy=cy-r.top, k=ns/s;
  s=ns; im.style.width=(im.naturalWidth*s)+'px';
  lb.scrollLeft=ox*k-cx; lb.scrollTop=oy*k-cy;
}
function fitView(){
  fit=Math.min(1, lb.clientWidth/im.naturalWidth, lb.clientHeight/im.naturalHeight);
  s=fit; im.style.width=(im.naturalWidth*s)+'px'; lb.scrollLeft=0; lb.scrollTop=0;
}
function centre(f){ setScale(s*f, lb.clientWidth/2, lb.clientHeight/2); }
function openLb(src){
  lb.style.display='flex'; bar.style.display=hint.style.display='block';
  document.body.style.overflow='hidden';
  im.onload=fitView; im.src=src;
  if(im.complete && im.naturalWidth){ fitView(); }
  lb.focus();
}
function closeLb(){
  lb.style.display='none'; bar.style.display=hint.style.display='none';
  document.body.style.overflow=''; dragging=false;
}
Array.prototype.forEach.call(document.querySelectorAll('img.chart'), function(i){
  i.addEventListener('click', function(){ openLb(i.src); });
});
lb.addEventListener('wheel', function(e){
  e.preventDefault(); setScale(s*(e.deltaY<0?1.2:1/1.2), e.clientX, e.clientY);
}, {passive:false});
im.addEventListener('dragstart', function(e){ e.preventDefault(); });
im.addEventListener('mousedown', function(e){
  if(e.button!==0) return;
  dragging=true; sx=e.clientX; sy=e.clientY; sl=lb.scrollLeft; st=lb.scrollTop;
  im.style.cursor='grabbing'; e.preventDefault();
});
window.addEventListener('mousemove', function(e){
  if(!dragging) return;
  lb.scrollLeft=sl-(e.clientX-sx); lb.scrollTop=st-(e.clientY-sy);
});
window.addEventListener('mouseup', function(){ dragging=false; im.style.cursor='grab'; });
im.addEventListener('dblclick', function(e){
  if(s>fit*1.05) fitView(); else setScale(Math.max(1, fit*2.5), e.clientX, e.clientY);
});
document.getElementById('zin').onclick=function(){ centre(1.4); };
document.getElementById('zout').onclick=function(){ centre(1/1.4); };
document.getElementById('zfit').onclick=fitView;
document.getElementById('z100').onclick=function(){ setScale(1, lb.clientWidth/2, lb.clientHeight/2); };
document.getElementById('zx').onclick=closeLb;
document.addEventListener('keydown', function(e){
  if(lb.style.display!=='flex') return;
  if(e.key==='Escape') closeLb();
  else if(e.key==='+'||e.key==='=') centre(1.4);
  else if(e.key==='-') centre(1/1.4);
  else if(e.key==='0') fitView();
});
})();
</script>
</body></html>"""

def patterns_html(tf, info, last):
    """Chart patterns (head and shoulders, triangles, cup and handle ...) with breakout level, target and stop."""
    pats = info.get("cpat") or []
    if not pats:
        return "<p>No classical chart pattern found on the completed candles.</p>"
    lis = ""
    for p in pats:
        cls = "pos" if p["bias"] > 0 else "neg" if p["bias"] < 0 else "neu"
        lis += f"<li class='{cls}'>{cp.describe(p, last, UNIT[tf])}</li>"
    return f"<ul>{lis}</ul>"


def formations_html(tf, info):
    items = best_per_candle(info["found"], len(info["done"]), RECENT[tf])[:MAX_CALLOUTS]
    if not items:
        return "<p>No classic formation in the recent candles.</p>"
    lis = ""
    for p in items:
        cls = "pos" if p["bias"] > 0 else "neg" if p["bias"] < 0 else "neu"
        when = info["done"].index[p["i"]].strftime(DATE_FMT[tf])
        lis += (f"<li><b class='{cls}'>{mark(p)} {p['name']}</b> ({when}): {PATTERNS[p['name']][2]}</li>")
    return f"<ul>{lis}</ul>"


def price_line(tfs, row):
    d = tfs["D"]["bars"]
    return ns.price_line_html(float(d["Close"].iloc[-1]), d.index[-1].strftime("%d %b %Y"),
                              row["buy_above"], row["stop_loss"])


def stock_html(symbol, row, tfs):
    q = row.get("quality", "") if hasattr(row, "get") else ""
    q = "" if pd.isna(q) else q
    notes = row.get("quality_notes", "")
    notes = "" if pd.isna(notes) else notes
    parts = [f"<h2>{symbol} &nbsp;<span class='q {q.replace(' ', '')}'>{q}</span> &nbsp;"
             f"<small>{row['status']}</small> &nbsp;<a class='nav' href='{REPORT_LINK}#{symbol}' target='{REPORT_TARGET}'>{REPORT_NAME}</a> &nbsp;{ns.live_links_html(symbol)}</h2>",
             f"<p>{price_line(tfs, row)}</p>",
             f"<p>{notes}</p>",
             f"<img class='chart' title='Click to zoom' src='candles/{symbol}.png'>"]
    for tf in ("D", "W", "M"):
        info = tfs[tf]
        p = info["plan"]
        pos = "".join(f"<li>{t}</li>" for t in p["pos"])
        neg = "".join(f"<li>{t}</li>" for t in p["neg"])
        parts.append(f"<h3>{TF_NAME[tf]}: formations spotted</h3>{formations_html(tf, info)}")
        if cp is not None and tf in ("D", "W"):
            last_px = float(tfs["D"]["bars"]["Close"].iloc[-1])
            parts.append(f"<h3>{TF_NAME[tf]}: chart patterns</h3>{patterns_html(tf, info, last_px)}")
        parts.append(f"<h3>{NEXT_NAME[tf]}: what to watch</h3><p>{p['read']}</p>")
        if p["forming"]:
            parts.append(f"<p><i>{p['forming']}</i></p>")
        parts.append(f"<div class='cols'><div class='good'><b>Positive signs</b><ul>{pos}</ul></div>"
                     f"<div class='bad'><b>Warning signs</b><ul>{neg}</ul></div></div>"
                     f"<p class='small'>{p['note']}</p>")
    return "".join(parts)


def write_report(rows, out_dir, last_dates):
    def cell(tfs, tf):
        lat = tfs[tf]["plan"]["latest"]
        return ", ".join(f"{mark(p)} {p['name']}" for p in lat) if lat else "-"

    def pat_cell(tfs):
        out = []
        for tf in ("D", "W"):
            names = ", ".join(f"{p['name']}{' (broke out)' if p['state'] == 'breakout' else ''}" for p in tfs[tf].get("cpat", []))
            if names:
                out.append(f"{tf}: {names}")
        return "<br>".join(out) if out else "-"

    head = ["Stock", "Status", "Quality", "Daily last candle", "Weekly last candle", "Monthly last candle", "Chart patterns (D / W)"]
    trs = ""
    for sym, row, tfs in rows:
        q = row.get("quality", "")
        q = "" if pd.isna(q) else q
        trs += (f"<tr><td><a href='#{sym}'>{sym}</a></td><td>{row['status']}</td><td>{q}</td>"
                f"<td>{cell(tfs, 'D')}</td><td>{cell(tfs, 'W')}</td><td>{cell(tfs, 'M')}</td><td>{pat_cell(tfs)}</td></tr>")
    body = "".join(f"<div id='{sym}'>{stock_html(sym, row, tfs)}</div><hr>" for sym, row, tfs in rows)
    html = (PAGE_HEAD.replace("__DATE__", str(dt.date.today())).replace("__LAST__", last_dates)
            .replace("__HEAD__", "".join(f"<th>{h}</th>" for h in head)).replace("__ROWS__", trs)
            .replace("__BODY__", body).replace("__REPORT_LINK__", REPORT_LINK)
            .replace("__REPORT_TARGET__", REPORT_TARGET).replace("__REPORT_LABEL__", REPORT_LABEL))
    path = out_dir / "candle_report.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------- MAIN -----------------------------------------
def build(symbol, df, row):
    """Everything for one stock: {tf: info}, plus the support/resistance bands and levels."""
    daily, dropped = drop_partial(naive(df))
    last_close = float(daily["Close"].iloc[-1])
    bands, sup, res = sr_levels(daily, last_close)
    buy = None if pd.isna(row["buy_above"]) else float(row["buy_above"])
    stop = None if pd.isna(row["stop_loss"]) else float(row["stop_loss"])
    levels = {"holding": bool(row.get("holding", False)), "buy": buy, "stop": stop, "pivot": buy / 1.005 if buy else None, "sup": sup, "res": res,
              "last": last_close, "target": ns.target_price(buy, stop) if buy and stop else None}
    series = {"D": daily, "W": ns.resample_ohlc(daily, "W"), "M": ns.resample_ohlc(daily, "M")}
    tfs = {}
    for tf, bars in series.items():
        forming = is_forming(tf, bars, daily.index[-1])
        done = bars.iloc[:-1] if forming else bars
        found = detect(done)
        plan = make_plan(tf, bars, done, forming, found, levels)
        cpat = []
        if cp is not None and tf in ("D", "W"):
            try:
                cpat = cp.detect(done, tf)
            except Exception:  # noqa: BLE001
                cpat = []
        tfs[tf] = {"bars": bars, "done": done, "forming": forming, "found": found, "plan": plan, "cpat": cpat}
    return tfs, bands, levels, last_close, daily.index[-1], dropped


def run(results, prices, out_dir):
    (out_dir / "candles").mkdir(parents=True, exist_ok=True)
    rows, signal_rows, last_day, dropped_any = [], [], None, False
    for _, row in results.iterrows():
        sym = row["symbol"]
        if sym not in prices:
            print(f"{sym}: no price data, skipped.")
            continue
        try:
            tfs, bands, levels, last_close, day, dropped = build(sym, prices[sym], row)
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: could not analyse ({e}), skipped.")
            continue
        last_day, dropped_any = day, dropped_any or dropped
        make_chart(sym, row, tfs, bands, levels, last_close, out_dir / "candles" / f"{sym}.png")
        rows.append((sym, row, tfs))
        for tf in ("D", "W", "M"):
            info = tfs[tf]
            for p in best_per_candle(info["found"], len(info["done"]), RECENT[tf]):
                signal_rows.append({"symbol": sym, "timeframe": TF_NAME[tf],
                                    "candle": info["done"].index[p["i"]].date(), "formation": p["name"],
                                    "bias": {1: "bullish", -1: "bearish", 0: "neutral"}[p["bias"]]})
        for tf in ("D", "W"):
            info = tfs[tf]
            for p in info.get("cpat", []):
                signal_rows.append({"symbol": sym, "timeframe": TF_NAME[tf],
                                    "candle": info["done"].index[min(p["i1"], len(info["done"]) - 1)].date(),
                                    "formation": f"Chart pattern: {p['name']} ({p['state']})",
                                    "bias": {1: "bullish", -1: "bearish", 0: "neutral"}[p["bias"]]})
        lat = tfs["D"]["plan"]["latest"]
        print(f"  {sym:<12} daily last candle: "
              f"{', '.join(mark(p) + ' ' + p['name'] for p in lat) if lat else '-'}")
    if not rows:
        return None
    pd.DataFrame(signal_rows).to_csv(out_dir / "candle_signals.csv", index=False)
    note = last_day.strftime("%d %b %Y") + (" (today's unfinished candle ignored)" if dropped_any else "")
    return write_report(rows, out_dir, note)


def main():
    files = sorted(Path("output").glob("*/results.csv"))
    if not files:
        sys.exit("No results.csv found. Run nse_screener.py first.")
    path = files[-1]
    res = pd.read_csv(path)
    args = [a.upper() for a in sys.argv[1:]]
    if args:
        missing = [a for a in args if a not in set(res["symbol"])]
        if missing:
            print("Not in the latest results.csv, skipped: " + ", ".join(missing))
        res = res[res["symbol"].isin(args)]
    else:
        if ns.SKIP_WEAK and "quality" in res.columns:
            res = res[res["quality"] != "WEAK"]
        res = res.head(ns.TOP_N)
    if res.empty:
        sys.exit("Nothing to draw.")
    print(f"Using {path}  ({len(res)} stocks)")
    prices = ns.download_prices(list(res["symbol"]))
    print("\nFinding candle formations and drawing charts ...")
    report = run(res, prices, path.parent)
    if report is None:
        sys.exit("No charts could be made.")
    print(f"\nReport: {report}")
    if OPEN_REPORT:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
