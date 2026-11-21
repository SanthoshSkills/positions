#!/usr/bin/env python3
"""
Trading hub in your browser

    python hub_web.py            (or: python trading_hub.py web)

Starts a small web page on YOUR computer only (http://127.0.0.1:8765) and opens it. From the page you can:
  * run the screener + candlestick view
  * search stocks (suggestions after 3 letters), pick several and analyse them
  * import your broker holdings files and open the portfolio report, set stops, preview or send exit alerts, see where sale proceeds could go
  * log, update and show paper trades, close one
  * rank ETFs and gold, see your fund holdings, add and track mutual funds (by scheme code)
  * run the backtest
  * set the Supertrend period and multiplier used by every Heikin Ashi chart (saved in hub_settings.json)
  * open any report (screener, candlesticks, lookups, paper, backtest)
Progress is shown live on the page. When a task finishes, a button opens its report.
Every stock in the reports has Google and Yahoo live-chart links (NSE data there can be delayed).

Nothing needs installing: it uses only Python's built-in web server. It is not reachable from other
computers, and pages from other websites cannot trigger tasks (a random token is required).
Press Ctrl+C in this window, or the Quit button on the page, to stop it.

Keep this file in the same folder as trading_hub.py and the other scripts.
"""

import yf_cloud_patch as _yf_patch; _yf_patch.apply()  # must be first: patches yfinance before any module imports it

import contextlib
import datetime as dt
import io
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import hub_settings
import trading_hub as hub
from origin import ORIGIN, with_origin

HOST = os.environ.get("HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
TOKEN = secrets.token_hex(16)
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "1") == "1"  # opens the page for you; set OPEN_BROWSER=0 to stop that

LOCK = threading.Lock()
JOBS = {}
STATE = {"current": None, "table": None}


# ------------------------------- JOBS -----------------------------------------
class Job:
    def __init__(self, name):
        self.id = secrets.token_hex(4)
        self.name = name
        self.text = ""
        self.status = "running"
        self.result = None
        self.started = time.time()


class Tee(io.TextIOBase):
    def __init__(self, job):
        self.job = job

    def write(self, s):
        with LOCK:
            self.job.text += s
        return len(s)

    def flush(self):
        pass


def newest(pattern, since):
    """Newest file matching the pattern that was written during this job, as a (url, target) pair."""
    files = [p for p in sorted(Path().glob(pattern)) if p.stat().st_mtime >= since - 1]
    return files[-1] if files else None


def _url(p):
    return "/files/" + p.as_posix()


def do_task(job, task, a):
    since = job.started
    if task == "screen":
        hub._run(hub.screen)
        p = newest("output/20*/report.html", since)
        if p:
            job.result = {"url": _url(p), "target": "screener_view", "label": "Open the screener report"}
    elif task == "lookup":
        hub._run(hub.lookup, a["symbols"])
        p = newest("output/lookup_*/report.html", since)
        if p:
            job.result = {"url": _url(p), "target": "screener_view", "label": "Open the lookup report"}
    elif task == "paper":
        hub._run(hub.paper, a["args"])
        p = newest("paper_report.html", since)
        if p:
            job.result = {"url": _url(p), "target": "_blank", "label": "Open the paper-trade report"}
    elif task == "portfolio":
        hub._run(hub.portfolio, a["args"])
        p = newest("output/portfolio/portfolio_report.html", since)
        if p:
            job.result = {"url": _url(p), "target": "_blank", "label": "Open the portfolio report"}
    elif task == "alerts":
        hub._run(hub.alerts, a["args"])
    elif task == "redeploy":
        hub._run(hub.redeploy, a["args"])
        p = newest("output/portfolio/redeploy_*.html", since)
        if p:
            job.result = {"url": _url(p), "target": "_blank", "label": "Open the where-could-the-money-go page"}
    elif task == "plan":
        hub._run(hub.plan, a["args"])
    elif task == "funds":
        hub._run(hub.funds, a["args"])
        if a["args"][:1] == ["report"]:
            p = newest("output/funds/funds_report.html", since)
            if p:
                job.result = {"url": _url(p), "target": "_blank", "label": "Open the ETFs, funds and gold report"}
    elif task == "backtest":
        hub._run(hub.backtest)
        p = newest("backtest_output/backtest_report.html", since)
        if p:
            job.result = {"url": _url(p), "target": "_blank", "label": "Open the backtest report"}


def run_job(job, task, a):
    out = Tee(job)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            do_task(job, task, a)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    with LOCK:
        job.status = "done"
        STATE["current"] = None


def start_job(body):
    task = body.get("task", "")
    syms = [str(s).strip().upper() for s in body.get("symbols", []) if str(s).strip()]
    price = str(body.get("price", "")).strip()
    if task == "screen":
        name, a = "Screen the market", {}
    elif task == "lookup":
        if not syms:
            return None, "Choose at least one stock first."
        name, a = "Stock lookup", {"symbols": syms}
    elif task == "backtest":
        name, a = "Backtest", {}
    elif task in ("portfolio_report", "portfolio_show", "portfolio_import", "portfolio_stop"):
        sub = task.split("_")[1]
        args = [sub]
        if sub == "import":
            path = str(body.get("path", "")).strip().strip('"')
            if not path:
                return None, "Paste the full path of the broker file first."
            args += [path]
        elif sub == "stop":
            if not syms or not price:
                return None, "Type the symbol and the stop price."
            args += [syms[0], price]
        name, task, a = "Portfolio: " + sub, "portfolio", {"args": args}
    elif task == "redeploy":
        if not syms:
            return None, "Type the symbol of the holding you are thinking of selling."
        name, a = "Where could the money go", {"args": [syms[0]] + ([price] if price else [])}
    elif task in ("plan_check", "plan_explain"):
        if task == "plan_explain":
            if not syms:
                return None, "Type the symbol of the holding to explain."
            args = ["explain", syms[0]]
        else:
            args = ["check"]
        name, task, a = "Add / hold / exit: " + task.split("_")[1], "plan", {"args": args}
    elif task in ("funds_report", "funds_mfshow", "funds_mfadd", "funds_mfsearch", "funds_mfremove", "funds_watch"):
        sub = task.split("_", 1)[1]
        code = syms[0] if syms else ""
        if sub == "report":
            args = ["report"]
        elif sub == "mfshow":
            args = ["mf", "show"]
        elif sub == "mfadd":
            units = str(body.get("units", "")).strip()
            if not code or not units or not price:
                return None, "Type the scheme code, the number of units (0 to just watch) and your average NAV."
            args = ["mf", "add", code, units, price]
        elif sub == "mfsearch":
            text = str(body.get("text", "")).strip()
            if len(text) < 3:
                return None, "Type at least 3 letters of the fund name."
            args = ["mf", "search"] + text.split()
        elif sub == "watch":
            if not code:
                return None, "Type the ETF symbol to add to the ranked list."
            args = ["watch", code] + ([str(body.get("text", "")).strip()] if str(body.get("text", "")).strip() else [])
        else:
            if not code:
                return None, "Type the scheme code to remove."
            args = ["mf", "remove", code]
        name, task, a = "ETFs, funds and gold: " + sub, "funds", {"args": args}
    elif task in ("alerts_preview", "alerts_send", "alerts_status"):
        sub = task.split("_")[1]
        name, task, a = "Exit alerts: " + sub, "alerts", {"args": {"preview": ["check", "--dry"], "send": ["check"],
                                                                   "status": ["status"]}[sub]}
    elif task in ("paper_add", "paper_update", "paper_show", "paper_close"):
        sub = task.split("_")[1]
        args = [sub]
        if sub == "add":
            args += syms
        elif sub == "close":
            if not syms:
                return None, "Type the symbol to close."
            args += [syms[0]] + ([price] if price else [])
        name, task, a = "Paper trades: " + sub, "paper", {"args": args}
    else:
        return None, "Unknown task."
    with LOCK:
        if STATE["current"] is not None:
            return None, "Another task is still running. Wait for it to finish."
        job = Job(name)
        JOBS[job.id] = job
        STATE["current"] = job.id
    threading.Thread(target=run_job, args=(job, task, a), daemon=True).start()
    return job, None


# ------------------------------- DATA FOR THE PAGE ----------------------------
def get_table():
    if STATE["table"] is None:
        import stock_lookup as sl

        try:
            STATE["table"] = sl.load_symbols()
        except Exception:  # noqa: BLE001
            import pandas as pd

            STATE["table"] = pd.DataFrame({"Symbol": [], "Name": []})
    return STATE["table"]


def search(q):
    import stock_lookup as sl

    found = sl.find(get_table(), q)
    return [{"symbol": r["Symbol"].upper(), "name": r["Name"]} for _, r in found.iterrows()]


def stamp(p):
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %Y %H:%M")


def list_reports():
    items = []
    for p in reversed(sorted(Path().glob("output/20*/report.html"))[-5:]):
        links = [{"text": "Screener report", "url": _url(p), "target": "screener_view"}]
        if (p.parent / "results.csv").exists():
            links.append({"text": "Download CSV", "url": "/download?file=" + (p.parent / "results.csv").as_posix(),
                          "target": "_self"})
        cr = p.parent / "candle_report.html"
        if cr.exists():
            links.append({"text": "Candlestick report", "url": _url(cr), "target": "candle_view"})
        items.append({"label": f"Screen of {p.parent.name}", "when": stamp(p), "links": links})
    for p in reversed(sorted(Path().glob("output/lookup_*/report.html"))[-5:]):
        links = [{"text": "Lookup report", "url": _url(p), "target": "screener_view"}]
        if (p.parent / "lookup_results.csv").exists():
            links.append({"text": "Download CSV", "url": "/download?file=" + (p.parent / "lookup_results.csv").as_posix(),
                          "target": "_self"})
        cr = p.parent / "candle_report.html"
        if cr.exists():
            links.append({"text": "Candlestick report", "url": _url(cr), "target": "candle_view"})
        items.append({"label": "Lookup " + p.parent.name.replace("lookup_", ""), "when": stamp(p), "links": links})
    for label, f in (("My portfolio", "output/portfolio/portfolio_report.html"),
                     ("ETFs, funds and gold", "output/funds/funds_report.html"),
                     ("Paper trades", "paper_report.html"),
                     ("Backtest", "backtest_output/backtest_report.html")):
        p = Path(f)
        if p.exists():
            links = [{"text": "Open", "url": _url(p), "target": "_blank"}]
            cr = p.parent / "candle_report.html"
            if label == "My portfolio" and cr.exists():
                links.append({"text": "Candlestick report", "url": _url(cr), "target": "candle_view"})
            items.append({"label": label, "when": stamp(p), "links": links})
    return items


def run_stamp(p):
    """Date and time the file was written (= when the screen ran), for file names: 2026-11-20_0851."""
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d_%H%M")


def csv_download(rel):
    """One results csv as a download. Returns (file name, bytes) or None."""
    p = allowed_file(rel)
    if p is None or p.suffix.lower() != ".csv":
        return None
    import pandas as pd

    try:
        data = with_origin(pd.read_csv(p)).to_csv(index=False).encode("utf-8")   # old files get the origin column too
    except Exception:  # noqa: BLE001  (an empty or damaged file: send it as it is)
        data = p.read_bytes()
    if p.name == "lookup_results.csv":   # the folder name already holds the date and time of the run
        return f"{p.parent.name}.csv", data
    return f"screen_{p.parent.name}_ran_{run_stamp(p)}.csv", data


def collate(pattern, prefix, date_of):
    """Join many csv files into one (newest run first), with run_date and run_time columns. Returns (name, bytes) or None."""
    import pandas as pd

    frames = []
    # newest data pull first (by the time each file was written)
    for p in sorted(Path().glob(pattern), key=lambda q: (q.stat().st_mtime, q.name), reverse=True):
        try:
            df = pd.read_csv(p).drop(columns=["origin"], errors="ignore")   # one origin column, added first below
        except Exception:  # noqa: BLE001  (skip a damaged file)
            continue
        df.insert(0, "run_time", dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"))
        df.insert(0, "run_date", date_of(p))
        df.insert(0, "origin", ORIGIN)
        frames.append(df)
    if not frames:
        return None
    name = f"{prefix}_{dt.datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
    return name, pd.concat(frames, ignore_index=True).to_csv(index=False).encode("utf-8")


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def tracker_download(kind):
    """The tracker as Excel (all sheets) or as one csv table. Returns (name, bytes, content type) or None."""
    import tracker

    tables = tracker.build()
    if tables["history"].empty:
        return None
    s = tracker.stamp()
    if kind == "excel":
        return f"5.5_tracker_{s}.xlsx", tracker.excel_bytes(tables), XLSX_TYPE
    if kind in tracker.CSV_TABLES:
        return f"5.5_tracker_{kind}_{s}.csv", tracker.csv_bytes(tables, kind), "text/csv; charset=utf-8"
    return None


def collate_screens():
    """All screening results.csv files in one csv."""
    return collate("output/20*/results.csv", "all_screens", lambda p: p.parent.name)


def collate_lookups():
    """All stock-lookup csv files in one csv (run_date = the lookup folder's date and time)."""
    return collate("output/lookup_*/lookup_results.csv", "all_lookups",
                   lambda p: p.parent.name.replace("lookup_", ""))


def allowed_file(rel):
    base = Path.cwd().resolve()
    p = (base / rel).resolve()
    ok = (p == base / "paper_report.html" or base / "output" in p.parents
          or base / "backtest_output" in p.parents)
    return p if ok and p.is_file() else None


# ------------------------------- WEB SERVER -----------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _host_ok(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "Forbidden")
        u = urlparse(self.path)
        path, qs = unquote(u.path), parse_qs(u.query)
        if path == "/":
            return self._send(200, INDEX.replace("__TOKEN__", TOKEN).replace("__ORIGIN__", ORIGIN))
        if path == "/api/search":
            return self._json(search(qs.get("q", [""])[0]))
        if path == "/tracker":
            return self._send(200, TRACKER_PAGE.replace("__ORIGIN__", ORIGIN))
        if path == "/api/tracker":
            import tracker
            return self._json(tracker.web_data())
        if path == "/api/settings":
            return self._json(hub_settings.load())
        if path == "/api/reports":
            return self._json(list_reports())
        if path == "/api/job":
            job = JOBS.get(qs.get("id", [""])[0])
            if job is None:
                return self._json({"error": "unknown job"}, 404)
            since = int(qs.get("since", ["0"])[0] or 0)
            with LOCK:
                text, status, result = job.text[since:], job.status, job.result
                offset = len(job.text)
            return self._json({"text": text, "offset": offset, "status": status, "result": result})
        if path == "/download/tracker":
            got = tracker_download(qs.get("kind", [""])[0])
            if got is None:
                return self._send(404, "Nothing to download yet. Run a screen first.")
            name, data, ctype = got
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/download", "/download/all", "/download/all_lookups"):
            got = (collate_screens() if path == "/download/all" else
                   collate_lookups() if path == "/download/all_lookups" else
                   csv_download(qs.get("file", [""])[0]))
            if got is None:
                return self._send(404, "Nothing to download yet. Run a screen first.")
            name, data = got
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/files/"):
            p = allowed_file(path[len("/files/"):])
            if p is None:
                return self._send(404, "Not found")
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            return self._send(200, p.read_bytes(), ctype)
        return self._send(404, "Not found")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Hub-Token") != TOKEN:
            return self._send(403, "Forbidden")
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad request"}, 400)
        if path == "/api/run":
            job, err = start_job(body)
            if err:
                return self._json({"error": err}, 409)
            return self._json({"job": job.id})
        if path == "/api/settings":
            with LOCK:
                busy = STATE["current"] is not None
            if busy:
                return self._json({"error": "A task is running. Wait for it to finish so one report does not mix two settings."}, 409)
            try:
                if body.get("reset"):
                    return self._json(hub_settings.reset())
                return self._json(hub_settings.update(supertrend_period=body.get("supertrend_period", ""),
                                                      supertrend_mult=body.get("supertrend_mult", "")))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return self._send(404, "Not found")


def serve():
    hub.OPEN_REPORT = False            # the page gives you buttons instead of auto-opening tabs
    import backtest as bt
    import candle_view as cv
    import nse_screener as ns
    import paper_tracker as pt
    import portfolio as pf
    import stock_lookup as sl
    import alerts as al
    import funds as fu

    for mod in (bt, cv, ns, pt, pf, sl, al, fu):
        mod.OPEN_REPORT = False
    sl.choose_symbols = lambda table, args: list(args)   # the page already picked exact symbols

    server = None
    for port in range(PORT, PORT + 10):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not start the web page: ports 8765-8774 are all busy.")
    url = f"http://{'127.0.0.1' if HOST in ('0.0.0.0', '') else HOST}:{server.server_address[1]}/"   # browsers cannot open 0.0.0.0
    print(f"Trading hub is running at {url}\nLeave this window open. Press Ctrl+C to stop.")
    if OPEN_BROWSER:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Stopped.")


# ------------------------------- THE PAGE -------------------------------------
INDEX = """<!doctype html><html><head><meta charset='utf-8'><title>Trading hub - __ORIGIN__</title>
<style>
body{font-family:Arial,sans-serif;font-size:22px;margin:0;background:#f4f6f9;line-height:1.5}
header{background:#1f4e9c;color:white;padding:16px 28px;display:flex;justify-content:space-between;align-items:center}
header h1{margin:0;font-size:30px} main{max-width:1250px;margin:0 auto;padding:20px 28px}
.card{background:white;border-radius:12px;padding:16px 22px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.15)}
.card h2{margin:0 0 6px;font-size:25px}
button,a.btn{font-size:21px;padding:10px 20px;margin:6px 8px 6px 0;border-radius:8px;border:0;background:#1f4e9c;
 color:white;cursor:pointer;text-decoration:none;display:inline-block}
button.alt{background:#555} button.stop{background:#b22222} button:disabled{background:#aaa;cursor:wait}
input{font-size:21px;padding:8px 10px;width:420px;max-width:100%;border:2px solid #999;border-radius:6px}
#sugg{border:2px solid #999;border-radius:6px;background:white;max-width:700px;display:none}
#sugg div{padding:6px 12px;cursor:pointer;border-bottom:1px solid #eee} #sugg div:hover{background:#e8f0fe}
.chip{display:inline-block;background:#e8f0fe;border:2px solid #1f4e9c;border-radius:20px;padding:3px 14px;margin:4px 8px 4px 0}
.chip b{cursor:pointer;color:#b22222;margin-left:8px} .chip a{font-size:17px;margin-left:8px}
pre{background:#101820;color:#d6f5d6;font-size:18px;padding:12px;border-radius:8px;height:300px;overflow:auto;white-space:pre-wrap}
.hint{color:#555;font-size:18px} #status{font-weight:bold}
table{border-collapse:collapse;font-size:20px} td{padding:6px 14px;border-bottom:1px solid #ddd}
details.dl{display:inline-block;vertical-align:top;margin:6px 8px 6px 0} details.dl summary{list-style:none;font-size:21px;padding:10px 20px;border-radius:8px;background:#1f4e9c;color:white;cursor:pointer}
details.dl summary::-webkit-details-marker{display:none} details.dl .sub{background:white;border:2px solid #1f4e9c;border-radius:8px;padding:8px 14px;margin-top:4px;min-width:330px}
details.dl .sub a{display:block;padding:3px 0} details.dl .sub b{display:block;margin-top:6px}
</style></head><body>
<header><h1>Trading hub <span style='font-size:22px;background:white;color:#1f4e9c;padding:3px 14px;border-radius:20px;margin-left:12px'>__ORIGIN__</span></h1><button class='stop' onclick='quit()'>Quit</button></header>
<main>
<div class='card'><h2>1. Screen the market</h2>
<p class='hint'>Runs the screener, then the candlestick view for the stocks it finalizes (several minutes).</p>
<button class='run' onclick="start('screen')">Screen + candlesticks</button></div>

<div class='card'><h2>2. Analyse stocks I choose</h2>
<p class='hint'>Type at least 3 letters of a symbol or company name. Nothing is hidden: weak businesses and
stocks that fail the price rules are shown with the reasons.</p>
<input id='q' placeholder='for example: tata, infy, reliance' autocomplete='off'>
<div id='sugg'></div><div id='chips'></div>
<button class='run' onclick="start('lookup')">Analyse chosen stocks</button></div>

<div class='card'><h2>3. Paper trades (no real money)</h2>
<button class='run' onclick="start('paper_add')">Log the top picks</button>
<button class='run' onclick="start('paper_update')">Update with latest prices</button>
<button class='run' onclick="start('paper_show')">Show the log</button><br>
<span class='hint'>Specific stocks to log, or the one to close (symbols, spaces or commas):</span><br>
<input id='psyms' placeholder='LALPATHLAB'> <input id='pprice' placeholder='sell price (optional)' style='width:240px'><br>
<button class='run' onclick="startPaper('paper_add')">Log these stocks</button>
<button class='run alt' onclick="startPaper('paper_close')">Close / cancel this one</button></div>

<div class='card'><h2>4. My real portfolio</h2>
<p class='hint'>Holdings from your ICICI Direct and Shoonya files. Paste the full path of a downloaded file to import it
(importing a broker again replaces that broker's earlier list).</p>
<input id='pfile' placeholder='C:\\Users\\you\\Downloads\\holdings.csv' style='width:700px'><br>
<button class='run' onclick="startPortfolio('portfolio_import')">Import this file</button>
<button class='run' onclick="start('portfolio_report')">Portfolio report</button>
<button class='run' onclick="start('portfolio_show')">Show holdings</button><br>
<span class='hint'>Your own stop-loss for a holding (symbol, then price):</span><br>
<input id='pstopsym' placeholder='NRBBEARING' style='width:240px'> <input id='pstopprice' placeholder='stop price' style='width:200px'>
<button class='run alt' onclick="startPortfolio('portfolio_stop')">Set stop</button><br>
<span class='hint'>Add, hold or exit? A prompt for every holding (pyramiding checklist, Supertrend exit rules), or the full checklist for one:</span><br>
<button class='run' onclick="start('plan_check')">Prompts for my holdings</button>
<input id='plansym' placeholder='NRBBEARING' style='width:240px'>
<button class='run alt' onclick="start('plan_explain',{symbols:[$('plansym').value]})">Explain this one</button><br>
<span class='hint'>Exit alerts for your holdings (bearish formations plus other warning signs):</span><br>
<button class='run' onclick="start('alerts_preview')">Preview alerts (sends nothing)</button>
<button class='run' onclick="start('alerts_send')">Check and send to Telegram</button>
<button class='run alt' onclick="start('alerts_status')">Active alerts</button><br>
<span class='hint'>If I sold a holding, where could the money go? (symbol, then quantity or leave empty for all):</span><br>
<input id='rsym' placeholder='NRBBEARING' style='width:240px'> <input id='rqty' placeholder='quantity (optional)' style='width:240px'>
<button class='run' onclick="startRedeploy()">Show options + 3-stock splits</button></div>

<div class='card'><h2>4b. ETFs, mutual funds and gold</h2>
<p class='hint'>Ranks ETFs (broad index, sector, gold and silver, international) inside their groups by trend and strength
against the Nifty, and shows the ETFs you hold from your broker files. Mutual funds are added by hand with their scheme code
(units 0 = just watch it).</p>
<button class='run' onclick="start('funds_report')">Rank ETFs and gold + my holdings</button>
<button class='run alt' onclick="start('funds_mfshow')">Show my mutual funds</button><br>
<span class='hint'>Find a mutual fund (at least 3 letters of its name):</span><br>
<input id='ftext' placeholder='parag parikh flexi' style='width:420px'>
<button class='run' onclick="startFunds('funds_mfsearch')">Search</button><br>
<span class='hint'>Add or update a mutual fund (scheme code, units, your average NAV) or remove one by code:</span><br>
<input id='fcode' placeholder='scheme code' style='width:200px'> <input id='funits' placeholder='units (0 = watch)' style='width:220px'>
<input id='fnav' placeholder='average NAV' style='width:200px'><br>
<button class='run' onclick="startFunds('funds_mfadd')">Save this fund</button>
<button class='run alt' onclick="startFunds('funds_mfremove')">Remove this code</button><br>
<span class='hint'>Add an ETF to the ranked list (symbol in the box above, then its group, for example Gold and silver):</span><br>
<input id='fetf' placeholder='HDFCGOLD' style='width:200px'> <input id='fgroup' placeholder='group (optional)' style='width:300px'>
<button class='run alt' onclick="startFunds('funds_watch')">Add ETF</button></div>

<div class='card'><h2>5. Backtest</h2>
<p class='hint'>Replays the screener's price rules on 10 years of history (first run takes several minutes).</p>
<button class='run' onclick="start('backtest')">Run backtest</button></div>

<div class='card'><h2>6. Chart settings</h2>
<p class='hint'>Supertrend on the Heikin Ashi charts (screener, stock lookup, portfolio). The new values are used the next
time charts are drawn; reports you already made keep the old ones until you run them again.</p>
Period <input id='stp' style='width:120px'> &nbsp; Multiplier <input id='stm' style='width:120px'><br>
<button onclick='saveSettings()'>Save</button>
<button class='alt' onclick='resetSettings()'>Back to 10 and 2</button>
<p id='setmsg' class='hint'></p></div>

<div class='card'><h2>7. Tracker</h2>
<p class='hint'>How the screener's stocks did over the run days: the list of stocks worth following, with a journey strip for each.</p>
<a class='btn' href='/tracker'>Open tracker</a></div>

<div class='card'><h2>Progress</h2>
<p id='status'>Idle.</p><div id='result'></div><pre id='log'></pre></div>

<div class='card'><h2>Reports</h2><p class='hint'>Every stock in the reports has Google and Yahoo live-chart links.</p>
<p><a class='btn' href='/download/all'>Download all screens in one CSV</a>
<a class='btn' href='/download/all_lookups'>Download all lookups in one CSV</a>
<details class='dl'><summary>Download tracker &#9662;</summary><div class='sub'>
<b>Excel</b><a href='/download/tracker?kind=excel'>All sheets in one Excel file</a>
<b>CSV (one table each)</b>
<a href='/download/tracker?kind=leaderboard'>Leaderboard (stocks, highest Breakout count first)</a>
<a href='/download/tracker?kind=journeys'>Journeys (stock by day grid)</a>
<a href='/download/tracker?kind=overview'>Overview (stocks per status each day)</a>
<a href='/download/tracker?kind=events'>Events (callouts)</a>
<a href='/download/tracker?kind=transitions'>Transitions (near breakout to breakout)</a>
<a href='/download/tracker?kind=history'>History (every row)</a></div></details>
<span class='hint'>The file name has the date and time you click. Each screen and each lookup also has its own "Download CSV" link below, named with the date and time it ran.</span></p>
<div id='reports'></div></div>
</main>
<script>
const TOKEN="__TOKEN__";
let chosen=[], timer=null, offset=0;
const $=id=>document.getElementById(id);
function enc(s){return encodeURIComponent(s);}
function renderChips(){
  $('chips').innerHTML='';
  chosen.forEach((s,i)=>{
    const d=document.createElement('span'); d.className='chip'; d.textContent=s;
    const g=document.createElement('a'); g.textContent='Google'; g.target='_blank';
    g.href='https://www.google.com/finance/quote/'+enc(s)+':NSE';
    const y=document.createElement('a'); y.textContent='Yahoo'; y.target='_blank';
    y.href='https://finance.yahoo.com/chart/'+enc(s)+'.NS';
    const x=document.createElement('b'); x.textContent='\\u00d7'; x.onclick=()=>{chosen.splice(i,1);renderChips();};
    d.append(g,y,x); $('chips').appendChild(d);
  });
}
function add(sym){ sym=sym.trim().toUpperCase(); if(sym&&!chosen.includes(sym)){chosen.push(sym);renderChips();}
  $('q').value=''; $('sugg').style.display='none'; $('q').focus(); }
$('q').addEventListener('input',()=>{
  clearTimeout(timer);
  const t=$('q').value.split(',').pop().trim();
  if(t.length<3){$('sugg').style.display='none';return;}
  timer=setTimeout(async()=>{
    const r=await fetch('/api/search?q='+enc(t)); const m=await r.json(); const box=$('sugg');
    box.innerHTML=''; if(!m.length){box.style.display='none';return;}
    m.forEach(x=>{const d=document.createElement('div'); d.textContent=x.symbol+'  -  '+x.name;
      d.onclick=()=>add(x.symbol); box.appendChild(d);});
    box.style.display='block';
  },200);
});
$('q').addEventListener('keydown',e=>{
  if(e.key!=='Enter') return;
  const first=$('sugg').firstChild;
  if(first&&$('sugg').style.display==='block') add(first.textContent.split(' ')[0]);
  else $('q').value.split(',').forEach(add);
});
function setBusy(b){document.querySelectorAll('button.run').forEach(x=>x.disabled=b);}
async function start(task,extra){
  setBusy(true); $('log').textContent=''; $('result').innerHTML=''; offset=0;
  const body=Object.assign({task:task,symbols:chosen},extra||{});
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json','X-Hub-Token':TOKEN},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){$('status').textContent=j.error||'Could not start.';setBusy(false);return;}
  $('status').textContent='Running ... (leave this page open)';
  poll(j.job);
}
function startPaper(task){
  const syms=$('psyms').value.split(/[ ,]+/).filter(Boolean);
  start(task,{symbols:syms,price:$('pprice').value});
}
function startFunds(task){
  const syms=task==='funds_watch'?[$('fetf').value]:[$('fcode').value];
  start(task,{symbols:syms,units:$('funits').value,price:$('fnav').value,
              text:task==='funds_watch'?$('fgroup').value:$('ftext').value});
}
function startRedeploy(){ start('redeploy',{symbols:[$('rsym').value],price:$('rqty').value}); }
function startPortfolio(task){
  if(task==='portfolio_stop') start(task,{symbols:[$('pstopsym').value],price:$('pstopprice').value});
  else start(task,{path:$('pfile').value});
}
async function poll(id){
  const r=await fetch('/api/job?id='+id+'&since='+offset); const j=await r.json();
  if(j.text){$('log').textContent+=j.text;$('log').scrollTop=$('log').scrollHeight;}
  offset=j.offset;
  if(j.status==='running'){setTimeout(()=>poll(id),1000);return;}
  setBusy(false); $('status').textContent='Finished.';
  if(j.result){const a=document.createElement('a');a.className='btn';a.textContent=j.result.label;
    a.href=j.result.url;a.target=j.result.target;$('result').appendChild(a);}
  loadReports();
}
async function loadReports(){
  const r=await fetch('/api/reports'); const items=await r.json(); const box=$('reports');
  box.innerHTML=''; if(!items.length){box.textContent='No reports yet. Run a task above.';return;}
  const t=document.createElement('table');
  items.forEach(it=>{const tr=document.createElement('tr');
    const a=document.createElement('td'); a.textContent=it.label; const b=document.createElement('td'); b.textContent=it.when;
    const c=document.createElement('td');
    it.links.forEach(l=>{const x=document.createElement('a');x.textContent=l.text;x.href=l.url;x.target=l.target;
      x.style.marginRight='18px';c.appendChild(x);});
    tr.append(a,b,c); t.appendChild(tr);});
  box.appendChild(t);
}
async function quit(){
  if(!confirm('Stop the trading hub?')) return;
  await fetch('/api/quit',{method:'POST',headers:{'Content-Type':'application/json','X-Hub-Token':TOKEN},body:'{}'});
  document.body.innerHTML='<p style="padding:40px;font-size:24px">Stopped. You can close this tab.</p>';
}
async function loadSettings(){
  const r=await fetch('/api/settings'); const s=await r.json();
  $('stp').value=s.supertrend_period; $('stm').value=s.supertrend_mult;
}
async function postSettings(body){
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Hub-Token':TOKEN},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){$('setmsg').textContent=j.error||'Could not save.';return;}
  $('stp').value=j.supertrend_period; $('stm').value=j.supertrend_mult;
  $('setmsg').textContent='Saved: Supertrend ('+j.supertrend_period+', '+j.supertrend_mult+'). Used by the next charts you draw.';
}
function saveSettings(){ postSettings({supertrend_period:$('stp').value,supertrend_mult:$('stm').value}); }
function resetSettings(){ postSettings({reset:true}); }
loadReports();
loadSettings();
</script></body></html>"""



TRACKER_PAGE = r"""<!doctype html><html><head><meta charset='utf-8'><title>Tracker - __ORIGIN__</title>
<style>
body{font-family:Arial,sans-serif;font-size:16px;margin:0;background:#f4f6f9;line-height:1.4}
header{background:#1f4e9c;color:white;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}
header h1{margin:0;font-size:24px} header a{color:white;font-size:16px}
.pill{font-size:16px;background:white;color:#1f4e9c;padding:2px 12px;border-radius:20px;margin-left:10px}
main{padding:14px 24px}
.card{background:white;border-radius:10px;padding:12px 16px;margin:12px 0;box-shadow:0 1px 4px rgba(0,0,0,.15)}
.bar{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;margin:6px 0}
button.v{font-size:15px;padding:6px 14px;border-radius:6px;border:2px solid #1f4e9c;background:white;color:#1f4e9c;cursor:pointer}
button.v.on{background:#1f4e9c;color:white}
button.more{font-size:16px;padding:8px 18px;border-radius:6px;border:0;background:#1f4e9c;color:white;cursor:pointer;margin:8px 0}
select,input{font-size:15px;padding:4px 6px;border:1px solid #999;border-radius:5px}
input.n{width:60px} input.s{width:170px}
label{color:#333;font-size:14px}
.hint{color:#555;font-size:14px}
.wrap{overflow:auto;max-height:78vh;position:relative}
table{border-collapse:separate;border-spacing:0}
th{background:#e8eef8;font-size:14px;text-align:left;padding:0 10px;height:46px;cursor:pointer;white-space:nowrap;border-bottom:2px solid #1f4e9c;position:sticky;top:0;z-index:2;box-sizing:border-box}
td{padding:0 10px;height:29px;font-size:15px;white-space:nowrap;border-bottom:1px solid #ddd;box-sizing:border-box;background:#fff}
tr:nth-child(even) td{background:#fafbfd}
th[data-f]{z-index:4} td[data-f]{position:sticky;z-index:1}
.lastf{border-right:2px solid #1f4e9c!important}
th.dh{width:26px;min-width:26px;padding:0;text-align:center;cursor:default;font-size:11px;font-weight:normal;line-height:1.15;color:#222}
th.dh b{display:block;font-size:14px}
td.dc{width:26px;min-width:26px;padding:0;text-align:center;font-weight:bold;font-size:13px;position:relative;border-right:1px solid #fff}
td.dc i{position:absolute;top:1px;right:1px;width:6px;height:6px;border-radius:50%;background:#d22;border:1px solid #fff}
.dim{color:#888}
th.nar{white-space:normal;text-align:center;padding:0 6px;width:62px;line-height:1.15}
th.buy{white-space:normal;line-height:1.15;width:118px;min-width:118px}
td.nar{text-align:center;padding:0 6px}
tr.pick td{background:#e8f0fd!important} tr.sym{cursor:pointer}
td.panel{padding:0;background:#fff!important;height:471px;border-bottom:2px solid #1f4e9c;white-space:normal;vertical-align:top}
.pbox{width:640px;height:470px;overflow:hidden;padding:8px 12px;box-sizing:border-box}
.pbox h3{margin:0 0 4px;font-size:17px} .pbox p{margin:2px 0;font-size:14px}
.pbox table{width:100%} .pbox th{height:26px;cursor:default;font-size:13px} .pbox td{height:22px;font-size:13px}
#readout{font-size:13px;color:#333;min-height:18px}
.leg{display:inline-block;margin:2px 14px 2px 0;font-size:14px}
.leg b{display:inline-block;width:22px;text-align:center;border-radius:3px;margin-right:6px}
</style></head><body>
<header><h1>Tracker <span class='pill'>__ORIGIN__</span></h1>
<span><a href='/download/tracker?kind=excel'>Download tracker (Excel)</a> &nbsp; <a href='/'>Back to the hub</a></span></header>
<main>
<div class='card'>
<div class='bar' id='views'></div>
<div class='bar'>
<label>Status <select id='fstatus'></select></label>
<label>Alert (ever) <select id='falert'><option value=''>Any</option><option value='yes'>Yes</option><option value='no'>No</option></select></label>
<label>Quality <select id='fqual'><option value=''>Any</option><option>STRONG</option><option>WEAK</option><option>CHECK</option></select></label>
<label>Minimum times listed <input class='n' id='fmin' type='number' min='0' value='0'></label>
<label>Symbol <input class='s' id='fsym' placeholder='search symbol'></label>
<label>Rows <select id='frows'><option>25</option><option>50</option><option>100</option><option value='0'>All</option></select></label>
<button class='v' id='freset'>Reset</button>
</div>
<div class='hint' id='info'></div>
</div>
<div class='card'>
<div id='legend'></div>
<div class='wrap' id='wrap'></div>
<div class='hint' id='count'></div>
<div id='moreBox'></div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
const VIEWS=[['worth','Worth following'],['now','Listed now'],['broke','Ever broke out'],['all','All stocks']];
const STATUS={B:'Breakout',W:'Breakout, weak volume',N:'Near breakout',n:'Near breakout (watch only)',S:'Building base',E:'Extended'};
const COLS=[['symbol','Symbol'],['last_code','Status now'],['breakouts','Breakouts'],['last_buy','Recommended buy price'],
 ['appearances','Times listed'],['last_alert','Last alert'],['quality','Quality'],['vs_nifty','vs Nifty']];
const FROZEN=4;      // the first four columns stay in place when the table scrolls sideways
let scrollEnd=true;  // true = the next drawing scrolls to the newest day (the right end)
const NAR={appearances:1,vs_nifty:1};
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const CW=26;   // width of one strip cell in pixels
let open=null; // symbol whose detail panel is open
let D=null, view='worth', limit=25, shown=25, sortKey=null, sortDir=-1;
function el(tag,cls,text){const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e;}
function fmt(v){return v===null||v===undefined||v===''?'-':String(v);}
function matches(r){
  if(view==='worth'&&r.worth!=='Yes') return false;
  if(view==='now'&&r.listed_now!=='Yes') return false;
  if(view==='broke'&&r.breakouts+r.weak===0) return false;
  const st=$('fstatus').value; if(st&&r.last_code!==st) return false;
  const al=$('falert').value; if(al==='yes'&&r.alert_days===0) return false; if(al==='no'&&r.alert_days>0) return false;
  const q=$('fqual').value; if(q&&r.quality!==q) return false;
  if(r.appearances<(parseInt($('fmin').value,10)||0)) return false;
  const s=$('fsym').value.trim().toUpperCase(); if(s&&!r.symbol.toUpperCase().includes(s)) return false;
  return true;
}
function sorted(list){
  if(sortKey===null) return list;
  const k=sortKey;
  return list.slice().sort((a,b)=>{
    let x=a[k], y=b[k];
    const xn=(x===null||x===undefined||x===''), yn=(y===null||y===undefined||y==='');
    if(xn&&yn) return a._i-b._i; if(xn) return 1; if(yn) return -1;   // empty values go last
    if(x<y) return -sortDir; if(x>y) return sortDir; return a._i-b._i;
  });
}
function svg(tag,attrs){const e=document.createElementNS('http://www.w3.org/2000/svg',tag); for(const k in attrs) e.setAttribute(k,attrs[k]); return e;}
function detail(r){
  const box=el('div','pbox'), nd=D.days.length;
  box.onclick=ev=>ev.stopPropagation();
  box.appendChild(el('h3',null,r.symbol+' - '+(STATUS[r.last_code]||r.last_code)));
  const cn=Object.keys(r.counts).map(c=>(STATUS[c]||c)+': '+r.counts[c]).join('; ');
  box.appendChild(el('p',null,'First listed '+r.first_seen+'. Last listed '+r.last_seen+'. Times listed: '+r.appearances+'.'));
  box.appendChild(el('p',null,'Days per status - '+(cn||'none')+'.'));
  box.appendChild(el('p',null,'Last recommended buy price '+(r.last_buy===null?'-':r.last_buy.toFixed(2))+'. Stop-loss '+(r.last_stop===null?'-':r.last_stop.toFixed(2))+
    '. Target '+(r.last_target===null?'-':r.last_target.toFixed(2))+'. Last alert '+(r.last_alert||'none')+'.'));
  // days from the first listed day to the newest data day
  let a=0; while(a<nd&&r.codes[a]===' ') a++;
  const idx=[]; for(let i=a;i<nd;i++) idx.push(i);
  const vals=[]; idx.forEach(i=>{if(r.closes[i]!==null) vals.push(r.closes[i]); if(r.buys[i]!==null) vals.push(r.buys[i]);});
  const ro=el('div',null,'Hover the chart to see a day.'); ro.id='readout'; box.appendChild(ro);
  if(vals.length){
    const W=616,H=150,L=48,R=8,T=8,B=18; let lo=Math.min(...vals), hi=Math.max(...vals); if(hi===lo){hi+=1; lo-=1;} const pad=(hi-lo)*0.08; lo-=pad; hi+=pad;
    const X=k=>L+(idx.length<2?(W-L-R)/2:k*(W-L-R)/(idx.length-1)), Y=v=>T+(hi-v)*(H-T-B)/(hi-lo);
    const s=svg('svg',{width:W,height:H,viewBox:'0 0 '+W+' '+H});
    for(let g=0;g<=3;g++){const v=lo+(hi-lo)*g/3, y=Y(v); s.appendChild(svg('line',{x1:L,x2:W-R,y1:y,y2:y,stroke:'#e3e3e3'}));
      const t=svg('text',{x:L-4,y:y+4,'text-anchor':'end','font-size':11,fill:'#555'}); t.textContent=v.toFixed(0); s.appendChild(t);}
    const lab=(k)=>{const d=D.days[idx[k]]; const t=svg('text',{x:X(k),y:H-4,'text-anchor':k===0?'start':(k===idx.length-1?'end':'middle'),'font-size':11,fill:'#555'}); t.textContent=d.slice(5); s.appendChild(t);};
    lab(0); if(idx.length>2) lab(Math.floor((idx.length-1)/2)); if(idx.length>1) lab(idx.length-1);
    [['closes','#1f4e9c'],['buys','#e07b00']].forEach(([key,col])=>{
      let path='', pen=false;
      idx.forEach((i,k)=>{const v=r[key][i]; if(v===null){pen=false;return;} path+=(pen?'L':'M')+X(k)+' '+Y(v)+' '; pen=true;});
      s.appendChild(svg('path',{d:path.trim(),fill:'none',stroke:col,'stroke-width':2}));
      idx.forEach((i,k)=>{const v=r[key][i]; if(v!==null) s.appendChild(svg('circle',{cx:X(k),cy:Y(v),r:3,fill:col}));});
    });
    const vl=svg('line',{x1:0,x2:0,y1:T,y2:H-B,stroke:'#888','stroke-dasharray':'3 3',visibility:'hidden'}); s.appendChild(vl);
    const day=k=>{const i=idx[k], c=r.codes[i];
      if(c===' ') return D.days[i]+': not on the list';
      return D.days[i]+' - '+(STATUS[c]||c)+' - price '+(r.closes[i]===null?'-':r.closes[i].toFixed(2))+' - recommended buy price '+(r.buys[i]===null?'-':r.buys[i].toFixed(2))+
        (r.scores[i]!==null?' - score '+r.scores[i]:'')+(r.alerts[i]==='1'?' - ALERT':'');};
    s.addEventListener('mousemove',ev=>{const bb=s.getBoundingClientRect(), x=ev.clientX-bb.left; let k=Math.round((x-L)*(idx.length-1)/(W-L-R)); if(idx.length<2) k=0;
      k=Math.max(0,Math.min(idx.length-1,k)); vl.setAttribute('x1',X(k)); vl.setAttribute('x2',X(k)); vl.setAttribute('visibility','visible'); ro.textContent=day(k);});
    s.addEventListener('mouseleave',()=>{vl.setAttribute('visibility','hidden');});
    box.appendChild(s);
    const lg=el('div','hint'); lg.textContent='Blue line = price (close). Orange line = recommended buy price. A gap = not on the list that day, or no buy price (watch only).'; box.appendChild(lg);
  } else box.appendChild(el('p','hint','No prices to draw for this stock.'));
  // table: one row per run day the stock was listed, newest first
  const t=el('table'), hr=el('tr'); ['Date','Status','Price','Recommended buy price'].forEach(h=>hr.appendChild(el('th',null,h))); t.appendChild(hr);
  for(let k=idx.length-1;k>=0;k--){const i=idx[k], c=r.codes[i]; if(c===' ') continue;
    const tr=el('tr'); tr.appendChild(el('td',null,D.days[i])); tr.appendChild(el('td',null,(STATUS[c]||c)+(r.alerts[i]==='1'?' (ALERT)':'')));
    tr.appendChild(el('td',null,r.closes[i]===null?'-':r.closes[i].toFixed(2))); tr.appendChild(el('td',null,r.buys[i]===null?'-':r.buys[i].toFixed(2))); t.appendChild(tr);}
  const tw=el('div'); tw.style.cssText='max-height:150px;overflow-y:auto;margin-top:6px'; tw.appendChild(t); box.appendChild(tw);
  return box;
}
function render(){
  const all=D.rows, list=sorted(all.filter(matches));
  const n=(limit===0)?list.length:Math.min(shown,list.length), part=list.slice(0,n);
  const wrap=$('wrap'), oldL=wrap.scrollLeft, oldT=wrap.scrollTop, atEnd=(wrap.scrollLeft+wrap.clientWidth>=wrap.scrollWidth-25); wrap.innerHTML='';
  if(!all.length){wrap.textContent='No screener runs yet. Run a screen on the hub page first.'; $('count').textContent=''; $('moreBox').innerHTML=''; return;}
  const t=el('table'), hr=el('tr'), ND=D.days.length;
  COLS.forEach(([k,label],ci)=>{const th=el('th',(k==='last_buy'?'buy':(NAR[k]?'nar':null)),label+(sortKey===k?(sortDir<0?' ▼':' ▲'):'')); if(k==='vs_nifty') th.title='vs Nifty score';
    if(ci<FROZEN) th.dataset.f=ci; if(ci===FROZEN-1) th.classList.add('lastf');
    th.onclick=()=>{ if(sortKey===k) sortDir=-sortDir; else {sortKey=k; sortDir=(k==='symbol'||k==='quality'||k==='last_code')?1:-1;} render(); };
    hr.appendChild(th);});
  D.days.forEach(d=>{const p=d.split('-'), h=el('th','dh'); h.appendChild(el('b',null,String(parseInt(p[2],10)))); h.appendChild(document.createTextNode(MON[parseInt(p[1],10)-1])); h.title=d; hr.appendChild(h);});
  t.appendChild(hr);
  part.forEach(r=>{
    const tr=el('tr','sym'+(open===r.symbol?' pick':'')); const dim=r.listed_now!=='Yes';
    tr.onclick=()=>{open=(open===r.symbol)?null:r.symbol; render();};
    const add=(cls,text,title)=>{const td=el('td',cls,text); if(title) td.title=title; tr.appendChild(td); return td;};
    add(null,r.symbol);
    const sd=r.last_seen.split('-'), seen=parseInt(sd[2],10)+' '+MON[parseInt(sd[1],10)-1];
    add(dim?'dim':null,(STATUS[r.last_code]||r.last_code)+(dim?', seen '+seen:''),dim?'Not on the list on the newest day. Last seen '+r.last_seen:null);
    add(null,r.breakouts+(r.weak?' (+'+r.weak+' weak)':''));
    add(null,r.last_buy===null?'-':r.last_buy.toFixed(2));
    add('nar',r.appearances);
    add(r.last_alert?null:'dim',r.last_alert||'none',r.last_alert?null:'No alert so far (best score '+fmt(r.best_score)+'). An alert needs a score of 70 or more and the Nifty gate on.');
    add(null,fmt(r.quality));
    add('nar',r.vs_nifty===null?'-':r.vs_nifty.toFixed(1));
    const cells=tr.children; for(let ci=0;ci<FROZEN;ci++){cells[ci].dataset.f=ci;} cells[FROZEN-1].classList.add('lastf');
    for(let i=0;i<ND;i++){
      const code=r.codes[i], c=el('td','dc'), day=D.days[i];
      if(code!==' '){
        c.textContent=code; c.style.background=D.fills[code]||'#ccc'; c.style.color=D.fonts[code]||'#000';
        const sc=r.scores[i]; c.title=day+': '+(STATUS[code]||code)+(sc!==null?' (score '+sc+')':'')+(r.alerts[i]==='1'?' - ALERT':'');
        if(r.alerts[i]==='1') c.appendChild(el('i'));
      } else c.title=day+': not on the list';
      tr.appendChild(c);
    }
    t.appendChild(tr);
    if(open===r.symbol){   // detail panel: one row under the stock; the box stays at the left edge while the table scrolls sideways
      const pr=el('tr'), pd=el('td','panel'); pd.colSpan=COLS.length+ND;
      const hold=el('div'); hold.style.cssText='position:sticky;left:0;width:640px'; hold.appendChild(detail(r)); pd.appendChild(hold); pr.appendChild(pd); t.appendChild(pr);}
  });
  wrap.appendChild(t);
  // freeze: each frozen column sticks at the sum of the widths of the frozen columns before it
  const ths=hr.children; let left=0;
  for(let ci=0;ci<FROZEN;ci++){const w=ths[ci].offsetWidth; t.querySelectorAll('[data-f="'+ci+'"]').forEach(c=>{c.style.left=left+'px';}); left+=w;}
  if(scrollEnd||atEnd){wrap.scrollLeft=1e9; wrap.scrollTop=oldT; scrollEnd=false; setTimeout(()=>{wrap.scrollLeft=1e9;},60);}   // again after the layout settles else {wrap.scrollLeft=oldL; wrap.scrollTop=oldT;}
  // count line and "Show more"
  const hidden=all.length-list.length;
  $('count').textContent='Showing '+n+' of '+list.length+' matching stocks. '+hidden+' other stock'+(hidden===1?' is':'s are')+
    ' hidden by the view and the filters ('+all.length+' stocks in all).';
  const box=$('moreBox'); box.innerHTML='';
  if(limit!==0&&n<list.length){const b=el('button','more','Show more ('+Math.min(limit,list.length-n)+' more)'); b.onclick=()=>{shown+=limit; render();}; box.appendChild(b);}
}
function reset(){shown=limit||25; render();}
function resetAll(){   // back to the default: Worth following, no filters, no search, default sort, 25 rows
  view='worth'; open=null; scrollEnd=true; sortKey=null; sortDir=-1; limit=D.first_rows; shown=limit;
  $('fstatus').value=''; $('falert').value=''; $('fqual').value=''; $('fmin').value='0'; $('fsym').value=''; $('frows').value=String(limit);
  $('views').querySelectorAll('button').forEach((b,i)=>b.classList.toggle('on',VIEWS[i][0]===view));
  render();
}
function setup(){
  const vb=$('views');
  VIEWS.forEach(([k,label])=>{const b=el('button','v'+(k===view?' on':''),label);
    b.onclick=()=>{view=k; vb.querySelectorAll('button').forEach(x=>x.classList.remove('on')); b.classList.add('on'); reset();}; vb.appendChild(b);});
  const fs=$('fstatus'); fs.appendChild(Object.assign(el('option'),{value:'',textContent:'Any'}));
  Object.keys(STATUS).forEach(c=>fs.appendChild(Object.assign(el('option'),{value:c,textContent:STATUS[c]})));
  ['fstatus','falert','fqual','fmin','fsym'].forEach(id=>{$(id).addEventListener(id==='fsym'||id==='fmin'?'input':'change',reset);});
  $('frows').onchange=()=>{limit=parseInt($('frows').value,10); shown=limit; render();};
  $('freset').onclick=resetAll;
  const lg=$('legend');
  D.legend.forEach(([c,text])=>{const s=el('span','leg'); const b=el('b',null,c==='(blank)'?'':c);
    if(D.fills[c]){b.style.background=D.fills[c]; b.style.color=D.fonts[c]||'#000';} else b.style.border='1px solid #bbb';
    s.appendChild(b); s.appendChild(document.createTextNode(text)); lg.appendChild(s);});
  const al=el('span','leg'); const dot=el('b',null,'●'); dot.style.color='#d22'; al.appendChild(dot);
  al.appendChild(document.createTextNode('Alert that day (score 70 or more)')); lg.appendChild(al);
  $('info').textContent=D.rows.length?('Run days: '+D.days.length+' ('+D.days[0]+' to '+D.days[D.days.length-1]+'). The first four columns and the title row stay in place. The strip opens at the newest day; scroll left for older days. Worth following = a breakout or an alert at least once, or near breakout now, and listed in the last '+D.recent_days+' run days.'):'';
  D.rows.forEach((r,i)=>{r._i=i;});
  scrollEnd=true; render();
}
fetch('/api/tracker').then(r=>r.json()).then(d=>{D=d; limit=D.first_rows; shown=limit; setup();});
</script></body></html>"""


if __name__ == "__main__":
    serve()
