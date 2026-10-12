#!/usr/bin/env python3
"""
Classical chart patterns (the list taught by Sunil Gurjar) found on daily and weekly candles

Used automatically by candle_view.py (keep this file in the same folder): the patterns are drawn on the daily and weekly
candlestick charts and described in the candlestick report, with the breakout level, a measured-move target and a stop.
Test it on its own:   python chart_patterns.py TCS INFY      (needs yfinance; prints what it finds)

Patterns looked for
  Reversal   double top, double bottom, triple top, triple bottom, head and shoulders, inverse head and shoulders,
             rounding top, rounding bottom
  Continuation / range   ascending triangle, descending triangle, symmetrical triangle, rectangle, rising wedge, falling wedge,
             up channel, down channel, bull flag, bear flag, bull pennant, bear pennant
  Bullish base   cup and handle
These are the names in his books and courses (Price Action Trading, Breakout Trading Made Easy, his technical analysis course).

How a pattern is found (all numbers are in CFG and the constants below; they are MY starting values)
  * Swing highs and lows are found (a high or low that stands out for k candles each side) and made to alternate.
  * Each detector looks for its shape in the most recent swings: equal tops or bottoms within a tolerance, a head clearly above two
    similar shoulders, two trend lines through the last swing highs and lows (flat, rising or falling, converging or parallel),
    a sharp pole followed by a short, shallow pause, a rounded cup with a small handle, a curved (quadratic) fit.
  * It then asks where price is: still FORMING inside the pattern, or a recent BREAKOUT / CONFIRMATION (a CLOSE beyond the
    neckline or trend line). Old breakouts and patterns that price has already broken the wrong way are dropped.
  * For every pattern it gives the breakout level(s), a measured-move target (the pattern's height projected from the
    breakout) and a stop (the other side of the pattern).

Honest limits
  * Only the pattern NAMES come from Gurjar's material. His exact rules and tolerances were not verified: the shapes above are
    textbook definitions turned into numbers, and two people drawing the same chart will often disagree. Treat every hit as a
    candidate to look at, never as a signal. If a drawn line looks wrong to your eye, trust your eye.
  * Patterns work best at support or resistance and with a volume surge on the breakout; the notes show the breakout volume.
  * About a third of breakouts fail. The measured-move target is a planning level, not a prediction.
  * Daily and weekly candles only (the monthly chart has too few candles). The unfinished weekly candle is not used.
  * Tested on made-up price paths only; real Yahoo Finance data has not been run through it.
"""

import sys

import numpy as np
import pandas as pd

# ----------------------------- SETTINGS (edit me) -----------------------------
CFG = {
    # k: swing size (candles each side); tol: "equal" levels within this fraction; gap: minimum candles between two tops/bottoms;
    # dep: minimum depth between them; span: minimum candles for a line pattern; flat: a line moving less than this fraction over
    # the pattern is flat; pole: minimum flagpole rise; cup: minimum cup length; old: ignore breakouts older than this many
    # candles; stale: ignore a forming pattern whose last swing is older than this; win: how far back to look
    "D": dict(k=4, tol=0.03, gap=10, dep=0.06, span=18, flat=0.025, pole=0.15, pole_bars=20, cup=40, old=10, stale=35, win=170),
    "W": dict(k=3, tol=0.04, gap=6, dep=0.08, span=10, flat=0.03, pole=0.25, pole_bars=10, cup=20, old=6, stale=18, win=110),
}
MAX_PATTERNS = 3                # patterns kept per timeframe (best first)
MIN_HEIGHT_ATR = 5.0            # a pattern must be at least this many average candle ranges (ATR) tall: filters out noise
BREAKOUT_VOLUME = 1.5           # a breakout is "on volume" at this multiple of the 50-candle average
# ------------------------------------------------------------------------------

MEANING = {
    "Double top": (-1, "Price rose to about the same high twice and fell back: buyers failed twice at that level. "
                       "Confirmed by a close below the low between the two tops (the neckline)."),
    "Double bottom": (1, "Price fell to about the same low twice and bounced: sellers failed twice at that level. "
                         "Confirmed by a close above the high between the two bottoms (the neckline)."),
    "Triple top": (-1, "Three failed attempts at about the same high. Confirmed by a close below the lowest dip between them."),
    "Triple bottom": (1, "Three defended lows at about the same level. Confirmed by a close above the highest bounce between them."),
    "Head and shoulders": (-1, "A high (the head) flanked by two lower, similar highs (the shoulders). Confirmed by a close below "
                               "the line joining the two dips (the neckline)."),
    "Inverse head and shoulders": (1, "A low (the head) flanked by two higher, similar lows (the shoulders). Confirmed by a close above "
                                      "the line joining the two bounces (the neckline)."),
    "Rounding top": (-1, "Price curved over slowly from rising to falling: buying fades gradually. A close below the left rim "
                         "level confirms."),
    "Rounding bottom": (1, "Price curved up slowly from falling to rising: selling fades gradually (a saucer). A close above the "
                           "left rim level confirms."),
    "Ascending triangle": (1, "Flat resistance with rising lows: buyers keep stepping up and press on one level. Usually breaks up."),
    "Descending triangle": (-1, "Flat support with falling highs: sellers keep stepping down and press on one level. Usually breaks down."),
    "Symmetrical triangle": (0, "Lower highs and higher lows squeezing together: a coil. It can break either way; trade the side it picks."),
    "Rectangle": (0, "Price bounces between a flat support and a flat resistance. A close outside either edge ends the range."),
    "Rising wedge": (-1, "Highs and lows both rising but squeezing together: the climb is losing force. Usually breaks down."),
    "Falling wedge": (1, "Highs and lows both falling but squeezing together: the drop is losing force. Usually breaks up."),
    "Up channel": (1, "Price rising between two parallel lines. A close below the lower line would end the up-trend."),
    "Down channel": (-1, "Price falling between two parallel lines. A close above the upper line would end the down-trend."),
    "Bull flag": (1, "A sharp rise (the pole) then a short, shallow drift down between parallel lines. Usually continues up."),
    "Bear flag": (-1, "A sharp fall (the pole) then a short, shallow drift up between parallel lines. Usually continues down."),
    "Bull pennant": (1, "A sharp rise then a small squeeze between converging lines. Usually continues up."),
    "Bear pennant": (-1, "A sharp fall then a small squeeze between converging lines. Usually continues down."),
    "Cup and handle": (1, "A rounded dip and recovery to the old high (the cup), then a small pullback (the handle). "
                          "Breaks out when price closes above the rim."),
}


# ------------------------------- HELPERS --------------------------------------
def _arrays(df):
    return {k: df[k].astype(float).values for k in ("Open", "High", "Low", "Close", "Volume")}


def pivots(X, k, start=0):
    """Alternating swing highs and lows: [(index, price, 'H' or 'L')], oldest first."""
    h, l, n = X["High"], X["Low"], len(X["High"])
    raw = []
    for i in range(max(k, start), n - k):
        if h[i] >= h[i - k:i + k + 1].max():
            raw.append((i, float(h[i]), "H"))
        if l[i] <= l[i - k:i + k + 1].min():
            raw.append((i, float(l[i]), "L"))
    raw.sort(key=lambda p: (p[0], p[2]))
    out = []
    for p in raw:
        if out and out[-1][2] == p[2]:
            better = p[1] > out[-1][1] if p[2] == "H" else p[1] < out[-1][1]
            if better:
                out[-1] = p
        else:
            out.append(p)
    return out


def _vol_ratio(X, i):
    v = X["Volume"]
    base = v[max(0, i - 50):i]
    return float(v[i] / base.mean()) if len(base) and base.mean() > 0 else None


def _first_close(X, i_from, level, direction):
    """First candle after i_from that CLOSES beyond level (direction +1 above, -1 below), or None."""
    c = X["Close"]
    for j in range(i_from + 1, len(c)):
        if (direction > 0 and c[j] > level) or (direction < 0 and c[j] < level):
            return j
    return None


def _p(name, i0, i1, lines, **kw):
    bias = MEANING[name][0]
    d = {"name": name, "bias": bias, "i0": int(i0), "i1": int(i1), "lines": lines, "state": "forming", "age": None,
         "level_up": None, "level_dn": None, "target_up": None, "target_dn": None, "stop_up": None, "stop_dn": None,
         "vol": None, "quality": 1.0, "dir": 0, "extra": ""}
    d.update(kw)
    return d


def _seg(x0, y0, x1, y1):
    return ([float(x0), float(x1)], [float(y0), float(y1)])


# ------------------------------- DETECTORS ------------------------------------
def _double_triple(X, piv, cfg, n):
    c, out = X["Close"], []
    P = piv
    # ---- double ----
    for j in range(max(0, len(P) - 7), len(P) - 2):
        a, m, b = P[j], P[j + 1], P[j + 2]
        if b[0] - a[0] < cfg["gap"]:
            continue
        if a[2] == "H" and m[2] == "L" and b[2] == "H":
            top = min(a[1], b[1])
            if abs(a[1] - b[1]) / max(a[1], b[1]) > cfg["tol"] or (top - m[1]) / top < cfg["dep"]:
                continue
            lo_before = c[max(0, a[0] - 40):a[0] + 1].min()
            if c[a[0]] / lo_before - 1 < 0.10:
                continue
            if X["High"][b[0] + 1:].size and X["High"][b[0] + 1:].max() > max(a[1], b[1]) * 1.01:
                continue
            j2 = _first_close(X, b[0], m[1], -1)
            st = "breakout" if j2 is not None else "forming"
            if st == "breakout" and n - 1 - j2 > cfg["old"]:
                continue
            if st == "forming" and n - 1 - b[0] > cfg["stale"]:
                continue
            h = max(a[1], b[1]) - m[1]
            out.append(_p("Double top", a[0], n - 1 if st == "forming" else j2, [_seg(a[0], m[1], max(n - 1, b[0]), m[1])],
                          level_dn=m[1], target_dn=m[1] - h, stop_dn=max(a[1], b[1]), state=st, dir=-1,
                          age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2),
                          quality=1.0 + 0.2 * (b[0] - a[0] > 2 * cfg["gap"]), extra=f"Tops {a[1]:,.2f} and {b[1]:,.2f}; neckline {m[1]:,.2f}.",
                          pts=[(a[0], a[1]), (b[0], b[1])]))
        if a[2] == "L" and m[2] == "H" and b[2] == "L":
            bot = max(a[1], b[1])
            if abs(a[1] - b[1]) / max(a[1], b[1]) > cfg["tol"] or (m[1] - bot) / m[1] < cfg["dep"]:
                continue
            hi_before = c[max(0, a[0] - 40):a[0] + 1].max()
            if hi_before / c[a[0]] - 1 < 0.10:
                continue
            if X["Low"][b[0] + 1:].size and X["Low"][b[0] + 1:].min() < min(a[1], b[1]) * 0.99:
                continue
            j2 = _first_close(X, b[0], m[1], 1)
            st = "breakout" if j2 is not None else "forming"
            if st == "breakout" and n - 1 - j2 > cfg["old"]:
                continue
            if st == "forming" and n - 1 - b[0] > cfg["stale"]:
                continue
            h = m[1] - min(a[1], b[1])
            out.append(_p("Double bottom", a[0], n - 1 if st == "forming" else j2, [_seg(a[0], m[1], max(n - 1, b[0]), m[1])],
                          level_up=m[1], target_up=m[1] + h, stop_up=min(a[1], b[1]), state=st, dir=1,
                          age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2),
                          quality=1.0 + 0.2 * (b[0] - a[0] > 2 * cfg["gap"]), extra=f"Bottoms {a[1]:,.2f} and {b[1]:,.2f}; neckline {m[1]:,.2f}.",
                          pts=[(a[0], a[1]), (b[0], b[1])]))
    # ---- triple ----
    for j in range(max(0, len(P) - 7), len(P) - 4):
        seq = P[j:j + 5]
        if seq[-1][0] - seq[0][0] < 2 * cfg["gap"]:
            continue
        if [s[2] for s in seq] == ["H", "L", "H", "L", "H"]:
            hs, ls = [seq[0][1], seq[2][1], seq[4][1]], [seq[1][1], seq[3][1]]
            if (max(hs) - min(hs)) / max(hs) > cfg["tol"] or (min(hs) - max(ls)) / min(hs) < cfg["dep"] / 2:
                continue
            neck = min(ls)
            j2 = _first_close(X, seq[-1][0], neck, -1)
            st = "breakout" if j2 is not None else "forming"
            if (st == "breakout" and n - 1 - j2 > cfg["old"]) or (st == "forming" and n - 1 - seq[-1][0] > cfg["stale"]):
                continue
            if X["High"][seq[-1][0] + 1:].size and X["High"][seq[-1][0] + 1:].max() > max(hs) * 1.01:
                continue
            h = max(hs) - neck
            out.append(_p("Triple top", seq[0][0], n - 1 if st == "forming" else j2, [_seg(seq[0][0], neck, max(n - 1, seq[-1][0]), neck)],
                          level_dn=neck, target_dn=neck - h, stop_dn=max(hs), state=st, dir=-1, age=None if j2 is None else n - 1 - j2,
                          vol=None if j2 is None else _vol_ratio(X, j2), quality=1.3,
                          extra=f"Three tops near {np.mean(hs):,.2f}; neckline {neck:,.2f}.", pts=[(s[0], s[1]) for s in seq if s[2] == "H"]))
        if [s[2] for s in seq] == ["L", "H", "L", "H", "L"]:
            ls_, hs_ = [seq[0][1], seq[2][1], seq[4][1]], [seq[1][1], seq[3][1]]
            if (max(ls_) - min(ls_)) / max(ls_) > cfg["tol"] or (min(hs_) - max(ls_)) / min(hs_) < cfg["dep"] / 2:
                continue
            neck = max(hs_)
            j2 = _first_close(X, seq[-1][0], neck, 1)
            st = "breakout" if j2 is not None else "forming"
            if (st == "breakout" and n - 1 - j2 > cfg["old"]) or (st == "forming" and n - 1 - seq[-1][0] > cfg["stale"]):
                continue
            if X["Low"][seq[-1][0] + 1:].size and X["Low"][seq[-1][0] + 1:].min() < min(ls_) * 0.99:
                continue
            h = neck - min(ls_)
            out.append(_p("Triple bottom", seq[0][0], n - 1 if st == "forming" else j2, [_seg(seq[0][0], neck, max(n - 1, seq[-1][0]), neck)],
                          level_up=neck, target_up=neck + h, stop_up=min(ls_), state=st, dir=1, age=None if j2 is None else n - 1 - j2,
                          vol=None if j2 is None else _vol_ratio(X, j2), quality=1.3,
                          extra=f"Three bottoms near {np.mean(ls_):,.2f}; neckline {neck:,.2f}.", pts=[(s[0], s[1]) for s in seq if s[2] == "L"]))
    return out


def _head_shoulders(X, piv, cfg, n):
    c, out = X["Close"], []
    P = piv
    for j in range(max(0, len(P) - 8), len(P) - 4):
        s = P[j:j + 5]
        if s[-1][0] - s[0][0] < 2 * cfg["gap"]:
            continue
        kinds = [q[2] for q in s]
        if kinds == ["H", "L", "H", "L", "H"]:
            ls, t1, hd, t2, rs = s
            if not (hd[1] > max(ls[1], rs[1]) * 1.03) or abs(ls[1] - rs[1]) / ((ls[1] + rs[1]) / 2) > 0.10:
                continue
            if c[ls[0]] / c[max(0, ls[0] - 40):ls[0] + 1].min() - 1 < 0.08:
                continue
            slope = (t2[1] - t1[1]) / (t2[0] - t1[0])
            neck = lambda x: t1[1] + slope * (x - t1[0])  # noqa: E731
            if abs(slope * (rs[0] - ls[0])) / hd[1] > 0.10:
                continue
            j2 = None
            for q in range(rs[0] + 1, n):
                if c[q] < neck(q):
                    j2 = q
                    break
            st = "breakout" if j2 is not None else "forming"
            if (st == "breakout" and n - 1 - j2 > cfg["old"]) or (st == "forming" and n - 1 - rs[0] > cfg["stale"]):
                continue
            if X["High"][rs[0] + 1:].size and X["High"][rs[0] + 1:].max() > hd[1]:
                continue
            h = hd[1] - neck(hd[0])
            end = n - 1 if st == "forming" else j2
            out.append(_p("Head and shoulders", ls[0], end, [_seg(t1[0], t1[1], end, neck(end))],
                          level_dn=neck(n - 1), target_dn=neck(end) - h, stop_dn=rs[1], state=st, dir=-1,
                          age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2), quality=1.4,
                          extra=f"Left shoulder {ls[1]:,.2f}, head {hd[1]:,.2f}, right shoulder {rs[1]:,.2f}; neckline now {neck(n - 1):,.2f}.",
                          pts=[(ls[0], ls[1]), (hd[0], hd[1]), (rs[0], rs[1])]))
        if kinds == ["L", "H", "L", "H", "L"]:
            ls, t1, hd, t2, rs = s
            if not (hd[1] < min(ls[1], rs[1]) * 0.97) or abs(ls[1] - rs[1]) / ((ls[1] + rs[1]) / 2) > 0.10:
                continue
            if c[max(0, ls[0] - 40):ls[0] + 1].max() / c[ls[0]] - 1 < 0.08:
                continue
            slope = (t2[1] - t1[1]) / (t2[0] - t1[0])
            neck = lambda x: t1[1] + slope * (x - t1[0])  # noqa: E731
            if abs(slope * (rs[0] - ls[0])) / t1[1] > 0.10:
                continue
            j2 = None
            for q in range(rs[0] + 1, n):
                if c[q] > neck(q):
                    j2 = q
                    break
            st = "breakout" if j2 is not None else "forming"
            if (st == "breakout" and n - 1 - j2 > cfg["old"]) or (st == "forming" and n - 1 - rs[0] > cfg["stale"]):
                continue
            if X["Low"][rs[0] + 1:].size and X["Low"][rs[0] + 1:].min() < hd[1]:
                continue
            h = neck(hd[0]) - hd[1]
            end = n - 1 if st == "forming" else j2
            out.append(_p("Inverse head and shoulders", ls[0], end, [_seg(t1[0], t1[1], end, neck(end))],
                          level_up=neck(n - 1), target_up=neck(end) + h, stop_up=rs[1], state=st, dir=1,
                          age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2), quality=1.4,
                          extra=f"Left shoulder {ls[1]:,.2f}, head {hd[1]:,.2f}, right shoulder {rs[1]:,.2f}; neckline now {neck(n - 1):,.2f}.",
                          pts=[(ls[0], ls[1]), (hd[0], hd[1]), (rs[0], rs[1])]))
    return out


def _line_patterns(X, piv, cfg, n):
    """Triangles, rectangle, wedges and channels from two trend lines through the last swing highs and lows."""
    c, out = X["Close"], []
    H = [p for p in piv if p[2] == "H"]
    L = [p for p in piv if p[2] == "L"]
    fl = cfg["flat"]
    for nh, nl in ((3, 3), (3, 2), (2, 3)):          # at least 5 touches: two lines through 2 points each are too easy to find
        hs, ls = H[-nh:], L[-nl:]
        if len(hs) < nh or len(ls) < nl:
            continue
        x0, x1 = min(hs[0][0], ls[0][0]), max(hs[-1][0], ls[-1][0])
        if x1 - x0 < cfg["span"] or n - 1 - x1 > cfg["stale"]:
            continue
        sh, ih = np.polyfit([p[0] for p in hs], [p[1] for p in hs], 1)
        sl, il = np.polyfit([p[0] for p in ls], [p[1] for p in ls], 1)
        mean = float(c[x0:x1 + 1].mean())
        if max(abs(p[1] - (sh * p[0] + ih)) for p in hs) / mean > 0.02 or max(abs(p[1] - (sl * p[0] + il)) for p in ls) / mean > 0.02:
            continue
        up = lambda x: sh * x + ih  # noqa: E731
        lo = lambda x: sl * x + il  # noqa: E731
        w0, w1 = up(x0) - lo(x0), up(x1) - lo(x1)
        if w0 <= 0 or w1 <= 0 or not 0.04 <= w0 / mean <= 0.40:
            continue
        cH, cL = sh * (x1 - x0) / mean, sl * (x1 - x0) / mean
        flatH, flatL = abs(cH) <= fl, abs(cL) <= fl
        upH, upL, dnH, dnL = cH > fl, cL > fl, cH < -fl, cL < -fl
        ratio = w1 / w0
        conv, para = ratio < 0.75, 0.85 <= ratio <= 1.15
        name = None
        if flatH and flatL and para:
            name = "Rectangle"
        elif flatH and upL and conv:
            name = "Ascending triangle"
        elif dnH and flatL and conv:
            name = "Descending triangle"
        elif dnH and upL and conv:
            name = "Symmetrical triangle"
        elif upH and upL and conv:
            name = "Rising wedge"
        elif dnH and dnL and conv:
            name = "Falling wedge"
        elif upH and upL and para:
            name = "Up channel"
        elif dnH and dnL and para:
            name = "Down channel"
        if name is None:
            continue
        brk = None
        for j in range(x1, n):
            if c[j] > up(j):
                brk = (j, 1)
                break
            if c[j] < lo(j):
                brk = (j, -1)
                break
        if brk:
            if n - 1 - brk[0] > cfg["old"]:
                continue
            state, bd, age, vol = "breakout", brk[1], n - 1 - brk[0], _vol_ratio(X, brk[0])
        else:
            ux, lx = up(n - 1), lo(n - 1)
            if not (lx * 0.99 <= c[-1] <= ux * 1.01):
                continue
            state, bd, age, vol = "forming", 0, None, None
        end = n - 1 if state == "forming" else brk[0]
        lines = [_seg(x0, up(x0), end, up(end)), _seg(x0, lo(x0), end, lo(end))]
        ux, lx = up(n - 1), lo(n - 1)
        d = _p(name, x0, end, lines, state=state, dir=bd, age=age, vol=vol, quality=1.0 + 0.15 * (nh + nl == 6),
               pts=[(p[0], p[1]) for p in hs + ls])
        chan = name in ("Up channel", "Down channel")
        if chan and nh + nl < 6:
            continue
        if not chan:
            d.update(level_up=ux, level_dn=lx, target_up=ux + w0, target_dn=lx - w0, stop_up=lx, stop_dn=ux)
            if state == "breakout":
                lvl = up(brk[0]) if bd > 0 else lo(brk[0])
                d.update(level_up=lvl if bd > 0 else None, level_dn=lvl if bd < 0 else None,
                         target_up=lvl + w0 if bd > 0 else None, target_dn=lvl - w0 if bd < 0 else None,
                         stop_up=lx if bd > 0 else None, stop_dn=ux if bd < 0 else None)
            expected = MEANING[name][0]
            if state == "breakout" and expected and bd != expected:
                d["extra"] = "It broke the opposite way to the usual one for this shape."
        else:
            d.update(level_up=ux, level_dn=lx)
            d["extra"] = "A channel has no measured target: the lines are the levels (upper line = resistance, lower = support)."
        out.append(d)
        break
    return out


def _flag(X, piv, cfg, n):
    h, l, c, out = X["High"], X["Low"], X["Close"], []
    look = min(n - 1, cfg["pole_bars"] + 25)
    for bull in (True, False):
        seg = h[n - look:] if bull else l[n - look:]
        p = n - look + int(np.argmax(seg) if bull else np.argmin(seg))
        m = n - 1 - p
        if not 5 <= m <= 25:
            continue
        lo_i = max(0, p - cfg["pole_bars"])
        if bull:
            s = lo_i + int(np.argmin(l[lo_i:p + 1]))
            pole = h[p] / l[s] - 1
        else:
            s = lo_i + int(np.argmax(h[lo_i:p + 1]))
            pole = h[s] / l[p] - 1
        if pole < cfg["pole"] or p - s < 2:
            continue
        pole_h = (h[p] - l[s]) if bull else (h[s] - l[p])
        cons_lo, cons_hi = l[p + 1:], h[p + 1:]
        retr = ((h[p] - cons_lo.min()) if bull else (cons_hi.max() - l[p])) / pole_h
        if retr > 0.5:
            continue
        if bull and cons_hi.max() > h[p] * 1.01 and c[-1] <= h[p]:
            continue
        xs = np.arange(p + 1, n)
        if len(xs) < 5:
            continue
        sh, ih = np.polyfit(xs, cons_hi, 1)
        sl, il = np.polyfit(xs, cons_lo, 1)
        up = lambda x: sh * x + ih  # noqa: E731
        lo = lambda x: sl * x + il  # noqa: E731
        w0, w1 = up(p + 1) - lo(p + 1), up(n - 1) - lo(n - 1)
        if w0 <= 0 or w1 <= 0:
            continue
        pennant = w1 / w0 < 0.7
        against = (sh < 0 and sl < 0) if bull else (sh > 0 and sl > 0)
        if not pennant and not against and abs(sh) * m / c[-1] > 0.02:
            continue
        name = ("Bull " if bull else "Bear ") + ("pennant" if pennant else "flag")
        brk = None
        for j in range(p + 1, n):
            if bull and c[j] > up(j):
                brk = j
                break
            if not bull and c[j] < lo(j):
                brk = j
                break
        if brk is None and ((bull and c[-1] < lo(n - 1)) or (not bull and c[-1] > up(n - 1))):
            continue
        if brk is not None and n - 1 - brk > cfg["old"]:
            continue
        end = n - 1 if brk is None else brk
        lines = [_seg(p + 1, up(p + 1), end, up(end)), _seg(p + 1, lo(p + 1), end, lo(end))]
        if bull:
            lvl = up(end) if brk is not None else up(n - 1)
            d = _p(name, s, end, lines, level_up=lvl, target_up=lvl + pole_h, stop_up=lo(n - 1), dir=1)
        else:
            lvl = lo(end) if brk is not None else lo(n - 1)
            d = _p(name, s, end, lines, level_dn=lvl, target_dn=lvl - pole_h, stop_dn=up(n - 1), dir=-1)
        d.update(state="forming" if brk is None else "breakout", age=None if brk is None else n - 1 - brk,
                 vol=None if brk is None else _vol_ratio(X, brk), quality=1.2,
                 extra=f"Pole {pole * 100:.0f}% over {p - s} candles, pause of {m} candles retracing {retr * 100:.0f}% of it.",
                 pts=[(s, l[s] if bull else h[s]), (p, h[p] if bull else l[p])])
        out.append(d)
    return out


def _cup(X, piv, cfg, n):
    h, l, c, out = X["High"], X["Low"], X["Close"], []
    maxlen = cfg["win"]
    best = None
    for i0 in range(max(0, n - maxlen), n - cfg["cup"]):
        if h[i0] < h[max(0, i0 - 10):i0 + 11].max():
            continue                                            # left rim must be a local peak
        rim = h[i0]
        b = i0 + 1 + int(np.argmin(l[i0 + 1:n]))
        r = b + 1 + int(np.argmax(h[b + 1:n])) if b + 1 < n else None
        if r is None or r - i0 < cfg["cup"]:
            continue
        depth = (rim - l[b]) / rim
        if not 0.12 <= depth <= 0.40 or h[r] < rim * 0.90 or h[r] > rim * 1.08:
            continue
        frac = (b - i0) / (r - i0)
        if not 0.25 <= frac <= 0.75:
            continue
        xs = np.arange(i0, r + 1)
        a2, b2, c2 = np.polyfit(xs, c[i0:r + 1], 2)
        fit = np.polyval([a2, b2, c2], xs)
        ss_res, ss_tot = ((c[i0:r + 1] - fit) ** 2).sum(), ((c[i0:r + 1] - c[i0:r + 1].mean()) ** 2).sum()
        if a2 <= 0 or ss_tot == 0 or 1 - ss_res / ss_tot < 0.6:
            continue
        hl = l[r + 1:] if r + 1 < n else np.array([l[r]])
        handle_drop = (h[r] - hl.min()) / (rim - l[b])
        hlen = n - 1 - r
        if handle_drop > 0.5 or hlen > (r - i0) / 3:
            continue
        lvl = max(h[r], rim)
        j2 = _first_close(X, r, lvl, 1)
        if j2 is not None and n - 1 - j2 > cfg["old"]:
            continue
        if j2 is None and c[-1] < l[b] + 0.55 * (rim - l[b]):
            continue
        cand = (r - i0, i0, b, r, rim, depth, lvl, j2, hl.min(), 1 - ss_res / ss_tot)
        if best is None or cand[0] > best[0]:
            best = cand
    if best:
        _, i0, b, r, rim, depth, lvl, j2, hlow, r2 = best
        end = n - 1 if j2 is None else j2
        out.append(_p("Cup and handle", i0, end, [_seg(i0, rim, end, rim)], level_up=lvl, target_up=lvl + depth * rim,
                      stop_up=hlow * 0.99 if hlow < lvl else l[b], state="forming" if j2 is None else "breakout", dir=1,
                      age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2), quality=1.4 + 0.2 * r2,
                      extra=f"Rim {rim:,.2f}, cup low {l[b]:,.2f} ({depth * 100:.0f}% deep), {r - i0} candles long.",
                      pts=[(i0, rim), (b, l[b]), (r, h[r])]))
    return out


def _rounding(X, piv, cfg, n):
    c, out = X["Close"], []
    N = min(n, 90 if cfg["k"] == 4 else 60)
    if N < 30:
        return out
    xs = np.linspace(0, 1, N)
    y = c[n - N:]
    a, b, k0 = np.polyfit(xs, y, 2)
    fit = np.polyval([a, b, k0], xs)
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - fit) ** 2).sum() / ss_tot if ss_tot else 0
    if r2 < 0.75 or a == 0:
        return out
    vx = -b / (2 * a)
    if not 0.3 <= vx <= 0.7:
        return out
    i0 = n - N
    if a > 0:
        rim = float(c[i0:i0 + N // 5].max())
        depth = (rim - y.min()) / rim
        if depth < 0.10 or c[-1] < y.min() + 0.5 * (rim - y.min()):
            return out
        j2 = _first_close(X, i0 + int(vx * N), rim, 1)
        if j2 is not None and n - 1 - j2 > cfg["old"]:
            return out
        end = n - 1 if j2 is None else j2
        out.append(_p("Rounding bottom", i0, end, [_seg(i0, rim, end, rim)], level_up=rim, target_up=rim + (rim - y.min()),
                      stop_up=float(y.min()), state="forming" if j2 is None else "breakout", dir=1,
                      age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2), quality=1.0 + 0.3 * r2,
                      extra=f"Left rim {rim:,.2f}, low {y.min():,.2f} ({depth * 100:.0f}% deep); curve fit {r2:.2f}.",
                      pts=[(i0, rim)]))
    else:
        rim = float(c[i0:i0 + N // 5].min())
        depth = (y.max() - rim) / y.max()
        if depth < 0.10 or c[-1] > y.max() - 0.5 * (y.max() - rim):
            return out
        j2 = _first_close(X, i0 + int(vx * N), rim, -1)
        if j2 is not None and n - 1 - j2 > cfg["old"]:
            return out
        end = n - 1 if j2 is None else j2
        out.append(_p("Rounding top", i0, end, [_seg(i0, rim, end, rim)], level_dn=rim, target_dn=rim - (y.max() - rim),
                      stop_dn=float(y.max()), state="forming" if j2 is None else "breakout", dir=-1,
                      age=None if j2 is None else n - 1 - j2, vol=None if j2 is None else _vol_ratio(X, j2), quality=1.0 + 0.3 * r2,
                      extra=f"Left rim {rim:,.2f}, high {y.max():,.2f}; curve fit {r2:.2f}.", pts=[(i0, rim)]))
    return out


# ------------------------------- PUBLIC API -----------------------------------
def detect(df, tf, max_patterns=MAX_PATTERNS):
    """Chart patterns on COMPLETED candles (df: OHLCV bars, oldest first; tf 'D' or 'W'). Best first. Each is a dict:
    name, bias (+1 bullish, -1 bearish, 0 neutral), state ('forming' or 'breakout'), age (candles since the breakout),
    i0, i1 (candle indexes), lines [([x0, x1], [y0, y1])], level_up / level_dn (breakout levels), target_up / target_dn,
    stop_up / stop_dn, vol (breakout volume vs average), extra (plain text)."""
    if tf not in CFG:
        return []
    cfg = CFG[tf]
    n = len(df)
    if n < 45:
        return []
    X = _arrays(df)
    piv = pivots(X, cfg["k"], start=max(0, n - cfg["win"]))
    found = []
    for fn in (_double_triple, _head_shoulders, _line_patterns, _flag, _cup, _rounding):
        try:
            found += fn(X, piv, cfg, n)
        except Exception:  # noqa: BLE001  (a bad shape must never stop the report)
            continue
    h, l, c = X["High"], X["Low"], X["Close"]
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = float(tr[-14:].mean()) if len(tr) >= 14 else 0.0
    keep = []
    for p in found:
        lv, tg = (p["level_up"], p["target_up"]) if p["target_up"] is not None and p["level_up"] is not None else \
                 (p["level_dn"], p["target_dn"]) if p["target_dn"] is not None and p["level_dn"] is not None else (None, None)
        if lv is not None and atr > 0 and abs(tg - lv) < MIN_HEIGHT_ATR * atr:
            continue
        keep.append(p)
    found = keep
    found.sort(key=lambda p: (0 if p["state"] == "breakout" else 1, -p["quality"], n - 1 - p["i1"]))
    out, names = [], set()
    for p in found:
        fam = "hs" if "head" in p["name"].lower() else "dt" if ("double" in p["name"].lower() or "triple" in p["name"].lower()) else p["name"]
        if p["name"] in names or (fam in ("hs", "dt") and fam in names):
            continue
        names.add(p["name"])
        names.add(fam)
        out.append(p)
        if len(out) >= max_patterns:
            break
    return out


def describe(p, last, unit="candle"):
    """Plain-English text for one pattern: what it is, where price is, levels, target, stop. Prices from the chart."""
    pct = lambda v: f"{(v / last - 1) * 100:+.1f}%"  # noqa: E731
    state = (f"BROKE OUT {'up' if p['dir'] > 0 else 'down'} {p['age']} {unit}(s) ago" if p["state"] == "breakout"
             else "still forming (price inside the pattern)")
    bits = [f"<b>{p['name']}</b> ({'bullish' if p['bias'] > 0 else 'bearish' if p['bias'] < 0 else 'neutral'}), {state}."]
    bits.append(MEANING[p["name"]][1])
    if p["extra"]:
        bits.append(p["extra"])
    up = p["level_up"] is not None and (p["state"] == "forming" or p["dir"] > 0)
    dn = p["level_dn"] is not None and (p["state"] == "forming" or p["dir"] < 0)
    if up:
        t = f" Measured target {p['target_up']:,.2f} ({pct(p['target_up'])})." if p["target_up"] else ""
        s = f" Stop {p['stop_up']:,.2f} ({pct(p['stop_up'])})." if p["stop_up"] else ""
        bits.append(f"<b>Up:</b> a close above {p['level_up']:,.2f} ({pct(p['level_up'])} from the latest price) " +
                    ("is the buy trigger." if p["state"] == "forming" else "was the breakout level.") + t + s)
    if dn:
        t = f" Measured target {p['target_dn']:,.2f} ({pct(p['target_dn'])})." if p["target_dn"] else ""
        s = f" Stop {p['stop_dn']:,.2f} ({pct(p['stop_dn'])})." if p["stop_dn"] else ""
        bits.append(f"<b>Down:</b> a close below {p['level_dn']:,.2f} ({pct(p['level_dn'])} from the latest price) " +
                    ("confirms it." if p["state"] == "forming" else "was the breakdown level.") + t + s)
    if p["vol"] is not None:
        bits.append(f"Breakout volume was {p['vol']:.1f}x its 50-{unit} average" +
                    (" (a strong surge)." if p["vol"] >= BREAKOUT_VOLUME else " (not a surge: be careful, weak volume breakouts fail more often)."))
    return " ".join(bits)


# ------------------------------- STAND-ALONE TEST -----------------------------
def main():
    import candle_view as cv
    import nse_screener as ns
    import stock_lookup as sl

    names = [a.upper() for a in sys.argv[1:]] or ["TCS"]
    prices = sl.download(names)
    for sym, df in prices.items():
        daily, _ = cv.drop_partial(cv.naive(df))
        last = float(daily["Close"].iloc[-1])
        weekly = ns.resample_ohlc(daily, "W")
        done = weekly.iloc[:-1] if cv.is_forming("W", weekly, daily.index[-1]) else weekly
        for tf, bars in (("D", daily), ("W", done)):
            pats = detect(bars, tf)
            print(f"\n{sym} {tf}: " + (", ".join(p["name"] for p in pats) if pats else "no pattern found"))
            for p in pats:
                import re
                print("   " + re.sub("<[^>]+>", "", describe(p, last)))


if __name__ == "__main__":
    main()
