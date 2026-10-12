#!/usr/bin/env python3
"""
Add, hold or exit? Rule-based prompts for the long positions you hold

What it does
  For every holding in portfolio.py it reads the newest completed daily candle and the last completed weekly candle and gives
  ONE prompt, with the reasons and a checklist you can verify on the chart:

     EXIT      your stop was touched, OR the daily Supertrend is red and 2 or more bearish Heikin Ashi candles have formed
               below the line (the exit of the Heikin Ashi + Supertrend swing method), OR both daily and weekly are red,
               OR a bearish candle formation was confirmed by a close below its low with enough other signs (alerts.py).
     WARNING   something is wrong but not confirmed: a red weekly Supertrend, a red daily one still waiting for its second
               candle, a close below the 200-day average, or an alerts.py WARNING. Look at the chart; consider tightening
               the stop or reducing.
     ADD       the position is working and every pyramiding condition below holds. The prompt shows how many shares fit the
               size rules, the stop for the whole position and what a stop-out would cost.
     HOLD      nothing to do. It may suggest raising your stop to the daily Supertrend line, and says which add-conditions
               are not met yet.

  Pyramiding checklist (ALL must pass for ADD; edit the numbers at the top of the file)
     1. In profit: price at least MIN_PROFIT_TO_ADD_PCT (5%) above your average cost. Never add to a loser.
     2. Trend: above the 50-day and 200-day averages, 200-day average rising, daily AND weekly Supertrend green.
     3. A trigger: a breakout (close above the highest high of the last 50 days plus 0.5%, on volume at least 1.2x its 50-day
        average, and not more than 7% past it) OR a pullback bounce (touched the 50-day average in the last 5 days, now
        back above it, above yesterday's high, on a bullish Heikin Ashi candle).
     4. Not stretched: not more than 25% above the 50-day average.
     5. Room above: no resistance band within 3% above the price.
     6. No exit warning (nothing above fires).
     7. A valid stop: the daily Supertrend line (or your stop, if higher) must be below the price.
     8. Size: the add is at most ADD_MAX_FRACTION (half) of the shares you hold, risks at most ADD_RISK_PCT (0.5%) of your
        portfolio value if the stop is hit, and keeps the stock under MAX_POSITION_PCT (20%) of the portfolio.

Commands (same folder as the other scripts)
  python position_actions.py check              prompt for every holding (downloads prices); EXIT and WARNING first
  python position_actions.py explain SYMBOL     the full checklist for one holding
  python position_actions.py replay SYMBOL [DAYS]   when the prompt WOULD have changed over the last DAYS (default 250)
                                                trading days, ignoring your cost and stop: use it to judge how noisy it is
The portfolio report (python portfolio.py report) shows the same prompt in an Action column with the checklist per stock.

Honest limits
  * These are screening prompts built from price rules, not advice and not predictions. Every number above is a starting
    point that has NOT been tested on real data: run replay on stocks you know before you rely on it.
  * The Heikin Ashi + Supertrend rules follow a summary of Harneet Singh Kharbanda's method (flip candle counts as candle 1;
    entry or exit on the 2nd or 3rd candle). The pyramiding conditions are common trend-following practice, not his rules.
  * End-of-day data only. Gaps can take the price through a stop; the prompt cannot see intraday moves.
  * It cannot know how many times you already added, why you bought, or your tax position. Selling realises tax (see
    redeploy.py for a rough figure). ETFs are checked the same way as shares.
"""

import sys

import numpy as np
import pandas as pd

import ha_chart as ha
import hub_settings
import nse_screener as ns

# ----------------------------- SETTINGS (edit me) -----------------------------
MIN_PROFIT_TO_ADD_PCT = 5.0     # only add when the position is at least this much in profit
ADD_MAX_FRACTION = 0.5          # an add is at most this fraction of the shares you hold (pyramids get smaller)
ADD_RISK_PCT = 0.5              # the add may risk at most this % of the portfolio value if the stop is hit
MAX_POSITION_PCT = ns.MAX_POSITION_PCT   # a stock stays under this % of the portfolio value after the add
EXTENDED_PCT = ns.EXTENDED_PCT  # a breakout more than this % above its breakout price is too late
STRETCH_PCT = 25.0              # more than this % above the 50-day average = stretched (no add; trim is your choice)
RESISTANCE_ROOM_PCT = 3.0       # need at least this much room below the nearest resistance band
PULLBACK_TOUCH_PCT = 1.0        # a "touch" of the 50-day average = low within this % above it
BREAKOUT_VOLUME = 1.2           # breakout volume vs its 50-day average
MIN_DAYS = 210                  # fewer days of prices than this: no prompt
# ------------------------------------------------------------------------------

COLORS = {"EXIT": "#b22222", "WARNING": "#d9730d", "ADD": "#1b7f3b", "HOLD": "#555555", "NO DATA": "#777777"}
RANK = {"EXIT": 0, "WARNING": 1, "ADD": 2, "HOLD": 3, "NO DATA": 4}


def pill(action):
    return (f"<span style='background:{COLORS.get(action, '#555')};color:white;padding:3px 12px;border-radius:6px;"
            f"font-size:19px;white-space:nowrap'>{action}</span>")


# ------------------------------- SIGNALS --------------------------------------
def _flip_state(ind):
    """Direction, line value, age of the latest flip and the swing set-up for it, from one timeframe's indicators."""
    dr, st = ind["dr"].values, ind["st"].values
    d = int(dr[-1])
    fl = np.where(np.diff(dr) != 0)[0]
    k = int(fl[-1]) + 1 if len(fl) else None
    sw = ha.swing_setup(ind["ha"], st, dr, k, ind["atr"].values) if (k is not None and d != 0) else None
    line = float(st[-1]) if np.isfinite(st[-1]) else None
    return {"dir": d, "line": line, "age": None if k is None else len(dr) - 1 - k, "sw": sw}


def _tf_text(name, f):
    if f is None:
        return f"{name}: not enough history"
    if f["dir"] == 0:
        return f"{name}: not enough history"
    colour = "green" if f["dir"] == 1 else "red"
    age = "" if f["age"] is None else f", flipped {f['age']} candle(s) ago"
    return f"{name} Supertrend {colour} at {f['line']:,.2f}{age}"


def evaluate(df, bench, pos=None, portfolio_value=None):
    """Prompt for one holding. pos: dict with qty, avg_cost, stop (None for a hypothetical holding: cost and sizing
    conditions are skipped, used by replay). Returns a dict; action is EXIT / WARNING / ADD / HOLD / NO DATA."""
    import alerts as al
    import candle_view as cv

    out = {"action": "NO DATA", "reasons": [], "checklist": [], "notes": [], "add": None, "signals": [], "last": None,
           "day": None, "stop_suggest": None}
    daily, _ = cv.drop_partial(cv.naive(df))
    n = len(daily)
    if n < MIN_DAYS:
        out["reasons"].append(f"Only {n} days of prices; about {MIN_DAYS} are needed.")
        return out
    ha.SUPERTREND_PERIOD, ha.SUPERTREND_MULT = hub_settings.supertrend()
    close, high, low = (daily[c].astype(float) for c in ("Close", "High", "Low"))
    vol = daily["Volume"].astype(float)
    last = float(close.iloc[-1])
    out["last"], out["day"] = last, str(daily.index[-1].date())
    stop = pos.get("stop") if pos else None

    ind_d = ha.compute(daily)
    D = _flip_state(ind_d)
    W = None
    try:
        weekly = ns.resample_ohlc(daily, "W")
        done = weekly.iloc[:-1] if cv.is_forming("W", weekly, daily.index[-1]) else weekly
        if len(done) >= 30:
            W = _flip_state(ha.compute(done))
    except Exception:  # noqa: BLE001
        W = None
    out["signals"] = [_tf_text("Daily", D), _tf_text("Weekly (last completed)", W)]
    sma50, sma200 = close.rolling(50).mean(), close.rolling(200).mean()
    ev = al.evaluate(df, bench, stop)

    # ------------------------- exit side -------------------------
    exit_why, warn_why = [], []
    if ev["tier"] == "EXIT":
        exit_why.append(ev["reasons"][0] if ev["score"] == 99 else
                        f"Exit alert: {ev['pattern']} confirmed by a close below its low, score {ev['score']} "
                        f"(see python alerts.py check).")
    elif ev["tier"] == "WARNING":
        warn_why.append(f"Bearish candle formation ({ev['pattern']}, {ev['pattern_date']}) with other warning signs, score "
                        f"{ev['score']}. A close below {ev['confirm_level']:,.2f} confirms it; above {ev['cancel_level']:,.2f} cancels it.")
    d_red = D["dir"] == -1
    if d_red:
        sw = D["sw"]
        if sw and sw["state"] == "ENTRY":
            since = (f"flipped {D['age']} candle(s) ago" if D["age"] <= 6 else f"red for {D['age']} candles, so this exit is long overdue")
            exit_why.append(f"Daily Supertrend is red and {len(sw['seq'])} bearish Heikin Ashi candles have formed below the line: "
                            f"the method's exit ({since}).")
        elif sw and sw["state"] == "WAIT":
            warn_why.append("Daily Supertrend just turned red; the method exits on the 2nd or 3rd bearish candle below the line. "
                            "Watch the next candle.")
        else:
            warn_why.append("Daily Supertrend is red, but the confirming bearish candles did not form cleanly "
                            f"({sw['broke'] if sw else 'no detail'}). A weaker signal: check the chart.")
    if W and W["dir"] == -1:
        (exit_why if d_red else warn_why).append(
            "Weekly Supertrend is red" + (" and the daily one is red too: both timeframes agree." if d_red
                                          else " while the daily is still green: the bigger trend has turned down."))
    if last < float(sma200.iloc[-1]):
        warn_why.append(f"Price {last:,.2f} is below the 200-day average {float(sma200.iloc[-1]):,.2f}: the long-term trend is broken.")

    # ------------------------- add side (checklist) -------------------------
    items = []

    def check(label, ok, detail):
        items.append((label, bool(ok), detail))

    avg = pos.get("avg_cost") if pos else None
    if avg:
        gain = (last / avg - 1) * 100
        check("In profit", gain >= MIN_PROFIT_TO_ADD_PCT, f"{gain:+.1f}% vs your average cost {avg:,.2f} (needs {MIN_PROFIT_TO_ADD_PCT:g}%+)")
    rising = bool(n > 221 and sma200.iloc[-1] > sma200.iloc[-21])
    a50, a200 = last > sma50.iloc[-1], last > sma200.iloc[-1]
    check("Trend", a50 and a200 and rising,
          f"{'above' if a50 else 'below'} the 50-day, {'above' if a200 else 'below'} the 200-day, 200-day {'rising' if rising else 'not rising'}")
    check("Supertrend green", D["dir"] == 1 and bool(W and W["dir"] == 1),
          f"daily {'green' if D['dir'] == 1 else 'red'}, weekly {'n/a' if W is None else 'green' if W['dir'] == 1 else 'red'}")
    base = daily.iloc[-ns.PIVOT_DAYS - 1:-1]
    pivot = float(base["High"].max())
    vr = float(vol.iloc[-5:].mean() / vol.iloc[-51:-1].mean()) if vol.iloc[-51:-1].mean() else 0.0
    brk = last > pivot * 1.005 and last <= pivot * (1 + EXTENDED_PCT / 100) and vr >= BREAKOUT_VOLUME
    touched = bool((low.iloc[-5:] <= sma50.iloc[-5:] * (1 + PULLBACK_TOUCH_PCT / 100)).any())
    ha_bull = float(ind_d["ha"]["Close"].iloc[-1]) > float(ind_d["ha"]["Open"].iloc[-1])
    pull = touched and last > sma50.iloc[-1] and last > float(high.iloc[-2]) and ha_bull
    why_trig = (f"breakout above {pivot:,.2f} on volume {vr:.1f}x average" if brk else
                f"pullback bounce off the 50-day average {float(sma50.iloc[-1]):,.2f}" if pull else
                f"none: price {last:,.2f} vs 50-day high {pivot:,.2f} (volume {vr:.1f}x); "
                f"{'touched' if touched else 'has not touched'} the 50-day average in 5 days"
                f"{'' if ha_bull else ', Heikin Ashi candle not bullish'}")
    check("Trigger", brk or pull, why_trig)
    ext = (last / float(sma50.iloc[-1]) - 1) * 100
    check("Not stretched", ext <= STRETCH_PCT, f"{ext:+.0f}% from the 50-day average (limit {STRETCH_PCT:g}%)")
    try:
        _, _, res = cv.sr_levels(daily, last)
    except Exception:  # noqa: BLE001
        res = None
    if res:
        room = (res["low"] / last - 1) * 100
        check("Room above", room >= RESISTANCE_ROOM_PCT, f"resistance band {res['low']:.0f}-{res['high']:.0f} is {room:.1f}% above (needs {RESISTANCE_ROOM_PCT:g}%+)")
    else:
        check("Room above", True, "no resistance band close above")
    no_warn = not exit_why and not warn_why
    check("No exit warning", no_warn, "nothing is firing" if no_warn else "see the reasons above")

    line = D["line"] if D["dir"] == 1 else None
    stops = [v for v in (line, stop) if v]
    protect = max(stops) if stops else None
    valid_stop = bool(protect and protect < last)
    check("Valid stop", valid_stop,
          f"stop for the whole position {protect:,.2f} ({(1 - protect / last) * 100:.1f}% below the price)" if valid_stop
          else "no Supertrend line or stop below the price")
    add = None
    if pos and portfolio_value and valid_stop:
        risk = last - protect
        q_hold = float(pos["qty"])
        by_risk = int(portfolio_value * ADD_RISK_PCT / 100 // risk)
        by_frac = int(q_hold * ADD_MAX_FRACTION)
        by_cap = int(max(portfolio_value * MAX_POSITION_PCT / 100 - q_hold * last, 0) // last)
        q = max(min(by_risk, by_frac, by_cap), 0)
        limit = min((by_risk, "risk"), (by_frac, "half the shares held"), (by_cap, "the position cap"))[1]
        check("Size", q >= 1, f"{q} share(s) fit (risk limit {by_risk}, half of holding {by_frac}, position cap {by_cap}; "
                              f"limited by {limit})")
        if q >= 1:
            new_qty = q_hold + q
            new_avg = (q_hold * avg + q * last) / new_qty
            add = {"qty": q, "price": last, "cost": q * last, "stop": protect, "loss_on_add": q * risk,
                   "new_avg": new_avg, "new_weight": new_qty * last / portfolio_value * 100,
                   "result_at_stop": (protect / new_avg - 1) * 100}
    out["checklist"], out["add"] = items, None

    # ------------------------- decide -------------------------
    if exit_why:
        out["action"], out["reasons"] = "EXIT", exit_why + warn_why
    elif warn_why:
        out["action"], out["reasons"] = "WARNING", warn_why
    elif all(ok for _, ok, _ in items):
        out["action"] = "ADD"
        out["add"] = add
        out["reasons"] = [f"Every pyramiding condition holds; trigger: {why_trig}."]
    else:
        out["action"] = "HOLD"
        miss = [lab for lab, ok, _ in items if not ok]
        out["reasons"] = ["No exit signal. " + (f"Add-conditions not met: {', '.join(miss)}." if miss else "")]
    if out["action"] in ("HOLD", "ADD") and line and line < last and (stop is None or line > stop * 1.005):
        res_txt = f" (that would exit at {(line / avg - 1) * 100:+.1f}% vs your average cost)" if avg else ""
        out["stop_suggest"] = line
        out["notes"].append(f"Stop idea: the daily Supertrend line {line:,.2f} is {'above your stop ' + format(stop, ',.2f') if stop else 'a place for a stop'}"
                            f"{res_txt}. It moves up with the trend.")
    if ext > STRETCH_PCT and out["action"] in ("HOLD", "ADD"):
        out["notes"].append(f"Stretched: {ext:.0f}% above the 50-day average. Some traders take part profits here; that is your choice, "
                            "not a signal from this tool.")
    return out


# ------------------------------- HTML (portfolio report) ----------------------
def action_html(act):
    if not act:
        return ""
    lis = "".join(f"<li>{t}</li>" for t in act["reasons"])
    chk = "".join(f"<li class='{'pos' if ok else 'neg'}'>{'&#10004;' if ok else '&#10008;'} <b>{lab}:</b> {det}</li>"
                  for lab, ok, det in act["checklist"])
    sig = "<br>".join(act["signals"])
    add = ""
    if act["add"]:
        a = act["add"]
        add = (f"<p><b>If you add:</b> about <b>{a['qty']:g} shares</b> (about Rs {a['cost']:,.0f}) at {a['price']:,.2f}; stop for the "
               f"whole position {a['stop']:,.2f}. The new shares would lose about <b class='neg'>Rs {a['loss_on_add']:,.0f}</b> if the stop "
               f"is hit; new average cost {a['new_avg']:,.2f}, so the position would exit at {a['result_at_stop']:+.1f}% vs that cost. "
               f"It would be {a['new_weight']:.1f}% of your portfolio.</p>")
    notes = "".join(f"<li>{t}</li>" for t in act["notes"])
    return (f"<div class='box'><b>Action prompt: {pill(act['action'])}</b> &nbsp;<span class='small'>(screening prompt, not advice)</span>"
            f"<ul>{lis}</ul><p class='small'>{sig}</p>{add}"
            f"<details><summary>Pyramiding checklist</summary><ul>{chk}</ul></details>"
            + (f"<ul>{notes}</ul>" if notes else "") + "</div>")


# ------------------------------- COMMANDS -------------------------------------
def _prepare():
    import portfolio as pf

    positions, _ = pf.build_positions()
    if not positions:
        sys.exit("No holdings yet. Run: python portfolio.py import FILE")
    syms = [p["symbol"] for p in positions if p["resolved"]]
    print(f"Downloading prices for {len(syms)} holdings ...")
    prices = pf.fetch_prices(syms)
    bench = pf.fetch_bench()
    pv = 0.0
    for p in positions:
        df = prices.get(p["symbol"])
        px = float(df["Close"].iloc[-1]) if df is not None else p["broker_ltp"]
        p["_px"] = px
        pv += p["qty"] * px if px else 0.0
    return positions, prices, bench, pv


def run_all(positions, prices, bench, pv):
    out = {}
    for p in positions:
        df = prices.get(p["symbol"])
        if df is None:
            continue
        try:
            out[p["symbol"]] = evaluate(df, bench, {"qty": p["qty"], "avg_cost": p["avg_cost"], "stop": p["stop"]}, pv)
        except Exception as e:  # noqa: BLE001
            print(f"  {p['symbol']}: could not be checked ({e}).")
    return out


def cmd_check(_args):
    positions, prices, bench, pv = _prepare()
    res = run_all(positions, prices, bench, pv)
    if not res:
        sys.exit("Nothing could be checked.")
    print(f"\n{'Prompt':<9} {'Stock':<12} {'Latest':>10}  Why")
    for sym, r in sorted(res.items(), key=lambda kv: RANK[kv[1]["action"]]):
        why = (r["reasons"][0] if r["reasons"] else "")[:105]
        print(f"{r['action']:<9} {sym:<12} {r['last'] or 0:>10,.2f}  {why}")
        if r["action"] == "ADD" and r["add"]:
            a = r["add"]
            print(f"{'':<9} -> add about {a['qty']:g} shares, stop {a['stop']:,.2f}, risk Rs {a['loss_on_add']:,.0f}")
    print("\nScreening prompts, not advice. Details: python position_actions.py explain SYMBOL")


def cmd_explain(args):
    if not args:
        sys.exit("Usage: python position_actions.py explain SYMBOL")
    sym = args[0].upper()
    positions, prices, bench, pv = _prepare()
    p = next((x for x in positions if x["symbol"] == sym), None)
    if p is None or prices.get(sym) is None:
        sys.exit(f"{sym} is not in your holdings (or has no price data).")
    r = evaluate(prices[sym], bench, {"qty": p["qty"], "avg_cost": p["avg_cost"], "stop": p["stop"]}, pv)
    print(f"\n{sym}: {r['action']}  (latest {r['last']:,.2f}, close of {r['day']})")
    for t in r["reasons"] + r["signals"]:
        print("  - " + t)
    print("\nPyramiding checklist:")
    for lab, ok, det in r["checklist"]:
        print(f"  [{'x' if ok else ' '}] {lab}: {det}")
    if r["add"]:
        a = r["add"]
        print(f"\nIf you add: {a['qty']:g} shares (about Rs {a['cost']:,.0f}), stop {a['stop']:,.2f}, "
              f"loss on the new shares if stopped about Rs {a['loss_on_add']:,.0f}, new average {a['new_avg']:,.2f}.")
    for t in r["notes"]:
        print("  * " + t)


def cmd_replay(args):
    if not args:
        sys.exit("Usage: python position_actions.py replay SYMBOL [DAYS]")
    import candle_view as cv
    import portfolio as pf

    sym, days = args[0].upper(), int(args[1]) if len(args) > 1 else 250
    df = pf.fetch_prices([sym]).get(sym)
    if df is None:
        sys.exit(f"No price data for {sym}.")
    df, bench = cv.naive(df), cv.naive(pf.fetch_bench())
    print(f"{sym}: prompts replayed over the last {days} trading days (your cost and stop are ignored, so ADD needs only the "
          "price conditions).\n")
    prev, changes = None, 0
    for end in df.index[-days:]:
        r = evaluate(df.loc[:end].iloc[-420:], bench.loc[:end], None)
        if r["action"] != prev and r["action"] != "NO DATA":
            changes += 1
            why = (r["reasons"][0] if r["reasons"] else "")[:90]
            print(f"  {end.date()}  {r['action']:<8} close {r['last']:>10,.2f}  {why}")
        prev = r["action"]
    print(f"\n{changes} change(s) in {days} days. Check each date on the chart: did price keep going after ADD, and did EXIT "
          "come early enough or whipsaw?")


def run_cmd(args):
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0].lower(), list(args[1:])
    {"check": cmd_check, "explain": cmd_explain, "replay": cmd_replay}.get(cmd, lambda _a: print(__doc__))(rest)


if __name__ == "__main__":
    run_cmd(sys.argv[1:])
