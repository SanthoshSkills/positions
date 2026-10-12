#!/usr/bin/env python3
"""
Paper-trading tracker (no real money)

It reads the newest results.csv made by nse_screener.py, writes down each pick
(buy-above price, stop-loss, quantity) in paper_log.csv, and later checks real
daily prices to see what would have happened.

Commands
  python paper_tracker.py add              log the top 5 tight-base picks from the latest screen
  python paper_tracker.py add LALPATHLAB   log specific stocks instead
  python paper_tracker.py update           download prices and update every open paper trade
  python paper_tracker.py show             show the log without downloading anything
                                           (also makes paper_report.html: large type, opens in your browser)
  python paper_tracker.py close SYMBOL     sell an open paper trade now (or cancel one still waiting)

How a paper trade moves
  WAITING  the price has not yet reached your buy-above price
  OPEN     bought: the price crossed buy-above (if it gapped up, you buy at the open)
  STOPPED  sold: the price fell to your stop-loss (if it gapped down, you sell at the open)
  EXPIRED  never reached the buy price within WAIT_DAYS, so the idea is dropped
  CLOSED   you closed it yourself with the close command
  CANCELLED you cancelled it while still waiting

Honest limits: it uses daily highs and lows, so it cannot know the order of moves
inside a day. If the buy and the stop could both happen in one day, it assumes the
stop was hit. The target price is shown for planning only: the tracker does not sell at it. Open trades just
show their running result, because the plan is to hold for months and review.
"""

import csv
import datetime as dt
import sys
import webbrowser
from pathlib import Path

import pandas as pd

# ----------------------------- SETTINGS (edit me) -----------------------------
CAPITAL = 25_000            # keep the same as in nse_screener.py (used for a warning only)
DEFAULT_PICKS = 5           # how many stocks 'add' logs when you do not name any
WAIT_DAYS = 30              # drop a pick if the buy price is not reached within this many days
COST_PCT_PER_SIDE = 0.15    # rough taxes and charges on each buy and each sell, in %
FLAT_SELL_FEE = 15          # rough flat fee per stock when selling, in Rs (check your broker)
OPEN_REPORT = True          # open paper_report.html in your browser after update/show
LOG_FILE = "paper_log.csv"
REPORT_FILE = "paper_report.html"
OUTPUT_DIR = "output"
# ------------------------------------------------------------------------------

COLS = ["symbol", "logged_on", "data_date", "status", "buy_above", "stop_loss", "target", "qty",
        "entry_date", "entry_price", "exit_date", "exit_price", "last_price", "last_date",
        "highest_since_entry", "net_pnl", "net_pnl_pct", "note"]
ACTIVE = ("WAITING", "OPEN")


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------- LOG FILE -------------------------------------
def load_log():
    if not Path(LOG_FILE).exists():
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for c in COLS:
            r.setdefault(c, "")
    return rows


def save_log(rows):
    try:
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in COLS})
    except PermissionError:
        sys.exit(f"Could not save {LOG_FILE}. Close it in Excel and run the command again.")


def latest_results():
    files = sorted(Path(OUTPUT_DIR).glob("*/results.csv"))
    if not files:
        sys.exit("No results.csv found. Run nse_screener.py first.")
    return pd.read_csv(files[-1]), files[-1]


# ------------------------------- SIMULATION -----------------------------------
def fetch(symbol, start):
    import yfinance as yf

    df = yf.download(symbol + ".NS", start=start, interval="1d", auto_adjust=True, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def simulate(r, df, today=None):
    """Walk through the daily bars after the signal date and update the trade dict r."""
    today = today or dt.date.today()
    buy, stop, qty = float(r["buy_above"]), float(r["stop_loss"]), int(float(r["qty"]))
    data_date = dt.date.fromisoformat(r["data_date"])
    highest = num(r["highest_since_entry"]) or 0.0

    for ts, bar in df.iterrows():
        day = ts.date()
        if day <= data_date:
            continue
        o, h, l = float(bar["Open"]), float(bar["High"]), float(bar["Low"])

        if r["status"] == "WAITING":
            if (day - data_date).days > WAIT_DAYS:
                r["status"] = "EXPIRED"
                break
            if h < buy:
                r["last_price"], r["last_date"] = f"{float(bar['Close']):.2f}", str(day)
                continue
            fill = max(buy, o)  # gap up: you would have bought at the open
            r.update(status="OPEN", entry_date=str(day), entry_price=f"{fill:.2f}")

        if r["status"] == "OPEN":
            highest = max(highest, h)
            r["highest_since_entry"] = f"{highest:.2f}"
            if l <= stop:
                exit_price = min(stop, o)  # gap down: you would have sold at the open
                r.update(status="STOPPED", exit_date=str(day), exit_price=f"{exit_price:.2f}")
                r["last_price"], r["last_date"] = f"{exit_price:.2f}", str(day)
                break
            r["last_price"], r["last_date"] = f"{float(bar['Close']):.2f}", str(day)

    if r["status"] == "WAITING" and (today - data_date).days > WAIT_DAYS:
        r["status"] = "EXPIRED"
    compute_pnl(r)


def compute_pnl(r):
    entry = num(r["entry_price"])
    if entry is None:
        return
    qty = int(float(r["qty"]))
    px = num(r["exit_price"]) if r["status"] in ("STOPPED", "CLOSED") else num(r["last_price"])
    if px is None:
        return
    gross = (px - entry) * qty
    costs = COST_PCT_PER_SIDE / 100 * (entry + px) * qty + FLAT_SELL_FEE
    net = gross - costs
    r["net_pnl"] = f"{net:.0f}"
    r["net_pnl_pct"] = f"{net / (entry * qty) * 100:.1f}"


# ------------------------------- COMMANDS -------------------------------------
def cmd_add(args):
    df, path = latest_results()
    rows = load_log()
    active = {r["symbol"] for r in rows if r["status"] in ACTIVE}
    chosen = []

    if args:
        for sym in [a.upper() for a in args]:
            hit = df[df["symbol"] == sym]
            if hit.empty:
                print(f"{sym}: not in the latest screen, skipped.")
                continue
            row = hit.iloc[0]
            if pd.isna(row["buy_above"]):
                print(f"{sym}: marked '{row['status']}', so there is no entry price. Skipped.")
            elif pd.isna(row["suggested_qty"]) or int(row["suggested_qty"]) < 1:
                print(f"{sym}: too expensive for your risk and size limits (quantity 0). Skipped.")
            elif sym in active:
                print(f"{sym}: already being tracked.")
            elif row.get("quality") == "WEAK":
                print(f"{sym}: marked WEAK ({row.get('quality_notes')}). Added anyway because you named it.")
                chosen.append(row)
            else:
                chosen.append(row)
    else:
        pool = df[(df["tight_base"] == "Yes") & df["buy_above"].notna()
                  & (df["suggested_qty"].fillna(0) >= 1) & ~df["symbol"].isin(active)]
        if "quality" in pool.columns:  # skip businesses the screener marked WEAK
            pool = pool[pool["quality"] != "WEAK"]
        chosen = [row for _, row in pool.head(DEFAULT_PICKS).iterrows()]

    if not chosen:
        print("Nothing new to add.")
        return

    today = str(dt.date.today())
    for row in chosen:
        rows.append({
            "symbol": row["symbol"], "logged_on": today, "data_date": str(row["data_date"]),
            "status": "WAITING", "buy_above": f"{row['buy_above']:.2f}",
            "stop_loss": f"{row['stop_loss']:.2f}", "qty": str(int(row["suggested_qty"])),
            "target": "" if pd.isna(row.get("target_price")) else f"{row['target_price']:.2f}",
            "note": f"{row['status']}; stop {row['stop_basis']}; quality {row.get('quality', 'n/a')}",
        })
        print(f"Logged {row['symbol']}: buy above {row['buy_above']:.2f}, "
              f"stop {row['stop_loss']:.2f}, "
              + ("" if pd.isna(row.get("target_price")) else f"target {row['target_price']:.2f}, ")
              + f"qty {int(row['suggested_qty'])}")
    save_log(rows)

    committed = sum(float(r["buy_above"]) * int(float(r["qty"])) for r in rows if r["status"] in ACTIVE)
    print(f"\nSource: {path}")
    print(f"If every waiting pick triggered you would have about Rs {committed:,.0f} invested "
          f"(your capital: Rs {CAPITAL:,}).")
    if committed > CAPITAL:
        print("That is more than your capital. Remove some with: python paper_tracker.py close SYMBOL")
    print("Next: run 'python paper_tracker.py update' every week or so.")


def cmd_update():
    rows = load_log()
    if not rows:
        sys.exit("The log is empty. Run 'python paper_tracker.py add' first.")
    for r in rows:
        if r["status"] not in ACTIVE:
            continue
        try:
            df = fetch(r["symbol"], r["data_date"])
        except Exception as e:  # noqa: BLE001
            print(f"{r['symbol']}: could not download prices ({e}). Left unchanged.")
            continue
        if df is None:
            print(f"{r['symbol']}: no price data returned. Left unchanged.")
            continue
        simulate(r, df)
    save_log(rows)
    show(rows)


def cmd_close(args):
    if not args:
        sys.exit("Usage: python paper_tracker.py close SYMBOL [price]")
    sym = args[0].upper()
    rows = load_log()
    for r in rows:
        if r["symbol"] == sym and r["status"] in ACTIVE:
            if r["status"] == "WAITING":
                r["status"] = "CANCELLED"
            else:
                px = num(args[1]) if len(args) > 1 else num(r["last_price"])
                if px is None:
                    sys.exit("No price known. Run 'update' first or give a price: close SYMBOL 123.4")
                r.update(status="CLOSED", exit_date=str(dt.date.today()), exit_price=f"{px:.2f}")
                compute_pnl(r)
            save_log(rows)
            print(f"{sym}: {r['status']}")
            return
    print(f"No active paper trade found for {sym}.")


def show(rows=None):
    rows = load_log() if rows is None else rows
    if not rows:
        print("The log is empty. Run 'python paper_tracker.py add' first.")
        return
    cols = ["symbol", "status", "buy_above", "stop_loss", "target", "qty", "entry_price",
            "last_price", "exit_price", "net_pnl", "net_pnl_pct"]
    print(pd.DataFrame(rows)[cols].to_string(index=False))

    closed = [r for r in rows if r["status"] in ("STOPPED", "CLOSED")]
    open_ = [r for r in rows if r["status"] == "OPEN"]
    waiting = [r for r in rows if r["status"] == "WAITING"]
    realised = sum(num(r["net_pnl"]) or 0 for r in closed)
    unreal = sum(num(r["net_pnl"]) or 0 for r in open_)
    wins = sum(1 for r in closed if (num(r["net_pnl"]) or 0) > 0)

    print("\nSummary (after rough costs)")
    print(f"  Waiting to trigger: {len(waiting)}   Open: {len(open_)}   Finished: {len(closed)}")
    print(f"  Finished trades: {wins} winners of {len(closed)}, net Rs {realised:,.0f}")
    print(f"  Open trades running result: Rs {unreal:,.0f}")
    for r in waiting:
        last, buy = num(r["last_price"]), num(r["buy_above"])
        if last:
            print(f"  {r['symbol']}: last {last:.2f}, needs {((buy / last) - 1) * 100:+.1f}% to trigger")
    for r in open_:
        last, stop, tgt = num(r["last_price"]), num(r["stop_loss"]), num(r["target"])
        if last:
            extra = f", target is {((tgt / last) - 1) * 100:+.1f}% away" if tgt else ""
            print(f"  {r['symbol']}: last {last:.2f}, stop is {((stop / last) - 1) * 100:.1f}% away{extra}")
    write_html(rows)


def write_html(rows):
    """Large-type version of the log, easy to read in a browser."""
    colour = {"OPEN": "#dbe9ff", "WAITING": "#fff6d6", "STOPPED": "#ffdcdc",
              "EXPIRED": "#eeeeee", "CLOSED": "#dff3e0", "CANCELLED": "#eeeeee"}
    head = ["Stock", "Status", "Buy above", "Stop-loss", "Target", "Qty", "Bought at", "Latest price",
            "Sold at", "Result Rs", "Result %"]
    keys = ["symbol", "status", "buy_above", "stop_loss", "target", "qty", "entry_price", "last_price",
            "exit_price", "net_pnl", "net_pnl_pct"]
    body = ""
    for r in rows:
        pnl = num(r["net_pnl"])
        cls = "" if pnl is None else ("pos" if pnl > 0 else "neg")
        cells = "".join(
            f"<td class='{cls if k in ('net_pnl', 'net_pnl_pct') else ''}'>{r.get(k, '')}</td>" for k in keys
        )
        body += f"<tr style='background:{colour.get(r['status'], '#fff')}'>{cells}</tr>"
    closed = [r for r in rows if r["status"] in ("STOPPED", "CLOSED")]
    open_ = [r for r in rows if r["status"] == "OPEN"]
    realised = sum(num(r["net_pnl"]) or 0 for r in closed)
    unreal = sum(num(r["net_pnl"]) or 0 for r in open_)
    wins = sum(1 for r in closed if (num(r["net_pnl"]) or 0) > 0)
    html = f"""<html><head><meta charset='utf-8'><title>Paper trades</title>
<style>
body{{font-family:Arial,sans-serif;font-size:22px;margin:28px;line-height:1.5}}
table{{border-collapse:collapse;font-size:21px}}
th{{text-align:left;padding:10px 14px;border-bottom:3px solid #888}}
td{{padding:10px 14px;border-bottom:1px solid #ccc}}
.pos{{color:#1b7f3b;font-weight:bold}} .neg{{color:#b22222;font-weight:bold}}
</style></head><body>
<h1>Paper trades - {dt.date.today()}</h1>
<p>Finished trades: <b>{wins} winners of {len(closed)}</b>, net <b>Rs {realised:,.0f}</b>.
Open trades running result: <b>Rs {unreal:,.0f}</b>. (After rough charges. No real money.)</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{body}</table>
<p>WAITING = price has not reached the buy-above price yet. OPEN = bought. STOPPED = sold at the
stop-loss. EXPIRED = never reached the buy price in time.</p>
</body></html>"""
    path = Path(REPORT_FILE)
    try:
        path.write_text(html, encoding="utf-8")
    except PermissionError:
        return
    print(f"\nLarge-type version: {path.resolve()}")
    if OPEN_REPORT:
        webbrowser.open(path.resolve().as_uri())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args = sys.argv[1].lower(), sys.argv[2:]
    if cmd == "add":
        cmd_add(args)
    elif cmd == "update":
        cmd_update()
    elif cmd == "show":
        show()
    elif cmd == "close":
        cmd_close(args)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
