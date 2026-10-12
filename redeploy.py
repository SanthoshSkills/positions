#!/usr/bin/env python3
"""
Where could the money go? Three options for the proceeds of a sale

What it does
  You name a holding you are thinking of selling (and how much), or just an amount. It then
    1. works out what you would actually get: price x quantity, minus rough selling charges, and
       a tax RANGE (see "Tax" below);
    2. looks at your latest screener results (results.csv) and your other holdings, and gives three options:
         OPTION 1  Best screener alternative: a stock you do not hold, not in the sector that would
                   already be too big, healthy business (STRONG before CHECK, never WEAK), still near
                   its breakout price, tight base, and moving least like the rest of your portfolio.
         OPTION 2  Same-theme swap: the strongest-relative-strength screener stock in the SAME sector
                   as the one you are selling (for an ETF: a broad index ETF), if you want to stay
                   exposed but leave the weakening stock.
         OPTION 3  Park it: a liquid ETF or fund while you wait, a broad index ETF, and the screener
                   stocks that are too extended to buy today (the "wait for a pullback" list).
    3. for each stock option shows buy-above, stop-loss, target, how many shares the proceeds buy and
       how much of the proceeds you would lose if the stop were hit.
    4. SPLIT OPTIONS: three baskets of 3 stocks each (A diversified, B strongest momentum, C quality first with the
       smallest stops), the proceeds divided equally inside a basket, with shares, cost and the loss if every stop were hit.
  Output: a large-type page output/portfolio/redeploy_<SYMBOL>.html plus a summary here.

Run (from the same folder as the other scripts):
    python redeploy.py NRBBEARING          sell ALL of that holding
    python redeploy.py NRBBEARING 50       sell 50 shares
    python redeploy.py NRBBEARING 50%      sell half
    python redeploy.py 50000               you already know the amount to place (Rs)
  Run the screener first (python trading_hub.py screen) so the results are fresh.

Tax (read this)
  Rates in the settings (20% short-term, 12.5% long-term gains, as I understand current Indian rules)
  are an ASSUMPTION: check them, and the yearly exemption, with your tax advisor. Holdings you imported
  have no buy dates, so shares from the baseline could be either. The page therefore shows the proceeds
  BEFORE tax and a low and high figure AFTER tax. Shares bought after your baseline (recorded with buy)
  are taxed by their real dates, and the page warns if waiting a few days would change the rate.
  Sizing uses the cautious (high-tax) figure. Losses may offset other gains; that is not modelled.

Honest limits
  * This is screening output, not advice. The options are ideas to research, not recommendations.
  * Screener results are end-of-day and only as fresh as your last screen. Buy-above levels can be stale.
  * Sector comes from Yahoo Finance's free data and may be missing; sector rules are skipped then.
  * Correlation uses about a year of daily returns; stocks that move together in a crash will not
    protect each other, whatever the number says.
"""

import datetime as dt
import json
import math
import re
import sys
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

import nse_screener as ns
import portfolio as pf

# ----------------------------- SETTINGS (edit me) -----------------------------
SELL_COST_PCT = 0.15          # rough taxes and charges on the sale, in %
FLAT_SELL_FEE = 15            # rough flat fee per sale (broker and depository), in Rs
STCG_PCT = 20.0               # ASSUMED tax on gains held 12 months or less (verify)
LTCG_PCT = 12.5               # ASSUMED tax on gains held more than 12 months (verify)
MAX_RESULTS_AGE_DAYS = 7      # warn when the latest screener results are older than this
POOL_FOR_CORRELATION = 12     # screener candidates whose prices are fetched to measure correlation
SECTOR_CAP_PCT = 35.0         # skip a candidate if its sector would exceed this share of the portfolio
BROAD_ETFS = ("NIFTYBEES", "JUNIORBEES")
LIQUID_ETF = "LIQUIDBEES"
WAIT_LIST = 5
SPLIT_STOCKS = 3              # stocks in each split option (the proceeds are divided equally between them)
OPEN_REPORT = True
OUT_DIR = pf.OUT_DIR
SECTOR_CACHE = pf.DATA_DIR / "sector_cache.json"
# ------------------------------------------------------------------------------

STATUS_RANK = {"Breakout": 0, "Near breakout price": 1, "Building base": 2, "Breakout, weak volume": 3}
QUALITY_RANK = {"STRONG": 0, "CHECK": 1, "NOT CHECKED": 1}


# ------------------------------- DATA -----------------------------------------
def latest_results():
    files = sorted(Path("output").glob("20*/results.csv"))
    if not files:
        return None, None, None
    path = files[-1]
    try:
        age = (dt.date.today() - dt.date.fromisoformat(path.parent.name)).days
    except ValueError:
        age = None
    return pd.read_csv(path), path, age


def sector_for(symbols):
    """{symbol: sector or None}; cached, best effort (Yahoo Finance)."""
    cache = json.loads(SECTOR_CACHE.read_text(encoding="utf-8")) if SECTOR_CACHE.exists() else {}
    todo = [s for s in symbols if s not in cache]
    if todo:
        try:
            import yfinance as yf
        except ImportError:
            yf = None
        for s in todo:
            sec = None
            if yf is not None:
                try:
                    sec = yf.Ticker(s + ".NS").info.get("sector")
                except Exception:  # noqa: BLE001
                    sec = None
            if sec:
                cache[s] = sec
        pf.DATA_DIR.mkdir(parents=True, exist_ok=True)
        SECTOR_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return {s: cache.get(s) for s in symbols}


def last_close(df):
    import candle_view as cv

    daily, _ = cv.drop_partial(cv.naive(df))
    return float(daily["Close"].iloc[-1]), daily


# ------------------------------- THE SALE -------------------------------------
def sale_plan(pos, qty, price):
    """Proceeds after charges and a tax range for selling qty shares of a position at price (oldest shares first)."""
    lots = sorted(pos["lots"], key=lambda l: (l["date"] is not None, l["date"] or dt.date.min))
    base = sum(l["qty"] * l["price"] for l in lots)
    ratio = pos["cost"] / base if base else 1.0           # honours a cost you set with the cost command
    today = pd.Timestamp(dt.date.today())
    need, gross = qty, qty * price
    cost = tax_lo = tax_hi = 0.0
    notes = []
    for l in lots:
        if need <= 1e-9:
            break
        take = min(l["qty"], need)
        need -= take
        basis = take * l["price"] * ratio
        gain = take * price - basis
        cost += basis
        if l["date"]:
            cut = pd.Timestamp(l["date"]) + pd.DateOffset(years=1) + pd.Timedelta(days=1)
            long_term = today >= cut
            rate = LTCG_PCT if long_term else STCG_PCT
            t = max(gain, 0) * rate / 100
            tax_lo += t
            tax_hi += t
            if not long_term and 0 < (cut - today).days <= 45 and gain > 0:
                saving = gain * (STCG_PCT - LTCG_PCT) / 100
                notes.append(f"{take:g} shares bought {l['date']:%d %b %Y} turn long-term on {cut:%d %b %Y} "
                             f"({(cut - today).days} days): waiting would cut the tax on them by about Rs {pf.inr(saving)}, "
                             f"if the price held.")
        else:
            tax_lo += max(gain, 0) * LTCG_PCT / 100
            tax_hi += max(gain, 0) * STCG_PCT / 100
    charges = gross * SELL_COST_PCT / 100 + FLAT_SELL_FEE
    undated = any(l["date"] is None for l in sorted(lots, key=lambda l: (l["date"] is not None, l["date"] or dt.date.min)))
    return {"qty": qty, "price": price, "gross": gross, "cost": cost, "gain": gross - cost, "charges": charges,
            "tax_lo": tax_lo, "tax_hi": tax_hi, "net_lo": gross - charges - tax_hi, "net_hi": gross - charges - tax_lo,
            "notes": notes, "undated": undated}


# ------------------------------- CORRELATION ----------------------------------
def returns(df):
    import candle_view as cv

    return cv.naive(df)["Close"].astype(float).pct_change().dropna().iloc[-250:]


def portfolio_returns(remaining, rets):
    """Value-weighted daily return of the holdings that stay."""
    parts = [(w, rets[s]) for s, w in remaining.items() if s in rets and w > 0]
    if not parts:
        return None
    tot = sum(w for w, _ in parts)
    frame = pd.concat([r * (w / tot) for w, r in parts], axis=1).dropna(how="all").fillna(0)
    return frame.sum(axis=1)


def correlation(cand_ret, port_ret):
    if port_ret is None or cand_ret is None:
        return None
    j = pd.concat([cand_ret, port_ret], axis=1).dropna()
    return float(j.corr().iloc[0, 1]) if len(j) >= 120 else None


# ------------------------------- OPTIONS --------------------------------------
def stock_card(row, budget, corr, sector, why):
    buy, stop, tgt = row["buy_above"], row["stop_loss"], row.get("target_price")
    card = {"symbol": row["symbol"], "status": row["status"], "quality": row.get("quality", "NOT CHECKED"),
            "notes": "" if pd.isna(row.get("quality_notes", "")) else row.get("quality_notes", ""),
            "buy": None if pd.isna(buy) else float(buy), "stop": None if pd.isna(stop) else float(stop),
            "target": None if tgt is None or pd.isna(tgt) else float(tgt), "last": float(row["last_close"]),
            "tight": row.get("tight_base"), "rs": row.get("vs_nifty_score"), "corr": corr, "sector": sector,
            "why": why, "qty": None, "cost": None, "risk_pct": None, "data_date": row.get("data_date")}
    if card["buy"] and card["stop"] and budget:
        q = int(budget // card["buy"])
        card.update(qty=q, cost=q * card["buy"], risk_pct=(q * (card["buy"] - card["stop"]) / budget * 100) if q else None)
    return card


def build_options(sold_sym, sold_etf, budget, remaining, held, results, rets, sectors):
    """-> (option1 card or None, option2 card or None, wait list, notes, splits)."""
    notes = []
    if results is None or results.empty:
        return None, None, [], ["No screener results found: run the screener first for options 1 and 2."], []
    res = results.copy()
    if "quality" not in res.columns:
        res["quality"] = "NOT CHECKED"
    res["quality"] = res["quality"].fillna("NOT CHECKED")
    ok = res[(res["quality"] != "WEAK") & ~res["symbol"].isin(held | {sold_sym})]
    port_ret = portfolio_returns(remaining, rets)
    rem_total = sum(remaining.values()) or 1.0
    sector_share = {}
    for s, w in remaining.items():
        sec = sectors.get(s)
        if sec:
            sector_share[sec] = sector_share.get(sec, 0) + w / rem_total * 100

    # option 1: diversifying breakout candidate
    pool = ok[(ok["tight_base"] == "Yes") & ok["buy_above"].notna() & ok["status"].isin(STATUS_RANK)].copy()
    pool["_s"] = pool["status"].map(STATUS_RANK)
    pool["_q"] = pool["quality"].map(QUALITY_RANK).fillna(1)
    pool = pool.sort_values(["_s", "_q", "vs_nifty_score"], ascending=[True, True, False]).head(POOL_FOR_CORRELATION)
    scored = []
    for _, r in pool.iterrows():
        sec = sectors.get(r["symbol"])
        if sec and sector_share.get(sec, 0) > SECTOR_CAP_PCT:
            continue
        c = correlation(rets.get(r["symbol"]), port_ret)
        scored.append((r, c, sec))
    scored.sort(key=lambda t: (t[0]["_s"], t[0]["_q"], 99 if t[1] is None else t[1] // 0.15, -t[0]["vs_nifty_score"]))
    opt1 = None
    if scored:
        r, c, sec = scored[0]
        why = [f"Screener status: {r['status']}, tight base, business quality {r['quality']}.",
               f"Beating the Nifty (relative-strength score {r['vs_nifty_score']:+.0f})."]
        if c is not None:
            why.append(f"Correlation with what you would still hold: {c:+.2f} "
                       f"({'low' if c < 0.4 else 'moderate' if c < 0.7 else 'high'}; 1.0 = moves identically).")
        else:
            notes.append("Correlation could not be measured (not enough overlapping price history).")
        if sec:
            why.append(f"Sector: {sec} ({sector_share.get(sec, 0):.0f}% of what you would still hold).")
        opt1 = stock_card(r, budget, c, sec, why)
    else:
        notes.append("No screener candidate met the Option 1 rules (tight base, near breakout, not WEAK, "
                     "not already held, sector not too large).")

    # option 2: same theme
    opt2 = None
    if not sold_sym:
        notes.append("Option 2 (same-theme swap) needs a holding to swap out of, so it was skipped for an amount.")
    elif sold_etf:
        notes.append(f"{sold_sym} is an ETF, so the same-theme idea is a broad index ETF (see Option 3).")
    else:
        sold_sec = sectors.get(sold_sym)
        if not sold_sec:
            notes.append(f"Sector of {sold_sym} is unknown, so Option 2 (same sector) was skipped.")
        else:
            same = ok[ok["symbol"].map(lambda s: sectors.get(s) == sold_sec)]
            same = same[same["buy_above"].notna()].sort_values("vs_nifty_score", ascending=False)
            if len(same):
                r = same.iloc[0]
                c = correlation(rets.get(r["symbol"]), port_ret)
                why = [f"Same sector as {sold_sym}: {sold_sec}.",
                       f"Strongest relative strength among screener stocks in that sector ({r['vs_nifty_score']:+.0f}).",
                       f"Screener status: {r['status']}, business quality {r['quality']}."]
                opt2 = stock_card(r, budget, c, sold_sec, why)
            else:
                notes.append(f"No screener stock in {sold_sec} passes today, so there is no same-theme swap.")

    # option 3 wait list: stocks the screener likes but that are too extended to buy now
    waiting = res[(res["buy_above"].isna()) & (res["quality"] != "WEAK") & ~res["symbol"].isin(held | {sold_sym})]
    waiting = waiting.sort_values("vs_nifty_score", ascending=False).head(WAIT_LIST)
    wait = [{"symbol": r["symbol"], "quality": r["quality"], "rs": r["vs_nifty_score"], "last": r["last_close"]}
            for _, r in waiting.iterrows()]
    splits, snotes = build_splits(ok, budget, rets, sectors, sector_share, port_ret)
    return opt1, opt2, wait, notes + snotes, splits


# ------------------------------- SPLIT OPTIONS --------------------------------
def pair_corr(a, b):
    if a is None or b is None:
        return None
    j = pd.concat([a, b], axis=1).dropna()
    return float(j.corr().iloc[0, 1]) if len(j) >= 120 else None


def _avg_corr(sym, picked, rets):
    vals = [pair_corr(rets.get(sym), rets.get(p)) for p in picked]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _choose(rows, n, sectors, sector_share, rets, spread, exclude=()):
    """Pick n stocks from the pre-sorted rows: one per sector (where the sector is known), sector not already too big.
    spread=True: after the first, prefer the stock that moves least like the ones already picked."""
    pool = [r for _, r in rows.iterrows() if r["symbol"] not in exclude][:16]
    picked = []
    used = set()
    while pool and len(picked) < n:
        ok = [(i, r) for i, r in enumerate(pool)
              if not (sectors.get(r["symbol"]) and (sectors.get(r["symbol"]) in used
                                                    or sector_share.get(sectors.get(r["symbol"]), 0) > SECTOR_CAP_PCT))]
        if not ok:
            break
        if spread and picked:
            def key(t):
                c = _avg_corr(t[1]["symbol"], [p["symbol"] for p in picked], rets)
                return (99 if c is None else c // 0.15, t[0])
            i, r = min(ok, key=key)
        else:
            i, r = ok[0]
        picked.append(r)
        if sectors.get(r["symbol"]):
            used.add(sectors[r["symbol"]])
        pool.pop(i)
    return picked


def build_splits(ok, budget, rets, sectors, sector_share, port_ret):
    """Three baskets of SPLIT_STOCKS screener stocks each; the proceeds are split equally inside a basket.
    A Diversified: best status and quality, picked to move least like each other and like what you keep.
    B Momentum: strongest relative strength against the Nifty (near breakout or breaking out).
    C Quality first: STRONG businesses first, then the smallest stop distances (the least to lose per rupee).
    Returns (list of split dicts, notes)."""
    notes, N = [], SPLIT_STOCKS
    if not budget:
        return [], notes
    pool = ok[ok["buy_above"].notna() & ok["status"].isin(STATUS_RANK) & ok["symbol"].isin(list(rets))].copy()
    if len(pool) < N:
        return [], [f"Only {len(pool)} screener stock(s) qualify with price history, so no {N}-stock splits were built."]
    pool["_s"] = pool["status"].map(STATUS_RANK)
    pool["_q"] = pool["quality"].map(QUALITY_RANK).fillna(1)
    pool["_t"] = (pool["tight_base"] != "Yes").astype(int)
    pool["_risk"] = pool["stop_loss_pct"].abs() if "stop_loss_pct" in pool.columns else 0.0
    orders = [
        ("A", "Diversified", "Best status and quality, chosen to move least like each other and like what you keep.",
         pool.sort_values(["_s", "_q", "_t", "vs_nifty_score"], ascending=[True, True, True, False]), True),
        ("B", "Strongest momentum", "The highest relative strength against the Nifty among stocks near or at their breakout.",
         pool.sort_values(["vs_nifty_score", "_s"], ascending=[False, True]), False),
        ("C", "Quality first, smaller stops", "STRONG businesses first, then the smallest distance to the stop-loss.",
         pool.sort_values(["_q", "_risk", "_s", "vs_nifty_score"], ascending=[True, True, True, False]), False),
    ]
    splits, seen = [], []
    for tag, name, blurb, rows, spread in orders:
        picked = _choose(rows, N, sectors, sector_share, rets, spread)
        tries, skip = 0, set()
        while len(picked) == N and any({p["symbol"] for p in picked} == s for s in seen) and tries < 8:
            tries += 1                                  # identical to an earlier basket: drop the last pick and look again
            skip.add(picked[-1]["symbol"])
            picked = _choose(rows, N, sectors, sector_share, rets, spread, exclude=skip)
        if len(picked) < N and skip:
            notes.append(f"Split {tag} ({name}) would have repeated an earlier basket; with the sector limits there were not "
                         f"enough different stocks.")
        if len(picked) < 2:
            notes.append(f"Split {tag} ({name}): fewer than 2 stocks qualified, so it was skipped.")
            continue
        if len(picked) < N:
            notes.append(f"Split {tag} ({name}): only {len(picked)} stocks qualified (one per sector rule).")
        seen.append({p["symbol"] for p in picked})
        each = budget / len(picked)
        cards = []
        for r in picked:
            c = correlation(rets.get(r["symbol"]), port_ret)
            sec = sectors.get(r["symbol"])
            why = [f"{r['status']}, business quality {r['quality']}, strength vs Nifty {r['vs_nifty_score']:+.0f}."]
            cards.append(stock_card(r, each, c, sec, why))
        spent = sum(c["cost"] or 0 for c in cards)
        loss = sum((c["qty"] or 0) * (c["buy"] - c["stop"]) for c in cards if c["buy"] and c["stop"])
        syms = [c["symbol"] for c in cards]
        pcs = [v for v in (pair_corr(rets.get(a), rets.get(b)) for i, a in enumerate(syms) for b in syms[i + 1:]) if v is not None]
        splits.append({"tag": tag, "name": name, "blurb": blurb, "cards": cards, "each": each, "spent": spent,
                       "left": budget - spent, "loss": loss, "loss_pct": loss / budget * 100,
                       "avg_corr": sum(pcs) / len(pcs) if pcs else None})
    return splits, notes


# ------------------------------- REPORT ---------------------------------------
PAGE = """<html><head><meta charset='utf-8'><title>Where could the money go - __SYM__</title>
<style>
body{font-family:Arial,sans-serif;font-size:21px;margin:28px;line-height:1.5;max-width:1200px}
table{border-collapse:collapse;font-size:19px}
td,th{padding:8px 14px;border-bottom:2px solid #ccc;text-align:left}
td.n,th.n{text-align:right}
.card{border:3px solid #1f4e9c;border-radius:12px;padding:6px 20px;margin:18px 0}
.card h2{margin:6px 0}
.q{padding:3px 12px;border-radius:6px;color:white;font-size:19px}
.STRONG{background:#1b7f3b} .CHECK,.NOTCHECKED{background:#b8860b} .WEAK{background:#b22222}
.pos{color:#1b7f3b} .neg{color:#b22222}
.warn{background:#fff3cd;border-left:8px solid #b8860b;padding:8px 18px;margin:12px 0}
.bad{background:#fdecea;border-left:8px solid #b22222;padding:8px 18px;margin:12px 0}
.box{background:#f2f2f2;border-left:8px solid #777;padding:8px 18px;margin:12px 0}
.small{font-size:17px;color:#444}
a.lv{font-size:19px;font-weight:normal}
</style></head><body>
<h1>Where could the money go - __SYM__</h1>
<div class='warn'><b>Screening output, not advice.</b> These are ideas to research, built from your latest
screener results (__RESDATE__) and your holdings. Check each stock yourself before acting.
Tax rates used are assumptions: confirm them with your tax advisor.</div>
__NOTES__
<h2>What a sale would give you</h2>
__SALE__
__OPTIONS__
<p class='small'>Charges are rough ({sell_cost}% plus Rs {flat}). Quantities use the cautious figure after the
higher tax estimate. Buy-above, stop and target come from the screener's formulas and are planning levels.</p>
</body></html>"""


def money(x):
    return "Rs " + pf.inr(x)


def stock_card_html(title, card):
    if card is None:
        return f"<div class='card'><h2>{title}</h2><p>Nothing qualified today. See the notes above.</p></div>"
    q = card["quality"].replace(" ", "")
    lines = "".join(f"<li>{w}</li>" for w in card["why"])
    plan = (f"Buy above <b>{card['buy']:,.2f}</b> ({(card['buy'] / card['last'] - 1) * 100:+.1f}% from the screen's "
            f"close {card['last']:,.2f}), stop-loss <b>{card['stop']:,.2f}</b> (loss "
            f"<b class='neg'>{abs(card['stop'] / card['buy'] - 1) * 100:.1f}%</b>)"
            + (f", target <b>{card['target']:,.2f}</b> (profit <b class='pos'>{(card['target'] / card['buy'] - 1) * 100:.1f}%</b>)"
               if card["target"] else "") + ".")
    size = ""
    if card["qty"]:
        size = (f"<p>The proceeds buy about <b>{card['qty']} shares</b> (about {money(card['cost'])}). If the stop were hit "
                f"you would lose about <b class='neg'>{card['risk_pct']:.1f}%</b> of the proceeds. "
                + ("That is a large bet on one stock: consider splitting it." if card["risk_pct"] and card["risk_pct"] > 4 else "")
                + "</p>")
    elif card["qty"] == 0:
        size = "<p>The proceeds are too small to buy even one share at the buy-above price.</p>"
    return (f"<div class='card'><h2>{title}: {card['symbol']} &nbsp;<span class='q {q}'>{card['quality']}</span> "
            f"&nbsp;{ns.live_links_html(card['symbol'])}</h2><ul>{lines}</ul><p>{plan}</p>{size}"
            + (f"<p class='small'>{card['notes']}</p>" if card["notes"] else "")
            + f"<p class='small'>Screen data of {card['data_date']}.</p></div>")


def splits_html(splits, budget):
    if not splits:
        return ""
    out = (f"<h2>Split options: {SPLIT_STOCKS} stocks each</h2><p class='small'>Each basket divides the {money(budget)} equally "
           f"between its stocks and buys whole shares at the buy-above price, so a little cash is left over. The loss shown is what the "
           f"stop-loss would cost if it were hit on every stock in the basket, as a share of the money placed. Stocks can appear in "
           f"more than one basket.</p>")
    for sp in splits:
        trs = ""
        for c in sp["cards"]:
            q = c["quality"].replace(" ", "")
            lose = (c["qty"] or 0) * (c["buy"] - c["stop"]) if c["buy"] and c["stop"] else None
            trs += (f"<tr><td><b>{c['symbol']}</b> {ns.live_links_html(c['symbol'])}</td><td>{c['status']}</td>"
                    f"<td><span class='q {q}'>{c['quality']}</span></td><td>{c['sector'] or '-'}</td>"
                    f"<td class='n'>{'-' if c['buy'] is None else format(c['buy'], ',.2f')}</td>"
                    f"<td class='n'>{'-' if c['stop'] is None else format(c['stop'], ',.2f')}</td>"
                    f"<td class='n'>{c['qty'] if c['qty'] is not None else '-'}</td>"
                    f"<td class='n'>{pf.inr(c['cost']) if c['cost'] else '-'}</td>"
                    f"<td class='n neg'>{'-' if lose is None else pf.inr(lose)}</td>"
                    f"<td class='n'>{'-' if c['corr'] is None else format(c['corr'], '+.2f')}</td></tr>")
        corr = "" if sp["avg_corr"] is None else f" Average correlation between the stocks: {sp['avg_corr']:+.2f} (1.0 = move identically)."
        out += (f"<div class='card'><h2>Split {sp['tag']}: {sp['name']}</h2><p>{sp['blurb']}</p>"
                f"<table><tr><th>Stock</th><th>Status</th><th>Quality</th><th>Sector</th><th class='n'>Buy above</th>"
                f"<th class='n'>Stop-loss</th><th class='n'>Shares</th><th class='n'>Cost Rs</th><th class='n'>Loss at stop Rs</th>"
                f"<th class='n'>Correlation with what you keep</th></tr>{trs}</table>"
                f"<p>About <b>{money(sp['spent'])}</b> invested, <b>{money(sp['left'])}</b> left over. If every stop were hit you would "
                f"lose about <b class='neg'>{money(sp['loss'])}</b> (<b class='neg'>{sp['loss_pct']:.1f}%</b> of the money placed).{corr}</p></div>")
    return out


def park_card_html(budget, wait, held):
    items = [f"<li><b>{LIQUID_ETF}</b> (a liquid ETF traded like a share) or a liquid or overnight mutual fund: "
             f"low risk while you decide. Check its current return and costs yourself.</li>"]
    broad = [b for b in BROAD_ETFS if b not in held]
    if broad:
        items.append(f"<li>A broad index ETF ({', '.join(broad)}) if you want market exposure without picking a stock.</li>")
    else:
        items.append("<li>You already hold the broad index ETFs on the list, so adding more would only raise that weight.</li>")
    if wait:
        w = "; ".join(f"{x['symbol']} ({x['quality']}, strength {x['rs']:+.0f})" for x in wait)
        items.append(f"<li>Wait for a pullback: the screener likes these but they are too far above their breakout "
                     f"price to buy now: {w}. Re-run the screener in a few weeks.</li>")
    return (f"<div class='card'><h2>Option 3: park it while you wait</h2><ul>{''.join(items)}</ul>"
            f"<p>Amount to place: <b>{money(budget)}</b>.</p></div>")


def sale_html(sale, sym):
    if sale is None:
        return "<p>You gave an amount, so no sale was modelled.</p>"
    tax = (f"{money(sale['tax_lo'])} to {money(sale['tax_hi'])}" if abs(sale["tax_hi"] - sale["tax_lo"]) > 0.5
           else money(sale["tax_hi"]))
    net = (f"<b>{money(sale['net_lo'])}</b> to <b>{money(sale['net_hi'])}</b>" if abs(sale["net_hi"] - sale["net_lo"]) > 0.5
           else f"<b>{money(sale['net_lo'])}</b>")
    pnl = f"<span class='{'pos' if sale['gain'] >= 0 else 'neg'}'>{money(sale['gain'])}</span>"
    rows = (f"<tr><td>Sell {sale['qty']:g} {sym} at the latest close {sale['price']:,.2f}</td><td class='n'>{money(sale['gross'])}</td></tr>"
            f"<tr><td>Your cost for those shares</td><td class='n'>{money(sale['cost'])}</td></tr>"
            f"<tr><td>Gain or loss</td><td class='n'>{pnl}</td></tr>"
            f"<tr><td>Rough selling charges</td><td class='n'>- {money(sale['charges'])}</td></tr>"
            f"<tr><td>Estimated tax</td><td class='n'>- {tax}</td></tr></table>")
    out = f"<table>{rows}<p>You could place about {net}.</p>"
    if sale["undated"]:
        out += ("<div class='box'>Some of these shares are from your imported baseline, so their buy date is unknown "
                "and the tax is shown as a range (long-term rate to short-term rate). Check the date in your broker's "
                "records.</div>")
    for n in sale["notes"]:
        out += f"<div class='warn'>{n}</div>"
    return out


def write_page(sym, sale, budget, opts, wait, held, notes, res_date, res_age, splits=()):
    n = ""
    if res_age is not None and res_age > MAX_RESULTS_AGE_DAYS:
        n += f"<div class='bad'>The screener results are {res_age} days old. Run the screener again for fresh options.</div>"
    for t in notes:
        n += f"<div class='box'>{t}</div>"
    html = (PAGE.replace("__SYM__", sym).replace("__RESDATE__", res_date or "none found").replace("__NOTES__", n)
            .replace("__SALE__", sale_html(sale, sym))
            .replace("__OPTIONS__", stock_card_html("Option 1: best screener alternative", opts[0])
                     + stock_card_html("Option 2: same-theme swap", opts[1]) + splits_html(splits, budget)
                     + park_card_html(budget, wait, held))
            .replace("{sell_cost}", f"{SELL_COST_PCT:g}").replace("{flat}", str(FLAT_SELL_FEE)))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"redeploy_{re.sub('[^A-Za-z0-9]', '', sym) or 'amount'}.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------- COMMAND --------------------------------------
def parse_args(args):
    if not args:
        sys.exit("Usage: python redeploy.py SYMBOL [QTY|N%]   or   python redeploy.py AMOUNT")
    first = args[0].replace(",", "")
    if re.fullmatch(r"\d+(\.\d+)?", first):
        return None, float(first), None
    return args[0].upper(), None, (args[1] if len(args) > 1 else "all")


def run(args):
    sym, amount, qty_arg = parse_args(args)
    positions, _ = pf.build_positions()
    if not positions:
        sys.exit("No holdings yet. Run: python portfolio.py import FILE")
    by = {p["symbol"]: p for p in positions}
    if sym and sym not in by:
        sys.exit(f"{sym} is not in your holdings. Held: " + ", ".join(sorted(by)))
    results, rpath, age = latest_results()
    held = set(by)
    fetch = [p["symbol"] for p in positions if p["resolved"]]
    if results is not None:
        cand = results[results["tight_base"] == "Yes"].head(60)["symbol"].tolist()
        fetch += [s for s in cand[:POOL_FOR_CORRELATION * 3] if s not in held]
    print(f"Downloading prices for {len(set(fetch))} stocks ...")
    prices = pf.fetch_prices(sorted(set(fetch)))

    sale, budget = None, amount
    if sym:
        df = prices.get(sym)
        px = last_close(df)[0] if df is not None else by[sym]["broker_ltp"]
        if not px:
            sys.exit(f"No price for {sym}, so the sale cannot be valued.")
        have = by[sym]["qty"]
        a = str(qty_arg).strip().lower()
        qty = have if a == "all" else have * float(a[:-1]) / 100 if a.endswith("%") else float(a)
        qty = math.floor(min(qty, have) + 1e-9)
        if qty < 1:
            sys.exit("Quantity must be at least 1 share.")
        sale = sale_plan(by[sym], qty, px)
        budget = sale["net_lo"]            # cautious: after the higher tax estimate
    else:
        sym = "AMOUNT"

    rets = {s: returns(df) for s, df in prices.items() if df is not None and len(df) > 130}
    remaining = {}
    for p in positions:
        df = prices.get(p["symbol"])
        px = last_close(df)[0] if df is not None else p["broker_ltp"]
        q = p["qty"] - (sale["qty"] if sale and p["symbol"] == args[0].upper() else 0)
        if px and q > 0:
            remaining[p["symbol"]] = q * px
    print("Looking up sectors (slow only the first time; saved afterwards) ...")
    secs = sector_for(sorted(set(held) | set(results["symbol"].head(60)) if results is not None else held))
    o1, o2, wait, notes, splits = build_options(args[0].upper() if sale else "", bool(sale and by[args[0].upper()]["etf"]),
                                        budget, remaining, held, results, rets, secs)
    page = write_page(sym, sale, budget, (o1, o2), wait, held, notes,
                      rpath.parent.name if rpath else None, age, splits)

    print("\n" + "=" * 60)
    if sale:
        print(f"Selling {sale['qty']:g} {sym} at {sale['price']:,.2f}: gross {money(sale['gross'])}, charges "
              f"{money(sale['charges'])}, tax {money(sale['tax_lo'])} to {money(sale['tax_hi'])}.")
        print(f"You could place {money(sale['net_lo'])} to {money(sale['net_hi'])}.")
    else:
        print(f"Amount to place: {money(budget)}")
    for title, c in (("Option 1 (best screener alternative)", o1), ("Option 2 (same-theme swap)", o2)):
        if c:
            qty_txt = f", about {c['qty']} shares" if c["qty"] else ""
            print(f"{title}: {c['symbol']} - {c['status']}, {c['quality']}, buy above {c['buy']:,.2f}, "
                  f"stop {c['stop']:,.2f}{qty_txt}")
        else:
            print(f"{title}: nothing qualified")
    for sp in splits:
        print(f"Split {sp['tag']} ({sp['name']}): " + ", ".join(f"{c['symbol']} x{c['qty']}" for c in sp["cards"])
              + f" - about {money(sp['spent'])} invested, loss if every stop is hit about {money(sp['loss'])} ({sp['loss_pct']:.1f}%)")
    print(f"Option 3 (park it): {LIQUID_ETF} or a liquid fund; broad index ETF; wait list: "
          + (", ".join(w["symbol"] for w in wait) or "none"))
    for n in notes + (sale["notes"] if sale else []):
        print("  ! " + n)
    print(f"\nFull page: {page}")
    if OPEN_REPORT:
        webbrowser.open(page.resolve().as_uri())


def run_cmd(args):
    if not args:
        print(__doc__)
        return
    run(list(args))


if __name__ == "__main__":
    run_cmd(sys.argv[1:])
