#!/usr/bin/env python3
"""
ETFs, mutual funds and gold: a ranked list and your own holdings

What it does
  1. RANKED LIST. Takes a list of NSE-traded ETFs (broad index, sector, gold and silver, international;
     edit UNIVERSE below or use the watch command) and ranks them inside each group with the same ideas the
     screener uses for shares: is the price above its 50-day and 200-day average, is the 200-day average
     rising, and has it beaten the Nifty over 6 and 12 months. Gold and silver ETFs are in their own group, so
     you can see at a glance whether gold is beating or lagging the Nifty. Every fund also gets the screener's
     daily / weekly / monthly Heikin Ashi chart (side by side) with buy-above, stop and target lines.
  2. YOUR HOLDINGS. ETFs and gold ETFs you hold come from your broker files (python portfolio.py import ...).
     The report shows quantity, average cost, result, share of your invested money, price health, your stop,
     and whether a higher-ranked fund exists in the same group (an idea to research, not advice).
  3. MUTUAL FUNDS. Mutual funds have only one price a day (the NAV) and are not in your broker holdings files,
     so you add them yourself with their scheme code (units 0 = just watch it). Each fund gets returns over
     1, 3, 6 months, 1 year and 3 years (a yearly rate), the gap to the Nifty for equity funds, how far the NAV is
     below its peak and whether it is above its own 200-day average. NAV history comes from the free mfapi.in
     service (AMFI data).

Commands (same folder as the other scripts)
  python funds.py                         rank the ETFs and gold, check your holdings, write the report
  python funds.py report                  the same
  python funds.py watch SYMBOL [GROUP]    add an ETF to the list, e.g.  watch HDFCGOLD "Gold and silver"
  python funds.py unwatch SYMBOL          remove an ETF you added
  python funds.py mf search TEXT          find a mutual fund's scheme code, e.g.  mf search parag flexi
  python funds.py mf add CODE UNITS AVG_NAV    record a holding (UNITS 0 = watch only)
  python funds.py mf remove CODE          remove a mutual fund
  python funds.py mf show                 list your mutual funds (no download of NAVs)
Output: output/funds/funds_report.html (large type), funds_ranking.csv and charts/.

Honest limits
  * The ETF list is a starting point. Tickers Yahoo Finance does not know, or ETFs with less than about a year of
    prices, are skipped with a message. Thinly traded ETFs are flagged: use limit orders, spreads can be wide.
  * Ranking uses past price moves only. It says what has been strong, not what will be. Gold and silver can
    swing a lot and often move opposite to shares: a ranking cannot tell you how much to hold.
  * The Nifty used for comparison is the price index (no dividends), while a mutual fund's NAV includes them, so
    equity funds look slightly better against it than they are. Debt funds are not compared with the Nifty.
  * Direct and regular plans are different schemes with different codes: check the name when you add one.
  * Sovereign gold bonds and physical gold are not covered. Gold ETFs are the proxy for the gold price.
  * Liquid and overnight ETFs (LIQUIDBEES) are cash-like and are left out of the ranking.
  * Mutual fund data and the new report were tested on made-up data only: real downloads have not been run.
"""

import csv
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

import nse_screener as ns
import portfolio as pf

# ----------------------------- SETTINGS (edit me) -----------------------------
UNIVERSE = {
    "Broad index": ["NIFTYBEES", "JUNIORBEES", "MID150BEES", "HDFCSML250"],
    "Sector and theme": ["BANKBEES", "ITBEES", "PSUBNKBEES", "CPSEETF"],
    "Gold and silver": ["GOLDBEES", "AXISGOLD", "HDFCGOLD", "SILVERBEES"],
    "International": ["MON100", "MAFANG"],
}
MIN_LIQUID_CR = 1.0          # median daily traded value below this (Rs crore) is flagged as thin
CHARTS_PER_GROUP = 3         # draw charts for this many best-ranked funds per group (plus every fund you hold)
MF_API = "https://api.mfapi.in/mf"
DATA_DIR = pf.DATA_DIR
EXTRA_FILE = DATA_DIR / "funds_universe.csv"     # ETFs you added with: funds.py watch
MF_FILE = DATA_DIR / "mf_holdings.csv"
OUT_DIR = Path("output") / "funds"
OPEN_REPORT = True
# ------------------------------------------------------------------------------

TREND_RANK = {"Uptrend": 0, "Mixed": 1, "Downtrend": 2}
MF_COLS = ["code", "name", "category", "units", "avg_nav", "added_on"]
OTHER = "Other ETFs"


# ------------------------------- THE LIST -------------------------------------
def universe():
    """{symbol: group}: the built-in list plus the ETFs you added."""
    out = {s: g for g, syms in UNIVERSE.items() for s in syms}
    for r in pf.load_csv(EXTRA_FILE):
        out[r["symbol"].strip().upper()] = r["group"].strip() or OTHER
    return out


def guess_group(symbol, name=""):
    t = f"{symbol} {name}".upper()
    return "Gold and silver" if ("GOLD" in t or "SILVER" in t) else OTHER


def cmd_watch(args):
    if not args:
        sys.exit('Usage: python funds.py watch SYMBOL [GROUP]   e.g.  watch HDFCGOLD "Gold and silver"')
    sym = args[0].upper()
    group = " ".join(args[1:]).strip() or guess_group(sym)
    rows = [r for r in pf.load_csv(EXTRA_FILE) if r["symbol"].upper() != sym] + [{"symbol": sym, "group": group}]
    pf.save_csv(EXTRA_FILE, ["symbol", "group"], rows)
    print(f"{sym} added to the group '{group}'. It is ranked the next time you run the report.")


def cmd_unwatch(args):
    if not args:
        sys.exit("Usage: python funds.py unwatch SYMBOL")
    sym = args[0].upper()
    rows = pf.load_csv(EXTRA_FILE)
    keep = [r for r in rows if r["symbol"].upper() != sym]
    if len(keep) == len(rows):
        print(f"{sym} is not in your added list (the built-in list is edited at the top of funds.py).")
        return
    pf.save_csv(EXTRA_FILE, ["symbol", "group"], keep)
    print(f"{sym} removed from your added list.")


# ------------------------------- ETF NUMBERS ----------------------------------
def _naive(obj):
    if getattr(obj.index, "tz", None) is not None:
        obj = obj.copy()
        obj.index = obj.index.tz_localize(None)
    return obj


def metrics(sym, group, df, bench):
    """Trend, returns, strength against the Nifty, drawdown and liquidity for one fund (daily bars)."""
    import candle_view as cv

    daily, _ = cv.drop_partial(cv.naive(df))
    close = daily["Close"].astype(float)
    n = len(close)
    last = float(close.iloc[-1])

    def ret(days):
        return (last / float(close.iloc[-days - 1]) - 1) * 100 if n > days else None

    def bret(days):
        return float(bench.iloc[-1] / bench.iloc[-days - 1] - 1) * 100 if len(bench) > days else None

    r1, r3, r6, r12 = ret(21), ret(63), ret(126), ret(252)
    b6, b12 = bret(126), bret(252)
    s50, s200 = close.rolling(50).mean(), close.rolling(200).mean()
    rising = bool(n > 221 and s200.iloc[-1] > s200.iloc[-21])
    if last > s200.iloc[-1] and last > s50.iloc[-1] and rising:
        trend = "Uptrend"
    elif last < s200.iloc[-1] and last < s50.iloc[-1]:
        trend = "Downtrend"
    else:
        trend = "Mixed"
    hi52 = float(daily["High"].astype(float).iloc[-252:].max())
    year = close.iloc[-252:]
    value_cr = float((close * daily["Volume"].astype(float)).rolling(50).median().iloc[-1]) / 1e7
    rs = ((r6 - b6) + (r12 - b12)) / 2 if None not in (r6, r12, b6, b12) else None
    return {"symbol": sym, "group": group, "last": last, "day": str(daily.index[-1].date()),
            "r1": r1, "r3": r3, "r6": r6, "r12": r12, "vs6": None if r6 is None or b6 is None else r6 - b6,
            "vs12": None if r12 is None or b12 is None else r12 - b12, "rs": rs, "trend": trend,
            "below_high": (1 - last / hi52) * 100, "vol": float(close.pct_change().iloc[-252:].std() * np.sqrt(252) * 100),
            "maxdd": float((year / year.cummax() - 1).min() * 100), "value_cr": value_cr,
            "thin": value_cr < MIN_LIQUID_CR, "sma200_ok": trend == "Uptrend", "above50": last > s50.iloc[-1],
            "above200": last > s200.iloc[-1]}


def sort_key(m):
    return (TREND_RANK[m["trend"]], -(m["rs"] if m["rs"] is not None else -999))


def rank_all(mets):
    """Sorted list with 'rank' (overall) and 'grank' (inside its group) added."""
    ordered = sorted(mets, key=sort_key)
    seen = {}
    for i, m in enumerate(ordered, 1):
        m["rank"] = i
        seen[m["group"]] = seen.get(m["group"], 0) + 1
        m["grank"] = seen[m["group"]]
    for m in ordered:
        m["gsize"] = seen[m["group"]]
    return ordered


# ------------------------------- MUTUAL FUNDS ---------------------------------
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def mf_fetch(code):
    """-> (meta dict, NAV series oldest first). Raises on a bad code or no network."""
    j = _get_json(f"{MF_API}/{code}")
    rows = j.get("data") or []
    vals = {}
    for r in rows:
        try:
            vals[pd.to_datetime(r["date"], format="%d-%m-%Y")] = float(r["nav"])
        except (KeyError, ValueError, TypeError):
            continue
    if not vals:
        raise ValueError("no NAV data returned for that code")
    return j.get("meta") or {}, pd.Series(vals).sort_index()


def mf_load():
    return pf.load_csv(MF_FILE)


def mf_save(rows):
    pf.save_csv(MF_FILE, MF_COLS, rows)


def cmd_mf(args):
    if not args:
        sys.exit("Usage: python funds.py mf search TEXT | add CODE UNITS AVG_NAV | remove CODE | show")
    sub, rest = args[0].lower(), list(args[1:])
    if sub == "search":
        text = " ".join(rest).strip()
        if len(text) < 3:
            sys.exit("Type at least 3 letters of the fund name, e.g.  mf search parag flexi")
        try:
            found = _get_json(f"{MF_API}/search?q={urllib.parse.quote(text)}")
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Could not search ({e}). Check your internet connection.")
        if not found:
            print("No fund found. Try fewer or different words.")
            return
        for r in found[:25]:
            print(f"  {r.get('schemeCode')}   {r.get('schemeName')}")
        print("\nAdd one:  python funds.py mf add CODE UNITS AVG_NAV     (UNITS 0 = just watch it)\n"
              "Choose carefully between Direct and Regular, and Growth and IDCW (dividend) plans: they are different codes.")
    elif sub == "add":
        if len(rest) < 3:
            sys.exit("Usage: python funds.py mf add CODE UNITS AVG_NAV     (UNITS 0 = just watch it)")
        code, units, avg = rest[0].strip(), pf.num(rest[1]), pf.num(rest[2])
        if not code.isdigit() or units is None or units < 0 or avg is None or avg < 0:
            sys.exit("CODE must be the scheme number; UNITS and AVG_NAV must be numbers (0 is allowed for UNITS).")
        name = category = ""
        try:
            meta, nav = mf_fetch(code)
            name, category = meta.get("scheme_name", ""), meta.get("scheme_category", "")
            print(f"{code}: {name}\n  category {category or '-'}; latest NAV {nav.iloc[-1]:,.4f} on {nav.index[-1]:%d %b %Y}")
        except Exception as e:  # noqa: BLE001
            print(f"Could not check the code online ({e}). Saved anyway; the name fills in when the report runs.")
        rows = [r for r in mf_load() if r["code"] != code] + [
            {"code": code, "name": name, "category": category, "units": units, "avg_nav": avg,
             "added_on": str(dt.date.today())}]
        mf_save(rows)
        print(f"Saved {code}: {units:g} units at average NAV {avg:,.4f}" + (" (watch only)." if units == 0 else "."))
    elif sub == "remove":
        if not rest:
            sys.exit("Usage: python funds.py mf remove CODE")
        rows = mf_load()
        keep = [r for r in rows if r["code"] != rest[0].strip()]
        mf_save(keep)
        print("Removed." if len(keep) < len(rows) else "That code is not in your list.")
    elif sub == "show":
        rows = mf_load()
        if not rows:
            print("No mutual funds yet. Find one: python funds.py mf search TEXT")
            return
        print(pd.DataFrame(rows)[["code", "name", "category", "units", "avg_nav"]].to_string(index=False))
        inv = sum((pf.num(r["units"]) or 0) * (pf.num(r["avg_nav"]) or 0) for r in rows)
        print(f"\n{len(rows)} fund(s), invested about Rs {pf.inr(inv)} at cost.")
    else:
        sys.exit("Usage: python funds.py mf search TEXT | add CODE UNITS AVG_NAV | remove CODE | show")


def _at(series, ts):
    """Last value on or before ts, or None when the series starts later."""
    if ts < series.index[0]:
        return None
    return float(series[:ts].iloc[-1])


def mf_metrics(nav, bench, equity):
    last_date, last = nav.index[-1], float(nav.iloc[-1])
    out = {"last": last, "day": str(last_date.date()), "stale": (dt.date.today() - last_date.date()).days}
    bend = _at(bench, last_date) if len(bench) else None
    for key, months in (("r1m", 1), ("r3m", 3), ("r6m", 6), ("r1y", 12)):
        t = last_date - pd.DateOffset(months=months)
        v0 = _at(nav, t)
        out[key] = None if v0 is None else (last / v0 - 1) * 100
        b0 = _at(bench, t) if len(bench) else None
        out["b" + key[1:]] = None if (b0 is None or bend is None) else (bend / b0 - 1) * 100
    v3 = _at(nav, last_date - pd.DateOffset(years=3))
    out["cagr3"] = None if v3 is None else ((last / v3) ** (1 / 3) - 1) * 100
    out["vs1y"] = (out["r1y"] - out["b1y"]) if equity and out["r1y"] is not None and out["b1y"] is not None else None
    peak = float(nav.iloc[-252:].max())
    out["below_peak"] = (1 - last / peak) * 100
    out["vol"] = float(nav.pct_change().iloc[-252:].std() * np.sqrt(252) * 100) if len(nav) > 30 else None
    if len(nav) >= 200:
        s50, s200 = nav.rolling(50).mean().iloc[-1], nav.rolling(200).mean().iloc[-1]
        out["trend"] = "Uptrend" if last > s200 and last > s50 else "Downtrend" if last < s200 and last < s50 else "Mixed"
    else:
        out["trend"] = "n/a"
    return out


def mf_collect(bench):
    """One dict per saved fund: its record, meta, metrics and (if the download failed) an error."""
    rows, out = mf_load(), []
    if rows:
        print(f"\nDownloading NAVs for {len(rows)} mutual fund(s) ...")
    changed = False
    for r in rows:
        item = {"row": r, "m": None, "error": ""}
        try:
            meta, nav = mf_fetch(r["code"])
            if not r["name"] and meta.get("scheme_name"):
                r["name"], r["category"] = meta["scheme_name"], meta.get("scheme_category", "")
                changed = True
            equity = "equity" in (r["category"] or "").lower()
            item["m"] = mf_metrics(nav, bench, equity)
            item["equity"] = equity
            print(f"  {r['code']}  {(r['name'] or '')[:55]}  1-year {item['m']['r1y'] if item['m']['r1y'] is None else format(item['m']['r1y'], '+.1f') + '%'}")
        except Exception as e:  # noqa: BLE001
            item["error"] = str(e)
            print(f"  {r['code']}: could not get NAVs ({e}).")
        out.append(item)
    if changed:
        mf_save([i["row"] for i in out])
    return out


# ------------------------------- REPORT ---------------------------------------
def f1(x, d=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.{d}f}%"


def c1(x, d=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"<span class='{'pos' if x > 0 else 'neg' if x < 0 else ''}'>{x:+.{d}f}%</span>"


def money(x):
    return "Rs " + pf.inr(x)


def pill(text, cls=None):
    return f"<span class='h {(cls or text).replace(' ', '')}'>{text}</span>"


def holding_notes(h, m, ordered):
    """Plain-English notes for a held fund."""
    notes = []
    if m is None:
        return ["No usable price history, so it could not be ranked."]
    if h["stop"] and m["last"] <= h["stop"]:
        notes.append(f"The latest close {m['last']:,.2f} is AT OR BELOW your stop {h['stop']:,.2f}.")
    if m["trend"] == "Downtrend":
        notes.append("Price is below both its 50-day and 200-day averages: the trend is down.")
    elif m["trend"] == "Mixed":
        notes.append("Mixed trend: price is on opposite sides of its 50-day and 200-day averages, or the 200-day is not rising.")
    else:
        notes.append("Uptrend: above both averages and the 200-day average is rising.")
    notes.append(f"Ranked {m['grank']} of {m['gsize']} in '{m['group']}' (overall {m['rank']} of {len(ordered)}).")
    peers = [x for x in ordered if x["group"] == m["group"] and x["symbol"] != m["symbol"] and not x["thin"] and sort_key(x) < sort_key(m)]
    if peers:
        b = peers[0]
        notes.append(f"{b['symbol']} ranks higher in the same group ({b['trend']}, 12 months vs Nifty {f1(b['vs12'])}). "
                     "An idea to research, not a recommendation: switching costs charges and may cost tax.")
    if m["thin"]:
        notes.append(f"Thinly traded (about Rs {m['value_cr']:.1f} crore a day): use limit orders.")
    return notes


PAGE = """<html><head><meta charset='utf-8'><title>ETFs, funds and gold __DATE__</title>
<style>
body{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5;max-width:1500px}
html{scroll-behavior:smooth}
table{border-collapse:collapse;font-size:19px}
td,th{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}
td.n,th.n{text-align:right}
img.chart{width:100%;height:auto}
.h{padding:3px 12px;border-radius:6px;color:white;font-size:19px;white-space:nowrap}
.Uptrend{background:#1b7f3b} .Mixed{background:#b8860b} .Downtrend{background:#b22222} .na{background:#777}
.Thin{background:#8a6d00} .Held{background:#1f4e9c}
.pos{color:#1b7f3b} .neg{color:#b22222}
.warn{background:#fff3cd;border-left:8px solid #b8860b;padding:8px 18px;margin:12px 0}
.bad{background:#fdecea;border-left:8px solid #b22222;padding:8px 18px;margin:12px 0}
.box{background:#f2f2f2;border-left:8px solid #777;padding:8px 18px;margin:12px 0}
.small{font-size:17px;color:#444}
a.lv{font-size:19px;font-weight:normal}
#top-btn{position:fixed;right:24px;bottom:24px;background:#1f4e9c;color:white;text-decoration:none;
 font-size:22px;font-weight:bold;padding:14px 20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.4)}
__ZOOMCSS__
</style></head><body id='top'>
<h1>ETFs, mutual funds and gold - __DATE__</h1>
<p>Prices are end-of-day (latest close in the data: __LAST__).</p>
<div class='warn'><b>How to read this.</b> Funds are ranked <b>inside their own group</b>: first by trend
(Uptrend = price above its 50-day and 200-day averages and the 200-day average rising; Downtrend = below both; Mixed =
anything else), then by strength against the Nifty over 6 and 12 months. It describes what has been strong, not what will
be. It is screening output, not advice: gold, silver and shares can all fall together or apart, and a ranking cannot say how much
to hold. Returns are before charges and tax.</div>
__BODY__
<a id='top-btn' href='#top'>&uarr; Top</a>__ZOOMHTML__</body></html>"""


def etf_rows_html(items, held_syms):
    head = ["#", "Fund", "Trend", "Latest", "1 month", "3 months", "6 months", "12 months", "vs Nifty 6m",
            "vs Nifty 12m", "Below 52w high", "Yearly swing", "Liquidity"]
    nums = {"#", "Latest", "1 month", "3 months", "6 months", "12 months", "vs Nifty 6m", "vs Nifty 12m",
            "Below 52w high", "Yearly swing"}
    trs = ""
    for m in items:
        tags = (" " + pill("Held", "Held") if m["symbol"] in held_syms else "")
        trs += (f"<tr><td class='n'>{m['grank']}</td><td><a href='#{m['symbol']}'>{m['symbol']}</a>{tags} "
                f"{ns.live_links_html(m['symbol'])}</td><td>{pill(m['trend'])}</td><td class='n'>{m['last']:,.2f}</td>"
                f"<td class='n'>{c1(m['r1'])}</td><td class='n'>{c1(m['r3'])}</td><td class='n'>{c1(m['r6'])}</td>"
                f"<td class='n'>{c1(m['r12'])}</td><td class='n'>{c1(m['vs6'])}</td><td class='n'>{c1(m['vs12'])}</td>"
                f"<td class='n'>-{m['below_high']:.1f}%</td><td class='n'>{m['vol']:.0f}%</td>"
                f"<td>{pill('Thin') + ' ' if m['thin'] else ''}Rs {m['value_cr']:.1f} cr/day</td></tr>")
    return ("<table><tr>" + "".join(f"<th class='{'n' if h in nums else ''}'>{h}</th>" for h in head) + f"</tr>{trs}</table>")


def holdings_etf_html(rows):
    if not rows:
        return ("<p>No ETF or gold ETF holdings found in your broker files. (Import them with "
                "<code>python portfolio.py import FILE</code>.)</p>")
    tot_cost = sum(r["cost"] for r in rows)
    tot_val = sum(r["value"] for r in rows if r["value"])
    tot_pnl = tot_val - sum(r["cost"] for r in rows if r["value"])
    head = ["Fund", "Group", "Qty", "Avg cost", "Latest", "Value Rs", "Result Rs", "Result %", "Of all you invested",
            "Your stop", "Trend", "Rank in group"]
    nums = set(head) - {"Fund", "Group", "Trend", "Rank in group"}
    trs = notes = ""
    for r in rows:
        m = r["m"]
        trs += (f"<tr><td><a href='#{r['symbol']}'>{r['symbol']}</a></td><td>{r['group']}</td><td class='n'>{r['qty']:g}</td>"
                f"<td class='n'>{r['avg_cost']:,.2f}</td><td class='n'>{'-' if not m else format(m['last'], ',.2f')}</td>"
                f"<td class='n'>{pf.inr(r['value'])}</td><td class='n'>{pf.pnl_cell(r['pnl'])}</td>"
                f"<td class='n'>{pf.pnl_cell(r['pnl_pct'], True)}</td><td class='n'>{r['share']:.1f}%</td>"
                f"<td class='n'>{'-' if r['stop'] is None else format(r['stop'], ',.2f')}</td>"
                f"<td>{pill(m['trend']) if m else '-'}</td>"
                f"<td>{'-' if not m else str(m['grank']) + ' of ' + str(m['gsize'])}</td></tr>")
        notes += f"<li><b>{r['symbol']}</b>: " + " ".join(r["notes"]) + "</li>"
    box = (f"<div class='box'><b>Summary:</b> {len(rows)} fund(s). Invested <b>{money(tot_cost)}</b>, worth "
           f"<b>{money(tot_val)}</b>, unrealised result <b>{c1(tot_pnl / tot_cost * 100 if tot_cost else None)}</b> "
           f"({money(tot_pnl)}), before charges and tax.</div>")
    return (box + "<table><tr>" + "".join(f"<th class='{'n' if h in nums else ''}'>{h}</th>" for h in head)
            + f"</tr>{trs}</table><ul>{notes}</ul>")


def mf_html(items):
    if not items:
        return ("<p>No mutual funds added yet. Find a scheme code with <code>python funds.py mf search TEXT</code> and add it with "
                "<code>python funds.py mf add CODE UNITS AVG_NAV</code> (units 0 = just watch it), or use the "
                "page in the browser hub.</p>")
    head = ["Fund", "Category", "Units", "Avg NAV", "NAV", "Value Rs", "Result %", "1 month", "3 months", "6 months",
            "1 year", "3 years (yearly)", "vs Nifty 1y", "Below peak", "Trend", "NAV date"]
    nums = set(head) - {"Fund", "Category", "Trend", "NAV date"}
    trs, errs = "", ""
    inv = val = 0.0
    order = sorted(items, key=lambda i: ((i["row"]["category"] or "~"), -(i["m"]["r1y"] or -999) if i["m"] else 999))
    for it in order:
        r, m = it["row"], it["m"]
        units, avg = pf.num(r["units"]) or 0.0, pf.num(r["avg_nav"]) or 0.0
        if m is None:
            errs += f"<li class='neg'>{r['name'] or r['code']}: could not get NAVs ({it['error']}).</li>"
            continue
        v, c = units * m["last"], units * avg
        if units:
            inv, val = inv + c, val + v
        res = (m["last"] / avg - 1) * 100 if units and avg else None
        stale = f" <span class='neg'>({m['stale']} days old)</span>" if m["stale"] > 5 else ""
        trs += (f"<tr><td><b>{r['name'] or r['code']}</b><br><span class='small'>code {r['code']}"
                f"{'' if units else ', watching only'}</span></td><td>{r['category'] or '-'}</td>"
                f"<td class='n'>{units:g}</td><td class='n'>{avg:,.4f}</td><td class='n'>{m['last']:,.4f}</td>"
                f"<td class='n'>{pf.inr(v) if units else '-'}</td><td class='n'>{c1(res)}</td>"
                f"<td class='n'>{c1(m['r1m'])}</td><td class='n'>{c1(m['r3m'])}</td><td class='n'>{c1(m['r6m'])}</td>"
                f"<td class='n'>{c1(m['r1y'])}</td><td class='n'>{c1(m['cagr3'])}</td><td class='n'>{c1(m['vs1y'])}</td>"
                f"<td class='n'>-{m['below_peak']:.1f}%</td><td>{pill(m['trend'], m['trend'].replace('/', ''))}</td>"
                f"<td>{m['day']}{stale}</td></tr>")
    box = (f"<div class='box'><b>Summary:</b> invested <b>{money(inv)}</b>, worth <b>{money(val)}</b>, unrealised result "
           f"<b>{c1((val / inv - 1) * 100 if inv else None)}</b> ({money(val - inv)}), before tax. Funds are grouped by category "
           f"and sorted by 1-year return inside it. Compare only funds of the same category.</div>") if inv else ""
    return (box + "<table><tr>" + "".join(f"<th class='{'n' if h in nums else ''}'>{h}</th>" for h in head)
            + f"</tr>{trs}</table><ul>{errs}</ul>"
            "<p class='small'>vs Nifty is shown for equity funds only (the Nifty here is the price index without dividends, so the "
            "gap is flattering to the fund). A fund NAV is after its running costs; direct and regular plans differ.</p>")


def make_report(prices, bench, positions, mfs, out_dir=None):
    """Build the ranked list, the holdings view and the report from data already downloaded. Returns the path."""
    import stock_lookup as sl

    out_dir = out_dir or OUT_DIR
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)
    uni = universe()
    held_all = {p["symbol"]: p for p in positions if p["resolved"]}
    held = {s: p for s, p in held_all.items() if p["etf"] or s in uni}
    mets, skipped = [], []
    for sym in list(uni) + [s for s in held if s not in uni]:
        if sym == "LIQUIDBEES":
            continue
        group = uni.get(sym) or guess_group(sym, held[sym]["name"])
        df = prices.get(sym)
        if df is None:
            skipped.append(sym)
            continue
        try:
            mets.append(metrics(sym, group, df, bench))
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: could not be analysed ({e}).")
            skipped.append(sym)
    ordered = rank_all(mets)
    bysym = {m["symbol"]: m for m in ordered}
    day = max((m["day"] for m in ordered), default="-")

    # holdings
    total_cost = sum(p["cost"] for p in positions) or 1.0
    hrows = []
    for sym, p in held.items():
        m = bysym.get(sym)
        last = m["last"] if m else p["broker_ltp"]
        value = p["qty"] * last if last else None
        pnl = value - p["cost"] if value else None
        h = {"symbol": sym, "group": m["group"] if m else guess_group(sym, p["name"]), "qty": p["qty"],
             "avg_cost": p["avg_cost"], "cost": p["cost"], "value": value, "pnl": pnl,
             "pnl_pct": (pnl / p["cost"] * 100) if pnl is not None and p["cost"] else None,
             "share": p["cost"] / total_cost * 100, "stop": p["stop"], "m": m}
        h["notes"] = holding_notes(h, m, ordered)
        hrows.append(h)
    hrows.sort(key=lambda r: -(r["value"] or 0))

    # charts: best few per group, plus everything held
    want, per = set(held), {}
    for m in ordered:
        per[m["group"]] = per.get(m["group"], 0) + 1
        if per[m["group"]] <= CHARTS_PER_GROUP:
            want.add(m["symbol"])
    charted = set()
    print("\nDrawing charts ...")
    for sym in sorted(want):
        df = prices.get(sym)
        if df is None or sym not in bysym:
            continue
        try:
            res = sl.analyze_any(df, bench)
            res["symbol"] = sym
            if sym in held and held[sym]["stop"]:
                res["stop_loss"] = held[sym]["stop"]
            ns.make_chart(sym, df, res, out_dir / "charts" / f"{sym}.png")
            charted.add(sym)
            bysym[sym]["plan"] = res
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: chart skipped ({e}).")

    held_syms = set(held)
    body = "<h2>My holdings</h2><h3>ETFs and gold ETFs</h3>" + holdings_etf_html(hrows)
    body += "<h3>Mutual funds</h3>" + mf_html(mfs)
    body += "<h2>Ranked list: ETFs and gold</h2>"
    groups = list(UNIVERSE) + sorted({m["group"] for m in ordered} - set(UNIVERSE))
    gold = bysym.get("GOLDBEES")
    for g in groups:
        items = [m for m in ordered if m["group"] == g]
        if not items:
            continue
        intro = ""
        if g == "Gold and silver" and gold and gold["vs12"] is not None:
            intro = (f"<p>Gold (GOLDBEES) is {c1(gold['r12'])} over 12 months, which is {c1(gold['vs12'])} against the Nifty "
                     f"({gold['trend']}). Gold ETFs follow the rupee gold price, so the rupee's moves count too.</p>")
        body += f"<h3>{g}</h3>{intro}{etf_rows_html(items, held_syms)}"
    if skipped:
        body += ("<div class='box'>No usable price history (unknown ticker on Yahoo Finance, or under about a year of prices), so "
                 "these were skipped: " + ", ".join(sorted(skipped)) + ". Fix or add tickers with <code>python funds.py watch SYMBOL GROUP</code>.</div>")
    body += "<h2>Charts and notes</h2>"
    for m in ordered:
        if m["symbol"] not in charted:
            continue
        sym = m["symbol"]
        extra = ""
        if sym in held:
            extra = "<ul>" + "".join(f"<li>{t}</li>" for t in next(r["notes"] for r in hrows if r["symbol"] == sym)) + "</ul>"
        plan = m.get("plan") or {}
        price = (ns.price_line_html(plan["last_close"], plan["data_date"],
                                    np.nan if plan.get("buy_above") is None else plan["buy_above"],
                                    np.nan if plan.get("stop_loss") is None else plan["stop_loss"]) if plan else "")
        body += (f"<div id='{sym}'><h2>{sym} &nbsp;{pill(m['trend'])} {pill('Held', 'Held') if sym in held else ''} &nbsp;"
                 f"{ns.live_links_html(sym)}</h2><p>{m['group']}: ranked {m['grank']} of {m['gsize']}. 12 months {c1(m['r12'])}, "
                 f"vs Nifty {c1(m['vs12'])}. Biggest fall in the last year {m['maxdd']:.1f}%.</p><p>{price}</p>{extra}"
                 f"<img class='chart' title='Click to zoom' src='charts/{sym}.png'></div><hr>")
    body += ("<p class='small'>Buy-above, stop and target on the charts use the screener's formulas for shares: they are planning levels "
             "for a fund, not advice. Funds without a chart are in the table above.</p>")
    html = (PAGE.replace("__DATE__", str(dt.date.today())).replace("__LAST__", day).replace("__BODY__", body)
            .replace("__ZOOMCSS__", ns.ZOOM_CSS).replace("__ZOOMHTML__", ns.ZOOM_HTML))
    path = out_dir / "funds_report.html"
    path.write_text(html, encoding="utf-8")
    pd.DataFrame([{k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items() if k != "plan"}
                  for m in ordered]).to_csv(out_dir / "funds_ranking.csv", index=False)
    return path, ordered, hrows


def cmd_report():
    import stock_lookup as sl

    positions, _ = pf.build_positions()
    uni = universe()
    syms = sorted({s for s in uni if s != "LIQUIDBEES"} |
                  {p["symbol"] for p in positions if p["resolved"] and (p["etf"] or p["symbol"] in uni)})
    print(f"Downloading prices for {len(syms)} ETFs ...")
    prices = sl.download(syms)
    bench = _naive(ns.download_benchmark())
    mfs = mf_collect(bench)
    path, ordered, hrows = make_report(prices, bench, positions, mfs)
    print(f"\n{'#':>3} {'Fund':<12} {'Group':<17} {'Trend':<9} {'12m':>8} {'vs Nifty 12m':>13}")
    for m in ordered:
        print(f"{m['rank']:>3} {m['symbol']:<12} {m['group']:<17} {m['trend']:<9} {f1(m['r12']):>8} {f1(m['vs12']):>13}")
    if hrows:
        print("\nYour fund holdings: " + ", ".join(r["symbol"] for r in hrows))
    print(f"\nReport: {path}")
    if OPEN_REPORT:
        webbrowser.open(path.resolve().as_uri())


# ------------------------------- MAIN -----------------------------------------
def run_cmd(args):
    if not args:
        cmd_report()
        return
    cmd, rest = args[0].lower(), list(args[1:])
    if cmd == "report":
        cmd_report()
    elif cmd == "watch":
        cmd_watch(rest)
    elif cmd == "unwatch":
        cmd_unwatch(rest)
    elif cmd == "mf":
        cmd_mf(rest)
    else:
        print(__doc__)


if __name__ == "__main__":
    run_cmd(sys.argv[1:])
