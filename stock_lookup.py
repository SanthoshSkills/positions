#!/usr/bin/env python3
"""
Stock lookup: the screener's analysis for stocks YOU choose (nothing is hidden)

What it does
  1. Opens a small window where you type a symbol or company name. After 3 letters it shows
     matching stocks; press Enter or double-click one to add it. Add as many as you like,
     then press "Analyse".   (No window? It falls back to typing in this console.)
  2. For every stock you chose it produces what nse_screener.py produces:
       status, buy-above price, stop-loss, quantity, support/resistance, the 3-panel chart
       (daily, weekly, monthly), plus the candlestick charts with formation call-outs and
       "what to watch in the next candle" notes.
  3. Unlike the screener, NOTHING is hidden:
       * a stock that fails the screener's price rules is still analysed, and the report says
         which rules it fails and why;
       * a WEAK business is still shown, with a call-out saying why the business looks weak
         (or strong, or why the free data was not enough to tell).
  Output goes to output/lookup_<date_time>/ :
       report.html            (large type, opens in your browser)
       candle_report.html     (candlestick view, linked from report.html)
       lookup_results.csv, charts/, candles/

Run:
    python stock_lookup.py                    opens the picker window
    python stock_lookup.py TCS INFY reliance  skip the window; names without an exact symbol
                                              are searched and you choose in this console
    python stock_lookup.py --console          use the console instead of the window

Keep this file in the same folder as nse_screener.py, candle_view.py and fundamentals.py.
(fundamentals.py must be the updated copy that has check_symbol_detailed.)

Honest limits
  * The suggestion list comes from NSE's list of listed shares (or the Nifty 500 list if that
    download fails) and is saved to nse_symbols_cache.csv for 30 days. You can still type any
    symbol Yahoo Finance knows.
  * Prices are end-of-day. The business check uses Yahoo's free statements, which are
    sometimes late or missing for Indian companies.
  * Buy-above and stop-loss for a stock that fails the price rules are what the screener's
    formulas would give. Treat them as hypothetical.
"""

import datetime as dt
import re
import sys
import time
import urllib.request
import webbrowser
from io import StringIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd

import candle_view as cv
import nse_screener as ns

try:
    import fundamentals as fd
except ImportError:
    fd = None

# ----------------------------- SETTINGS (edit me) -----------------------------
MIN_LETTERS = 3               # start suggesting after this many letters
MAX_SUGGESTIONS = 12
SYMBOL_CACHE = "nse_symbols_cache.csv"
CACHE_DAYS = 30               # reuse the saved symbol list for this many days
GUI_FONT = ("Arial", 16)      # text size in the picker window
OPEN_REPORT = True
# ------------------------------------------------------------------------------

# (url, symbol column, name column)
SOURCES = [
    ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "SYMBOL", "NAME OF COMPANY"),
    (ns.UNIVERSE_URL, "Symbol", "Company Name"),
]


# ------------------------------- SYMBOL LIST ----------------------------------
def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def load_symbols():
    """DataFrame with columns Symbol and Name (used for suggestions)."""
    cache = Path(SYMBOL_CACHE)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_DAYS * 86400:
        return pd.read_csv(cache).fillna("")
    for url, scol, ncol in SOURCES:
        try:
            df = pd.read_csv(StringIO(_fetch(url)))
            df.columns = df.columns.str.strip()
            if "SERIES" in df.columns:
                df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
            table = pd.DataFrame({"Symbol": df[scol].astype(str).str.strip(),
                                  "Name": df[ncol].astype(str).str.strip()})
            table = table.drop_duplicates("Symbol").reset_index(drop=True)
            if len(table) > 100:
                table.to_csv(cache, index=False)
                return table
        except Exception as e:  # noqa: BLE001
            print(f"  could not use {url}: {e}")
    if cache.exists():
        print("Could not refresh the symbol list; using the older saved copy.")
        return pd.read_csv(cache).fillna("")
    if Path(ns.LOCAL_TICKERS).exists():
        syms = pd.read_csv(ns.LOCAL_TICKERS)["Symbol"].astype(str).str.strip()
        return pd.DataFrame({"Symbol": syms, "Name": ""})
    print("Could not get a list of NSE symbols, so there will be no suggestions.\n"
          "You can still type exact symbols (for example TCS).")
    return pd.DataFrame({"Symbol": [], "Name": []})


def find(table, text, limit=MAX_SUGGESTIONS):
    """Matches for what was typed: symbol starts with it, symbol contains it, a word of the name starts with it."""
    t = text.strip().upper()
    if len(t) < MIN_LETTERS or table.empty:
        return table.iloc[0:0]
    sym = table["Symbol"].str.upper()
    name = table["Name"].str.upper()
    starts = table[sym.str.startswith(t)]
    contains = table[sym.str.contains(re.escape(t)) & ~sym.str.startswith(t)]
    words = table[name.str.contains(r"(?:^|[^A-Z0-9])" + re.escape(t), regex=True)
                  & ~sym.str.contains(re.escape(t))]
    return pd.concat([starts, contains, words]).drop_duplicates("Symbol").head(limit)


def resolve_token(table, tok):
    """An exact symbol wins; otherwise the best suggestion; otherwise what was typed."""
    t = tok.strip().upper()
    if not table.empty and t in set(table["Symbol"].str.upper()):
        return t
    hit = find(table, tok, 1)
    return hit.iloc[0]["Symbol"].upper() if len(hit) else t


# ------------------------------- PICKERS --------------------------------------
def pick_symbols_gui(table):
    import tkinter as tk

    names = dict(zip(table["Symbol"].str.upper(), table["Name"]))
    chosen, matches, result = [], [], {"go": False}
    root = tk.Tk()
    root.title("Choose stocks to analyse")
    F, FB = GUI_FONT, (GUI_FONT[0], GUI_FONT[1], "bold")

    tk.Label(root, text=f"Type at least {MIN_LETTERS} letters of a symbol or company name.\n"
                        "Press Enter or double-click a match to add it.",
             font=F, justify="left").pack(anchor="w", padx=12, pady=(12, 4))
    entry = tk.Entry(root, font=F, width=44)
    entry.pack(anchor="w", padx=12)
    hint = tk.Label(root, text="", font=F, fg="#555")
    hint.pack(anchor="w", padx=12)
    sugg = tk.Listbox(root, font=F, height=8, width=64, exportselection=False)
    sugg.pack(anchor="w", padx=12, pady=4)
    tk.Label(root, text="Stocks to analyse:", font=FB).pack(anchor="w", padx=12, pady=(10, 0))
    sel = tk.Listbox(root, font=F, height=6, width=64, exportselection=False)
    sel.pack(anchor="w", padx=12, pady=4)

    def label(sym):
        return f"{sym}  -  {names[sym]}" if names.get(sym) else sym

    def refresh(event=None):
        if event is not None and event.keysym in ("Up", "Down", "Return", "Tab"):
            return
        text = entry.get().split(",")[-1].strip()
        sugg.delete(0, "end")
        matches.clear()
        if len(text) < MIN_LETTERS:
            hint.config(text=f"Keep typing ({MIN_LETTERS} letters needed) ..." if text else "")
            return
        found = find(table, text)
        if found.empty:
            hint.config(text="No match. Press Enter to add what you typed as an exact symbol.")
            return
        hint.config(text=f"{len(found)} match(es):")
        for _, r in found.iterrows():
            matches.append(r["Symbol"].upper())
            sugg.insert("end", label(r["Symbol"].upper()))

    def add(sym):
        sym = sym.strip().upper()
        if sym and sym not in chosen:
            chosen.append(sym)
            sel.insert("end", label(sym))

    def add_from_list(event=None):
        idx = sugg.curselection()
        if idx:
            add(matches[idx[0]])
            entry.delete(0, "end")
            refresh()
            entry.focus_set()

    def add_typed(event=None):
        if sugg.curselection():
            return add_from_list()
        for tok in [t for t in entry.get().split(",") if t.strip()]:
            add(resolve_token(table, tok))
        entry.delete(0, "end")
        refresh()

    def to_list(event=None):
        if matches:
            sugg.focus_set()
            sugg.selection_clear(0, "end")
            sugg.selection_set(0)
            sugg.activate(0)
        return "break"

    def remove():
        for i in reversed(sel.curselection()):
            sel.delete(i)
            del chosen[i]

    def go():
        add_typed()
        if chosen:
            result["go"] = True
            root.destroy()

    entry.bind("<KeyRelease>", refresh)
    entry.bind("<Return>", add_typed)
    entry.bind("<Down>", to_list)
    sugg.bind("<Return>", add_from_list)
    sugg.bind("<Double-Button-1>", add_from_list)
    root.bind("<Escape>", lambda e: root.destroy())

    row = tk.Frame(root)
    row.pack(anchor="w", padx=12, pady=10)
    tk.Button(row, text="Analyse", font=FB, command=go, bg="#1f4e9c", fg="white", padx=14).pack(side="left")
    tk.Button(row, text="Remove selected", font=F, command=remove).pack(side="left", padx=10)
    tk.Button(row, text="Cancel", font=F, command=root.destroy).pack(side="left")
    entry.focus_set()
    root.mainloop()
    return chosen if result["go"] else []


def pick_symbols_console(table, tokens=None):
    if tokens is None:
        print("Type one or more symbols or names, separated by commas "
              f"(at least {MIN_LETTERS} letters to search). Example: TCS, INFY, reliance")
        tokens = [t.strip() for t in input("> ").split(",") if t.strip()]
    symset = set(table["Symbol"].str.upper()) if not table.empty else set()
    chosen = []
    for tok in tokens:
        t = tok.upper()
        if t in symset:
            chosen.append(t)
            continue
        found = find(table, tok)
        if found.empty:
            print(f"'{tok}': no match in the list, using it as the symbol {t}.")
            chosen.append(t)
            continue
        print(f"\nMatches for '{tok}':")
        for n, (_, r) in enumerate(found.iterrows(), 1):
            print(f"  {n:>2}. {r['Symbol']:<14} {r['Name']}")
        ans = input("Number(s) to add, for example 1 or 1,3 (Enter to skip): ").strip()
        for part in [p for p in ans.split(",") if p.strip().isdigit()]:
            k = int(part)
            if 1 <= k <= len(found):
                chosen.append(found.iloc[k - 1]["Symbol"].upper())
    return chosen


def choose_symbols(table, args):
    if args:
        return pick_symbols_console(table, tokens=args)
    if "--console" not in sys.argv:
        try:
            return pick_symbols_gui(table)
        except Exception as e:  # noqa: BLE001  (no tkinter or no display)
            print(f"Could not open the picker window ({e}). Using the console instead.")
    return pick_symbols_console(table)


# ------------------------------- DATA -----------------------------------------
def download(symbols):
    prices = ns.download_prices(symbols)
    missing = [s for s in symbols if s not in prices]
    if missing:
        import yfinance as yf

        for s in missing:  # a one-stock batch can come back in a different shape: retry alone
            try:
                df = yf.download(s + ".NS", period=ns.HISTORY, interval="1d", auto_adjust=True, progress=False)
                if df is not None and isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close"]) if df is not None and not df.empty else None
            except Exception:  # noqa: BLE001
                df = None
            if df is None or df.empty:
                print(f"{s}: no price data found on Yahoo Finance (check the symbol). Skipped.")
            elif len(df) <= 260:
                print(f"{s}: only {len(df)} days of prices (needs more than 260, about a year). Skipped.")
            else:
                prices[s] = df
    return prices


# ------------------------------- ANALYSIS -------------------------------------
def analyze_any(df, bench):
    """Same numbers as nse_screener.analyze, but never rejects: it records which price rules pass."""
    close, high, vol = df["Close"], df["High"], df["Volume"]
    last = float(close.iloc[-1])
    sma50, sma200 = close.rolling(50).mean(), close.rolling(200).mean()
    checks = []

    traded_value = float((close * vol).rolling(50).median().iloc[-1])
    checks.append(("Liquidity", traded_value >= ns.MIN_TRADED_VALUE,
                   f"median traded value Rs {traded_value / 1e7:.1f} crore a day "
                   f"(needs at least {ns.MIN_TRADED_VALUE / 1e7:.0f})"))

    above200, above50 = last > sma200.iloc[-1], last > sma50.iloc[-1]
    rising = sma200.iloc[-1] > sma200.iloc[-21]
    checks.append(("Long-term uptrend", bool(above200 and above50 and rising),
                   f"price {'above' if above200 else 'below'} the 200-day average, "
                   f"{'above' if above50 else 'below'} the 50-day average, "
                   f"200-day average {'rising' if rising else 'not rising'}"))

    hi52 = float(high.iloc[-252:].max())
    below_high = (1 - last / hi52) * 100
    checks.append(("Near the 52-week high", below_high <= ns.MAX_BELOW_52W_HIGH,
                   f"{below_high:.1f}% below the 52-week high (needs within {ns.MAX_BELOW_52W_HIGH:.0f}%)"))

    r6 = last / float(close.iloc[-127]) - 1
    r12 = last / float(close.iloc[-253]) - 1
    b6 = float(bench.iloc[-1] / bench.iloc[-127] - 1)
    b12 = float(bench.iloc[-1] / bench.iloc[-253] - 1)
    checks.append(("Beating the Nifty", bool(r6 > b6 and r12 > b12),
                   f"6 months {r6 * 100:+.0f}% vs Nifty {b6 * 100:+.0f}%; "
                   f"12 months {r12 * 100:+.0f}% vs Nifty {b12 * 100:+.0f}%"))
    rel_strength = ((r6 - b6) + (r12 - b12)) / 2 * 100

    base = df.iloc[-ns.PIVOT_DAYS - 1: -1]
    pivot = float(base["High"].max())
    depth = (pivot - float(base["Low"].min())) / pivot * 100
    vol_ratio = float(vol.iloc[-5:].mean() / vol.iloc[-51:-1].mean())
    if last > pivot * (1 + ns.EXTENDED_PCT / 100):
        status = "Extended - wait for pullback"
    elif last > pivot:
        status = "Breakout" if vol_ratio >= 1.2 else "Breakout, weak volume"
    elif pivot / last - 1 <= 0.03:
        status = "Near breakout price"
    else:
        status = "Building base"

    wdf = ns.resample_ohlc(df, "W").iloc[-260:]
    bands = ns.sr_bands(wdf, last)
    supports = sorted([b for b in bands if b["high"] <= last], key=lambda b: b["high"], reverse=True)
    resists = sorted([b for b in bands if b["low"] >= last], key=lambda b: b["low"])

    if status.startswith("Extended"):
        entry = stop = qty = target = float("nan")
        stop_basis = ""
    else:
        entry = pivot * 1.005
        stop, stop_basis = ns.choose_stop(entry, bands)
        target = ns.target_price(entry, stop)
        risk_amount = ns.CAPITAL * ns.RISK_PER_TRADE_PCT / 100
        by_risk = int(risk_amount // (entry - stop))
        by_cap = int(ns.CAPITAL * ns.MAX_POSITION_PCT / 100 // entry)
        qty = max(min(by_risk, by_cap), 0)

    return {
        "data_date": str(close.index[-1].date()),
        "last_close": round(last, 2),
        "status": status,
        "buy_above": round(entry, 2) if entry == entry else None,
        "stop_loss": round(stop, 2) if stop == stop else None,
        "stop_loss_pct": round((stop / entry - 1) * 100, 1) if stop == stop else None,
        "target_price": round(target, 2) if target == target else None,
        "profit_pct": round((target / entry - 1) * 100, 1) if target == target else None,
        "stop_basis": stop_basis,
        "suggested_qty": int(qty) if qty == qty else None,
        "tight_base": "Yes" if depth <= ns.MAX_BASE_DEPTH else "No",
        "base_depth_pct": round(depth, 1),
        "pct_below_52w_high": round(below_high, 1),
        "return_6m_pct": round(r6 * 100, 1),
        "return_12m_pct": round(r12 * 100, 1),
        "vs_nifty_score": round(rel_strength, 1),
        "volume_vs_avg": round(vol_ratio, 2),
        "avg_daily_value_cr": round(traded_value / 1e7, 1),
        "support_band": ns.band_text(supports[0] if supports else None),
        "resistance_band": ns.band_text(resists[0] if resists else None),
        "passes_screen": all(ok for _, ok, _ in checks),
        "screen_checks": checks,
        "_pivot": pivot,
        "_bands": bands,
    }


def get_fundamentals(symbols):
    """{symbol: detailed dict or None}. Never raises for a single bad stock."""
    out = {}
    if fd is None or not hasattr(fd, "check_symbol_detailed"):
        print("fundamentals.py is missing or is an older copy (no check_symbol_detailed), "
              "so profit and debt were not checked.")
        return {s: None for s in symbols}
    print("\nChecking profit growth and debt ...")
    for i, sym in enumerate(symbols, 1):
        try:
            out[sym] = fd.check_symbol_detailed(sym)
        except Exception:  # noqa: BLE001
            out[sym] = {"verdict": "CHECK", "fails": [], "unknown": ["could not fetch company data"],
                        "notes": [], "sector": None}
        print(f"  quality check {i}/{len(symbols)}: {sym} -> {out[sym]['verdict']}")
        time.sleep(0.3)
    return out


# ------------------------------- REPORT ---------------------------------------
def quality_text(f):
    if f is None:
        return "NOT CHECKED", ""
    return f["verdict"], "; ".join(f["fails"] + f["unknown"] + f["notes"]) or "no data found"


def fund_box(f):
    if f is None:
        return ("<div class='box NOTCHECKED'><b>Business quality: not checked.</b> "
                "fundamentals.py is missing or out of date.</div>")
    v = f["verdict"]
    head = {"STRONG": "Strong business: profits are growing and debt is under control "
                      "(every check that could be run passed).",
            "WEAK": f"Weak business: {len(f['fails'])} check(s) failed.",
            "CHECK": "Unclear: the free data was incomplete, so look this company up yourself "
                     "before deciding."}[v]
    lis = ""
    for t in f["fails"]:
        lis += f"<li class='neg'>&#10008; <b>Failed:</b> {t}</li>"
    for t in f["unknown"]:
        lis += f"<li class='unk'>? <b>Could not check:</b> {t}</li>"
    for t in f["notes"]:
        lis += f"<li class='pos'>&#10004; {t}</li>"
    sector = f"Sector: {f['sector']}. " if f.get("sector") else ""
    rules = (f"Rules used: yearly profit growth at least {fd.MIN_PROFIT_GROWTH_PCT:.0f}%, latest quarter at "
             f"least {fd.MIN_QTR_PROFIT_GROWTH_PCT:.0f}% vs a year ago, sales growth at least "
             f"{fd.MIN_SALES_GROWTH_PCT:.0f}%, debt/equity at most {fd.MAX_DEBT_TO_EQUITY:.1f}, operating "
             f"profit covering interest at least {fd.MIN_INTEREST_COVER:.0f} times. Banks and lenders skip "
             f"the debt rules. Data: Yahoo Finance free statements (can be late or partial).")
    return (f"<div class='box {v}'><b>Business quality: {v}.</b> {head}<ul>{lis}</ul>"
            f"<p class='small'>{sector}{rules}</p></div>")


def screen_box(res):
    ok = res["passes_screen"]
    head = ("Passes all the screener's price rules." if ok else
            "Does NOT pass the screener's price rules, so the screener would not have listed it.")
    lis = "".join(
        f"<li class='{'pos' if p else 'neg'}'>{'&#10004;' if p else '&#10008;'} <b>{lab}:</b> {det}</li>"
        for lab, p, det in res["screen_checks"])
    extra = "" if ok else ("<p class='small'>The buy-above and stop-loss shown for this stock come from the screener's "
                           "formulas. Treat them as hypothetical while the price rules are not met.</p>")
    return f"<div class='box {'GOOD' if ok else 'BAD'}'><b>Screener price rules:</b> {head}<ul>{lis}</ul>{extra}</div>"


def write_lookup_report(rows, names, out_dir):
    """rows: list of (symbol, result dict, fundamentals dict or None)."""
    trs, body = "", ""
    for sym, res, f in rows:
        q, _ = quality_text(f)
        qcls = q.replace(" ", "")
        link = f"<a class='cv' href='candle_report.html#{sym}' target='candle_view'>candlesticks</a>"
        trs += (f"<tr><td><a href='#{sym}'>{sym}</a> &nbsp;{link} &nbsp;{ns.live_links_html(sym)}</td><td>{names.get(sym, '')}</td>"
                f"<td>{'Passes' if res['passes_screen'] else 'Does not pass'}</td>"
                f"<td><span class='q {qcls}'>{q}</span></td><td>{res['status']}</td>"
                f"<td>{res['last_close']:,.2f}</td>"
                f"<td>{'-' if res['buy_above'] is None else format(res['buy_above'], ',.2f')}</td>"
                f"<td>{'-' if res['stop_loss'] is None else format(res['stop_loss'], ',.2f') + ' (loss ' + format(abs(res['stop_loss_pct']), '.1f') + '%)'}</td>"
                f"<td>{'-' if res['target_price'] is None else format(res['target_price'], ',.2f') + ' (+' + format(res['profit_pct'], '.1f') + '%)'}</td></tr>")
        price = ns.price_line_html(res["last_close"], res["data_date"],
                                   np.nan if res["buy_above"] is None else res["buy_above"],
                                   np.nan if res["stop_loss"] is None else res["stop_loss"])
        qty = res["suggested_qty"]
        qty_txt = "" if qty is None else f" &nbsp;|&nbsp; Suggested quantity <b>{qty}</b>"
        body += (f"<div id='{sym}'><h2>{sym} &nbsp;<span class='q {qcls}'>{q}</span> &nbsp;{link} &nbsp;{ns.live_links_html(sym)}</h2>"
                 f"<p><b>{names.get(sym, '')}</b> &nbsp; <small>{res['status']}</small></p>"
                 f"<p>{price}{qty_txt}</p>{fund_box(f)}{screen_box(res)}"
                 f"<img class='chart' title='Click to zoom' src='charts/{sym}.png'></div><hr>")
    head = ["Stock", "Company", "Price rules", "Business quality", "Status", "Latest price",
            "Buy above", "Stop-loss (loss)", "Target (profit)"]
    html = f"""<html><head><meta charset='utf-8'><title>Stock lookup {dt.date.today()}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5}}
html{{scroll-behavior:smooth}}
table{{border-collapse:collapse;font-size:19px}}
td,th{{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}}
img{{width:100%;height:auto}}
.q{{padding:3px 12px;border-radius:6px;color:white;font-size:19px}}
.STRONG{{background:#1b7f3b}} .CHECK{{background:#b8860b}} .WEAK{{background:#b22222}} .NOTCHECKED{{background:#777}}
.box{{padding:6px 18px;margin:10px 0;border-left:8px solid #777;background:#f2f2f2}}
div.box.STRONG{{background:#e6f4ea;border-left-color:#1b7f3b;color:inherit}}
div.box.WEAK{{background:#fdecea;border-left-color:#b22222;color:inherit}}
div.box.CHECK{{background:#fff3cd;border-left-color:#b8860b;color:inherit}}
div.box.GOOD{{background:#e6f4ea;border-left-color:#1b7f3b}}
div.box.BAD{{background:#fdecea;border-left-color:#b22222}}
.pos{{color:#1b7f3b}} .neg{{color:#b22222}} .unk{{color:#8a6d00}}
.small{{font-size:17px;color:#444}}
a.cv,a.lv{{font-size:19px;font-weight:normal}}
#top-btn{{position:fixed;right:24px;bottom:24px;background:#1f4e9c;color:white;text-decoration:none;
 font-size:22px;font-weight:bold;padding:14px 20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.4)}}
{ns.ZOOM_CSS}
</style></head><body id='top'>
<script>window.name='screener_view';</script>
<h1>Stock lookup - {dt.date.today()}</h1>
<p>Same analysis as the screener, for the stocks you chose. <b>Nothing is hidden</b>: stocks that fail the price
rules or look weak as businesses are shown with the reasons. Prices are end-of-day.
<b>Buy above</b> = the highest price of the last 10 weeks plus 0.5%: you buy only if the price trades above it.
<b>Stop-loss</b> = the price at which you sell to cap the loss.
<b>Target</b> = the buy price plus {ns.TARGET_RR:g} times the risk (buy price minus stop-loss): a planning level, not a prediction.
Profit and loss percentages are measured from the buy price, before charges.
<b>Latest price</b> = the most recent closing price in the data. Not advice.
<b>Click any chart to zoom</b> (mouse wheel to zoom, drag to move, double-click to zoom in or out, Esc to close).</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{trs}</table>
{body}<a id='top-btn' href='#top'>&uarr; Top</a>{ns.ZOOM_HTML}</body></html>"""
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------- MAIN -----------------------------------------
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    table = load_symbols()
    symbols = []
    for s in choose_symbols(table, args):
        if s not in symbols:
            symbols.append(s)
    if not symbols:
        sys.exit("No stocks chosen.")
    names = dict(zip(table["Symbol"].str.upper(), table["Name"])) if not table.empty else {}
    print(f"\nAnalysing: {', '.join(symbols)}")

    prices = download(symbols)
    bench = ns.download_benchmark()
    symbols = [s for s in symbols if s in prices]
    if not symbols:
        sys.exit("No usable price data for the chosen stocks.")

    out_dir = Path("output") / f"lookup_{dt.datetime.now():%Y-%m-%d_%H%M}"
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    fund = get_fundamentals(symbols)
    rows, table_rows = [], []
    print("\nAnalysing prices and drawing charts ...")
    for sym in symbols:
        try:
            res = analyze_any(prices[sym], bench)
            res["symbol"] = sym
            ns.make_chart(sym, prices[sym], res, out_dir / "charts" / f"{sym}.png")
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: could not analyse ({e}). Skipped.")
            continue
        q, notes = quality_text(fund.get(sym))
        res["quality"], res["quality_notes"] = q, notes
        rows.append((sym, res, fund.get(sym)))
        rec = {k: v for k, v in res.items() if not k.startswith("_") and k != "screen_checks"}
        rec["screen_failures"] = "; ".join(f"{lab}: {det}" for lab, ok, det in res["screen_checks"] if not ok)
        table_rows.append(rec)
        print(f"  {sym:<12} {res['status']:<30} price rules: "
              f"{'pass' if res['passes_screen'] else 'fail'}   business: {q}")
    if not rows:
        sys.exit("Nothing could be analysed.")

    results = pd.DataFrame(table_rows)
    lead = ["symbol", "data_date", "status", "quality", "quality_notes", "passes_screen", "screen_failures"]
    results = results[lead + [c for c in results.columns if c not in lead]]
    results.to_csv(out_dir / "lookup_results.csv", index=False)

    print("\nFinding candle formations and drawing candlestick charts ...")
    candle = cv.run(results, prices, out_dir)
    report = write_lookup_report(rows, names, out_dir)
    print(f"\nReport: {report}")
    if candle:
        print(f"Candlestick report: {candle}")
    if OPEN_REPORT:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
