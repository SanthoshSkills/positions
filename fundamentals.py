#!/usr/bin/env python3
"""
Business quality check: profit growth and debt.

Used automatically by nse_screener.py (keep this file in the same folder).
You can also try it on its own:   python fundamentals.py TCS INFY

For each stock it gives one verdict:
  STRONG  profits are growing and debt is under control (every check that could be done passed)
  WEAK    at least one check failed (loss-making, profits falling, heavy debt, ...)
  CHECK   the free data was incomplete, so look the company up yourself before deciding

Where the numbers come from: Yahoo Finance's free company statements. For Indian companies
they are sometimes late, partial or missing, which is why CHECK exists. A CHECK is not a pass.
"""

import sys
import time

# ----------------------------- SETTINGS (edit me) -----------------------------
MIN_PROFIT_GROWTH_PCT = 10.0      # net profit must grow at least this much vs the year before
MIN_QTR_PROFIT_GROWTH_PCT = 0.0   # latest quarter's profit vs the same quarter last year
MIN_SALES_GROWTH_PCT = 5.0        # sales (revenue) growth vs the year before
MAX_DEBT_TO_EQUITY = 1.0          # total debt divided by shareholders' money
MIN_INTEREST_COVER = 3.0          # operating profit must cover interest at least this many times
FINANCIAL_SECTORS = ("Financial Services",)  # banks and lenders live on debt: debt rules skipped
# ------------------------------------------------------------------------------


def _series(df, names):
    """First matching row of a yfinance statement, newest value first, blanks removed."""
    if df is None or getattr(df, "empty", True):
        return []
    for name in names:
        if name in df.index:
            row = df.loc[name]
            if getattr(row, "ndim", 1) > 1:  # duplicate row names: take the first
                row = row.iloc[0]
            row = row.dropna()
            if len(row):
                return [float(v) for v in row.sort_index(ascending=False).values]
    return []


def evaluate_detailed(income_a, income_q, balance, sector=None):
    """Turn the three statements into (verdict, fails, unknown, notes) as separate lists. Pure function."""
    fails, unknown, notes = [], [], []
    finance = sector in FINANCIAL_SECTORS
    profit_done = debt_done = False

    profit = _series(income_a, ["Net Income", "Net Income Common Stockholders"])
    sales = _series(income_a, ["Total Revenue", "Operating Revenue"])
    q_profit = _series(income_q, ["Net Income", "Net Income Common Stockholders"])

    # --- Profit growth -------------------------------------------------------
    if profit:
        profit_done = True
        cur = profit[0]
        if cur <= 0:
            fails.append("loss-making last year")
        elif len(profit) > 1:
            prev = profit[1]
            if prev > 0:
                g = (cur / prev - 1) * 100
                if g < MIN_PROFIT_GROWTH_PCT:
                    fails.append(f"profit growth only {g:+.0f}%")
                else:
                    notes.append(f"profit {g:+.0f}% last year")
            else:
                notes.append("profit turned positive last year")
    else:
        unknown.append("no yearly profit data")

    if len(q_profit) >= 5:
        if q_profit[0] <= 0:
            fails.append("loss in the latest quarter")
        elif q_profit[4] > 0:
            g = (q_profit[0] / q_profit[4] - 1) * 100
            if g < MIN_QTR_PROFIT_GROWTH_PCT:
                fails.append(f"latest quarter profit down {g:.0f}% vs a year ago")
            else:
                notes.append(f"latest quarter {g:+.0f}% vs a year ago")

    if len(sales) > 1 and sales[1] > 0:
        g = (sales[0] / sales[1] - 1) * 100
        if g < MIN_SALES_GROWTH_PCT:
            fails.append(f"sales growth only {g:+.0f}%")
        else:
            notes.append(f"sales {g:+.0f}%")

    # --- Debt ----------------------------------------------------------------
    if finance:
        debt_done = True
        notes.append("bank or lender: debt rules skipped")
    else:
        debt_fails = []
        debt = _series(balance, ["Total Debt"])
        equity = _series(balance, ["Stockholders Equity", "Common Stock Equity",
                                   "Total Equity Gross Minority Interest"])
        if equity and equity[0] <= 0:
            debt_fails.append("negative shareholder equity")
            debt_done = True
        elif debt and equity:
            de = debt[0] / equity[0]
            debt_done = True
            if de > MAX_DEBT_TO_EQUITY:
                debt_fails.append(f"debt/equity {de:.1f} is high")
            else:
                notes.append(f"debt/equity {de:.1f}")
        else:
            unknown.append("no debt data")

        ebit = _series(income_a, ["EBIT", "Operating Income"])
        interest = _series(income_a, ["Interest Expense", "Interest Expense Non Operating"])
        if ebit and interest and abs(interest[0]) > 0:
            cover = ebit[0] / abs(interest[0])
            if cover < MIN_INTEREST_COVER:
                debt_fails.append(f"interest cover only {cover:.1f}x")
            else:
                notes.append(f"interest cover {cover:.1f}x")

        # If we could not even confirm the sector, a "high debt" result might be a lender: only CHECK.
        (fails if sector else unknown).extend(debt_fails)

    if fails:
        verdict = "WEAK"
    elif profit_done and debt_done and not unknown:
        verdict = "STRONG"
    else:
        verdict = "CHECK"
    return verdict, fails, unknown, notes


def evaluate(income_a, income_q, balance, sector=None):
    """Turn the three statements into (verdict, notes). Same checks as evaluate_detailed."""
    verdict, fails, unknown, notes = evaluate_detailed(income_a, income_q, balance, sector)
    return verdict, "; ".join(fails + unknown + notes) or "no data found"


def fetch_statements(symbol):
    import yfinance as yf

    t = yf.Ticker(symbol + ".NS")

    def grab(attr):
        try:
            return getattr(t, attr)
        except Exception:  # noqa: BLE001
            return None

    sector = None
    try:
        sector = t.info.get("sector")
    except Exception:  # noqa: BLE001
        pass
    return grab("income_stmt"), grab("quarterly_income_stmt"), grab("balance_sheet"), sector


def check_symbol(symbol):
    return evaluate(*fetch_statements(symbol))


def check_symbol_detailed(symbol):
    """Like check_symbol, but returns a dict that says WHY: failed checks, unknowns and passed facts."""
    income_a, income_q, balance, sector = fetch_statements(symbol)
    verdict, fails, unknown, notes = evaluate_detailed(income_a, income_q, balance, sector)
    return {"verdict": verdict, "fails": fails, "unknown": unknown, "notes": notes, "sector": sector}


def check_many(symbols):
    """Return {symbol: (verdict, notes)}. Never raises for a single bad stock."""
    out = {}
    for i, sym in enumerate(symbols, 1):
        try:
            out[sym] = check_symbol(sym)
        except Exception:  # noqa: BLE001
            out[sym] = ("CHECK", "could not fetch company data")
        print(f"  quality check {i}/{len(symbols)}: {sym} -> {out[sym][0]}")
        time.sleep(0.3)
    return out


if __name__ == "__main__":
    names = [a.upper() for a in sys.argv[1:]] or ["TCS", "INFY"]
    for sym, (verdict, text) in check_many(names).items():
        print(f"{sym}: {verdict}  ({text})")
