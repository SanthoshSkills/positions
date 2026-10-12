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

HOST = os.environ.get("HUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))
TOKEN = secrets.token_hex(16)
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "0") == "1"  # off by default on servers

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
        cr = p.parent / "candle_report.html"
        if cr.exists():
            links.append({"text": "Candlestick report", "url": _url(cr), "target": "candle_view"})
        items.append({"label": f"Screen of {p.parent.name}", "when": stamp(p), "links": links})
    for p in reversed(sorted(Path().glob("output/lookup_*/report.html"))[-5:]):
        links = [{"text": "Lookup report", "url": _url(p), "target": "screener_view"}]
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
        if os.environ.get("RAILWAY_ENVIRONMENT"):
            return True  # on Railway, any host is fine (it's behind their proxy)
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "Forbidden")
        u = urlparse(self.path)
        path, qs = unquote(u.path), parse_qs(u.query)
        if path == "/":
            return self._send(200, INDEX.replace("__TOKEN__", TOKEN))
        if path == "/api/search":
            return self._json(search(qs.get("q", [""])[0]))
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
    url = f"http://{HOST}:{server.server_address[1]}/"
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
INDEX = """<!doctype html><html><head><meta charset='utf-8'><title>Trading hub</title>
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
</style></head><body>
<header><h1>Trading hub</h1><button class='stop' onclick='quit()'>Quit</button></header>
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

<div class='card'><h2>Progress</h2>
<p id='status'>Idle.</p><div id='result'></div><pre id='log'></pre></div>

<div class='card'><h2>Reports</h2><p class='hint'>Every stock in the reports has Google and Yahoo live-chart links.</p>
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


if __name__ == "__main__":
    serve()
