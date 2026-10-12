#!/usr/bin/env python3
"""
NSE positional screener, version 3 (for holding 6-12+ months)

What it does
  1. Loads the Nifty 500 list (or your own tickers.csv).
  2. Downloads ~5 years of daily prices (Yahoo Finance, NSE tickers end in .NS).
  3. Keeps stocks that are: liquid, in a long-term uptrend, close to their
     52-week high, and beating the Nifty over 6 and 12 months.
  4. Looks at the last ~10 weeks for a "base" and tells you whether the stock
     is breaking out, near its breakout price, or still building.
  5. Finds support and resistance bands from the weekly chart and, where it
     can, places the stop-loss just under the nearest support band.
  6. Checks profit growth and debt (needs fundamentals.py in the same folder)
     and hides businesses that look weak.
  7. Saves results.csv, one 3-panel chart per stock (daily, weekly, monthly)
     and a report.html in large type that opens in your browser.

Setup (once):   pip install yfinance pandas matplotlib numpy
Run:            python nse_screener.py

This is a screening tool, not advice. Always check a stock yourself and
backtest rules before risking real money (see backtest.py).
"""

import datetime as dt
import math
import sys
import urllib.request
import webbrowser
from io import StringIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

try:
    from fundamentals import check_many
except ImportError:  # the screener still works without it
    check_many = None

# ----------------------------- SETTINGS (edit me) -----------------------------
CAPITAL = 25_000             # your total trading capital in Rs
RISK_PER_TRADE_PCT = 2.0     # lose at most this % of capital if the stop is hit
MAX_POSITION_PCT = 20.0      # never put more than this % of capital in one stock

USE_SUPPORT_STOP = True      # put the stop just under a support band when possible
STOP_PCT = 12.0              # fallback stop distance below entry (when no good support)
MIN_STOP_PCT = 6.0           # a support-based stop must be at least this far below entry
MAX_STOP_PCT = 15.0          # ...and no further than this
TARGET_RR = 2.0              # target sale price = buy price + this many times the risk (buy price minus stop)

MIN_TRADED_VALUE = 5e7       # min median daily traded value (Rs 5 crore)
MAX_BELOW_52W_HIGH = 15.0    # must be within this % of the 52-week high
PIVOT_DAYS = 50              # base window (about 10 weeks) used for the breakout price
MAX_BASE_DEPTH = 25.0        # a "tight" base falls less than this % from top to bottom
EXTENDED_PCT = 7.0           # more than this % above the breakout price = too late
TOP_N = 15                   # how many stocks to chart and list

CHECK_FUNDAMENTALS = True    # check profit growth and debt (see fundamentals.py)
FUNDAMENTALS_FOR = 40        # check this many of the best-ranked stocks
SKIP_WEAK = True             # hide stocks whose business quality looks WEAK

CHART_FONT_SIZE = 16         # make this bigger (18, 20 ...) if you want larger text
CHART_LAYOUT = "side"        # "side" = daily, weekly, monthly next to each other; "stacked" = one under another
CHART_STYLE = "heikin"       # "heikin" = Heikin Ashi + Supertrend (10, 2) + MACD + RSI (ha_chart.py); "classic" = the old price-line charts
OPEN_REPORT = True           # open report.html in your browser when finished

BENCHMARK = "^NSEI"          # Nifty 50
HISTORY = "5y"               # how much history to download
UNIVERSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
LOCAL_TICKERS = "tickers.csv"  # optional: a file with a 'Symbol' column
# ------------------------------------------------------------------------------

# Click-to-zoom viewer for chart images (same as the candlestick report). Used by the screener and lookup reports.
ZOOM_CSS = r'''img.chart{cursor:zoom-in}
#lb{position:fixed;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.92);display:none;z-index:50;overflow:auto}
#lb img{display:block;margin:auto;flex:none;max-width:none;cursor:grab;user-select:none;-webkit-user-drag:none}
#lbbar{position:fixed;right:24px;top:14px;z-index:60;display:none}
#lbbar button{font-size:24px;padding:8px 16px;margin-left:8px;border-radius:8px;border:0;cursor:pointer}
#lbhint{position:fixed;left:20px;top:20px;z-index:60;color:#eee;font-size:19px;display:none;
 background:rgba(0,0,0,.55);padding:4px 10px;border-radius:6px}
'''
ZOOM_HTML = r'''<div id='lbhint'>Wheel = zoom &nbsp; Drag = move &nbsp; Double-click = zoom in/out &nbsp; Esc = close</div>
<div id='lbbar'><button id='zin'>+</button><button id='zout'>&minus;</button><button id='zfit'>Fit</button><button id='z100'>100%</button><button id='zx'>Close</button></div>
<div id='lb' tabindex='-1'><img id='lbimg' alt='' draggable='false'></div>
<script>
(function(){
var lb=document.getElementById('lb'), im=document.getElementById('lbimg');
var bar=document.getElementById('lbbar'), hint=document.getElementById('lbhint');
var s=1, fit=1, MAXS=3, dragging=false, sx=0, sy=0, sl=0, st=0;
function setScale(ns, cx, cy){
  ns=Math.min(Math.max(ns, fit), Math.max(MAXS, fit));
  if(Math.abs(ns-s)<1e-6) return;
  var r=im.getBoundingClientRect(), ox=cx-r.left, oy=cy-r.top, k=ns/s;
  s=ns; im.style.width=(im.naturalWidth*s)+'px';
  lb.scrollLeft=ox*k-cx; lb.scrollTop=oy*k-cy;
}
function fitView(){
  fit=Math.min(1, lb.clientWidth/im.naturalWidth, lb.clientHeight/im.naturalHeight);
  s=fit; im.style.width=(im.naturalWidth*s)+'px'; lb.scrollLeft=0; lb.scrollTop=0;
}
function centre(f){ setScale(s*f, lb.clientWidth/2, lb.clientHeight/2); }
function openLb(src){
  lb.style.display='flex'; bar.style.display=hint.style.display='block';
  document.body.style.overflow='hidden';
  im.onload=fitView; im.src=src;
  if(im.complete && im.naturalWidth){ fitView(); }
  lb.focus();
}
function closeLb(){
  lb.style.display='none'; bar.style.display=hint.style.display='none';
  document.body.style.overflow=''; dragging=false;
}
Array.prototype.forEach.call(document.querySelectorAll('img.chart'), function(i){
  i.addEventListener('click', function(){ openLb(i.src); });
});
lb.addEventListener('wheel', function(e){
  e.preventDefault(); setScale(s*(e.deltaY<0?1.2:1/1.2), e.clientX, e.clientY);
}, {passive:false});
im.addEventListener('dragstart', function(e){ e.preventDefault(); });
im.addEventListener('mousedown', function(e){
  if(e.button!==0) return;
  dragging=true; sx=e.clientX; sy=e.clientY; sl=lb.scrollLeft; st=lb.scrollTop;
  im.style.cursor='grabbing'; e.preventDefault();
});
window.addEventListener('mousemove', function(e){
  if(!dragging) return;
  lb.scrollLeft=sl-(e.clientX-sx); lb.scrollTop=st-(e.clientY-sy);
});
window.addEventListener('mouseup', function(){ dragging=false; im.style.cursor='grab'; });
im.addEventListener('dblclick', function(e){
  if(s>fit*1.05) fitView(); else setScale(Math.max(1, fit*2.5), e.clientX, e.clientY);
});
document.getElementById('zin').onclick=function(){ centre(1.4); };
document.getElementById('zout').onclick=function(){ centre(1/1.4); };
document.getElementById('zfit').onclick=fitView;
document.getElementById('z100').onclick=function(){ setScale(1, lb.clientWidth/2, lb.clientHeight/2); };
document.getElementById('zx').onclick=closeLb;
document.addEventListener('keydown', function(e){
  if(lb.style.display!=='flex') return;
  if(e.key==='Escape') closeLb();
  else if(e.key==='+'||e.key==='=') centre(1.4);
  else if(e.key==='-') centre(1/1.4);
  else if(e.key==='0') fitView();
});
})();
</script>
'''

STATUS_ORDER = {
    "Breakout": 0,
    "Near breakout price": 1,
    "Building base": 2,
    "Breakout, weak volume": 3,
    "Extended - wait for pullback": 4,
}

OHLC_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}

plt.rcParams.update({
    "font.size": CHART_FONT_SIZE,
    "axes.titlesize": CHART_FONT_SIZE + 3,
    "axes.titleweight": "bold",
    "legend.fontsize": CHART_FONT_SIZE + 1,
    "legend.handlelength": 2.6,
    "xtick.labelsize": CHART_FONT_SIZE,
    "ytick.labelsize": CHART_FONT_SIZE,
    "figure.titlesize": CHART_FONT_SIZE + 4,
    "figure.titleweight": "bold",
    "lines.linewidth": 1.8,
})


# ------------------------------- DATA -----------------------------------------
def get_universe():
    """Return a list of NSE symbols (without .NS)."""
    if Path(LOCAL_TICKERS).exists():
        df = pd.read_csv(LOCAL_TICKERS)
        return df["Symbol"].astype(str).str.strip().tolist()
    req = urllib.request.Request(UNIVERSE_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        sys.exit(
            f"Could not download the Nifty 500 list ({e}).\n"
            f"Download it from niftyindices.com, save a 'Symbol' column as "
            f"{LOCAL_TICKERS} next to this script, and run again."
        )
    return pd.read_csv(StringIO(text))["Symbol"].astype(str).str.strip().tolist()


def download_prices(symbols):
    """Download daily OHLCV for each symbol in batches. Returns {symbol: DataFrame}."""
    import yfinance as yf

    prices = {}
    for i in range(0, len(symbols), 100):
        batch = [s + ".NS" for s in symbols[i : i + 100]]
        print(f"Downloading {i + 1}-{i + len(batch)} of {len(symbols)} ...")
        raw = yf.download(
            batch, period=HISTORY, interval="1d", auto_adjust=True,
            group_by="ticker", progress=False, threads=True,
        )
        for t in batch:
            try:
                df = raw[t].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(df) > 260:
                prices[t[:-3]] = df
    return prices


def download_benchmark():
    import yfinance as yf

    df = yf.download(BENCHMARK, period=HISTORY, interval="1d", auto_adjust=True, progress=False)
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.dropna()


def resample_ohlc(df, kind):
    """Turn daily bars into weekly ('W') or monthly ('M') bars."""
    rules = {"W": ["W-FRI"], "M": ["ME", "M"]}[kind]  # 'ME' on new pandas, 'M' on old
    for rule in rules:
        try:
            return df.resample(rule).agg(OHLC_AGG).dropna()
        except ValueError:
            continue
    raise RuntimeError("Could not resample prices")


# --------------------- SUPPORT / RESISTANCE AND STOP --------------------------
def swing_points(wdf, k=4):
    """Prices of weekly swing highs and lows (a high/low that stands out for k weeks each side)."""
    highs, lows = wdf["High"].values, wdf["Low"].values
    pts = []
    for i in range(k, len(wdf) - k):
        if highs[i] == highs[i - k : i + k + 1].max():
            pts.append(float(highs[i]))
        if lows[i] == lows[i - k : i + k + 1].min():
            pts.append(float(lows[i]))
    return pts


def sr_bands(wdf, last, tol=0.03, max_bands=6):
    """
    Group swing points that sit within ~3% of each other into bands.
    A band touched more often is stronger. Returns the bands nearest to the price.
    """
    clusters = []
    for p in sorted(swing_points(wdf)):
        if clusters and p <= clusters[-1][0] * (1 + tol):
            clusters[-1].append(p)
        else:
            clusters.append([p])
    bands = []
    for c in clusters:
        if len(c) < 2:
            continue
        low, high = min(c), max(c)
        if (high - low) / low < 0.01:  # make very thin bands visible
            low, high = low * 0.995, high * 1.005
        mid = (low + high) / 2
        if 0.6 * last <= mid <= 1.5 * last:
            bands.append({"low": low, "high": high, "touches": len(c)})
    bands.sort(key=lambda b: abs((b["low"] + b["high"]) / 2 / last - 1))
    return bands[:max_bands]


def choose_stop(entry, bands):
    """Stop just under the nearest sensible support band, else a flat % stop."""
    if USE_SUPPORT_STOP:
        below = sorted([b for b in bands if b["low"] < entry * 0.97],
                       key=lambda b: b["low"], reverse=True)
        for b in below:
            stop = b["low"] * 0.99
            dist = (entry - stop) / entry * 100
            if MIN_STOP_PCT <= dist <= MAX_STOP_PCT:
                return stop, "below support band"
    return entry * (1 - STOP_PCT / 100), f"flat {STOP_PCT:.0f}%"


def target_price(entry, stop):
    """Target sale price: TARGET_RR times the distance between the buy price and the stop-loss, above the buy price."""
    return entry + TARGET_RR * (entry - stop)


def band_text(b):
    return f"{b['low']:.0f}-{b['high']:.0f} ({b['touches']} touches)" if b else ""


# ------------------------------- SCREEN ---------------------------------------
def analyze(df, bench):
    """Return a dict of results if the stock passes all filters, else None."""
    close, high, vol = df["Close"], df["High"], df["Volume"]
    if len(close) < 253 or len(bench) < 253:
        return None

    last = float(close.iloc[-1])
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    # 1. Liquidity: can you buy and sell easily?
    traded_value = float((close * vol).rolling(50).median().iloc[-1])
    if traded_value < MIN_TRADED_VALUE:
        return None

    # 2. Long-term uptrend
    if not (last > sma200.iloc[-1] and last > sma50.iloc[-1]
            and sma200.iloc[-1] > sma200.iloc[-21]):
        return None

    # 3. Close to the 52-week high
    hi52 = float(high.iloc[-252:].max())
    below_high = (1 - last / hi52) * 100
    if below_high > MAX_BELOW_52W_HIGH:
        return None

    # 4. Beating the Nifty over 6 and 12 months
    r6 = last / float(close.iloc[-127]) - 1
    r12 = last / float(close.iloc[-253]) - 1
    b6 = float(bench.iloc[-1] / bench.iloc[-127] - 1)
    b12 = float(bench.iloc[-1] / bench.iloc[-253] - 1)
    if r6 <= b6 or r12 <= b12:
        return None
    rel_strength = ((r6 - b6) + (r12 - b12)) / 2 * 100

    # 5. Base and breakout status (base = prior PIVOT_DAYS days, excluding today)
    base = df.iloc[-PIVOT_DAYS - 1 : -1]
    pivot = float(base["High"].max())
    base_low = float(base["Low"].min())
    depth = (pivot - base_low) / pivot * 100
    vol_ratio = float(vol.iloc[-5:].mean() / vol.iloc[-51:-1].mean())

    if last > pivot * (1 + EXTENDED_PCT / 100):
        status = "Extended - wait for pullback"
    elif last > pivot:
        status = "Breakout" if vol_ratio >= 1.2 else "Breakout, weak volume"
    elif pivot / last - 1 <= 0.03:
        status = "Near breakout price"
    else:
        status = "Building base"

    # 6. Support / resistance bands from the weekly chart
    wdf = resample_ohlc(df, "W").iloc[-260:]
    bands = sr_bands(wdf, last)
    supports = sorted([b for b in bands if b["high"] <= last], key=lambda b: b["high"], reverse=True)
    resists = sorted([b for b in bands if b["low"] >= last], key=lambda b: b["low"])

    # 7. Entry, stop and position size
    if status.startswith("Extended"):
        entry = stop = qty = target = float("nan")
        stop_basis = ""
    else:
        entry = pivot * 1.005
        stop, stop_basis = choose_stop(entry, bands)
        target = target_price(entry, stop)
        risk_amount = CAPITAL * RISK_PER_TRADE_PCT / 100
        by_risk = math.floor(risk_amount / (entry - stop))
        by_cap = math.floor(CAPITAL * MAX_POSITION_PCT / 100 / entry)
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
        "tight_base": "Yes" if depth <= MAX_BASE_DEPTH else "No",
        "base_depth_pct": round(depth, 1),
        "pct_below_52w_high": round(below_high, 1),
        "return_6m_pct": round(r6 * 100, 1),
        "return_12m_pct": round(r12 * 100, 1),
        "vs_nifty_score": round(rel_strength, 1),
        "volume_vs_avg": round(vol_ratio, 2),
        "avg_daily_value_cr": round(traded_value / 1e7, 1),
        "support_band": band_text(supports[0] if supports else None),
        "resistance_band": band_text(resists[0] if resists else None),
        "_pivot": pivot,
        "_bands": bands,
    }


# ------------------------------- CHARTS ---------------------------------------
def draw_panel(ax, data, title, window, mas, per_year, res, daily=False,
               legend_cols=3, legend_y=-0.14, two_line=False):
    last = res["last_close"]
    d = data.iloc[-window:]
    ax.plot(d.index, d["Close"], color="black", label="Close")
    for n, label, color in mas:
        ma = data["Close"].rolling(n).mean().iloc[-window:]
        ax.plot(ma.index, ma, linewidth=1.5, label=label, color=color)

    # Trend channel: straight-line fit on log prices, +/- 2 standard deviations
    y = np.log(d["Close"].values.astype(float))
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    fit = slope * x + intercept
    sd = float((y - fit).std())
    ax.plot(d.index, np.exp(fit), color="purple", linewidth=1.6, linestyle="--", label="Trend")
    ax.fill_between(d.index, np.exp(fit - 2 * sd), np.exp(fit + 2 * sd), color="purple", alpha=0.06)
    annual = (np.exp(slope * per_year) - 1) * 100

    # Support / resistance bands (green below price, red above, grey if price is inside)
    lo, hi = float(d["Low"].min()), float(d["High"].max())
    shown = set()
    for b in res["_bands"]:
        if b["high"] < lo * 0.9 or b["low"] > hi * 1.1:
            continue
        if b["high"] <= last:
            color, name = "tab:green", "Support"
        elif b["low"] >= last:
            color, name = "tab:red", "Resistance"
        else:
            color, name = "tab:gray", "Price inside band"
        alpha = min(0.10 + 0.05 * b["touches"], 0.40)
        ax.axhspan(b["low"], b["high"], color=color, alpha=alpha,
                   label=None if name in shown else name)
        shown.add(name)

    if daily:
        ax.axhline(res["_pivot"], color="green", linestyle=":", linewidth=2, label="Breakout price")
        if res["stop_loss"]:
            ax.axhline(res["stop_loss"], color="red", linestyle=":", linewidth=2, label="Stop-loss")

    ax.set_ylim(lo * 0.95, hi * 1.05)
    loc_ = mdates.AutoDateLocator(minticks=3, maxticks=5)  # fewer, cleaner date labels
    ax.xaxis.set_major_locator(loc_)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc_))
    sep = "\n" if two_line else "   |   "
    ax.set_title(f"{title}{sep}trend {annual:+.0f}% per year", loc="left")
    # Legend sits BELOW the chart so it never covers the price line
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, legend_y), ncol=legend_cols, frameon=False)
    ax.grid(alpha=0.25)


def make_chart(symbol, df, res, out_path):
    if CHART_STYLE == "heikin":
        import ha_chart
        ha_chart.make_chart(symbol, df, res, out_path, font=CHART_FONT_SIZE, layout=CHART_LAYOUT)
        return
    weekly = resample_ohlc(df, "W")
    monthly = resample_ohlc(df, "M")
    if CHART_LAYOUT == "side":
        fig, axes = plt.subplots(1, 3, figsize=(27, 10.5), constrained_layout=True)
        kw = dict(legend_cols=2, legend_y=-0.09, two_line=True)
        dpi = 80
    else:
        fig, axes = plt.subplots(3, 1, figsize=(11, 20), constrained_layout=True)
        kw = {}
        dpi = 90
    draw_panel(axes[0], df, "Daily (1 year)", 252,
               [(50, "50-day avg", "tab:blue"), (200, "200-day avg", "tab:orange")], 252, res,
               daily=True, **kw)
    draw_panel(axes[1], weekly, "Weekly (3 years)", min(156, len(weekly)),
               [(30, "30-week avg", "tab:blue")], 52, res, **kw)
    draw_panel(axes[2], monthly, "Monthly (5 years)", min(60, len(monthly)),
               [(10, "10-month avg", "tab:blue")], 12, res, **kw)
    buy, stop = res["buy_above"], res["stop_loss"]
    last = res["last_close"]
    plan = (f"latest {last} | buy above {buy} / stop {stop} / target {res['target_price']}" if buy
            else f"latest {last} | extended - no entry now")
    fig.suptitle(f"{symbol}  -  {res['status']}   |   {plan}")
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)


def live_links_html(symbol):
    """Links to live charts of the stock on Google Finance and Yahoo Finance (new tab). NSE data there can be delayed."""
    from urllib.parse import quote

    q = quote(symbol, safe="")
    return (f"<a class='lv' href='https://www.google.com/finance/quote/{q}:NSE' target='_blank' "
            f"rel='noopener'>Google chart</a> &nbsp;"
            f"<a class='lv' href='https://finance.yahoo.com/chart/{q}.NS' target='_blank' "
            f"rel='noopener'>Yahoo chart</a>")


def price_line_html(last, day, buy, stop):
    """One line: latest price, buy-above, stop-loss with the loss if stopped, target with the profit."""
    parts = [f"Latest price <b>{last:,.2f}</b> (close of {day})"]
    if pd.isna(buy):
        parts.append("No buy price (extended: wait for a pullback)")
        return " &nbsp;|&nbsp; ".join(parts)
    parts.append(f"Buy above <b>{buy:,.2f}</b> ({(buy / last - 1) * 100:+.1f}% from latest)")
    if not pd.isna(stop):
        tgt = target_price(buy, stop)
        parts.append(f"Stop-loss <b>{stop:,.2f}</b> (<b style='color:#b22222'>loss {abs(stop / buy - 1) * 100:.1f}%</b> "
                     f"from the buy price)")
        parts.append(f"Target <b>{tgt:,.2f}</b> (<b style='color:#1b7f3b'>profit {(tgt / buy - 1) * 100:.1f}%</b> "
                     f"from the buy price, {TARGET_RR:g}:1 reward to risk)")
    return " &nbsp;|&nbsp; ".join(parts)


def write_report(shown, hidden, out_dir):
    cols = ["symbol", "status", "quality", "last_close", "buy_above", "stop_loss",
            "suggested_qty", "tight_base"]
    tbl = shown[cols].copy()
    tbl["symbol"] = tbl["symbol"].map(
        lambda s: f"<a href='#{s}'>{s}</a> &nbsp;<a class='cv' href='candle_report.html#{s}' target='candle_view'>candlesticks</a> &nbsp;{live_links_html(s)}")
    tbl["stop_loss"] = ["-" if pd.isna(v) else f"{v:,.2f} (loss {abs(p):.1f}%)"
                        for v, p in zip(shown["stop_loss"], shown["stop_loss_pct"])]
    tbl.insert(tbl.columns.get_loc("stop_loss") + 1, "target (profit)",
               ["-" if pd.isna(v) else f"{v:,.2f} (+{p:.1f}%)"
                for v, p in zip(shown["target_price"], shown["profit_pct"])])
    tbl = tbl.rename(columns={"last_close": "latest price (close)", "stop_loss": "stop-loss (loss)"})
    table = tbl.to_html(index=False, border=0, na_rep="-", escape=False)
    parts = []
    for _, r in shown.iterrows():
        q = r.get("quality", "")
        notes = r.get("quality_notes", "")
        parts.append(
            f"<div id='{r['symbol']}'><h2>{r['symbol']} &nbsp;<span class='q {q}'>{q}</span> &nbsp;"
            f"<a class='cv' href='candle_report.html#{r['symbol']}' target='candle_view'>candlesticks</a> &nbsp;{live_links_html(r['symbol'])}</h2>"
            f"<p>{price_line_html(r['last_close'], r['data_date'], r['buy_above'], r['stop_loss'])}</p>"
            f"<p>{notes}</p><img class='chart' title='Click to zoom' src='charts/{r['symbol']}.png'></div>"
        )
    hid = ""
    if len(hidden):
        items = "".join(f"<li><b>{r['symbol']}</b>: {r['quality_notes']}</li>" for _, r in hidden.iterrows())
        hid = f"<h2>Hidden because the business looks weak</h2><ul>{items}</ul>"
    if CHART_STYLE == "heikin":
        chart_help = ("candles are Heikin Ashi (smoothed: their open and close are not real prices). The green or red "
                      "line on the price panel is the Supertrend (green under price = uptrend, red over price = downtrend, "
                      "a triangle marks each flip). Under it: MACD (blue) with its signal line (orange), a triangle at every "
                      "crossover, then RSI. The dotted lines are the real buy-above, stop-loss and target prices.")
    else:
        chart_help = "the purple dashed line is the trend (shaded area = normal range)."
    html = f"""<html><head><meta charset='utf-8'><title>NSE screen {dt.date.today()}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5}}
table{{border-collapse:collapse;font-size:19px}}
td,th{{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}}
img{{width:100%;height:auto}}
.q{{padding:3px 12px;border-radius:6px;color:white;font-size:19px}}
.STRONG{{background:#1b7f3b}} .CHECK{{background:#b8860b}} .WEAK{{background:#b22222}}
.NOT{{background:#777}}
a.cv,a.lv{{font-size:19px;font-weight:normal}}
html{{scroll-behavior:smooth}}
#top-btn{{position:fixed;right:24px;bottom:24px;background:#1f4e9c;color:white;text-decoration:none;font-size:22px;font-weight:bold;padding:14px 20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.4)}}
{ZOOM_CSS}
</style></head><body id='top'>
<script>window.name='screener_view';</script>
<h1>NSE positional screen - {dt.date.today()}</h1>
<p>Prices are end-of-day. Buy-above and stop-loss are starting points, not advice. <b>Buy above</b> = the highest price of the last 10 weeks plus 0.5%: you buy only if the price trades above it. <b>Stop-loss</b> = the price at which you sell to cap the loss. <b>Target</b> = the buy price plus {TARGET_RR:g} times the risk (buy price minus stop-loss): a planning level, not a prediction. Profit and loss percentages are measured from the buy price, before charges. <b>Latest price</b> = the most recent closing price in the data.
Quantity is sized so a stop-out loses about {RISK_PER_TRADE_PCT}% of Rs {CAPITAL:,}.</p>
<p><b>Business quality:</b> STRONG = profits growing and debt under control.
CHECK = the free data was incomplete, so look the company up yourself first.
WEAK = hidden from this list.</p>
<p><b>Reading the charts:</b> {chart_help}
Green bands are support, red bands are resistance, darker means more touches.
Bands are a guide, not a promise. If one looks wrong to your eye, trust your eye.
<b>Click any chart to zoom</b> (mouse wheel to zoom, drag to move, double-click to zoom in or out, Esc to close).</p>
{table}{''.join(parts)}{hid}<a id='top-btn' href='#top'>&uarr; Top</a>{ZOOM_HTML}</body></html>"""
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------- MAIN -----------------------------------------
def run(prices, bench):
    rows = []
    for symbol, df in prices.items():
        try:
            res = analyze(df, bench)
        except Exception:  # noqa: BLE001  (skip any stock with bad data)
            continue
        if res:
            res["symbol"] = symbol
            rows.append(res)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_order"] = out["status"].map(STATUS_ORDER)
    out["_tight"] = (out["tight_base"] != "Yes").astype(int)
    out = out.sort_values(["_order", "_tight", "vs_nifty_score"], ascending=[True, True, False])
    return out.reset_index(drop=True)


def add_quality(results):
    """Add 'quality' and 'quality_notes' columns (checks the best-ranked stocks only)."""
    results["quality"] = "NOT CHECKED"
    results["quality_notes"] = ""
    if not CHECK_FUNDAMENTALS:
        return results
    if check_many is None:
        print("fundamentals.py not found next to this script, so profit and debt were not checked.")
        return results
    n = min(FUNDAMENTALS_FOR, len(results))
    print(f"\nChecking profit growth and debt for the best {n} stocks (about a minute or two) ...")
    verdicts = check_many(list(results["symbol"].head(n)))
    for sym, (verdict, notes) in verdicts.items():
        mask = results["symbol"] == sym
        results.loc[mask, "quality"] = verdict
        results.loc[mask, "quality_notes"] = notes
    return results


def main():
    symbols = get_universe()
    print(f"{len(symbols)} symbols in universe")
    prices = download_prices(symbols)
    bench = download_benchmark()
    results = run(prices, bench)

    out_dir = Path("output") / str(dt.date.today())
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    if results.empty:
        print("No stocks passed the filters today. That is normal in weak markets.")
        return

    results = add_quality(results)
    weak = results[results["quality"] == "WEAK"]
    shown = results[results["quality"] != "WEAK"] if SKIP_WEAK else results
    shown = shown.head(TOP_N)
    hidden = weak.head(TOP_N) if SKIP_WEAK else weak.iloc[0:0]

    print("\nDrawing charts ...")
    for _, row in shown.iterrows():
        make_chart(row["symbol"], prices[row["symbol"]], row, out_dir / "charts" / f"{row['symbol']}.png")

    cols = ["symbol", "data_date", "status", "quality", "quality_notes", "last_close", "buy_above",
            "stop_loss", "stop_loss_pct", "target_price", "profit_pct", "stop_basis", "suggested_qty", "tight_base", "base_depth_pct",
            "pct_below_52w_high", "return_6m_pct", "return_12m_pct", "vs_nifty_score",
            "volume_vs_avg", "avg_daily_value_cr", "support_band", "resistance_band"]
    results[cols].to_csv(out_dir / "results.csv", index=False)
    report = write_report(shown, hidden, out_dir)

    show = ["symbol", "status", "quality", "last_close", "buy_above", "stop_loss", "suggested_qty"]
    print(f"\n{len(results)} stocks passed the price rules; {len(weak)} hidden as weak businesses.")
    print(f"Top {len(shown)}:\n")
    print(shown[show].to_string(index=False))
    print(f"\nReport: {report}")
    print("To start paper trading the best ones:  python paper_tracker.py add")
    if OPEN_REPORT:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
