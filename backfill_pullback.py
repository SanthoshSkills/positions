"""Backfill the pullback prices that were added on 22 Nov 2026 into OLD screen results (positions repo).

Run it on your own computer (it downloads prices from Yahoo), from the folder that holds `output/`:

    python backfill_pullback.py --dry-run     (only count what it would do)
    python backfill_pullback.py               (write the files)

What it fills (only EMPTY cells; it never changes a value that is already there):
  - Extended stocks: pullback_price = pivot,  pullback_buy = pivot + 0.5%
(This repo has no Nifty gate and no WATCH ONLY stocks, so only Extended rows are filled.)
The pivot is the highest high of the 50 trading days BEFORE the data date (same rule as the screener).

The numbers are close, not exact: Yahoo prices are adjusted for later dividends, so the pivot is scaled
to the close that the old run saved. A row is skipped (and listed) when the close in Yahoo history today
does not match the saved close within 5% (for example after a split or bonus), or when the old status
does not agree with the new pivot. Each file that changes gets a copy first: results.csv.before_backfill.
Rows that were filled get `backfilled = Yes`.
"""
import sys
from pathlib import Path

import pandas as pd

import nse_screener as ns

OUTPUT_DIR = Path("output")
EXTENDED_PCT = ns.EXTENDED_PCT          # same limit as the screener (7% above the pivot)
MATCH_TOLERANCE = 0.05                  # saved close vs Yahoo close today
NEW_COLS = ["pullback_price", "pullback_buy"]


def is_missing(v):
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() == ""


def pivot_for(prices, symbol, day, saved_close):
    """(pivot, reason). pivot is None when the row cannot be filled; reason says why."""
    df = prices.get(symbol)
    if df is None:
        return None, "no price history"
    df = df.copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    part = df.loc[: pd.Timestamp(day)]
    if len(part) < ns.PIVOT_DAYS + 1 or part.index[-1].strftime("%Y-%m-%d") != str(day):
        return None, "no price for that day"
    close_then = float(part["Close"].iloc[-1])
    if is_missing(saved_close) or abs(close_then / float(saved_close) - 1) > MATCH_TOLERANCE:
        return None, f"close {close_then:.2f} in Yahoo today vs {saved_close} saved (split or bonus?)"
    base = part.iloc[-ns.PIVOT_DAYS - 1 : -1]
    scale = float(saved_close) / close_then       # remove the dividend adjustment made after that run
    return float(base["High"].max()) * scale, ""


def run(output_dir=OUTPUT_DIR, dry_run=False, loader=ns.download_prices):
    files = sorted(Path(output_dir).glob("20*/results.csv"))
    tables, wanted = {}, {}
    for p in files:
        try:
            df = pd.read_csv(p)
        except Exception:  # noqa: BLE001
            continue
        if df.empty or "symbol" not in df.columns or "status" not in df.columns:
            continue
        for c in NEW_COLS:
            if c not in df.columns:
                df[c] = float("nan")
        day_col = "data_date" if "data_date" in df.columns else None
        todo = []
        for i, r in df.iterrows():
            if str(r["status"]).startswith("Extended") and is_missing(r["pullback_price"]):
                todo.append(i)
        if todo:
            tables[p] = (df, todo, day_col)
            for i in todo:
                wanted.setdefault(df.at[i, "symbol"], set())
    n_rows = sum(len(t[1]) for t in tables.values())
    print(f"{len(files)} result files; {len(tables)} need a backfill ({n_rows} rows, {len(wanted)} stocks).")
    if not tables or dry_run:
        if dry_run:
            print("Dry run: nothing written.")
        return {"files": len(tables), "rows": n_rows, "filled": 0, "skipped": []}

    prices = loader(sorted(wanted))
    filled, skipped = 0, []
    for p, (df, todo, day_col) in tables.items():
        changed = False
        if "backfilled" not in df.columns:
            df["backfilled"] = ""
        df["backfilled"] = df["backfilled"].fillna("").astype(str).replace("nan", "")
        for i in todo:
            r = df.loc[i]
            day = r[day_col] if day_col else p.parent.name
            pv, why = pivot_for(prices, r["symbol"], day, r["last_close"])
            if pv is None:
                skipped.append((p.parent.name, r["symbol"], why))
                continue
            if float(r["last_close"]) <= pv * (1 + EXTENDED_PCT / 100):
                skipped.append((p.parent.name, r["symbol"], "old status Extended does not fit the new pivot"))
                continue
            df.at[i, "pullback_price"] = round(pv, 2)
            df.at[i, "pullback_buy"] = round(pv * 1.005, 2)
            df.at[i, "backfilled"] = "Yes"
            filled += 1
            changed = True
        if changed:
            backup = p.with_name(p.name + ".before_backfill")
            if not backup.exists():
                backup.write_bytes(p.read_bytes())
            cols = list(df.columns)
            for c in NEW_COLS:                         # put the new columns next to buy_above
                cols.remove(c)
            at = cols.index("buy_above") + 1 if "buy_above" in cols else len(cols)
            cols[at:at] = NEW_COLS
            df[cols].to_csv(p, index=False)
    print(f"Filled {filled} rows. Skipped {len(skipped)}.")
    for day, sym, why in skipped[:40]:
        print(f"  skipped {day} {sym}: {why}")
    if len(skipped) > 40:
        print(f"  ... and {len(skipped) - 40} more")
    return {"files": len(tables), "rows": n_rows, "filled": filled, "skipped": skipped}


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
