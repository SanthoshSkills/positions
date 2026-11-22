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
- Detail panel chart (21 Nov 2026, user request): status letter boxes (same colours as the strip) above the points, and a drawn legend with thick blue and orange lines. Same page as `markets`.
- Overview tab (21 Nov 2026, user request): the page now has tabs "Overview | List" and opens on Overview (stacked status chart, Nifty gate shading, event flags, click a bar for that day's stocks, events list, transitions table). `tracker.py` `transitions()` now shows strong Breakout, weak-volume breakout only, either, and the weak-volume share (columns changed; old `percent` is gone). Same page as `markets`. Tested on made-up files only (data, both routes, headless page, downloads). Not run on real results.
- Transitions table changed (21 Nov 2026, user request): every episode is counted once, in four groups: strong, weak volume only, still open, none. The `full_window` column is gone; new columns `still_open` and `no_breakout`. The percentages are shares of all episodes started ("so far" figures). Same as `markets`. Tested on made-up files only.
- Detail panel fix (21 Nov 2026, user screenshots): the panel had a fixed height of 500 px and cut off its own date table when the text was larger. The panel now grows with its content. Same as `markets`. Tested on made-up data at normal size and 1.5x zoom.
- Week filter (21 Nov 2026, user request): a click on a date header in the List strip shows only the stocks listed on at least one run day in that calendar week (Monday to Sunday). A label "Week of ..." with an x button shows next to Reset. Click a header of the same week again, the x, or Reset to clear. It works together with the view, the filters and the symbol search. The strip keeps all days (no highlight). Same as `markets`. Tested on made-up data.

## Pullback prices, target in tracker chart, charts, backfill: copied from `markets` (22 Nov 2026, user request)
Done with the `positions` logic (old strategy: no Nifty gate, no WATCH ONLY, no readiness score). Tested on made-up data only. NOT run on real data.
- `nse_screener.py` `analyze()`: for "Extended - wait for pullback" stocks, `pullback_price` = the pivot and `pullback_buy` = pivot + 0.5% (the normal entry rule).
  `buy_above`, stop-loss, target and quantity stay EMPTY for Extended (no entry now), so `redeploy.py`, `paper_tracker.py` and `backtest.py` are not affected.
  New columns in `results.csv`: `pullback_price`, `pullback_buy` (empty for other statuses).
- Shown in the screen report (price line, classic chart title), the candlestick report (`candle_view.py`), and the charts: `ha_chart.py` (default style) draws a teal dotted
  "Pullback target" line and a green dotted "Buy above (after pullback)" line; the classic chart adds a dash-dot "Buy above (after pullback)" line.
- Tracker (`tracker.py`, `hub_web.py` page): the detail chart has a Target line (purple) and, for Extended stocks, a Pullback target line (teal); on Extended days the
  "Recommended buy price" is the buy price after the pullback. Table has Target and Pullback target columns. The csv/Excel history has the two new columns.
- NOT done here (as in `markets`): `stock_lookup.py` (own copy of the plan), `portfolio.py` holding charts (they have no pullback prices).
- WATCH ONLY buy price: not applicable here (this repo has no Nifty gate).
- Backfill: new `backfill_pullback.py`. The user must run it on their computer (needs Yahoo and the `output/` folder):
  `python backfill_pullback.py --dry-run`, then `python backfill_pullback.py`. It fills only EMPTY cells for Extended rows, scales the pivot to the saved close,
  skips rows where the saved close differs from Yahoo by more than 5% (split or bonus), keeps `results.csv.before_backfill`, and marks rows `backfilled = Yes`.
  The user has NOT run it yet.
