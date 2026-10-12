#!/usr/bin/env python3
"""
Backtest: how would the screener's price rules have done in past years?

What it does
  At the end of every past month it pretends it is that day, runs the same screener
  rules on only the data available then, and takes the picks. Each pick is then
  followed forward with the same plan you use for paper trading:
     - buy only if the price crosses the buy-above price within WAIT_DAYS
     - sell if the price hits the stop-loss
     - otherwise sell after HOLD_DAYS (about 12 months)
  It compares each trade with simply holding the Nifty over the same days.

Run:  python backtest.py        (first run downloads 10 years of prices: allow several minutes)
Then open backtest_output/backtest_report.html

Read this before trusting any number
  1. Survivorship bias: it uses today's Nifty 500 list, so companies that fell out of the
     index or vanished are missing. That makes results look BETTER than real life would have.
  2. It tests the PRICE rules only. Profit and debt data from free sources only goes back a
     few years, so the quality check cannot be tested fairly.
  3. Costs are a rough flat percentage and there is no allowance for missed fills.
  4. Past results do not predict the future. Use this to learn what the rules tend to do
     (how often they lose, how big the losses are), not to forecast profits.
"""

import datetime as dt
import pickle
import time
import webbrowser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import nse_screener as ns

# ----------------------------- SETTINGS (edit me) -----------------------------
HISTORY = "10y"             # price history to download
PICKS_PER_MONTH = 5         # the "top picks" group: same number the paper tracker adds
WAIT_DAYS = 30              # buy only if the buy-above price is reached within this many days
HOLD_DAYS = 250             # trading days to hold if the stop is never hit (about 12 months)
ROUND_TRIP_COST_PCT = 0.30  # rough charges for one buy plus one sell, in % of the trade
CACHE_FILE = "backtest_cache.pkl"
CACHE_DAYS = 7              # reuse downloaded prices for this many days
OUT_DIR = Path("backtest_output")
FONT = 14
OPEN_REPORT = True
# ------------------------------------------------------------------------------


# ------------------------------- DATA -----------------------------------------
def load_data():
    cache = Path(CACHE_FILE)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_DAYS * 86400:
        print(f"Using saved prices from {CACHE_FILE} (delete it to download fresh data).")
        with open(cache, "rb") as f:
            return pickle.load(f)
    ns.HISTORY = HISTORY
    symbols = ns.get_universe()
    print(f"Downloading {HISTORY} of prices for {len(symbols)} stocks ...")
    prices = ns.download_prices(symbols)
    bench = ns.download_benchmark()
    with open(cache, "wb") as f:
        pickle.dump((prices, bench), f)
    return prices, bench


def _naive(obj):
    if getattr(obj.index, "tz", None) is not None:
        obj = obj.copy()
        obj.index = obj.index.tz_localize(None)
    return obj


def month_ends(bench):
    s = bench.index.to_series()
    return [pd.Timestamp(d) for d in s.groupby([s.dt.year, s.dt.month]).max().values]


# ------------------------------ ONE TRADE -------------------------------------
def simulate_trade(df, bench, signal_date, buy, stop):
    """Follow one pick forward. Uses daily highs and lows; if buy and stop could both
    happen on the same day it assumes the stop was hit."""
    fut = df.loc[df.index > signal_date]
    if fut.empty:
        return {"outcome": "no data"}
    o, h = fut["Open"].values, fut["High"].values
    lo, c = fut["Low"].values, fut["Close"].values
    dates = fut.index

    entry_i = None
    for i in range(len(fut)):
        if (dates[i] - signal_date).days > WAIT_DAYS:
            break
        if h[i] >= buy:
            entry_i = i
            break
    if entry_i is None:
        waited_out = (dates[-1] - signal_date).days > WAIT_DAYS
        return {"outcome": "never triggered" if waited_out else "still waiting"}

    entry = max(buy, float(o[entry_i]))  # gap up: bought at the open
    exit_i = exit_px = None
    reason = ""
    for j in range(entry_i, len(fut)):
        if lo[j] <= stop:
            exit_i, exit_px, reason = j, min(stop, float(o[j])), "hit stop-loss"
            break
        if j - entry_i >= HOLD_DAYS:
            exit_i, exit_px, reason = j, float(c[j]), "held 12 months"
            break
    outcome = "closed"
    if exit_i is None:
        exit_i, exit_px, reason, outcome = len(fut) - 1, float(c[-1]), "still holding", "open"

    ret = (exit_px / entry - 1) * 100 - ROUND_TRIP_COST_PCT
    b_in = float(bench.loc[: dates[entry_i]].iloc[-1])
    b_out = float(bench.loc[: dates[exit_i]].iloc[-1])
    b_ret = (b_out / b_in - 1) * 100
    return {
        "outcome": outcome, "reason": reason,
        "entry_date": dates[entry_i].date(), "entry_price": round(entry, 2),
        "exit_date": dates[exit_i].date(), "exit_price": round(exit_px, 2),
        "days_held": (dates[exit_i] - dates[entry_i]).days,
        "return_pct": round(ret, 2), "nifty_pct": round(b_ret, 2),
        "vs_nifty_pct": round(ret - b_ret, 2),
    }


# ------------------------------ THE BACKTEST ----------------------------------
def run_backtest(prices, bench, verbose=True):
    bench = _naive(bench)
    prices = {k: _naive(v) for k, v in prices.items()}
    dates = [d for d in month_ends(bench) if len(bench.loc[:d]) >= 253]
    trades = []
    for n, T in enumerate(dates, 1):
        snap = {}
        for sym, df in prices.items():
            part = df.loc[:T]
            if len(part) >= 253 and (T - part.index[-1]).days <= 5:
                snap[sym] = part
        res = ns.run(snap, bench.loc[:T])
        if res.empty:
            continue
        cand = res[(res["tight_base"] == "Yes") & res["buy_above"].notna()]
        if verbose:
            print(f"  {T.date()}  ({n}/{len(dates)})  {len(cand)} candidates")
        for rank, (_, row) in enumerate(cand.iterrows(), 1):
            t = simulate_trade(prices[row["symbol"]], bench, T, float(row["buy_above"]),
                               float(row["stop_loss"]))
            t.update(symbol=row["symbol"], signal_date=T.date(), signal_year=T.year,
                     status_at_signal=row["status"], stop_basis=row["stop_basis"],
                     rank=rank, top_pick=rank <= PICKS_PER_MONTH)
            trades.append(t)
    return pd.DataFrame(trades)


# ------------------------------ STATISTICS ------------------------------------
def summarize(t):
    """Plain numbers for a group of trades."""
    done = t[t["outcome"] == "closed"]
    entered = t[t["outcome"].isin(["closed", "open"])]
    never = (t["outcome"] == "never triggered").sum()
    out = {
        "picks": len(t),
        "bought": len(entered),
        "never reached buy price": int(never),
        "finished trades": len(done),
    }
    if len(done):
        wins = done[done["return_pct"] > 0]
        losses = done[done["return_pct"] <= 0]
        out.update({
            "winners %": round(len(wins) / len(done) * 100, 1),
            "average result %": round(done["return_pct"].mean(), 1),
            "typical (median) result %": round(done["return_pct"].median(), 1),
            "average winner %": round(wins["return_pct"].mean(), 1) if len(wins) else 0.0,
            "average loser %": round(losses["return_pct"].mean(), 1) if len(losses) else 0.0,
            "best %": round(done["return_pct"].max(), 1),
            "worst %": round(done["return_pct"].min(), 1),
            "stopped out %": round((done["reason"] == "hit stop-loss").mean() * 100, 1),
            "average days held": int(done["days_held"].mean()),
            "Nifty over same days %": round(done["nifty_pct"].mean(), 1),
            "beat the Nifty %": round((done["vs_nifty_pct"] > 0).mean() * 100, 1),
        })
    return out


def by_group(done, col):
    g = done.groupby(col)
    tab = pd.DataFrame({
        "trades": g.size(),
        "winners %": g["return_pct"].apply(lambda s: round((s > 0).mean() * 100, 1)),
        "average result %": g["return_pct"].mean().round(1),
        "Nifty same days %": g["nifty_pct"].mean().round(1),
    })
    return tab.reset_index()


# ------------------------------ REPORT ----------------------------------------
def make_chart(done_top, path):
    plt.rcParams.update({"font.size": FONT, "axes.titlesize": FONT + 3, "axes.titleweight": "bold",
                         "legend.fontsize": FONT, "xtick.labelsize": FONT, "ytick.labelsize": FONT})
    fig, axes = plt.subplots(2, 1, figsize=(11, 12), constrained_layout=True)
    r = done_top["return_pct"]
    axes[0].hist(r, bins=30, color="tab:blue", alpha=0.85)
    axes[0].axvline(0, color="black", linewidth=2)
    axes[0].axvline(r.mean(), color="orange", linewidth=2.5, label=f"average {r.mean():+.1f}%")
    axes[0].set_title("How each finished trade ended (top picks)")
    axes[0].set_xlabel("Result of the trade, %")
    axes[0].set_ylabel("Number of trades")
    axes[0].legend()
    yr = done_top.groupby("signal_year").agg(picks=("return_pct", "mean"), nifty=("nifty_pct", "mean"))
    x = np.arange(len(yr))
    axes[1].bar(x - 0.2, yr["picks"], width=0.4, label="Screener picks", color="tab:blue")
    axes[1].bar(x + 0.2, yr["nifty"], width=0.4, label="Nifty, same days", color="tab:gray")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(yr.index.astype(str))
    axes[1].axhline(0, color="black", linewidth=1.5)
    axes[1].set_title("Average result by year the pick was made")
    axes[1].set_ylabel("Average result, %")
    axes[1].legend()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def write_report(trades, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False)
    top = trades[trades["top_pick"]]
    all_ = trades
    s_top, s_all = summarize(top), summarize(all_)
    table = pd.DataFrame({f"Top {PICKS_PER_MONTH} picks each month": pd.Series(s_top),
                          "All tight-base candidates": pd.Series(s_all)})
    done_top = top[top["outcome"] == "closed"]
    done_all = all_[all_["outcome"] == "closed"]

    sections = [f"<h2>Summary</h2>{table.to_html(border=0, na_rep='-')}"]
    if len(done_top):
        make_chart(done_top, out_dir / "backtest_chart.png")
        sections.append("<img src='backtest_chart.png'>")
        sections.append("<h2>By status when the pick was made (all candidates)</h2>"
                        + by_group(done_all, "status_at_signal").to_html(index=False, border=0))
        sections.append("<h2>By stop-loss type (all candidates)</h2>"
                        + by_group(done_all, "stop_basis").to_html(index=False, border=0))
        sections.append("<h2>By year (top picks)</h2>"
                        + by_group(done_top, "signal_year").to_html(index=False, border=0))

    headline = "Not enough finished trades to say anything."
    if len(done_top):
        headline = (
            f"Of {s_top['finished trades']} finished top-pick trades, {s_top['winners %']}% made money. "
            f"The average trade returned {s_top['average result %']:+.1f}% (typical trade "
            f"{s_top['typical (median) result %']:+.1f}%) against {s_top['Nifty over same days %']:+.1f}% "
            f"for the Nifty over the same days. {s_top['stopped out %']}% of trades ended at the stop-loss, "
            f"with an average loser of {s_top['average loser %']}% and a worst trade of {s_top['worst %']}%."
        )

    html = f"""<html><head><meta charset='utf-8'><title>Backtest</title>
<style>
body{{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5;max-width:1150px}}
table{{border-collapse:collapse;font-size:19px}}
td,th{{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}}
img{{width:100%;max-width:1100px;height:auto}}
.warn{{background:#fff3cd;border-left:8px solid #b8860b;padding:12px 18px}}
</style></head><body>
<h1>Backtest of the screener rules</h1>
<p><b>{headline}</b></p>
<div class='warn'><b>Be careful with these numbers.</b>
<ul>
<li>It uses today's Nifty 500 list, so failed companies are missing. Real results would likely be worse.</li>
<li>Only the price rules are tested, not the profit and debt check.</li>
<li>Many picks in the same month move together, so the trades are not fully independent.</li>
<li>The past does not predict the future. Look at how often it loses and how much, not just the average.</li>
</ul></div>
{''.join(sections)}
<p>Every individual trade is in trades.csv. Plan: buy only if the price crosses the buy-above price
within {WAIT_DAYS} days, sell at the stop-loss, otherwise sell after about 12 months. Rough charges of
{ROUND_TRIP_COST_PCT}% per trade are included.</p>
</body></html>"""
    path = out_dir / "backtest_report.html"
    path.write_text(html, encoding="utf-8")
    return path, headline


def main():
    prices, bench = load_data()
    print("Replaying the rules month by month (several minutes) ...")
    trades = run_backtest(prices, bench)
    if trades.empty:
        print("No picks were found in the history. Nothing to report.")
        return
    path, headline = write_report(trades, OUT_DIR)
    print("\n" + headline)
    print(f"\nFull report: {path}")
    if OPEN_REPORT:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
