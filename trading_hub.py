#!/usr/bin/env python3
"""
Trading hub: one file that runs everything

Start it with no arguments for a menu:
    python trading_hub.py

Or go straight to a task:
    python trading_hub.py screen                  screener, then candlestick view (opens report.html)
    python trading_hub.py screen TCS INFY         same, but the candlestick view covers only these
    python trading_hub.py lookup                  pick any stocks (type 3 letters to search) and analyse them
    python trading_hub.py lookup TCS INFY         analyse these stocks, nothing hidden
    python trading_hub.py paper add               log the top 5 picks from the latest screen (no real money)
    python trading_hub.py paper add LALPATHLAB    log specific stocks
    python trading_hub.py paper update            update every open paper trade with real prices
    python trading_hub.py paper show              show the paper log
    python trading_hub.py paper close SYMBOL      close or cancel a paper trade
    python trading_hub.py backtest                replay the rules on past years
    python trading_hub.py portfolio ...           your real holdings (import FILE, report, show, stop, cost, buy, sell, trades)
    python trading_hub.py alerts check            exit alerts for your holdings (check --dry, status, replay SYMBOL, setup, test)
    python trading_hub.py redeploy SYMBOL [QTY]   if I sold this holding, where could the money go (3 options)
    python trading_hub.py plan check              add / hold / exit prompts for my holdings (plan explain SYMBOL, plan replay SYMBOL)
    python trading_hub.py funds                   rank ETFs and gold, show my fund holdings (funds mf add/search/show/remove, watch SYMBOL)
    python trading_hub.py settings                show chart settings; settings supertrend 10 2 changes them; settings reset
    python trading_hub.py reports                 open the newest screener and lookup reports
    python trading_hub.py web                     the same things from a page in your browser

Keep this file in the same folder as nse_screener.py, candle_view.py, fundamentals.py,
stock_lookup.py, paper_tracker.py, portfolio.py, alerts.py, redeploy.py, funds.py, backtest.py, ha_chart.py, hub_settings.py, position_actions.py and hub_web.py. It replaces run_all.py.
"""

import datetime as dt
import shlex
import sys
import webbrowser
from pathlib import Path

OPEN_REPORT = True   # open report.html in your browser after a screen


def _run(task, *args):
    """Run one task. A task that stops itself (sys.exit with a message) does not close the hub."""
    try:
        task(*args)
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"\nStopped: {e.code}")
    except KeyboardInterrupt:
        print("\nCancelled.")


# ------------------------------- TASKS ----------------------------------------
def screen(symbols=()):
    import candle_view as cv
    import nse_screener as ns

    ns.OPEN_REPORT = False   # this file decides what opens, so you do not get two tabs
    cv.OPEN_REPORT = False
    print("=" * 60 + "\nSTEP 1 of 2: screening the market\n" + "=" * 60)
    ns.main()

    today = Path("output") / str(dt.date.today())
    if not (today / "results.csv").exists():
        sys.exit("The screener produced no results today, so there is nothing to draw.")

    print("\n" + "=" * 60 + "\nSTEP 2 of 2: candlestick view\n" + "=" * 60)
    old = sys.argv
    sys.argv = ["candle_view.py"] + list(symbols)   # candle_view reads its symbols from here
    try:
        cv.main()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"\nCandlestick step stopped: {e.code}")
    finally:
        sys.argv = old

    report = today / "report.html"
    print(f"\nDone. Screener report: {report.resolve()}")
    if (today / "candle_report.html").exists():
        print(f"Candlestick report:   {(today / 'candle_report.html').resolve()}")
    if OPEN_REPORT and report.exists():
        webbrowser.open(report.resolve().as_uri())


def lookup(symbols=()):
    import stock_lookup as sl

    old = sys.argv
    sys.argv = ["stock_lookup.py"] + list(symbols)
    try:
        sl.main()
    finally:
        sys.argv = old


def paper(args):
    import paper_tracker as pt

    if not args:
        print(pt.__doc__)
        return
    cmd, rest = args[0].lower(), list(args[1:])
    if cmd == "add":
        pt.cmd_add(rest)
    elif cmd == "update":
        pt.cmd_update()
    elif cmd == "show":
        pt.show()
    elif cmd == "close":
        pt.cmd_close(rest)
    else:
        print(pt.__doc__)


def portfolio(args):
    import portfolio as pf

    pf.run_cmd(list(args))


def alerts(args):
    import alerts as al

    al.run_cmd(list(args))


def redeploy(args):
    import redeploy as rd

    rd.run_cmd(list(args))


def plan(args):
    import position_actions as pa

    pa.run_cmd(list(args))


def funds(args):
    import funds as fu

    fu.run_cmd(list(args))


def settings(args):
    import hub_settings as hs

    hs.run_cmd(list(args))


def backtest():
    import backtest as bt

    bt.main()


def web():
    import hub_web

    hub_web.serve()


def reports():
    found = False
    for label, pattern in (("Screener report", "output/20*/report.html"),
                           ("Lookup report", "output/lookup_*/report.html")):
        files = sorted(Path().glob(pattern))
        if files:
            found = True
            print(f"{label}: {files[-1].resolve()}")
            webbrowser.open(files[-1].resolve().as_uri())
    if not found:
        print("No reports yet. Run a screen or a lookup first.")


# ------------------------------- MENU -----------------------------------------
MENU = """
==============================================
  TRADING HUB
==============================================
  1  Screen the market + candlestick view
  2  Analyse stocks I choose (nothing hidden)
  3  Paper trades: log the top picks
  4  Paper trades: update with latest prices
  5  Paper trades: show the log
  6  Paper trades: close or cancel one
  7  Backtest the rules on past years
  8  Open the newest reports
  9  Use the hub in my browser (web page)
 10  My real portfolio (import, report, stops, trades)
 11  Exit alerts (check, preview, replay, Telegram set-up)
 12  If I sold a holding, where could the money go?
 13  Chart settings (Supertrend period and multiplier)
 14  ETFs, mutual funds and gold (ranked list + my holdings)
 15  Add, hold or exit? Prompts for my holdings
  q  Quit
"""


def menu():
    while True:
        print(MENU)
        choice = input("Choose: ").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            return
        if choice == "1":
            _run(screen)
        elif choice == "2":
            _run(lookup)
        elif choice == "3":
            _run(paper, ["add"])
        elif choice == "4":
            _run(paper, ["update"])
        elif choice == "5":
            _run(paper, ["show"])
        elif choice == "6":
            sym = input("Symbol to close or cancel: ").strip()
            if sym:
                _run(paper, ["close", sym])
        elif choice == "7":
            _run(backtest)
        elif choice == "8":
            _run(reports)
        elif choice == "9":
            _run(web)
        elif choice == "10":
            print("Type a portfolio command, for example:  report   |   import C:\\path\\file.csv   |   show\n"
                  "                                        stop TCS 3600   |   buy TCS 5 3800   |   trades FILE icici")
            line = input("portfolio> ").strip() or "report"
            _run(portfolio, shlex.split(line, posix=False))
        elif choice == "11":
            print("Type an alerts command, for example:  check --dry   |   check   |   status\n"
                  "                                      replay TCS   |   setup   |   test")
            line = input("alerts> ").strip() or "check --dry"
            _run(alerts, shlex.split(line, posix=False))
        elif choice == "12":
            line = input("Holding to sell, and how much (for example:  NRBBEARING   or   NRBBEARING 50   or   50000): ").strip()
            if line:
                _run(redeploy, shlex.split(line, posix=False))
        elif choice == "13":
            _run(settings, [])
            print("\nTo change it type, for example:  supertrend 10 2   |   reset   (Enter to leave it)")
            line = input("settings> ").strip()
            if line:
                _run(settings, shlex.split(line, posix=False))
        elif choice == "14":
            print("Press Enter to rank the ETFs and gold and see your holdings, or type a command, for example:\n"
                  "  mf search parag flexi   |   mf add 122639 150 62.5   |   mf show   |   mf remove 122639\n"
                  "  watch HDFCGOLD \"Gold and silver\"   |   unwatch HDFCGOLD")
            line = input("funds> ").strip() or "report"
            _run(funds, shlex.split(line, posix=False))
        elif choice == "15":
            print("Press Enter for a prompt on every holding, or type:  explain TCS   |   replay TCS   |   replay TCS 500")
            line = input("plan> ").strip() or "check"
            _run(plan, shlex.split(line, posix=False))
        else:
            print("Please type a number from the menu, or q to quit.")


def main():
    if len(sys.argv) < 2:
        menu()
        return
    cmd, rest = sys.argv[1].lower(), sys.argv[2:]
    if cmd == "screen":
        _run(screen, [a.upper() for a in rest])
    elif cmd == "lookup":
        _run(lookup, rest)
    elif cmd == "paper":
        _run(paper, rest)
    elif cmd == "portfolio":
        _run(portfolio, rest)
    elif cmd == "alerts":
        _run(alerts, rest)
    elif cmd == "redeploy":
        _run(redeploy, rest)
    elif cmd == "plan":
        _run(plan, rest)
    elif cmd == "funds":
        _run(funds, rest)
    elif cmd == "settings":
        _run(settings, rest)
    elif cmd == "backtest":
        _run(backtest)
    elif cmd == "reports":
        _run(reports)
    elif cmd == "web":
        _run(web)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
