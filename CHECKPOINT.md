# Checkpoint

Last updated: 14 Nov 2026

## How to work
- Read this file first. Read other files only when the Next action says so.
- Do small steps. Ask one question each turn.
- Commit and push after each decision.
- Write in ASD-STE100 style about 80% of the time (short sentences, simple words).
- "Ideate only" means: discuss, do not change files, until the user says they agree.

## Goal
Make a NEW, smaller repo from this one. Keep only what the user likes:
the screener, the charts, the recommendations and the write-ups.
Then think about moving off Railway to Cloudflare.

## Decided
- Remove paper trades, portfolio and ETF/fund features from the new repo.
- Make a FRESH COPY of the kept files (no git history).
- Keep: the screener, the charts, the recommendations, the write-ups.
- New repo is `SanthoshSkills/markets` (empty, attached and cloned on 14 Nov 2026).
- Keep `trading_hub.py` and `hub_web.py` in `markets`, with the removed parts cut out.
- The `positions` repo stays as it is. Do not change its code files.

## Import check (done 14 Nov 2026, imports only)
- Clean (no import of removed files): `nse_screener.py`, `fundamentals.py`, `ha_chart.py`, `hub_settings.py`,
  `candle_view.py`, `chart_patterns.py`, `yf_cloud_patch.py`, `stock_lookup.py`, `backtest.py`.
- `trading_hub.py` imports `paper_tracker`, `portfolio`, `alerts`, `redeploy`, `position_actions`, `funds` (lines 97-140).
- `hub_web.py` imports `paper_tracker`, `portfolio`, `alerts`, `funds` (lines 389-393).
- Not yet checked: text and links to the portfolio report in the screener and candlestick reports.

## Likely files to keep (from PROJECT_NOTES.md; imports NOT yet checked)
- `nse_screener.py`, `fundamentals.py`
- `ha_chart.py`, `hub_settings.py`
- `candle_view.py`, `chart_patterns.py`
- `nse_symbols_cache.csv`, `yf_cloud_patch.py`, `requirements.txt`, `.gitignore`
- Maybe: `stock_lookup.py`, `backtest.py`, `trading_hub.py`, `hub_web.py` (trim the paper, portfolio and fund parts)

## Likely files to leave behind
`paper_tracker.py`, `paper_log.csv`, `portfolio.py`, `funds.py`, `alerts.py`,
`position_actions.py`, `redeploy.py`, `run_all.py`, `Procfile`, `railway.json`

## Open questions (ideas only, NOT decided)
- Cloudflare design. Leading idea: a scheduled job builds the report pages and charts,
  and Cloudflare serves them as static files. The user has not agreed yet.
  Other options discussed: Cloudflare Containers (run as is), keep Python on another host
  and use Cloudflare only for DNS, Tunnel and Access.
  Workers do not run pandas, matplotlib and yfinance well (Pyodide limits). Check current limits.
- Data source. Options: keep Yahoo (yfinance); NSE bhavcopy files in our own store (needs
  split and bonus adjustment, no fundamentals); Shoonya API; paid vendor.
  Test first: can a cloud job download NSE files (NSE may block cloud IP addresses)?
- Chart idea: replace matplotlib PNGs with a browser chart library
  (for example TradingView Lightweight Charts) and write JSON data only.
- Which small jobs stay in the new repo (backtest and lookup are optional).

## Checks to do before the copy
- Find imports between the kept files and the removed files (portfolio.py, funds.py, alerts.py,
  position_actions.py, paper_tracker.py). Remove links to them.
- Remove links to the portfolio report from the screener and candlestick reports.
- The code was tested on made-up data only. Plan a first run on real data.

## Next action
Steps 1 to 3 are done (new repo `markets` attached; import check done).
4. Copy the clean kept files into `/home/claude/markets` (fresh copy, no history). Commit and push.
5. Copy `trading_hub.py` and `hub_web.py`. Cut the removed parts and their imports. Commit and push.
6. Find and remove portfolio report links in the screener and candlestick reports.
7. Plan a first run on real data.
Ask ONE question per turn.
