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
Steps 1 to 4 are done (new repo `markets` attached; import check done;
the 9 clean files, `nse_symbols_cache.csv`, `requirements.txt` and `.gitignore` copied and pushed to `markets`, 14 Nov 2026).
5a. DONE (14 Nov 2026): `trading_hub.py` copied to `markets` and trimmed. The user agreed.
    Menu now has 6 items: screen, lookup, backtest, reports, web, settings. User may add parts back later.
5b. DONE (14 Nov 2026): `hub_web.py` copied to `markets` and trimmed. The user agreed to the cut list.
    It compiles. It was NOT run (no real run yet). Railway settings (host 0.0.0.0, RAILWAY_ENVIRONMENT) kept for now.
6. DONE (14 Nov 2026): no portfolio report links existed in `nse_screener.py` or `candle_view.py`.
   Fixed stale paper/portfolio text in messages and comments (`nse_screener.py`, `candle_view.py`, `ha_chart.py`,
   `hub_settings.py`, `backtest.py`). Left the "holding" and "your stop" code path in `candle_view.py` (dead, harmless).
   Backtest wording now says "the plan the screener suggests": the user may check that it is right.
7. NEXT: first run on real data (code was tested on made-up data only). Nothing in `markets` has run yet.
   14 Nov 2026: this Claude session CANNOT reach Yahoo or NSE (its network allows only package sites and GitHub;
   curl to Yahoo, nseindia.com and nsearchives.nseindia.com all failed). This says nothing about NSE blocking
   Cloudflare or other cloud jobs. The first real run must happen on the user's computer (or another host).
   Then go back to the open questions (Cloudflare design, data source).
Ask ONE question per turn.

## Origin label (20 Nov 2026, user request)
- New file `origin.py`: `ORIGIN = "v.5.5/Positions"`. The `markets` repo has its own copy with `markets/markets`.
- The label shows in the hub web page header and title, and in the title and heading of every report page.
- Added an `origin` first column to: `results.csv`, `lookup_results.csv`, `trades.csv`, `candle_signals.csv`,
  `funds_ranking.csv`, `portfolio_positions.csv`, `paper_log.csv`, and all hub CSV downloads (also for old files).
- NOT changed (on purpose): the alerts log (append file, an old header would not match) and the portfolio
  data files that the user keeps and edits (`holdings` and similar).
- Tested on made-up files only.

## Reporting tracker: web page copied from `markets` (21 Nov 2026, user request)
- `tracker.py` is the `markets` file (download names stay `5.5_tracker_...`). `hub_web.py` has the routes `/tracker` and `/api/tracker`,
  the page `TRACKER_PAGE`, and a card "7. Tracker" with a button "Open tracker".
- The page has the list with filters, search, Rows menu, Reset, the journey strip and the detail panel (summary, price and buy-price chart, table).
  Rows with a date that is not a real date are discarded. See the `markets` CHECKPOINT.md for the full list of choices.
- Only the reporting part was copied. The daily run and the strategy in this repo are not changed.
- Tested on made-up files only (data function, both routes, hub card, Excel download). Not run on real results.
- Updated the same day: the list is one table. The first four columns (Symbol, Status now, Breakouts, Last buy price) and the title row stay in place; the rest scrolls with the strip, which opens at the newest day. Same page as `markets`.
- Label change (21 Nov 2026): the list column is now "Recommended buy price" (panel: "Last recommended buy price"). Same page as `markets`.
- Frozen columns changed (21 Nov 2026, user request): now six (Symbol, Status now, Times listed, Breakouts, Recommended buy price, vs Nifty). Last alert and Quality scroll with the strip. Same page as `markets`.
- Grid columns (21 Nov 2026, user request): "Last alert" and "Quality" are removed from the grid and shown in the detail panel. The grid has the six frozen columns and the strip. The Quality filter stays. Same page as `markets`.
