#!/usr/bin/env python3
"""
Portfolio: your REAL holdings from ICICI Direct and Shoonya in one view
 
What it does
  * Imports the holdings file from each broker (ICICI Direct .xls/.csv, Shoonya .csv). Re-importing a
    broker REPLACES that broker's snapshot, so nothing is counted twice.
  * Merges both brokers into one position per stock (weighted average cost, "held at" split).
  * Looks each holding up on Yahoo Finance and gives a price-health reading: trend, distance to YOUR
    stop, relative strength vs the Nifty, the latest daily candle formation, business quality (checked
    again every month) and what today's screener rules say about it.
  * Has a place for TRADE HISTORY (buy dates, partial sells). Add it any time and the report starts
    showing buy dates, days held and when shares turn long-term. Nothing needs redoing.
  * Writes output/portfolio/portfolio_report.html (large type) and portfolio_positions.csv.
 
Commands (run from the same folder as the other scripts)
  python portfolio.py import FILE [icici|shoonya]    load a broker holdings file (broker is auto-detected)
  python portfolio.py report                         download prices, analyse, write the report
  python portfolio.py show                           list holdings and data problems (no download)
  python portfolio.py stop SYMBOL PRICE|off [note]   your own stop-loss for a holding
  python portfolio.py cost SYMBOL PRICE|off          override the average cost (e.g. after a demerger)
  python portfolio.py map CODE_OR_ISIN NSESYMBOL     fix a stock that could not be matched, e.g. map ICICI:MIRS50 MASPTOP50
  python portfolio.py buy  SYMBOL QTY PRICE [DATE] [BROKER]    record one real buy in the trade log
  python portfolio.py sell SYMBOL QTY PRICE [DATE] [BROKER]    record one real sell
  python portfolio.py trades FILE [broker]           import a trade book / contract-note CSV (see below)
 
Trade history rules
  * Trades dated ON OR BEFORE your last holdings import are treated as history that explains the file:
    the buys give real dates and prices, and shares the trades cannot explain stay as "bought before
    your trade history", with the broker's average cost.
  * Trades dated AFTER the last import are added on top of the file (so a new buy shows up at once).
  * The trade file needs a date, a symbol, buy/sell, quantity and price column. Common header names are
    recognised; brokerage or charges columns are added to the cost. If your file is not recognised,
    the message lists its headers and we add a mapping for that layout.
 
Honest limits
  * Holdings files carry no dates, so until trades are added there are no buy dates or tax-holding info.
  * ICICI uses its own 6-letter codes. They are matched to NSE symbols through the ISIN (NSE's list).
    A few built-in guesses are used only if that list cannot be downloaded, and they are flagged.
  * Splits, bonuses and demergers are NOT adjusted. Yahoo prices are adjusted but your cost is not, so a
    holding with such an event can show a wrong P&L. Use the cost command to correct it.
  * Price health is a set of simple conditions, not a sell signal. It prepares the ground for exit alerts.
"""
 
import csv
import datetime as dt
import json
import re
import shutil
import sys
import time
import urllib.request
import webbrowser
from io import StringIO
from pathlib import Path
 
import matplotlib
 
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
 
import nse_screener as ns
 
# ----------------------------- SETTINGS (edit me) -----------------------------
DATA_DIR = Path("portfolio_data")        # your holdings, trades, stops, symbol fixes
OUT_DIR = Path("output") / "portfolio"   # report and charts
CHECK_QUALITY = True                     # profit growth and debt check (fundamentals.py)
QUALITY_REFRESH_DAYS = 30                # re-check a business after this many days
DRAW_CHARTS = True                       # daily / weekly / monthly chart per holding
COST_MISMATCH_PCT = 25.0                 # flag when two brokers' average cost differ by more than this
STALE_IMPORT_DAYS = 7                    # warn when a holdings file is older than this
FAR_BELOW_HIGH_PCT = 25.0                # "far below 52-week high" warning
FONT = ns.CHART_FONT_SIZE
OPEN_REPORT = True
# ------------------------------------------------------------------------------
 
CANDLE_SYMS = set()     # holdings that have a page in the candlestick report (filled by cmd_report)
HOLDINGS = DATA_DIR / "holdings.csv"
TRADES = DATA_DIR / "trades.csv"
NOTES = DATA_DIR / "notes.csv"
SYMMAP = DATA_DIR / "symbol_map.csv"
ISINMAP = DATA_DIR / "isin_map.csv"
QCACHE = DATA_DIR / "quality_cache.json"
 
HCOLS = ["broker", "code", "isin", "name", "qty", "avg_cost", "ltp", "cost_note", "realized_pl",
         "imported_on", "source_file"]
TCOLS = ["broker", "code", "kind", "isin", "date", "side", "qty", "price", "charges", "note", "added_on"]
NCOLS = ["symbol", "stop", "cost_override", "note"]
 
ISIN_SOURCES = [
    ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "SYMBOL", "ISIN NUMBER"),
    (ns.UNIVERSE_URL, "Symbol", "ISIN Code"),
]
 
# Used ONLY when the ISIN list is unavailable. Unverified guesses, always flagged in the report.
SEED_ICICI = {
    "EXIIND": "EXIDEIND", "NRBBEA": "NRBBEARING", "BANIND": "BANKINDIA", "DCB": "DCBBANK",
    "IDFBAN": "IDFCFIRSTB", "MIRS50": "MASPTOP50", "NIFBEE": "NIFTYBEES", "PITLAM": "PITTIENG",
    "ITCHOT": "ITCHOTELS", "KPITE": "KPITTECH", "TECEEC": "TECHNOE", "TIMTEC": "TIMETECHNO",
    "ONGC": "ONGC", "BHAINF": "INDUSTOWER", "IDECEL": "IDEA", "ITC": "ITC",
}
 
 
# ------------------------------- SMALL HELPERS --------------------------------
def num(x):
    """'(255.25)' -> -255.25, '+ 0.12' -> 0.12, '55.66 %' -> 55.66, '1,234.5' -> 1234.5, blank -> None."""
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace("%", "").replace(" ", "")
    if not s or s.lower() in ("nan", "none", "-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v
 
 
def inr(x, d=0):
    """Indian digit grouping: 218454 -> 2,18,454."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    whole, _, frac = f"{abs(x):.{d}f}".partition(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if x < 0 else "") + whole + ("." + frac if frac else "")
 
 
def pct(x, d=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.{d}f}%"
 
 
def norm(col):
    return re.sub(r"[^a-z0-9]", "", str(col).lower())
 
 
def pick(cols, *aliases):
    """Actual column name whose normalised form matches one of the aliases (in order)."""
    m = {norm(c): c for c in cols}
    for a in aliases:
        if a in m:
            return m[a]
    return None
 
 
def load_csv(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
 
 
def save_csv(path, cols, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
    except PermissionError:
        sys.exit(f"Could not save {path}. Close it in Excel and run the command again.")
 
 
def parse_date(text):
    t = str(text).strip()
    iso = re.match(r"^\d{4}-\d{2}-\d{2}", t)       # ISO dates are never day-first
    ts = pd.to_datetime(t, dayfirst=not iso, errors="coerce")
    return None if pd.isna(ts) else ts.date()
 
 
def strip_suffix(code):
    return re.sub(r"-(EQ|BE|BZ|SM|ST|N1|IL)$", "", str(code).strip().upper())
 
 
def is_etf(symbol, isin="", name=""):
    s, n = (symbol or "").upper(), (name or "").upper()
    return (isin or "").startswith("INF") or s.endswith("BEES") or "ETF" in s or " ETF" in n
 
 
# ------------------------------- READING FILES --------------------------------
def read_table(path):
    """Read a broker file: real Excel, tab-separated or comma-separated text."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    head = path.read_bytes()[:4]
    if head[:2] == b"PK" or head == b"\xd0\xcf\x11\xe0":
        try:
            df = pd.read_excel(path, dtype=str)
        except ImportError:
            sys.exit("This is a real Excel file. Run: pip install openpyxl xlrd   (or save it as CSV).")
    else:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        first = text.splitlines()[0] if text else ""
        sep = "\t" if first.count("\t") >= first.count(",") and "\t" in first else ","
        df = pd.read_csv(StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.lower().startswith("unnamed")]]
    first_col = df.columns[0]
    return df[df[first_col].astype(str).str.strip() != ""].reset_index(drop=True)
 
 
def detect_broker(cols):
    n = {norm(c) for c in cols}
    if "isincode" in n and "averagecostprice" in n:
        return "ICICI"
    if "instrument" in n and ("avgcost" in n or "avgprice" in n):
        return "SHOONYA"
    return None
 
 
def parse_icici(df):
    c = list(df.columns)
    col = {k: pick(c, *a) for k, a in {
        "code": ("stocksymbol", "symbol"), "name": ("companyname",), "isin": ("isincode", "isin"),
        "qty": ("qty", "quantity"), "avg": ("averagecostprice", "avgcost"),
        "cost": ("valueatcost",), "mv": ("valueatmarketprice",), "real": ("realizedprofitloss",)}.items()}
    rows = []
    for _, r in df.iterrows():
        qty = num(r[col["qty"]])
        if not qty or qty <= 0:
            continue
        avg, cost = num(r[col["avg"]]) if col["avg"] else None, num(r[col["cost"]]) if col["cost"] else None
        note = ""
        if not avg and cost:
            avg, note = cost / qty, "average cost derived from invested value"
        mv = num(r[col["mv"]]) if col["mv"] else None
        rows.append({"code": r[col["code"]].strip().upper(), "isin": r[col["isin"]].strip().upper() if col["isin"] else "",
                     "name": r[col["name"]].strip() if col["name"] else "", "qty": int(qty),
                     "avg_cost": round(avg or 0, 4), "ltp": round(mv / qty, 2) if mv else "",
                     "cost_note": note, "realized_pl": num(r[col["real"]]) if col["real"] else ""})
    return rows
 
 
def parse_shoonya(df):
    c = list(df.columns)
    col = {k: pick(c, *a) for k, a in {
        "exch": ("exchange",), "code": ("instrument",), "qty": ("qty",), "avg": ("avgcost", "avgprice"),
        "inv": ("invval", "invvalue", "investedvalue"), "ltp": ("ltp",), "real": ("rpnl",),
        "t1": ("t1qty",)}.items()}
    rows, skipped = [], []
    for _, r in df.iterrows():
        code = strip_suffix(r[col["code"]])
        if col["exch"] and str(r[col["exch"]]).strip().upper() not in ("NSE", ""):
            skipped.append(f"{code} ({r[col['exch']]})")
            continue
        qty = num(r[col["qty"]])
        if not qty or qty <= 0:
            continue
        avg, inv = num(r[col["avg"]]) if col["avg"] else None, num(r[col["inv"]]) if col["inv"] else None
        notes = []
        if not avg and inv:
            avg = inv / qty
            notes.append("broker average cost is 0; derived from invested value")
        t1 = num(r[col["t1"]]) if col["t1"] else 0
        if t1:
            notes.append(f"{int(t1)} shares in T1 settlement are not included")
        rows.append({"code": code, "isin": "", "name": "", "qty": int(qty), "avg_cost": round(avg or 0, 4),
                     "ltp": num(r[col["ltp"]]) if col["ltp"] else "", "cost_note": "; ".join(notes),
                     "realized_pl": num(r[col["real"]]) if col["real"] else ""})
    return rows, skipped
 
 
# ------------------------------- SYMBOL MATCHING ------------------------------
def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")
 
 
def isin_tables(download=False):
    """({ISIN: NSE symbol}, set of NSE symbols). Downloads NSE's list at most once a month."""
    fresh = ISINMAP.exists() and (time.time() - ISINMAP.stat().st_mtime) < 30 * 86400
    if download and not fresh:
        frames = []
        for url, scol, icol in ISIN_SOURCES:
            try:
                df = pd.read_csv(StringIO(_fetch(url)), dtype=str)
                df.columns = df.columns.str.strip()
                frames.append(pd.DataFrame({"ISIN": df[icol].str.strip().str.upper(),
                                            "Symbol": df[scol].str.strip().str.upper()}))
            except Exception as e:  # noqa: BLE001
                print(f"  could not use {url}: {e}")
        if frames:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            pd.concat(frames).dropna().drop_duplicates("ISIN").to_csv(ISINMAP, index=False)
    if ISINMAP.exists():
        df = pd.read_csv(ISINMAP, dtype=str).fillna("")
        return dict(zip(df["ISIN"], df["Symbol"])), set(df["Symbol"])
    return {}, set()
 
 
def load_usermap():
    return {r["key"].strip().upper(): r["symbol"].strip().upper() for r in load_csv(SYMMAP)}
 
 
def resolve(broker, code, isin, kind, usermap, isinmap, nse_syms):
    """-> (NSE symbol or '', how it was matched)."""
    code = (code or "").strip().upper()
    if kind == "nse":
        return code, "typed"
    for key in (f"{broker}:{code}", (isin or "").upper()):
        if key and key in usermap:
            return usermap[key], "your map"
    if isin and isin.upper() in isinmap:
        return isinmap[isin.upper()], "ISIN"
    if broker == "SHOONYA":
        return code, "broker symbol"
    if broker == "ICICI" and code in SEED_ICICI:
        return SEED_ICICI[code], "guess"
    if code in nse_syms:
        return code, "same code"
    return "", "unresolved"
 
 
# ------------------------------- NOTES (stops, cost fixes) --------------------
def load_notes():
    out = {}
    for r in load_csv(NOTES):
        out[r["symbol"].upper()] = {"stop": num(r.get("stop")), "cost": num(r.get("cost_override")),
                                    "note": r.get("note", "")}
    return out
 
 
def save_notes(notes):
    rows = [{"symbol": s, "stop": "" if v["stop"] is None else v["stop"],
             "cost_override": "" if v["cost"] is None else v["cost"], "note": v["note"]}
            for s, v in sorted(notes.items()) if v["stop"] is not None or v["cost"] is not None or v["note"]]
    save_csv(NOTES, NCOLS, rows)
 
 
# ------------------------------- IMPORT ---------------------------------------
def cmd_import(args):
    if not args:
        sys.exit("Usage: python portfolio.py import FILE [icici|shoonya]")
    path = Path(args[0].strip('"'))
    df = read_table(path)
    broker = args[1].upper() if len(args) > 1 else detect_broker(df.columns)
    if broker not in ("ICICI", "SHOONYA"):
        sys.exit("Could not tell which broker this file is from. Its headers are:\n  "
                 + ", ".join(df.columns) + "\nRun again and add icici or shoonya at the end.")
    skipped = []
    if broker == "ICICI":
        rows = parse_icici(df)
    else:
        rows, skipped = parse_shoonya(df)
    if not rows:
        sys.exit("No holdings found in that file.")
    today = str(dt.date.today())
    for r in rows:
        r.update(broker=broker, imported_on=today, source_file=path.name)
    kept = [h for h in load_csv(HOLDINGS) if h["broker"] != broker]
    save_csv(HOLDINGS, HCOLS, kept + rows)
    imp = DATA_DIR / "imports"
    imp.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, imp / f"{broker}_{dt.datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
 
    print(f"Imported {len(rows)} holdings from {broker} ({path.name}). This replaced the earlier {broker} snapshot.")
    print("Looking up NSE symbols by ISIN ...")
    isin_tables(download=True)
    if skipped:
        print("Skipped (not NSE, not supported yet): " + ", ".join(skipped))
    positions, problems = build_positions()
    for p in problems:
        print("  ! " + p)
    print("\nNext: python portfolio.py show     (check the list)\n"
          "      python portfolio.py report   (prices, health, report)")
 
 
# ------------------------------- TRADES ---------------------------------------
def norm_side(s):
    s = str(s).strip().upper()
    if s.startswith("B") or s in ("PURCHASE", "P"):
        return "BUY"
    if s.startswith("S"):
        return "SELL"
    return None
 
 
def add_trades(new):
    old = load_csv(TRADES)
    key = lambda t: (t["broker"], t["code"], str(t["date"]), t["side"], str(int(float(t["qty"]))),
                     f"{float(t['price']):.4f}")
    have = {key(t) for t in old}
    added = 0
    for t in new:
        if key(t) in have:
            continue
        have.add(key(t))
        old.append(t)
        added += 1
    save_csv(TRADES, TCOLS, old)
    return added
 
 
def cmd_trades(args):
    if not args:
        sys.exit("Usage: python portfolio.py trades FILE [icici|shoonya]")
    df = read_table(Path(args[0].strip('"')))
    broker = args[1].upper() if len(args) > 1 else detect_broker(df.columns)
    if broker not in ("ICICI", "SHOONYA"):
        sys.exit("Say which broker this trade file is from, so its stock codes are read correctly:\n"
                 "  python portfolio.py trades FILE icici     (or shoonya)")
    c = list(df.columns)
    dcol = pick(c, "tradedate", "date", "transactiondate", "orderdate", "executiondate", "tradetime")
    scol = pick(c, "symbol", "stocksymbol", "scrip", "scripname", "tradingsymbol", "instrument", "stock", "securityname")
    bcol = pick(c, "buysell", "side", "action", "type", "transactiontype", "tradetype", "buyorsell")
    qcol = pick(c, "qty", "quantity", "tradedqty", "tradequantity")
    pcol = pick(c, "price", "rate", "tradeprice", "tradedprice", "avgprice", "executionprice", "netrate")
    chcol = pick(c, "charges", "brokerage", "totalcharges", "totalbrokerage", "fees")
    icol = pick(c, "isincode", "isin")
    ncol = pick(c, "companyname", "stockname")
    miss = [n for n, v in (("date", dcol), ("symbol", scol), ("buy/sell", bcol), ("quantity", qcol), ("price", pcol)) if not v]
    if miss:
        sys.exit("Could not find these columns: " + ", ".join(miss) + ".\nThe file's headers are:\n  "
                 + ", ".join(c) + "\nSend me a few rows of this layout and I will add a mapping for it.")
    rows, bad = [], 0
    for _, r in df.iterrows():
        side, qty, price, day = norm_side(r[bcol]), num(r[qcol]), num(r[pcol]), parse_date(r[dcol])
        if not (side and qty and price and day):
            bad += 1
            continue
        code = strip_suffix(r[scol]) if broker == "SHOONYA" else str(r[scol]).strip().upper()
        rows.append({"broker": broker, "code": code, "kind": "broker", "isin": r[icol].strip().upper() if icol else "",
                     "date": str(day), "side": side, "qty": int(abs(qty)), "price": price,
                     "charges": (num(r[chcol]) or 0) if chcol else 0, "note": r[ncol] if ncol else "",
                     "added_on": str(dt.date.today())})
    added = add_trades(rows)
    print(f"Read {len(rows)} trades ({bad} rows skipped as unreadable); {added} new, {len(rows) - added} already in the log.")
    for p in build_positions()[1]:
        print("  ! " + p)
 
 
def cmd_trade_one(side, args):
    if len(args) < 3:
        sys.exit(f"Usage: python portfolio.py {side.lower()} SYMBOL QTY PRICE [YYYY-MM-DD] [BROKER]")
    sym, qty, price = args[0].upper(), num(args[1]), num(args[2])
    if not qty or not price:
        sys.exit("Quantity and price must be numbers.")
    day, broker = dt.date.today(), None
    for extra in args[3:]:
        d = parse_date(extra) if re.search(r"\d", extra) else None
        if d:
            day = d
        else:
            broker = extra.upper()
    if broker is None:
        held = sorted({h["broker"] for h in load_csv(HOLDINGS) if h["code"].upper() == sym
                       or SEED_ICICI.get(h["code"].upper()) == sym})
        broker = held[0] if len(held) == 1 else "MANUAL"
        if len(held) > 1:
            print(f"{sym} is held at {' and '.join(held)}; logged as MANUAL. Add the broker name to choose one.")
    add_trades([{"broker": broker, "code": sym, "kind": "nse", "isin": "", "date": str(day), "side": side,
                 "qty": int(qty), "price": price, "charges": 0, "note": "", "added_on": str(dt.date.today())}])
    print(f"Logged {side} {int(qty)} {sym} at {price:,.2f} on {day} ({broker}).")
    for p in build_positions()[1]:
        print("  ! " + p)
 
 
# ------------------------------- LOTS AND POSITIONS ---------------------------
def apply_trades(trades, lots):
    """Apply trades to a FIFO list of lots (oldest first). Sells beyond the lots are ignored."""
    for t in sorted(trades, key=lambda t: (t["date"], t["side"] != "BUY")):
        if t["side"] == "BUY":
            lots.append({"qty": t["qty"], "price": t["eff"], "date": t["date"]})
            continue
        need = t["qty"]
        for lot in lots:
            take = min(lot["qty"], need)
            lot["qty"] -= take
            need -= take
            if need <= 0:
                break
    return [l for l in lots if l["qty"] > 1e-9]
 
 
def make_lots(snap_qty, snap_cost, trades, snap_date):
    """Lots for one stock at one broker, and notes about how well trades and file agree."""
    notes = []
    if snap_date is None:                                   # no holdings file for this broker
        return apply_trades(trades, []), notes
    before = [t for t in trades if t["date"] <= snap_date]
    after = [t for t in trades if t["date"] > snap_date]
    lots_b = apply_trades(before, [])
    remaining = sum(l["qty"] for l in lots_b)
    opening = snap_qty - remaining
    if opening < -1e-9:
        notes.append(f"trade log shows {remaining:g} shares but the broker file has {snap_qty:g}: "
                     "trade history ignored for this stock (a sell, split or bonus may be missing)")
        lots_b, opening = [], snap_qty
    cost_b = sum(l["qty"] * l["price"] for l in lots_b)
    lots = []
    if opening > 1e-9:
        lots.append({"qty": opening, "price": max(snap_cost - cost_b, 0) / opening, "date": None})
    elif lots_b and snap_cost > 0 and abs(cost_b - snap_cost) / snap_cost > 0.02:
        notes.append(f"cost from your trades ({inr(cost_b)}) differs from the broker's ({inr(snap_cost)}); "
                     "trade prices are used")
    return apply_trades(after, lots + lots_b), notes
 
 
def build_positions():
    """-> (list of position dicts, list of problem messages). No network needed."""
    hold, trades_raw, notes = load_csv(HOLDINGS), load_csv(TRADES), load_notes()
    usermap = load_usermap()
    isinmap, nse_syms = isin_tables(download=False)
    problems, legs, snap_date = [], {}, {}
    for h in hold:
        sym, how = resolve(h["broker"], h["code"], h["isin"], "broker", usermap, isinmap, nse_syms)
        b = h["broker"]
        snap_date[b] = max(snap_date.get(b, dt.date.min), dt.date.fromisoformat(h["imported_on"]))
        key = (b, sym or "?" + h["code"])
        qty, avg = float(h["qty"]), num(h["avg_cost"]) or 0.0
        leg = legs.setdefault(key, {"broker": b, "symbol": sym, "code": h["code"], "name": h["name"], "isin": h["isin"],
                                    "how": how, "snap_qty": 0.0, "snap_cost": 0.0, "ltp": num(h["ltp"]),
                                    "notes": [], "trades": [], "imported_on": h["imported_on"]})
        leg["snap_qty"] += qty
        leg["snap_cost"] += qty * avg
        if h["cost_note"]:
            leg["notes"].append(h["cost_note"])
        if not leg["name"] and h["name"]:
            leg["name"] = h["name"]
    for t in trades_raw:
        sym, how = resolve(t["broker"], t["code"], t["isin"], t["kind"], usermap, isinmap, nse_syms)
        key = (t["broker"], sym or "?" + t["code"])
        leg = legs.setdefault(key, {"broker": t["broker"], "symbol": sym, "code": t["code"], "name": "", "isin": t["isin"],
                                    "how": how, "snap_qty": 0.0, "snap_cost": 0.0, "ltp": None, "notes": [],
                                    "trades": [], "imported_on": ""})
        qty, price, chg = float(t["qty"]), float(t["price"]), num(t["charges"]) or 0.0
        eff = price + (chg / qty if t["side"] == "BUY" and qty else 0.0)
        leg["trades"].append({"date": dt.date.fromisoformat(t["date"]), "side": t["side"], "qty": qty, "eff": eff})
 
    by_symbol = {}
    for (b, _), leg in legs.items():
        lots, lnotes = make_lots(leg["snap_qty"], leg["snap_cost"], leg["trades"], snap_date.get(b))
        leg["notes"] += lnotes
        for l in lots:
            l["broker"] = b
        leg["lots"] = lots
        by_symbol.setdefault(leg["symbol"] or "?" + leg["code"], []).append(leg)
 
    today = dt.date.today()
    positions = []
    for sym, group in by_symbol.items():
        lots = [l for g in group for l in g["lots"]]
        qty = sum(l["qty"] for l in lots)
        if qty <= 1e-9:
            continue
        cost = sum(l["qty"] * l["price"] for l in lots)
        name = next((g["name"] for g in group if g["name"]), "")
        isin = next((g["isin"] for g in group if g["isin"]), "")
        flags = [f"{g['broker']}: {n}" for g in group for n in g["notes"]]
        resolved = not sym.startswith("?")
        shown = sym.lstrip("?")
        if not resolved:
            flags.append(f"could not match '{shown}' to an NSE symbol. Fix with: python portfolio.py map ICICI:{shown} NSESYMBOL")
        elif any(g["how"] == "guess" for g in group):
            flags.append("NSE symbol is a built-in guess (the ISIN list was not available); check the chart is the right company")
        # cost disagreement between brokers
        per = {}
        for g in group:
            q = sum(l["qty"] for l in g["lots"])
            if q > 0:
                per[g["broker"]] = sum(l["qty"] * l["price"] for l in g["lots"]) / q
        if len(per) > 1 and min(per.values()) > 0 and (max(per.values()) / min(per.values()) - 1) * 100 > COST_MISMATCH_PCT:
            flags.append("average cost differs a lot between brokers (" + ", ".join(f"{b} {v:,.2f}" for b, v in per.items())
                         + "). A demerger or bonus may be the reason; check and use the cost command")
        nt = notes.get(sym, {"stop": None, "cost": None, "note": ""})
        avg = cost / qty
        if nt["cost"]:
            avg, cost = nt["cost"], nt["cost"] * qty
            flags.append(f"average cost set by you to {nt['cost']:,.2f}")
        dated = [l for l in lots if l["date"]]
        lt_cut = lambda d: pd.Timestamp(d) + pd.DateOffset(years=1) + pd.Timedelta(days=1)
        lt_qty = sum(l["qty"] for l in dated if pd.Timestamp(today) >= lt_cut(l["date"]))
        nxt = [lt_cut(l["date"]).date() for l in dated if pd.Timestamp(today) < lt_cut(l["date"])]
        ltps = [g["ltp"] for g in group if g["ltp"]]
        positions.append({
            "symbol": shown, "resolved": resolved, "name": name, "isin": isin, "qty": qty, "cost": cost, "avg_cost": avg,
            "etf": is_etf(shown, isin, name), "brokers": {g["broker"]: sum(l["qty"] for l in g["lots"]) for g in group
                                                          if sum(l["qty"] for l in g["lots"]) > 0},
            "broker_ltp": ltps[0] if ltps else None, "flags": flags, "stop": nt["stop"], "note": nt["note"],
            "lots": lots, "dated_qty": sum(l["qty"] for l in dated), "first_buy": min((l["date"] for l in dated), default=None),
            "lt_qty": lt_qty, "next_lt": min(nxt) if nxt else None,
            "imported_on": max((g["imported_on"] for g in group), default=""),
        })
    positions.sort(key=lambda p: -p["cost"])
    for b, d in snap_date.items():
        if (today - d).days > STALE_IMPORT_DAYS:
            problems.append(f"{b} holdings file was imported {(today - d).days} days ago. Import a fresh one for current quantities.")
    for p in positions:
        for f in p["flags"]:
            if "could not match" in f:
                problems.append(f"{p['symbol']}: {f}")
    return positions, problems
 
 
# ------------------------------- SHOW / STOP / COST / MAP ---------------------
def cmd_show():
    positions, problems = build_positions()
    if not positions:
        print("No holdings yet. Run: python portfolio.py import FILE")
        return
    rows = []
    for p in positions:
        rows.append({"stock": p["symbol"], "held at": ", ".join(f"{b} {q:g}" for b, q in p["brokers"].items()),
                     "qty": f"{p['qty']:g}", "avg cost": f"{p['avg_cost']:,.2f}", "invested": inr(p["cost"]),
                     "broker price": "" if p["broker_ltp"] is None else f"{p['broker_ltp']:,.2f}",
                     "stop": "" if p["stop"] is None else f"{p['stop']:,.2f}", "flags": len(p["flags"])})
    print(pd.DataFrame(rows).to_string(index=False))
    total = sum(p["cost"] for p in positions)
    print(f"\n{len(positions)} stocks, total invested Rs {inr(total)}.")
    guessed = [p["symbol"] for p in positions if any("built-in guess" in f for f in p["flags"])]
    for p in positions:
        for f in p["flags"]:
            if "built-in guess" not in f:
                print(f"  ! {p['symbol']}: {f}")
    if guessed:
        print(f"  ! {len(guessed)} symbols were matched from a built-in list because NSE's ISIN list could not be "
              "downloaded (the report run tries again). Check the charts are the right companies.")
    for m in problems:
        if "could not match" not in m:
            print("  ! " + m)
 
 
def cmd_stop(args):
    if len(args) < 2:
        sys.exit("Usage: python portfolio.py stop SYMBOL PRICE|off [note]")
    sym = args[0].upper()
    notes = load_notes()
    nt = notes.setdefault(sym, {"stop": None, "cost": None, "note": ""})
    if args[1].lower() == "off":
        nt["stop"] = None
        print(f"{sym}: stop removed.")
    else:
        v = num(args[1])
        if not v:
            sys.exit("The stop must be a number, or 'off'.")
        nt["stop"] = v
        print(f"{sym}: stop set to {v:,.2f}.")
    if len(args) > 2:
        nt["note"] = " ".join(args[2:])
    save_notes(notes)
 
 
def cmd_cost(args):
    if len(args) < 2:
        sys.exit("Usage: python portfolio.py cost SYMBOL PRICE|off")
    sym = args[0].upper()
    notes = load_notes()
    nt = notes.setdefault(sym, {"stop": None, "cost": None, "note": ""})
    nt["cost"] = None if args[1].lower() == "off" else num(args[1])
    save_notes(notes)
    print(f"{sym}: average cost " + ("override removed." if nt["cost"] is None else f"set to {nt['cost']:,.2f}."))
 
 
def cmd_map(args):
    if len(args) < 2:
        sys.exit("Usage: python portfolio.py map ICICI:CODE NSESYMBOL   (or an ISIN instead of ICICI:CODE)")
    key = args[0].upper()
    if ":" not in key and not re.fullmatch(r"IN[A-Z0-9]{10}", key):
        key = "ICICI:" + key
    rows = [r for r in load_csv(SYMMAP) if r["key"].upper() != key] + [{"key": key, "symbol": args[1].upper()}]
    save_csv(SYMMAP, ["key", "symbol"], rows)
    print(f"{key} -> {args[1].upper()} saved.")
 
 
# ------------------------------- PRICE ANALYSIS -------------------------------
def fetch_prices(symbols):
    import stock_lookup as sl
    return sl.download(symbols)
 
 
def fetch_bench():
    return ns.download_benchmark()
 
 
def analyse_price(df, bench, stop):
    """Price-health reading from daily prices. Returns a dict (warnings carry points; see health())."""
    import candle_view as cv
 
    daily, _ = cv.drop_partial(cv.naive(df))
    close = daily["Close"]
    last = float(close.iloc[-1])
    out = {"last": last, "day": str(daily.index[-1].date()), "warn": [], "good": [], "score": 0,
           "candle": [], "stop_dist": None}
 
    def warn(points, text):
        out["warn"].append(text)
        out["score"] += points
 
    s50 = close.rolling(50).mean()
    s200 = close.rolling(200).mean()
    if len(close) >= 200:
        if last < s200.iloc[-1]:
            warn(2, f"Price {last:,.2f} is below the 200-day average {s200.iloc[-1]:,.2f}: the long-term trend is broken.")
        else:
            out["good"].append("Above the 200-day average (long-term trend intact).")
        if len(close) >= 221 and s200.iloc[-1] < s200.iloc[-21]:
            warn(1, "The 200-day average itself is falling.")
    if len(close) >= 50:
        if last < s50.iloc[-1]:
            warn(1, f"Price is below the 50-day average {s50.iloc[-1]:,.2f}.")
        else:
            out["good"].append("Above the 50-day average.")
    if len(close) > 127 and len(bench) > 127:
        r6, b6 = last / float(close.iloc[-127]) - 1, float(bench.iloc[-1] / bench.iloc[-127] - 1)
        if r6 < b6:
            warn(1, f"Lagging the Nifty over 6 months ({r6 * 100:+.0f}% vs {b6 * 100:+.0f}%).")
        else:
            out["good"].append(f"Beating the Nifty over 6 months ({r6 * 100:+.0f}% vs {b6 * 100:+.0f}%).")
    hi = float(daily["High"].iloc[-252:].max())
    out["below_high"] = (1 - last / hi) * 100
    if out["below_high"] > FAR_BELOW_HIGH_PCT:
        warn(1, f"{out['below_high']:.0f}% below its 52-week high of {hi:,.2f}.")
    if stop:
        if last <= stop:
            warn(3, f"Latest close {last:,.2f} is AT OR BELOW your stop {stop:,.2f}.")
        else:
            out["stop_dist"] = (last / stop - 1) * 100
    if len(daily) >= 30:
        n = len(daily)
        latest = [p for p in cv.detect(daily) if p["i"] == n - 1]
        out["candle"] = [f"{cv.mark(p)} {p['name']}" for p in latest]
        bear = [p["name"] for p in latest if p["bias"] < 0]
        bull = [p["name"] for p in latest if p["bias"] > 0]
        if bear:
            warn(1, f"Latest daily candle shows a bearish formation ({', '.join(bear)}). One formation is a weak "
                    f"signal: look for a close below its low next.")
        if bull:
            out["good"].append(f"Latest daily candle shows a bullish formation ({', '.join(bull)}).")
    out["health"] = "Trend intact" if out["score"] == 0 else "Watch" if out["score"] <= 2 else "Weak"
    return out
 
 
def quality_for(symbols):
    """{symbol: dict(verdict, fails, unknown, notes, changed_from)} - cached, refreshed monthly."""
    try:
        import fundamentals as fd
    except ImportError:
        return {}
    cache = json.loads(QCACHE.read_text(encoding="utf-8")) if QCACHE.exists() else {}
    today = dt.date.today()
    if symbols:
        print("\nBusiness quality (cached for a month) ...")
    for i, sym in enumerate(symbols, 1):
        c = cache.get(sym)
        if c and (today - dt.date.fromisoformat(c["date"])).days < QUALITY_REFRESH_DAYS:
            continue
        try:
            r = fd.check_symbol_detailed(sym)
            new = {"date": str(today), "verdict": r["verdict"], "fails": r["fails"], "unknown": r["unknown"],
                   "notes": r["notes"], "sector": r.get("sector")}
        except Exception:  # noqa: BLE001
            new = {"date": str(today), "verdict": "CHECK", "fails": [], "unknown": ["could not fetch company data"],
                   "notes": [], "sector": None}
        if c and c["verdict"] != new["verdict"]:
            new["changed_from"] = c["verdict"]
        cache[sym] = new
        print(f"  quality check {i}/{len(symbols)}: {sym} -> {new['verdict']}")
        time.sleep(0.3)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QCACHE.write_text(json.dumps(cache), encoding="utf-8")
    return {s: cache[s] for s in symbols if s in cache}
 
 
# ------------------------------- CHARTS ---------------------------------------
HEALTH_COLOR = {"Trend intact": "#1b7f3b", "Watch": "#b8860b", "Weak": "#b22222", "No price data": "#777777"}
 
 
def holding_chart(sym, df, res, p, path):
    weekly, monthly = ns.resample_ohlc(df, "W"), ns.resample_ohlc(df, "M")
    res = dict(res)
    res["stop_loss"] = p["stop"]                       # draw YOUR stop, not the screener's hypothetical one
    if ns.CHART_STYLE == "heikin":
        import ha_chart
        ha_chart.make_chart(sym, df, res, path, font=ns.CHART_FONT_SIZE, layout=ns.CHART_LAYOUT,
                            extra_levels=[(p["avg_cost"], "darkorange", "Your average cost")],
                            title=f"{sym}  -  you hold {p['qty']:g}  |  average cost {p['avg_cost']:,.2f}  |  latest {res['last_close']:,.2f}"
                                  + (f"  |  your stop {p['stop']:,.2f}" if p["stop"] else ""))
        return
    if ns.CHART_LAYOUT == "side":
        fig, axes = plt.subplots(1, 3, figsize=(27, 10.5), constrained_layout=True)
        kw, dpi = dict(legend_cols=2, legend_y=-0.09, two_line=True), 80
    else:
        fig, axes = plt.subplots(3, 1, figsize=(11, 20), constrained_layout=True)
        kw, dpi = {}, 90
    ns.draw_panel(axes[0], df, "Daily (1 year)", 252,
                  [(50, "50-day avg", "tab:blue"), (200, "200-day avg", "tab:orange")], 252, res, daily=True, **kw)
    ns.draw_panel(axes[1], weekly, "Weekly (3 years)", min(156, len(weekly)), [(30, "30-week avg", "tab:blue")], 52, res, **kw)
    ns.draw_panel(axes[2], monthly, "Monthly (5 years)", min(60, len(monthly)), [(10, "10-month avg", "tab:blue")], 12, res, **kw)
    lo, hi = axes[0].get_ylim()                        # make room so YOUR cost and stop are always visible
    marks = [v for v in (p["avg_cost"], p["stop"]) if v]
    axes[0].set_ylim(min([lo] + [v * 0.96 for v in marks]), max([hi] + [v * 1.04 for v in marks]))
    axes[0].axhline(p["avg_cost"], color="darkorange", linestyle="-.", linewidth=2, label="Your average cost")
    axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, kw.get("legend_y", -0.14)),
                   ncol=kw.get("legend_cols", 3), frameon=False)
    fig.suptitle(f"{sym}  -  you hold {p['qty']:g}  |  average cost {p['avg_cost']:,.2f}  |  latest {res['last_close']:,.2f}"
                 + (f"  |  your stop {p['stop']:,.2f}" if p["stop"] else ""))
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
 
 
def weights_chart(rows, path):
    plt.rcParams.update({"font.size": FONT, "axes.titlesize": FONT + 3, "axes.titleweight": "bold"})
    rows = [r for r in rows if r["value"]][:20]
    fig, ax = plt.subplots(figsize=(14, max(4, 0.7 * len(rows) + 1.5)), constrained_layout=True)
    y = np.arange(len(rows))[::-1]
    ax.barh(y, [r["weight"] for r in rows], color=[HEALTH_COLOR.get(r["health"], "#777") for r in rows])
    ax.set_yticks(y)
    ax.set_yticklabels([r["symbol"] for r in rows])
    for yy, r in zip(y, rows):
        ax.text(r["weight"] + 0.3, yy, f"{r['weight']:.1f}%", va="center", fontsize=FONT)
    ax.set_xlabel("Share of total value, %.  Colour = price health: green intact, amber watch, red weak, grey no data")
    ax.set_title("Where your money is")
    ax.set_xlim(0, max(r["weight"] for r in rows) * 1.15)
    ax.grid(axis="x", alpha=0.25)
    fig.savefig(path, dpi=90)
    plt.close(fig)
 
 
# ------------------------------- REPORT ---------------------------------------
PAGE = """<html><head><meta charset='utf-8'><title>Portfolio __DATE__</title>
<style>
body{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5;max-width:1500px}
html{scroll-behavior:smooth}
table{border-collapse:collapse;font-size:19px}
td,th{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}
td.n,th.n{text-align:right}
img{width:100%;height:auto}
.h{padding:3px 12px;border-radius:6px;color:white;font-size:19px;white-space:nowrap}
.Trendintact{background:#1b7f3b} .Watch{background:#b8860b} .Weak{background:#b22222} .Nopricedata{background:#777}
.STRONG{background:#1b7f3b} .CHECK{background:#b8860b} .WEAK{background:#b22222} .ETF{background:#555}
.pos{color:#1b7f3b} .neg{color:#b22222}
.warn{background:#fff3cd;border-left:8px solid #b8860b;padding:8px 18px;margin:12px 0}
.bad{background:#fdecea;border-left:8px solid #b22222;padding:8px 18px;margin:12px 0}
.box{background:#f2f2f2;border-left:8px solid #777;padding:8px 18px;margin:12px 0}
.small{font-size:17px;color:#444}
a.lv,a.cv{font-size:19px;font-weight:normal}
#top-btn{position:fixed;right:24px;bottom:24px;background:#1f4e9c;color:white;text-decoration:none;
 font-size:22px;font-weight:bold;padding:14px 20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.4)}
</style></head><body id='top'>
<script>window.name='portfolio_view';</script>
<h1>My portfolio - __DATE__</h1>
<p>Prices are end-of-day (latest close in the data: __LAST__). Holdings come from the broker files you imported:
__IMPORTS__. __TRADENOTE__</p>
<div class='warn'><b>Action prompts</b> (EXIT, WARNING, ADD, HOLD) come from price rules, including the Heikin Ashi + Supertrend swing method: they are prompts to look at the chart, not advice.</div>
<div class='warn'><b>How to read this.</b> <b>Price health</b> counts simple warning conditions (below the 200-day or
50-day average, lagging the Nifty, far below the 52-week high, a bearish candle formation, your stop touched).
Trend intact = none, Watch = one or two small ones, Weak = several. It is a prompt to look closer, not a sell signal.
Profit and loss are before charges and tax.</div>
__SUMMARY__
__ATTENTION__
<img src='portfolio_weights.png'>
<table><tr>__HEAD__</tr>__ROWS__</table>
__BODY__
<a id='top-btn' href='#top'>&uarr; Top</a></body></html>"""
 
 
def cv_link(sym, small=False):
    """Link to the stock's page in the candlestick report (same window every time), or nothing if it was not drawn."""
    if sym not in CANDLE_SYMS:
        return ""
    return f" &nbsp;<a class='cv' href='candle_report.html#{sym}' target='candle_view'>candlesticks</a>"


def act_pill(r):
    a = r.get("act")
    if not a:
        return "-"
    import position_actions as pa

    return pa.pill(a["action"])


def act_html(r):
    a = r.get("act")
    if not a:
        return ""
    import position_actions as pa

    return pa.action_html(a)


def add_actions(rows, prices, bench):
    """Add / hold / exit prompt for every holding (position_actions.py). Never stops the report."""
    try:
        import position_actions as pa
    except Exception as e:  # noqa: BLE001
        print(f"  action prompts skipped ({e}).")
        return
    pv = sum(r["value"] for r in rows if r["value"])
    print("\nChecking add / hold / exit prompts ...")
    for r in rows:
        r["act"] = None
        df = prices.get(r["symbol"])
        if df is None or r["an"] is None:
            continue
        try:
            r["act"] = pa.evaluate(df, bench, {"qty": r["qty"], "avg_cost": r["avg_cost"], "stop": r["stop"]}, pv)
            print(f"  {r['symbol']:<12} {r['act']['action']}")
        except Exception as e:  # noqa: BLE001
            print(f"  {r['symbol']}: prompt skipped ({e}).")


def pnl_cell(v, p=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    cls = "pos" if v > 0 else "neg" if v < 0 else ""
    return f"<span class='{cls}'>{pct(v) if p else inr(v)}</span>"
 
 
def lots_html(p):
    if not p["lots"]:
        return ""
    lis = ""
    for l in sorted(p["lots"], key=lambda l: (l["date"] is None, l["date"] or dt.date.min)):
        when = l["date"].strftime("%d %b %Y") if l["date"] else "before your trade history (date unknown)"
        lis += f"<li>{l['broker']}: {l['qty']:g} shares at {l['price']:,.2f}, bought {when}</li>"
    extra = ""
    if p["dated_qty"]:
        extra = (f"<p class='small'>{p['lt_qty']:g} of {p['dated_qty']:g} dated shares are held more than 12 months."
                 + (f" Next ones pass 12 months on {p['next_lt']:%d %b %Y}." if p["next_lt"] else "")
                 + " Check holding-period rules with your tax advisor.</p>")
    return f"<ul>{lis}</ul>{extra}"
 
 
def write_report(rows, positions, problems, last_day, imports, traded, out_dir):
    tot_cost = sum(r["cost"] for r in rows)
    tot_val = sum(r["value"] for r in rows if r["value"])
    tot_pnl = tot_val - sum(r["cost"] for r in rows if r["value"])
    day_note = f"{len(rows)} stocks"
    brokers = {}
    for p in positions:
        for b, q in p["brokers"].items():
            r = next(x for x in rows if x["symbol"] == p["symbol"])
            px = r["last"] or p["avg_cost"]
            c = brokers.setdefault(b, {"cost": 0.0, "value": 0.0})
            c["cost"] += q * (p["cost"] / p["qty"])
            c["value"] += q * px
    brow = "".join(f"<tr><td>{b}</td><td class='n'>{inr(v['cost'])}</td><td class='n'>{inr(v['value'])}</td>"
                   f"<td class='n'>{pnl_cell(v['value'] - v['cost'])}</td></tr>" for b, v in sorted(brokers.items()))
    top3 = sorted(rows, key=lambda r: -r["weight"])[:3]
    summary = (f"<div class='box'><b>Summary:</b> {day_note}. Invested <b>Rs {inr(tot_cost)}</b>, worth "
               f"<b>Rs {inr(tot_val)}</b>, unrealised result <b>{pnl_cell(tot_pnl)}</b> "
               f"({pnl_cell(tot_pnl / tot_cost * 100 if tot_cost else None, True)}). "
               f"Biggest positions: " + ", ".join(f"{r['symbol']} {r['weight']:.0f}%" for r in top3) + "."
               f"<table><tr><th>Broker</th><th class='n'>Invested Rs</th><th class='n'>Worth Rs</th>"
               f"<th class='n'>Result Rs</th></tr>{brow}</table></div>")
 
    att = []
    for r in rows:
        if r["health"] == "Weak":
            att.append(f"<li><b>{r['symbol']}</b>: price health is Weak. {' '.join(r['an']['warn'][:2]) if r['an'] and r['an']['warn'] else ''}</li>")
        if r["stop_hit"]:
            att.append(f"<li><b>{r['symbol']}</b>: latest close is at or below your stop.</li>")
        q = r["quality"]
        if q and q.get("changed_from"):
            att.append(f"<li><b>{r['symbol']}</b>: business quality changed from {q['changed_from']} to {q['verdict']}.</li>")
    guessed = [p["symbol"] for p in positions if any("built-in guess" in f for f in p["flags"])]
    for p in positions:
        for f in p["flags"]:
            if "built-in guess" not in f:
                att.append(f"<li><b>{p['symbol']}</b>: {f}.</li>")
    if guessed:
        att.append(f"<li>{len(guessed)} symbols were matched from a built-in list (NSE's ISIN list could not be downloaded). "
                   "Check each chart is the right company: " + ", ".join(guessed) + ".</li>")
    for m in problems:
        if "could not match" not in m:
            att.append(f"<li>{m}</li>")
    for r in rows:
        a = r.get("act")
        if a and a["action"] in ("EXIT", "WARNING"):
            att.append(f"<li><b>{r['symbol']}</b>: action prompt <b>{a['action']}</b>. {a['reasons'][0] if a['reasons'] else ''}</li>")
    nostop = [r["symbol"] for r in rows if r["stop"] is None and not r["etf"]]
    if nostop:
        att.append("<li>No stop set for: " + ", ".join(nostop) + ". Set one with: python portfolio.py stop SYMBOL PRICE</li>")
    attention = f"<div class='bad'><b>Needs your attention</b><ul>{''.join(att)}</ul></div>" if att else ""
    adds = [r for r in rows if r.get("act") and r["act"]["action"] == "ADD"]
    if adds:
        lis = "".join(f"<li><b>{r['symbol']}</b>: every pyramiding condition holds"
                      + (f"; about {r['act']['add']['qty']:g} shares fit the size rules, stop {r['act']['add']['stop']:,.2f}" if r["act"]["add"] else "")
                      + ".</li>" for r in adds)
        attention += (f"<div class='box'><b>Add candidates</b> (screening prompts, not advice; read the checklist under each stock)"
                      f"<ul>{lis}</ul></div>")
 
    head = ["Stock", "Held at", "Qty", "Avg cost", "Latest", "Value Rs", "Weight", "Result Rs", "Result %",
            "Your stop", "To stop", "Price health", "Action", "Business", "Candle today"]
    trs, body = "", ""
    for r in rows:
        q = r["quality"]
        qlabel = "ETF" if r["etf"] else (q["verdict"] if q else "-")
        held = ", ".join(f"{b} {n:g}" for b, n in r["brokers"].items())
        trs += (f"<tr><td><a href='#{r['symbol']}'>{r['symbol']}</a>{cv_link(r['symbol'], True)}</td><td>{held}</td><td class='n'>{r['qty']:g}</td>"
                f"<td class='n'>{r['avg_cost']:,.2f}</td><td class='n'>{'-' if r['last'] is None else format(r['last'], ',.2f')}</td>"
                f"<td class='n'>{inr(r['value'])}</td><td class='n'>{r['weight']:.1f}%</td>"
                f"<td class='n'>{pnl_cell(r['pnl'])}</td><td class='n'>{pnl_cell(r['pnl_pct'], True)}</td>"
                f"<td class='n'>{'-' if r['stop'] is None else format(r['stop'], ',.2f')}</td>"
                f"<td class='n'>{'-' if r['an'] is None or r['an']['stop_dist'] is None else format(r['an']['stop_dist'], '.1f') + '%'}</td>"
                f"<td><span class='h {r['health'].replace(' ', '')}'>{r['health']}</span></td>"
                f"<td>{act_pill(r)}</td>"
                f"<td><span class='h {qlabel}'>{qlabel}</span></td>"
                f"<td>{', '.join(r['an']['candle']) if r['an'] and r['an']['candle'] else '-'}</td></tr>")
        an = r["an"]
        warn = "".join(f"<li class='neg'>{t}</li>" for t in (an["warn"] if an else []))
        good = "".join(f"<li class='pos'>{t}</li>" for t in (an["good"] if an else []))
        qbox = ""
        if q:
            items = "".join([f"<li class='neg'>&#10008; {t}</li>" for t in q["fails"]]
                            + [f"<li>? {t}</li>" for t in q["unknown"]] + [f"<li class='pos'>&#10004; {t}</li>" for t in q["notes"]])
            qbox = f"<p><b>Business quality {q['verdict']}</b> (checked {q['date']}):</p><ul>{items}</ul>"
        scr = f"<p>Today's screener view: <b>{r['screen_status']}</b>.</p>" if r["screen_status"] else ""
        img = f"<img src='charts/{r['symbol']}.png'>" if r["chart"] else ""
        price_src = "" if r["last"] is not None and r["price_source"] == "Yahoo" else \
            "<p class='small'>No Yahoo price history, so the price shown is from your broker file (as of import).</p>"
        body += (f"<div id='{r['symbol']}'><h2>{r['symbol']} &nbsp;<span class='h {r['health'].replace(' ', '')}'>{r['health']}</span>"
                 f"{cv_link(r['symbol'])} &nbsp;{ns.live_links_html(r['symbol'])}</h2>"
                 f"<p><b>{r['name']}</b> &nbsp; {held}. Average cost <b>{r['avg_cost']:,.2f}</b>, latest "
                 f"<b>{'-' if r['last'] is None else format(r['last'], ',.2f')}</b>, result {pnl_cell(r['pnl'])} "
                 f"({pnl_cell(r['pnl_pct'], True)})."
                 + (f" Your stop <b>{r['stop']:,.2f}</b>." if r["stop"] else "") + (f" Note: {r['note']}" if r["note"] else "") + "</p>"
                 f"{price_src}<ul>{warn}{good}</ul>{scr}{qbox}{act_html(r)}{lots_html(r['pos'])}{img}</div><hr>")
    imports_txt = ", ".join(imports) or "none"
    tradenote = (f"{traded} trades are in your trade log." if traded else
                 "No trade history yet, so buy dates and holding periods are unknown (add trades with the trades, buy or sell commands).")
    html = (PAGE.replace("__DATE__", str(dt.date.today())).replace("__LAST__", last_day or "-")
            .replace("__IMPORTS__", imports_txt).replace("__TRADENOTE__", tradenote)
            .replace("__SUMMARY__", summary).replace("__ATTENTION__", attention)
            .replace("__HEAD__", "".join(f"<th class='{'n' if h in ('Qty', 'Avg cost', 'Latest', 'Value Rs', 'Weight', 'Result Rs', 'Result %', 'Your stop', 'To stop') else ''}'>{h}</th>" for h in head))
            .replace("__ROWS__", trs).replace("__BODY__", body))
    path = out_dir / "portfolio_report.html"
    path.write_text(html, encoding="utf-8")
    return path
 
 
def draw_candles(rows, prices, cstat):
    """Candlestick view of every holding (same pages as the screener's): output/portfolio/candle_report.html.
    It uses YOUR stop (not a buy-above plan) and links back to the portfolio report. Never stops the report."""
    global CANDLE_SYMS
    CANDLE_SYMS = set()
    crow = [{"symbol": r["symbol"], "status": cstat[r["symbol"]], "buy_above": np.nan,
             "stop_loss": r["stop"] if r["stop"] else np.nan, "target_price": np.nan,
             "quality": "ETF" if r["etf"] else (r["quality"]["verdict"] if r["quality"] else ""),
             "quality_notes": "", "holding": True}
            for r in rows if r["symbol"] in cstat and prices.get(r["symbol"]) is not None]
    if not crow:
        return
    try:
        import candle_view as cv
    except Exception as e:  # noqa: BLE001
        print(f"  candlestick view skipped ({e}).")
        return
    old = (cv.REPORT_LINK, cv.REPORT_TARGET, cv.REPORT_LABEL, cv.REPORT_NAME)
    cv.REPORT_LINK, cv.REPORT_TARGET = "portfolio_report.html", "portfolio_view"
    cv.REPORT_LABEL, cv.REPORT_NAME = "your portfolio report", "portfolio report"
    started = time.time()
    print("\nFinding candle formations and drawing candlestick charts ...")
    try:
        cv.run(pd.DataFrame(crow), prices, OUT_DIR)
    except Exception as e:  # noqa: BLE001
        print(f"  candlestick view skipped ({e}).")
    finally:
        cv.REPORT_LINK, cv.REPORT_TARGET, cv.REPORT_LABEL, cv.REPORT_NAME = old
    CANDLE_SYMS = {c["symbol"] for c in crow
                   if (OUT_DIR / "candles" / f"{c['symbol']}.png").exists()
                   and (OUT_DIR / "candles" / f"{c['symbol']}.png").stat().st_mtime >= started - 1
                   and (OUT_DIR / "candle_report.html").exists()}


def cmd_report():
    positions, problems = build_positions()
    if not positions:
        sys.exit("No holdings yet. Run: python portfolio.py import FILE")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "charts").mkdir(exist_ok=True)
    isin_tables(download=True)
    positions, problems = build_positions()
    syms = [p["symbol"] for p in positions if p["resolved"]]
    print(f"Downloading prices for {len(syms)} stocks ...")
    prices = fetch_prices(syms)
    bench = fetch_bench()
    quality = quality_for([p["symbol"] for p in positions if p["resolved"] and not p["etf"]]) if CHECK_QUALITY else {}
 
    rows, last_day, cstat = [], None, {}
    import stock_lookup as sl
    print("\nAnalysing holdings ...")
    for p in positions:
        sym, df = p["symbol"], prices.get(p["symbol"])
        an = res = None
        last, source = None, "Yahoo"
        if df is not None:
            try:
                an = analyse_price(df, bench, p["stop"])
                last = an["last"]
                last_day = max(last_day or an["day"], an["day"])
            except Exception as e:  # noqa: BLE001
                print(f"  {sym}: could not analyse prices ({e}).")
        if last is None:
            last, source = p["broker_ltp"], "broker file"
            p["flags"].append("no usable Yahoo price history (new listing, wrong symbol or delisted)" if p["resolved"]
                              else "no price lookup possible until the symbol is matched")
        value = p["qty"] * last if last else None
        pnl = value - p["cost"] if value else None
        screen_status, chart = "", False
        if df is not None and an is not None:
            try:
                res = sl.analyze_any(df, bench)
                cstat[sym] = res["status"]
                screen_status = res["status"] + (", passes the screener's price rules" if res["passes_screen"]
                                                 else ", does not pass the screener's price rules")
                if DRAW_CHARTS:
                    res["symbol"] = sym
                    holding_chart(sym, df, res, p, OUT_DIR / "charts" / f"{sym}.png")
                    chart = True
            except Exception as e:  # noqa: BLE001
                print(f"  {sym}: chart or screener view skipped ({e}).")
        health = an["health"] if an else "No price data"
        rows.append({"symbol": sym, "name": p["name"] or sym, "qty": p["qty"], "avg_cost": p["avg_cost"], "cost": p["cost"],
                     "last": last, "value": value, "pnl": pnl, "pnl_pct": (pnl / p["cost"] * 100) if pnl is not None and p["cost"] else None,
                     "stop": p["stop"], "stop_hit": bool(an and any("AT OR BELOW your stop" in w for w in an["warn"])),
                     "health": health, "an": an, "quality": quality.get(sym), "etf": p["etf"], "brokers": p["brokers"],
                     "note": p["note"], "pos": p, "screen_status": screen_status, "chart": chart,
                     "price_source": source, "weight": 0.0})
        print(f"  {sym:<12} {health:<14} {'' if last is None else format(last, ',.2f')}")
    tv = sum(r["value"] for r in rows if r["value"])
    for r in rows:
        r["weight"] = (r["value"] / tv * 100) if r["value"] and tv else 0.0
    rows.sort(key=lambda r: -(r["value"] or 0))
    weights_chart(rows, OUT_DIR / "portfolio_weights.png")
    add_actions(rows, prices, bench)
    draw_candles(rows, prices, cstat)
 
    csv_rows = [{"symbol": r["symbol"], "held_at": ", ".join(f"{b}:{q:g}" for b, q in r["brokers"].items()),
                 "qty": r["qty"], "avg_cost": round(r["avg_cost"], 2), "invested": round(r["cost"], 2),
                 "latest": r["last"], "value": None if r["value"] is None else round(r["value"], 2),
                 "weight_pct": round(r["weight"], 1), "pnl": None if r["pnl"] is None else round(r["pnl"], 2),
                 "pnl_pct": None if r["pnl_pct"] is None else round(r["pnl_pct"], 1), "stop": r["stop"],
                 "health": r["health"], "quality": "ETF" if r["etf"] else (r["quality"]["verdict"] if r["quality"] else ""),
                 "first_buy": r["pos"]["first_buy"], "flags": " | ".join(r["pos"]["flags"])} for r in rows]
    pd.DataFrame(csv_rows).to_csv(OUT_DIR / "portfolio_positions.csv", index=False)
    imports = sorted({f"{h['broker']} file {h['source_file']} (imported {h['imported_on']})" for h in load_csv(HOLDINGS)})
    report = write_report(rows, positions, problems, last_day, imports, len(load_csv(TRADES)), OUT_DIR)
    print(f"\nReport: {report}")
    if OPEN_REPORT:
        webbrowser.open(report.resolve().as_uri())
 
 
# ------------------------------- MAIN -----------------------------------------
def run_cmd(args):
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0].lower(), list(args[1:])
    if cmd == "import":
        cmd_import(rest)
    elif cmd == "report":
        cmd_report()
    elif cmd == "show":
        cmd_show()
    elif cmd == "stop":
        cmd_stop(rest)
    elif cmd == "cost":
        cmd_cost(rest)
    elif cmd == "map":
        cmd_map(rest)
    elif cmd == "buy":
        cmd_trade_one("BUY", rest)
    elif cmd == "sell":
        cmd_trade_one("SELL", rest)
    elif cmd == "trades":
        cmd_trades(rest)
    else:
        print(__doc__)
 
 
if __name__ == "__main__":
    run_cmd(sys.argv[1:])
 