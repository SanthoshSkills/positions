#!/usr/bin/env python3
"""
Tracker: how did the screener's stocks do over time?

    python tracker.py            writes output/tracker/5.5_tracker_<date>_<time>.xlsx and one csv per table,
                                 then prints the top of the leaderboard

It reads every output/<date>/results.csv (one row = one stock on one data day) and builds:
  Overview     stocks per status on each data day, the Nifty gate state, and events (callouts)
  Leaderboard  one row per stock: how often it appeared and in which status. Highest Breakout count first.
  Journeys     one line per stock, one cell per data day (see the code letters in Notes)
  Transitions  of the near-breakout stocks, how many became a breakout within 5 and 10 run days
               (strong Breakout, weak-volume breakout only, either, and the weak-volume share)
  History      every row (raw)
The hub page (hub_web.py) offers the same files in its Download menu.

A gap in a journey means "not on the list that day". results.csv lists only stocks that passed the filters.
Not built yet: Outcomes (target, stop-loss or still open for each recommendation).
"""

import datetime as dt
import io
from pathlib import Path

import numpy as np
import pandas as pd

from origin import ORIGIN, with_origin

OUTPUT_DIR = Path("output")
RECENT_DAYS = 20          # "worth following" needs the stock on the list within the last this many data days
FIRST_ROWS = 25           # the web page shows this many rows first (the "Rows" menu can change it)
WEB_STRIP_DAYS = 30       # the journey strip on the web page shows this many data days; older days scroll to the left

BREAKOUT, BREAKOUT_WV = "Breakout", "Breakout, weak volume"
NEAR, BASE, EXTENDED = "Near breakout price", "Building base", "Extended - wait for pullback"
BREAKOUTS = (BREAKOUT, BREAKOUT_WV)

# one letter for each cell of the journey grid
CODES = {BREAKOUT: "B", BREAKOUT_WV: "W", NEAR: "N", BASE: "S", EXTENDED: "E"}
WATCH_CODE = "n"          # near breakout, but watch only (the Nifty gate was off)
CODE_TEXT = [
    ("B", "Breakout (price above the pivot, volume 1.2x or more)"),
    ("W", "Breakout, weak volume"),
    ("N", "Near breakout price (within 3% below the pivot)"),
    ("n", "Near breakout, WATCH ONLY (the Nifty gate was off: no alert, no buy price)"),
    ("S", "Building base"),
    ("E", "Extended (more than 7% above the pivot: wait for a pullback)"),
    ("(blank)", "Not on the list that day"),
]
CODE_FILL = {"B": "1B7F3B", "W": "8FCB9B", "N": "E0A800", "n": "F3DE9A", "S": "D0D0D0", "E": "9FB0C8"}
CODE_FONT = {"B": "FFFFFF"}

HIST_COLS = ["day", "run_date", "symbol", "status", "readiness_score", "alert", "watch_only", "quality",
             "last_close", "buy_above", "stop_loss", "target_price", "suggested_qty", "tight_base",
             "vs_nifty_score", "pct_below_52w_high"]


# ------------------------------- DATA -----------------------------------------
def load_history(output_dir=OUTPUT_DIR):
    """Every results.csv in one table. 'day' = the data date; if two runs share a data date, the later run wins."""
    frames = []
    for p in sorted(Path(output_dir).glob("20*/results.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:  # noqa: BLE001  (skip an empty or damaged file)
            continue
        if df.empty or "symbol" not in df.columns or "status" not in df.columns:
            continue
        df = df.copy()
        df["run_date"] = p.parent.name
        df["day"] = df["data_date"].fillna(p.parent.name) if "data_date" in df.columns else p.parent.name
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=HIST_COLS)
    h = pd.concat(frames, ignore_index=True)
    for c in HIST_COLS:
        if c not in h.columns:
            h[c] = np.nan
    h["alert"] = h["alert"].fillna("").astype(str).replace("nan", "")
    h["watch_only"] = h["watch_only"].fillna("").astype(str).replace("nan", "")
    h["day"] = h["day"].astype(str).str.strip()
    h = h[h["day"].str.fullmatch(r"\d{4}-\d{2}-\d{2}")]       # discard rows whose date is missing or not a date (NaN, NaT, blank)
    h = h[HIST_COLS].sort_values(["day", "run_date", "symbol"])
    h = h.drop_duplicates(["day", "symbol"], keep="last")
    return h.reset_index(drop=True)


def cell_code(status, watch_only):
    if status == NEAR and watch_only == "Yes":
        return WATCH_CODE
    return CODES.get(status, "?")


# ------------------------------- TABLES ---------------------------------------
def leaderboard(h):
    """One row per stock. Sorted by Breakout count (highest first), then appearances, then most recent."""
    cols = ["symbol", "worth_following", "breakouts", "breakout_weak_volume", "near_breakout", "building_base",
            "extended", "appearances", "first_seen", "last_seen", "listed_now", "last_status", "watch_only_days",
            "alert_days", "best_score", "last_score", "last_close", "quality"]
    if h.empty:
        return pd.DataFrame(columns=cols)
    days = sorted(h["day"].unique())
    latest, recent = days[-1], set(days[-RECENT_DAYS:])
    rows = []
    for sym, g in h.groupby("symbol"):
        g = g.sort_values("day")
        last = g.iloc[-1]
        n = g["status"].value_counts()
        brk, brk_wv = int(n.get(BREAKOUT, 0)), int(n.get(BREAKOUT_WV, 0))
        alert_days = int((g["alert"] == "Yes").sum())
        listed_now = last["day"] == latest
        worth = ((brk + brk_wv > 0 or alert_days > 0 or (listed_now and last["status"] == NEAR))
                 and last["day"] in recent)
        rows.append({
            "symbol": sym, "worth_following": "Yes" if worth else "No",
            "breakouts": brk, "breakout_weak_volume": brk_wv,
            "near_breakout": int(n.get(NEAR, 0)), "building_base": int(n.get(BASE, 0)),
            "extended": int(n.get(EXTENDED, 0)), "appearances": len(g),
            "first_seen": g["day"].iloc[0], "last_seen": last["day"],
            "listed_now": "Yes" if listed_now else "No", "last_status": last["status"],
            "watch_only_days": int((g["watch_only"] == "Yes").sum()), "alert_days": alert_days,
            "best_score": g["readiness_score"].max(), "last_score": last["readiness_score"],
            "last_close": last["last_close"], "quality": last["quality"],
        })
    out = pd.DataFrame(rows, columns=cols)
    out = out.sort_values(["breakouts", "appearances", "last_seen", "symbol"],
                          ascending=[False, False, False, True])
    return out.reset_index(drop=True)


def journeys(h, lb):
    """Stock-by-day grid of one-letter codes, in leaderboard order."""
    if h.empty:
        return pd.DataFrame(columns=["symbol", "worth_following", "breakouts"])
    codes = h.assign(code=[cell_code(s, w) for s, w in zip(h["status"], h["watch_only"])])
    grid = codes.pivot(index="symbol", columns="day", values="code")
    grid = grid.reindex(lb["symbol"]).fillna("")
    grid.insert(0, "breakouts", lb["breakouts"].values)
    grid.insert(0, "worth_following", lb["worth_following"].values)
    grid.insert(0, "symbol", grid.index)
    return grid.reset_index(drop=True)


def overview(h):
    """One row per data day: stocks in each status, alerts, and the Nifty gate state."""
    cols = ["day", "listed", "Breakout", "Breakout weak volume", "Near breakout", "Near breakout (watch only)",
            "Building base", "Extended", "alerts", "nifty_gate"]
    rows = []
    for day, g in h.groupby("day"):
        watch = g["watch_only"] == "Yes"
        near = g["status"] == NEAR
        rows.append({
            "day": day, "listed": len(g),
            "Breakout": int((g["status"] == BREAKOUT).sum()),
            "Breakout weak volume": int((g["status"] == BREAKOUT_WV).sum()),
            "Near breakout": int((near & ~watch).sum()),
            "Near breakout (watch only)": int((near & watch).sum()),
            "Building base": int((g["status"] == BASE).sum()),
            "Extended": int((g["status"] == EXTENDED).sum()),
            "alerts": int((g["alert"] == "Yes").sum()),
            # a run that had watch-only stocks = the gate was off. Near stocks without it = the gate was on.
            "nifty_gate": "OFF" if watch.any() else ("ON" if near.any() else "unknown"),
        })
    return pd.DataFrame(rows, columns=cols)


def events(ov):
    """Callouts for the trend chart."""
    rows = []
    prev = None
    for _, r in ov.iterrows():
        gate = r["nifty_gate"]
        if gate != "unknown" and gate != prev:
            rows.append((r["day"], f"Nifty gate {gate}",
                         "Near-breakout stocks are watch only (no alert, no buy price)." if gate == "OFF"
                         else "Near-breakout stocks can get an alert and a trade plan."))
            prev = gate
    a = ov[ov["alerts"] > 0]
    if len(a):
        rows.append((a.iloc[0]["day"], "First alert", f"{int(a.iloc[0]['alerts'])} stock(s) with a score of 70 or more."))
    b = ov[(ov["Breakout"] + ov["Breakout weak volume"]) > 0]
    if len(b):
        rows.append((b.iloc[0]["day"], "First breakout", f"{int(b.iloc[0]['Breakout'] + b.iloc[0]['Breakout weak volume'])} stock(s)."))
        tot = b["Breakout"] + b["Breakout weak volume"]
        best = b.loc[tot.idxmax()]
        rows.append((best["day"], "Most breakouts in one day", f"{int(tot.max())} stock(s)."))
    out = pd.DataFrame(rows, columns=["day", "event", "detail"])
    return out.sort_values("day", kind="stable").reset_index(drop=True)


def transitions(h):
    """
    Of the near-breakout episodes (the first day of an unbroken run of 'Near breakout price'),
    how many became a breakout within 5 or 10 run days. Only episodes with a full window are counted.
    The counts do not overlap: an episode with at least one strong Breakout day in the window counts as "strong".
    An episode with only weak-volume breakout days (no strong Breakout) counts as "weak volume only".
    "either" = strong + weak volume only. weak_share_percent = weak volume only, as a share of "either".
    """
    cols = ["within_run_days", "episodes_started", "full_window", "became_breakout", "became_weak_volume_only",
            "became_either", "percent_strong", "percent_either", "weak_share_percent"]
    if h.empty:
        return pd.DataFrame(columns=cols)
    days = sorted(h["day"].unique())
    idx = {d: i for i, d in enumerate(days)}
    by = {s: dict(zip(g["day"], g["status"])) for s, g in h.groupby("symbol")}
    starts = []
    for s, m in by.items():
        for d, st in m.items():
            i = idx[d]
            if st == NEAR and (i == 0 or m.get(days[i - 1]) != NEAR):
                starts.append((s, i))
    rows = []
    for hz in (5, 10):
        full = strong = weak = 0
        for s, i in starts:
            window = [by[s].get(days[j]) for j in range(i + 1, min(i + hz, len(days) - 1) + 1)]
            has_strong = BREAKOUT in window
            has_weak = BREAKOUT_WV in window
            if has_strong or has_weak or len(days) - 1 - i >= hz:
                full += 1
                strong += int(has_strong)
                weak += int(has_weak and not has_strong)
        either = strong + weak
        pct = lambda a, b: round(100 * a / b, 1) if b else np.nan   # noqa: E731
        rows.append({"within_run_days": hz, "episodes_started": len(starts), "full_window": full,
                     "became_breakout": strong, "became_weak_volume_only": weak, "became_either": either,
                     "percent_strong": pct(strong, full), "percent_either": pct(either, full),
                     "weak_share_percent": pct(weak, either)})
    return pd.DataFrame(rows, columns=cols)


def notes_text(h):
    days = sorted(h["day"].unique()) if len(h) else []
    lines = [
        ("Origin", ORIGIN),
        ("Made on", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Data days", f"{len(days)} ({days[0]} to {days[-1]})" if days else "none yet"),
        ("Source", "Every output/<date>/results.csv. One row = one stock on one data day."),
        ("Gap in a journey", "Not on the list that day (it did not pass the filters, or it left the near-breakout top 10)."),
        ("Worth following", f"Yes = a Breakout, a breakout with weak volume or an alert at least once, or Near breakout "
                            f"on the latest day, AND on the list in the last {RECENT_DAYS} data days."),
        ("Leaderboard order", "Highest Breakout count first, then appearances, then most recent, then symbol."),
        ("Nifty gate (Overview)", "OFF = the run had watch-only stocks. ON = near-breakout stocks with a normal plan. "
                                  "unknown = no near-breakout stocks, or the run is older than the watch-only feature."),
        ("Transitions", "An episode starts on the first day of an unbroken run of 'Near breakout price'. "
                        "Run days are days with a screener run, not trading days. Only episodes with a full window are counted. "
                        "The counts do not overlap: strong = at least one strong Breakout day in the window; weak volume only = "
                        "a weak-volume breakout day but no strong Breakout; either = both added; weak_share_percent = weak volume only / either."),
        ("Not built yet", "Outcomes (target, stop-loss or still open for each recommendation)."),
    ]
    lines += [("Journey code " + c, t) for c, t in CODE_TEXT]
    return pd.DataFrame(lines, columns=["item", "text"])


def build(output_dir=OUTPUT_DIR):
    """All tables. 'History' empty means there is nothing to show yet."""
    h = load_history(output_dir)
    lb = leaderboard(h)
    ov = overview(h)
    return {"overview": ov, "events": events(ov), "leaderboard": lb, "journeys": journeys(h, lb),
            "transitions": transitions(h), "history": h, "notes": notes_text(h)}


def _num(v, digits=2):
    """A number for the web page, or None when the value is missing."""
    try:
        return None if pd.isna(v) else round(float(v), digits)
    except (TypeError, ValueError):
        return None


def web_data(output_dir=OUTPUT_DIR):
    """
    Everything the web page (/tracker in hub_web.py) needs, as plain Python data.
    rows = one per stock, in leaderboard order. codes = one letter per data day ("" -> space = not listed that day),
    alerts = "1" or "0" per data day, scores = readiness score per data day (None = not listed or no score).
    """
    h = load_history(output_dir)
    out = {"origin": ORIGIN, "recent_days": RECENT_DAYS, "first_rows": FIRST_ROWS, "strip_days": WEB_STRIP_DAYS,
           "days": [], "rows": [], "overview": [], "events": [], "transitions": [], "legend": [[c, t] for c, t in CODE_TEXT],
           "fills": {c: "#" + f for c, f in CODE_FILL.items()}, "fonts": {c: "#" + f for c, f in CODE_FONT.items()}}
    if h.empty:
        return out
    lb = leaderboard(h)
    days = sorted(h["day"].unique())
    pos = {d: i for i, d in enumerate(days)}
    out["days"] = days
    # Overview tab: all stocks (not the list filters). counts use the same letters as the strip.
    ov = overview(h)
    out["overview"] = [{"day": r["day"], "alerts": int(r["alerts"]), "gate": r["nifty_gate"],
                        "counts": {"B": int(r["Breakout"]), "W": int(r["Breakout weak volume"]),
                                   "N": int(r["Near breakout"]), "n": int(r["Near breakout (watch only)"]),
                                   "S": int(r["Building base"]), "E": int(r["Extended"])}}
                       for _, r in ov.iterrows()]
    out["events"] = [[r["day"], r["event"], r["detail"]] for _, r in events(ov).iterrows()]
    tr = transitions(h)
    out["transitions"] = [{k: (None if pd.isna(v) else (float(v) if isinstance(v, (float, np.floating)) else int(v)))
                           for k, v in r.items()} for r in tr.to_dict("records")]
    by = {s: g.sort_values("day") for s, g in h.groupby("symbol")}
    for _, r in lb.iterrows():
        g = by[r["symbol"]]
        codes, alerts, scores = [" "] * len(days), ["0"] * len(days), [None] * len(days)
        closes, buys = [None] * len(days), [None] * len(days)
        for d, st, wo, al, sc, cl, by_ in zip(g["day"], g["status"], g["watch_only"], g["alert"], g["readiness_score"],
                                              g["last_close"], g["buy_above"]):
            i = pos[d]
            codes[i] = cell_code(st, wo)
            alerts[i] = "1" if al == "Yes" else "0"
            scores[i] = _num(sc, 1)
            closes[i], buys[i] = _num(cl), _num(by_)
        last = g.iloc[-1]
        alert_rows = g[g["alert"] == "Yes"]
        buy = g["buy_above"].dropna()
        stop = g["stop_loss"].dropna()
        tgt = g["target_price"].dropna()
        vs = g["vs_nifty_score"].dropna()
        counts = {}
        for c in codes:
            if c != " ":
                counts[c] = counts.get(c, 0) + 1
        out["rows"].append({
            "symbol": r["symbol"], "worth": r["worth_following"],
            "breakouts": int(r["breakouts"]), "weak": int(r["breakout_weak_volume"]),
            "appearances": int(r["appearances"]), "listed_now": r["listed_now"],
            "last_code": cell_code(last["status"], last["watch_only"]), "last_seen": r["last_seen"],
            "alert_days": int(r["alert_days"]), "last_alert": alert_rows["day"].iloc[-1] if len(alert_rows) else "",
            "last_buy": _num(buy.iloc[-1]) if len(buy) else None,
            "quality": "" if pd.isna(last["quality"]) else str(last["quality"]),
            "vs_nifty": _num(vs.iloc[-1]) if len(vs) else None,
            "codes": "".join(codes), "alerts": "".join(alerts), "scores": scores,
            # detail panel: price and buy price per data day (None = not listed or no value), and the summary values
            "closes": closes, "buys": buys, "counts": counts, "first_seen": r["first_seen"],
            "last_stop": _num(stop.iloc[-1]) if len(stop) else None,
            "last_target": _num(tgt.iloc[-1]) if len(tgt) else None,
            "best_score": _num(r["best_score"], 1), "last_score": _num(r["last_score"], 1),
        })
    return out


# ------------------------------- FILES ----------------------------------------
CSV_TABLES =["overview", "events", "leaderboard", "journeys", "transitions", "history"]


def csv_bytes(tables, name):
    """One table as csv bytes, with the origin column first."""
    return with_origin(tables[name]).to_csv(index=False).encode("utf-8")


def excel_bytes(tables):
    """One workbook. Sheets: Overview (chart + events), Leaderboard, Journeys, Transitions, History, Notes."""
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_fill = PatternFill("solid", fgColor="1F4E9C")
    head_font = Font(bold=True, color="FFFFFF")

    def style_header(ws, row=1, ncols=None):
        for c in ws[row][: ncols or ws.max_column]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(vertical="center", wrap_text=True)

    def widths(ws, df, cap=40, minimum=9):
        for i, col in enumerate(df.columns, start=1):
            longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(300)])
            ws.column_dimensions[get_column_letter(i)].width = max(minimum, min(cap, longest + 2))

    buf = io.BytesIO()
    ov, ev = with_origin(tables["overview"]), with_origin(tables["events"])
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        chart_rows = 22
        ov.to_excel(xl, sheet_name="Overview", index=False, startrow=chart_rows)
        ev.to_excel(xl, sheet_name="Overview", index=False, startrow=1, startcol=len(ov.columns) + 1)
        for name, key in (("Leaderboard", "leaderboard"), ("Journeys", "journeys"),
                          ("Transitions", "transitions"), ("History", "history"), ("Notes", "notes")):
            with_origin(tables[key]).to_excel(xl, sheet_name=name, index=False)
        wb = xl.book

        # --- Overview: chart on top, table below, events (callouts) to the right
        ws = wb["Overview"]
        style_header(ws, row=chart_rows + 1, ncols=len(ov.columns))
        c0 = len(ov.columns) + 2
        ws.cell(row=1, column=c0, value="Events (callouts for the chart)").font = Font(bold=True, size=13)
        for c in ws[2][c0 - 1: c0 - 1 + len(ev.columns)]:
            c.fill, c.font = head_fill, head_font
        widths(ws, ov, cap=26)
        for j, col in enumerate(ev.columns):
            letter = get_column_letter(c0 + j)
            ws.column_dimensions[letter].width = {"origin": 18, "day": 12, "event": 28, "detail": 60}.get(col, 14)
        if len(ov):
            ch = BarChart()
            ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
            ch.title = f"Status by data day [{ORIGIN}]"
            ch.y_axis.title, ch.x_axis.title = "stocks", "data day"
            first = chart_rows + 2
            last = chart_rows + 1 + len(ov)
            colour = {"Breakout": "1B7F3B", "Breakout weak volume": "8FCB9B", "Near breakout": "E0A800",
                      "Near breakout (watch only)": "F3DE9A", "Building base": "BDBDBD", "Extended": "7F8FA6"}
            names = list(ov.columns)
            for col in colour:
                k = names.index(col) + 1
                ch.add_data(Reference(ws, min_col=k, min_row=chart_rows + 1, max_row=last), titles_from_data=True)
                ch.series[-1].graphicalProperties.solidFill = colour[col]
            ch.set_categories(Reference(ws, min_col=names.index("day") + 1, min_row=first, max_row=last))
            ch.height, ch.width = 10.5, 24
            ch.x_axis.delete = ch.y_axis.delete = False
            ws.add_chart(ch, "A1")
        ws.freeze_panes = None

        # --- Leaderboard
        ws = wb["Leaderboard"]
        lb = with_origin(tables["leaderboard"])
        style_header(ws)
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
        widths(ws, lb, cap=34)
        k = list(lb.columns).index("worth_following") + 1
        for r in range(2, ws.max_row + 1):
            c = ws.cell(row=r, column=k)
            if c.value == "Yes":
                c.font = Font(bold=True, color="1B7F3B")

        # --- Journeys: coloured letters, narrow columns, dates written sideways
        ws = wb["Journeys"]
        jr = with_origin(tables["journeys"])
        style_header(ws)
        fixed = 4   # origin, symbol, worth_following, breakouts
        ws.freeze_panes = ws.cell(row=2, column=fixed + 1)
        ws.row_dimensions[1].height = 78
        for i, col in enumerate(jr.columns, start=1):
            if i > fixed:
                ws.cell(row=1, column=i).alignment = Alignment(text_rotation=90, horizontal="center")
                ws.column_dimensions[get_column_letter(i)].width = 4.5
            else:
                ws.column_dimensions[get_column_letter(i)].width = {1: 18, 2: 16, 3: 10, 4: 10}[i]
        for r in range(2, ws.max_row + 1):
            for i in range(fixed + 1, ws.max_column + 1):
                c = ws.cell(row=r, column=i)
                if c.value in CODE_FILL:
                    c.fill = PatternFill("solid", fgColor=CODE_FILL[c.value])
                    c.font = Font(bold=True, color=CODE_FONT.get(c.value, "000000"))
                    c.alignment = Alignment(horizontal="center")
        ws.auto_filter.ref = ws.dimensions

        # --- Transitions, History, Notes
        for name, key in (("Transitions", "transitions"), ("History", "history"), ("Notes", "notes")):
            ws = wb[name]
            df = with_origin(tables[key])
            style_header(ws)
            widths(ws, df, cap=90 if name == "Notes" else 28)
            ws.freeze_panes = "A2"
            if name == "History":
                ws.auto_filter.ref = ws.dimensions
            if name == "Notes":
                for r in range(2, ws.max_row + 1):
                    ws.cell(row=r, column=3).alignment = Alignment(wrap_text=True, vertical="top")
        wb.active = 0
    return buf.getvalue()


def stamp():
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M")


def main():
    tables = build()
    if tables["history"].empty:
        print("No results.csv files found under output/. Run nse_screener.py first.")
        return
    out = OUTPUT_DIR / "tracker"
    out.mkdir(parents=True, exist_ok=True)
    s = stamp()
    (out / f"5.5_tracker_{s}.xlsx").write_bytes(excel_bytes(tables))
    for name in CSV_TABLES:
        (out / f"5.5_tracker_{name}_{s}.csv").write_bytes(csv_bytes(tables, name))
    h, lb = tables["history"], tables["leaderboard"]
    print(f"{h['day'].nunique()} data days, {lb['symbol'].nunique()} stocks. Files are in {out}")
    show = ["symbol", "worth_following", "breakouts", "breakout_weak_volume", "near_breakout", "appearances", "last_status"]
    print("\nTop of the leaderboard:\n")
    print(lb[show].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
