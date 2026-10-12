#!/usr/bin/env python3
"""
Exit alerts for the stocks you actually hold (Telegram)

What it does
  After the market closes it looks at the newest COMPLETED daily candle of every holding in portfolio.py
  and looks for a bearish formation (bearish engulfing, evening star, dark cloud cover, three black
  crows). One formation alone is a weak signal, so it adds up the other signs of trouble and sends a
  tiered message:

     WATCH    the formation appeared and nothing else agrees yet (look at the chart, do nothing).
              Shown on screen only; set SEND_WATCH = True to get these on Telegram too.
     WARNING  the formation plus at least one more sign (or a later close below its low)
     EXIT     the formation was confirmed by a close below its low AND enough other signs agree,
              or your own stop-loss was touched

  Each message says what fired, your result on the holding, your stop, the price that CONFIRMS the
  warning and the price that CANCELS it. The same alert is never sent twice; you hear again only when
  the tier gets worse, a new formation appears, or an EXIT is still unresolved (reminder every few days).

Signs counted (points in brackets; edit the numbers at the top of the file)
  closed below your last-seen candle low after the formation (2) | below the 50-day average (1)
  closed below the support band that was under the price at the formation (2)
  a down day on volume at least 1.5x its 50-day average since the formation (1)
  last completed WEEKLY candle also shows a bearish formation (2) | lagging the Nifty over 3 months (1)
  formation appeared at a resistance band (1) | after a 20%+ run in the 40 days before it (1)
  Your stop touched is always EXIT. Thresholds: WARNING at 2 points, EXIT needs the confirming close and 4 points.

Commands
  python alerts.py check            look at all holdings, send new alerts to Telegram (or print them if
                                    Telegram is not set up yet; nothing is marked as sent in that case)
  python alerts.py check --dry      print what would be sent, change nothing
  python alerts.py status           the current tier of every holding that has one
  python alerts.py replay SYMBOL [DAYS]   show when the rules WOULD have fired on that stock over the last
                                    DAYS (default 250) trading days: use it to judge how noisy the rules are
  python alerts.py setup            step-by-step Telegram set-up
  python alerts.py chatid           find your chat id after you have sent your bot a message
  python alerts.py test             send a test message

Telegram secrets live in environment variables, never in this file:
  TELEGRAM_BOT_TOKEN   and   TELEGRAM_CHAT_ID

Scheduling (Windows): run after 4 pm India time on trading days. In PowerShell (one line, edit the path):
  schtasks /Create /TN "ExitAlerts" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:45 /TR "python C:\\path\\to\\alerts.py check"
The PC must be on and its clock set to India time. On a market holiday there is no new candle, so the run
does nothing. Run  python portfolio.py import FILE  now and then so quantities stay current.

Honest limits
  * End-of-day only: it cannot catch an intraday fall until the day's candle is complete.
  * Bearish formations fail often. The scoring is a starting point: use replay to see how it behaves
    before you let it move real money, and treat every message as a prompt to look, not an instruction.
  * Gaps can carry a price straight through your stop; the message shows the close, not your fill.
"""

import csv
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import nse_screener as ns
import portfolio as pf

# ----------------------------- SETTINGS (edit me) -----------------------------
EXIT_PATTERNS = ("Bearish engulfing", "Evening star", "Dark cloud cover", "Three black crows")
PATTERN_WINDOW = 3          # a formation counts for this many candles after it appeared (0 = today only)
WARN_SCORE = 2              # points for WARNING
EXIT_SCORE = 4              # points for EXIT (also needs a close below the formation candle's low)
VOLUME_MULT = 1.5           # "heavy selling" = down day with volume this many times the 50-day average
RUN_UP_PCT = 20.0           # "after a big run" = this much gain in the 40 days before the formation
SEND_WATCH = False          # True = also send the weakest tier (WATCH) to Telegram; it fires often
EXIT_REMINDER_DAYS = 3      # repeat an unresolved EXIT message after this many days
STOP_LOOKBACK = 5           # candles checked for a touch of your stop (covers a missed run)
STATE_FILE = pf.DATA_DIR / "alerts_state.json"
LOG_FILE = pf.DATA_DIR / "alerts_log.csv"
MESSAGE_LIMIT = 3800        # Telegram's limit is 4096 characters per message
# ------------------------------------------------------------------------------

RANK = {"WATCH": 1, "WARNING": 2, "EXIT": 3}
ICON = {"WATCH": "\U0001F7E1", "WARNING": "\U0001F7E0", "EXIT": "\U0001F534"}


# ------------------------------- THE RULES ------------------------------------
def evaluate(df, bench, stop=None):
    """Judge one stock from its daily bars. Returns a dict (tier is None when nothing fired)."""
    import candle_view as cv

    daily, _ = cv.drop_partial(cv.naive(df))
    n = len(daily)
    out = {"tier": None, "key": "", "pattern": None, "pattern_date": None, "score": 0, "reasons": [],
           "confirm_level": None, "cancel_level": None, "last": None, "day": None}
    if n < 60:
        return out
    close, high, low, vol, opn = (daily[c].astype(float) for c in ("Close", "High", "Low", "Volume", "Open"))
    last = float(close.iloc[-1])
    out["last"], out["day"] = last, str(daily.index[-1].date())

    # --- your own stop: always EXIT -------------------------------------------------
    if stop:
        recent = daily.iloc[-STOP_LOOKBACK:]
        hit = recent[recent["Low"].astype(float) <= stop]
        if len(hit):
            d = hit.index[0].date()
            closed_below = last <= stop
            out.update(tier="EXIT", key=f"stop {d}", score=99)
            out["reasons"].append(
                f"Your stop {stop:,.2f} was touched on {d:%d %b} (latest close {last:,.2f}"
                + (", still below it)." if closed_below else ", back above it).")
                + " A gap can fill you well below the stop; check your broker.")
            return out

    # --- the formation -------------------------------------------------------------
    found = [p for p in cv.detect(daily) if p["bias"] < 0 and p["name"] in EXIT_PATTERNS
             and n - 1 - p["i"] <= PATTERN_WINDOW]
    if not found:
        return out
    p = max(found, key=lambda q: (q["i"], cv.PATTERNS[q["name"]][1]))
    i = p["i"]
    pdate = daily.index[i].date()
    age = n - 1 - i
    out.update(pattern=p["name"], pattern_date=str(pdate), key=f"{p['name']} {pdate}")
    c_low, p_high = float(low.iloc[i]), float(p["high"])
    out["confirm_level"], out["cancel_level"] = c_low, p_high
    when = "today's candle" if age == 0 else f"{pdate:%d %b}"
    out["reasons"].append(f"{p['name']} on {when}: {cv.PATTERNS[p['name']][2]}")

    score = 0

    def add(points, text):
        nonlocal score
        score += points
        out["reasons"].append(f"+{points}: {text}")

    confirmed = bool((close.iloc[i + 1:] < c_low).any())
    if confirmed:
        add(2, f"a later candle closed below the formation candle's low {c_low:,.2f}.")
    sma50 = float(close.rolling(50).mean().iloc[-1])
    if last < sma50:
        add(1, f"price {last:,.2f} is below its 50-day average {sma50:,.2f}.")
    try:
        _, sup, res = cv.sr_levels(daily.iloc[:i + 1], float(close.iloc[i]))
        if sup and last < sup["low"]:
            add(2, f"price closed below the support band {sup['low']:.0f}-{sup['high']:.0f} "
                   f"({sup['touches']} touches) that was under it at the formation.")
        if res and p_high >= res["low"] * 0.99:
            add(1, f"the formation appeared at a resistance band {res['low']:.0f}-{res['high']:.0f}.")
    except Exception:  # noqa: BLE001
        pass
    avg_vol = float(vol.iloc[max(0, i - 50):i].mean()) if i > 5 else 0.0
    since = daily.iloc[i:]
    heavy = since[(since["Close"] < since["Open"]) & (since["Volume"] >= VOLUME_MULT * avg_vol)] if avg_vol else since.iloc[0:0]
    if len(heavy):
        add(1, f"down day on heavy volume ({heavy['Volume'].max() / avg_vol:.1f}x the average) since the formation.")
    try:
        weekly = ns.resample_ohlc(daily, "W")
        done = weekly.iloc[:-1] if cv.is_forming("W", weekly, daily.index[-1]) else weekly
        wf = [q for q in cv.detect(done) if q["i"] == len(done) - 1 and q["bias"] < 0]
        if wf:
            add(2, f"the last completed weekly candle also shows a bearish formation ({wf[0]['name']}).")
    except Exception:  # noqa: BLE001
        pass
    if n > 64 and len(bench) > 64:
        r3, b3 = last / float(close.iloc[-64]) - 1, float(bench.iloc[-1] / bench.iloc[-64] - 1)
        if r3 < b3:
            add(1, f"lagging the Nifty over 3 months ({r3 * 100:+.0f}% vs {b3 * 100:+.0f}%).")
    f0 = p["first"]
    if f0 >= 41 and float(close.iloc[f0 - 1]) / float(close.iloc[f0 - 41]) - 1 >= RUN_UP_PCT / 100:
        add(1, f"it had run up {RUN_UP_PCT:.0f}%+ in the 40 days before the formation.")

    out["score"] = score
    if confirmed and score >= EXIT_SCORE:
        out["tier"] = "EXIT"
    elif score >= WARN_SCORE:
        out["tier"] = "WARNING"
    else:
        out["tier"] = "WATCH"
    return out


# ------------------------------- MESSAGES -------------------------------------
def holding_line(pos, last):
    held = " + ".join(f"{b.title()} {q:g}" for b, q in pos["brokers"].items())
    value = pos["qty"] * last
    pnl = value - pos["cost"]
    return (f"{pos['qty']:g} shares ({held}). Close {last:,.2f}, average cost {pos['avg_cost']:,.2f}, "
            f"result Rs {pf.inr(pnl)} ({pnl / pos['cost'] * 100:+.1f}%) before charges and tax.")


def build_message(sym, pos, ev, stop):
    head = f"{ICON[ev['tier']]} {ev['tier']}  {sym}   (as of the close of {ev['day']})"
    lines = [head, holding_line(pos, ev["last"]), ""]
    lines += [("- " + r) if not r.startswith("+") else ("  " + r) for r in ev["reasons"]]
    if ev["pattern"] and ev["score"] < 99:
        lines.append(f"Score {ev['score']} (WARNING from {WARN_SCORE}, EXIT from {EXIT_SCORE} with a confirming close).")
    lines.append("")
    if stop:
        lines.append(f"Your stop: {stop:,.2f} ({(ev['last'] / stop - 1) * 100:.1f}% below the close)." if ev["last"] > stop
                     else f"Your stop: {stop:,.2f} (the close is at or below it).")
    else:
        lines.append("You have no stop set for this holding (python portfolio.py stop SYMBOL PRICE).")
    if ev["confirm_level"] and ev["tier"] != "EXIT":
        lines.append(f"Confirms the warning: a close below {ev['confirm_level']:,.2f}.")
    if ev["cancel_level"]:
        lines.append(f"Cancels it: a close above {ev['cancel_level']:,.2f}.")
    lines.append("A prompt to look at the chart, not an instruction to sell.")
    return "\n".join(lines)


# ------------------------------- TELEGRAM -------------------------------------
def telegram_ready():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def _api(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def send_telegram(text):
    """True if Telegram accepted the message. Never raises."""
    try:
        chunks, cur = [], ""
        for block in text.split("\n\n\n"):
            if len(cur) + len(block) > MESSAGE_LIMIT and cur:
                chunks.append(cur)
                cur = ""
            cur += ("\n\n\n" if cur else "") + block
        chunks.append(cur)
        for c in chunks:
            res = _api("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=c[:4000])
            if not res.get("ok"):
                print(f"Telegram refused the message: {res}")
                return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Could not reach Telegram ({e}).")
        return False


# ------------------------------- STATE ----------------------------------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"last_data_date": "", "symbols": {}}


def save_state(state):
    pf.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def log_alert(sym, ev, sent):
    new = not LOG_FILE.exists()
    pf.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["logged_on", "data_date", "symbol", "tier", "pattern", "score", "sent", "reasons"])
        w.writerow([dt.date.today(), ev["day"], sym, ev["tier"], ev["pattern"] or "", ev["score"], sent,
                    " | ".join(ev["reasons"])])


def should_send(prev, ev, today):
    """Send when there is no earlier alert for this key, the tier got worse, or an EXIT is still open."""
    if not prev or prev.get("key") != ev["key"]:
        return True
    if RANK[ev["tier"]] > RANK.get(prev.get("tier"), 0):
        return True
    if ev["tier"] == "EXIT":
        return (today - dt.date.fromisoformat(prev["sent_on"])).days >= EXIT_REMINDER_DAYS
    return False


# ------------------------------- COMMANDS -------------------------------------
def analyse_all():
    """-> (positions, {symbol: evaluation}, last data day). Downloads prices."""
    positions, _ = pf.build_positions()
    if not positions:
        sys.exit("No holdings yet. Run: python portfolio.py import FILE")
    syms = [p["symbol"] for p in positions if p["resolved"]]
    print(f"Downloading prices for {len(syms)} holdings ...")
    prices = pf.fetch_prices(syms)
    bench = pf.fetch_bench()
    evs = {}
    for p in positions:
        df = prices.get(p["symbol"])
        if df is None:
            continue
        try:
            evs[p["symbol"]] = evaluate(df, bench, p["stop"])
        except Exception as e:  # noqa: BLE001
            print(f"  {p['symbol']}: could not be checked ({e}).")
    days = [e["day"] for e in evs.values() if e["day"]]
    return positions, evs, max(days) if days else None


def cmd_check(args):
    dry = "--dry" in args
    positions, evs, day = analyse_all()
    if day is None:
        sys.exit("No usable prices, so nothing was checked.")
    state = load_state()
    today = dt.date.today()
    if not dry and state.get("last_data_date") == day and "--force" not in args:
        print(f"The newest candle ({day}) was already checked. Nothing new (use --force to check again).")
        return
    bypos = {p["symbol"]: p for p in positions}
    firing = {s: e for s, e in evs.items() if e["tier"]}
    to_send = []
    quiet = [s for s, e in firing.items() if e["tier"] == "WATCH" and not SEND_WATCH]
    for sym, ev in sorted(firing.items(), key=lambda kv: -RANK[kv[1]["tier"]]):
        if ev["tier"] == "WATCH" and not SEND_WATCH:
            continue
        if should_send(state["symbols"].get(sym), ev, today):
            to_send.append((sym, ev, build_message(sym, bypos[sym], ev, bypos[sym]["stop"])))
    for sym in [s for s in state["symbols"] if s not in firing]:
        state["symbols"].pop(sym)          # signal gone: a future one will alert again

    print(f"\n{len(evs)} holdings checked, data to {day}. {len(firing)} with an active signal, "
          f"{len(to_send)} new alert(s).")
    if quiet:
        print("WATCH only (not sent; set SEND_WATCH = True to receive these): " + ", ".join(sorted(quiet)))
    if not to_send:
        if not dry:
            state["last_data_date"] = day
            save_state(state)
        return
    text = f"Exit alerts - close of {day}\n\n\n" + "\n\n\n".join(m for _, _, m in to_send)
    print("\n" + text.replace("\n\n\n", "\n\n" + "-" * 50 + "\n\n"))
    if dry:
        print("\n(dry run: nothing sent, nothing saved)")
        return
    if not telegram_ready():
        print("\nTelegram is not set up, so nothing was sent or marked as sent. Run: python alerts.py setup")
        return
    ok = send_telegram(text)
    for sym, ev, _ in to_send:
        log_alert(sym, ev, ok)
        if ok:
            state["symbols"][sym] = {"key": ev["key"], "tier": ev["tier"], "sent_on": str(today)}
    if ok:
        state["last_data_date"] = day
        save_state(state)
        print("\nSent to Telegram.")


def cmd_status(_args):
    state = load_state()
    if not state["symbols"]:
        print("No active alerts on record.")
        return
    for sym, s in sorted(state["symbols"].items(), key=lambda kv: -RANK[kv[1]["tier"]]):
        print(f"  {s['tier']:<8} {sym:<12} {s['key']}   (last sent {s['sent_on']})")
    print(f"\nLast candle processed: {state.get('last_data_date') or '-'}")


def cmd_replay(args):
    if not args:
        sys.exit("Usage: python alerts.py replay SYMBOL [DAYS]")
    sym, days = args[0].upper(), int(args[1]) if len(args) > 1 else 250
    df = pf.fetch_prices([sym]).get(sym)
    if df is None:
        sys.exit(f"No price data for {sym}.")
    bench = pf.fetch_bench()
    import candle_view as cv

    df = cv.naive(df)
    bench = cv.naive(bench)
    print(f"{sym}: rules replayed over the last {days} trading days (your stop is ignored).\n")
    prev, rows = None, 0
    for end in df.index[-days:]:
        part = df.loc[:end].iloc[-420:]
        ev = evaluate(part, bench.loc[:end], None)
        sig = (ev["tier"], ev["key"])
        if ev["tier"] and sig != prev:
            rows += 1
            print(f"  {end.date()}  {ev['tier']:<8} score {ev['score']:>2}  {ev['key']}   close {ev['last']:,.2f}")
        prev = sig
    print(f"\n{rows} alert-worthy changes in {days} days." if rows else "\nThe rules never fired in that period.")
    print("Check each date on the chart: did price keep falling afterwards, or recover?")


def cmd_setup(_args):
    print("""Telegram set-up (about 3 minutes, free):
  1. In Telegram, search for  @BotFather  and send:  /newbot   Choose a name and a username ending in 'bot'.
     BotFather replies with a TOKEN (a long text with a colon). Treat it like a password.
  2. Open a chat with your new bot and send it any message (for example: hi).
  3. In PowerShell, store the token (one time; open a NEW PowerShell window afterwards):
         setx TELEGRAM_BOT_TOKEN "paste-the-token-here"
  4. In the new window run:  python alerts.py chatid     It prints your chat id.
  5. Store it:  setx TELEGRAM_CHAT_ID "the-number"       then open another new PowerShell window.
  6. Run:  python alerts.py test        You should get a message on your phone.
  7. Schedule it (see the top of alerts.py) so  python alerts.py check  runs on weekdays at 16:45.
Never paste the token into a script or into a chat.""")


def cmd_chatid(_args):
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Run: python alerts.py setup")
    try:
        res = _api("getUpdates")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Could not reach Telegram ({e}).")
    chats = {}
    for u in res.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        c = m.get("chat")
        if c:
            chats[c["id"]] = c.get("first_name") or c.get("title") or ""
    if not chats:
        sys.exit("No messages found. Send your bot a message in Telegram first, then run this again.")
    for cid, name in chats.items():
        print(f"chat id {cid}   ({name})")


def cmd_test(_args):
    if not telegram_ready():
        sys.exit("Telegram is not set up. Run: python alerts.py setup")
    print("Sent." if send_telegram("Test from your trading hub: exit alerts are connected.") else "Not sent.")


def run_cmd(args):
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0].lower(), list(args[1:])
    {"check": cmd_check, "status": cmd_status, "replay": cmd_replay, "setup": cmd_setup,
     "chatid": cmd_chatid, "test": cmd_test}.get(cmd, lambda _a: print(__doc__))(rest)


if __name__ == "__main__":
    run_cmd(sys.argv[1:])
