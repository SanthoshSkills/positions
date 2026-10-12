# Positional trading toolkit: project notes

Keep this file in the project's files so a new chat starts with the full picture.
Last updated: 11 Oct 2026 (angles after every MACD crossover and Supertrend flip; new ETFs, mutual funds and gold section; earlier: Heikin Ashi / Supertrend / MACD / RSI charts, Supertrend settings in the hub, zoomable screener and lookup charts). Everything below was tested on synthetic data only; real Yahoo Finance / NSE downloads and the
Windows picker window have not been run by the assistant.

## Setup
- Windows, PowerShell, Python 3.11. One folder holds all scripts.
- Install once: `pip install yfinance pandas matplotlib numpy`
- Start anywhere with `python trading_hub.py` (menu) or `python trading_hub.py web` (page in the browser).

## Files (keep all in one folder)
| File | What it does |
|---|---|
| `trading_hub.py` | One entry point: menu or `screen`, `lookup`, `paper`, `backtest`, `reports`, `web`. Replaces `run_all.py`. |
| `hub_web.py` | Browser dashboard at http://127.0.0.1:8765 (local only): run tasks, search stocks after 3 letters, open reports. |
| `nse_screener.py` | Screens the Nifty 500 (liquid, uptrend, near 52-week high, beating the Nifty), finds base/breakout status, support/resistance, buy-above, stop-loss, target, quantity. Writes `output/<date>/report.html`, `results.csv`, `charts/`. |
| `fundamentals.py` | Profit growth and debt check: STRONG / WEAK / CHECK, with the reasons (`check_symbol_detailed`). |
| `hub_settings.py` | Shared chart settings in `hub_settings.json` (Supertrend period 1-100, multiplier 0.1-10; default 10 and 2). Change with `python trading_hub.py settings supertrend 10 2` (also `settings`, `settings reset`), menu option 13, or the web page card "6. Chart settings". `ha_chart.py` reads it every time it draws, so screener, lookup and portfolio charts all follow. |
| `ha_chart.py` | Heikin Ashi chart with Supertrend (10, 2), MACD (12, 26, 9) with signal line and crossover triangles, and RSI 14, for daily, weekly, monthly side by side. Used by the screener, lookup and portfolio charts when `CHART_STYLE = "heikin"` in `nse_screener.py` (set `"classic"` for the old charts). `python ha_chart.py TCS` makes a test PNG. |
| `candle_view.py` | Candlestick charts (daily, weekly, monthly side by side) with formation call-outs and "what to watch in the next candle" (positive and warning signs). Writes `candle_report.html`, `candles/`, `candle_signals.csv`. |
| `stock_lookup.py` | Analyse any stocks you pick (suggestions after 3 letters). Nothing hidden: failed price rules and weak businesses are shown with reasons. Writes `output/lookup_<date_time>/`. |
| `paper_tracker.py` | Paper trades (no real money): `add`, `update`, `show`, `close`. Writes `paper_log.csv`, `paper_report.html`. |
| `portfolio.py` | Your REAL holdings. `import FILE` (ICICI Direct .xls/.csv or Shoonya .csv, auto-detected; re-import replaces that broker), `report`, `show`, `stop`, `cost`, `map`, `buy`, `sell`, `trades FILE icici|shoonya`. Data in `portfolio_data/`; report in `output/portfolio/portfolio_report.html`. `report` also writes a candlestick report for every holding (`output/portfolio/candle_report.html`, same pages as the screener's, but with YOUR stop instead of a buy-above plan); each holding in the portfolio report has a "candlesticks" link, and the candlestick pages link back to the portfolio report. |
| `alerts.py` | Exit alerts for holdings: bearish formation + other warning signs -> WATCH / WARNING / EXIT, sent to Telegram (token in env vars). `check [--dry]`, `status`, `replay SYMBOL`, `setup`, `chatid`, `test`. State and log in `portfolio_data/`. Schedule weekdays 16:45 IST with Task Scheduler. |
| `funds.py` | ETFs, mutual funds and gold. Ranks NSE ETFs (broad index, sector and theme, gold and silver, international) inside their groups by trend and strength vs the Nifty, with the usual 3-timeframe Heikin Ashi charts; shows the ETFs you hold (from the broker files) with result, share of invested money, rank in group and a higher-ranked-peer idea; tracks mutual funds you add by scheme code (NAV history from mfapi.in). `python funds.py` / `report`, `watch SYMBOL [GROUP]`, `unwatch`, `mf search TEXT`, `mf add CODE UNITS AVG_NAV` (units 0 = watch only), `mf remove CODE`, `mf show`. Hub menu option 14; web page card "4b". Data in `portfolio_data/` (`mf_holdings.csv`, `funds_universe.csv`); report `output/funds/funds_report.html`. |
| `chart_patterns.py` | Classical chart patterns from Sunil Gurjar's books and courses (only the pattern names are from him; the shapes are textbook definitions turned into numbers, thresholds are my starting values): double and triple top and bottom, head and shoulders and inverse, rounding top and bottom, ascending, descending and symmetrical triangles, rectangle, rising and falling wedges, up and down channels, bull and bear flags and pennants, cup and handle. Finds them on completed daily and weekly candles, says whether price is still inside (forming) or has broken out, and gives breakout level, measured-move target and stop. Used by `candle_view.py`: drawn on the daily and weekly candlestick charts (dash-dot lines and a label), a "chart patterns" section per timeframe in the candlestick report, a table column and rows in `candle_signals.csv`. Also covers the screener, lookup and portfolio candlestick reports. `python chart_patterns.py TCS` prints what it finds. About 4 in 10 random-walk charts show some pattern, so treat hits as candidates. |
| `position_actions.py` | Add, hold or exit? One prompt per holding (EXIT / WARNING / ADD / HOLD) with reasons and a pyramiding checklist: exits from your stop, the Heikin Ashi + Supertrend exit (daily red + 2nd/3rd bearish candle, weekly red), alerts.py tiers and a close below the 200-day; ADD needs profit of 5%+, trend and both Supertrends green, a breakout or 50-day pullback bounce, not stretched, room below resistance, a valid stop, and a size inside the risk rules (add at most half the shares, risks 0.5% of the portfolio, stock under 20%). `check`, `explain SYMBOL`, `replay SYMBOL [DAYS]`; hub menu 15, `trading_hub.py plan`, web page button in card 4. The portfolio report has an Action column, a checklist per stock and an Add candidates box. All thresholds are untested starting points. |
| `redeploy.py` | "If I sold X, where could the money go": proceeds after charges and a tax range, then 3 options (best screener alternative, same-sector swap, park it) from the latest screener results. `python redeploy.py SYMBOL [QTY|N%]` or `python redeploy.py AMOUNT`. Page: `output/portfolio/redeploy_<SYMBOL>.html`. |
| `backtest.py` | Replays the price rules on 10 years of history. Writes `backtest_output/backtest_report.html`. |
| `run_all.py` | Older one-command runner, superseded by `trading_hub.py`. |

## Definitions used everywhere
- **Buy above** = highest price of the last 50 trading days (about 10 weeks, excluding today) + 0.5%. Buy only if price trades above it.
- **Stop-loss** = just under the nearest support band (6-15% below entry) if one exists, else a flat 12% below entry.
- **Target** = buy price + `TARGET_RR` (2.0) x risk, where risk = buy price minus stop. Planning level only.
- **Loss % / profit %** are measured from the buy price, before charges.
- **Latest price** = last closing price in the downloaded data (not a live quote).
- **Quantity** is sized so a stop-out loses about 2% of capital, capped at 20% of capital per stock.

## Key settings (top of each file)
- `nse_screener.py`: `CAPITAL` 25000, `RISK_PER_TRADE_PCT` 2, `MAX_POSITION_PCT` 20, `STOP_PCT` 12, `TARGET_RR` 2.0,
  `TOP_N` 15, `CHECK_FUNDAMENTALS`, `SKIP_WEAK`, `CHART_FONT_SIZE` 16, `CHART_LAYOUT` "side".
- `fundamentals.py`: minimum profit growth 10%, quarter 0%, sales 5%, debt/equity at most 1.0, interest cover at least 3x.
- `paper_tracker.py`: `WAIT_DAYS` 30, costs 0.15% per side + Rs 15 sell fee. Does not sell at the target.
- `backtest.py`: hold 250 trading days, 0.30% round-trip cost. Does not sell at the target.

## Heikin Ashi charts (built, tested on synthetic data only)
- Supertrend period/multiplier are set in the hub (`hub_settings.py`), not in `ha_chart.py`. Settings at the top of `ha_chart.py`: MACD and RSI lengths, `INDICATORS_ON` ("heikin" = indicators on Heikin Ashi candles like TradingView; "real" = real prices), candles drawn per timeframe (150 D / 104 W / 60 M).
- Angle right after events: every Supertrend flip gets a label with the line's angle over the next `FLIP_ANGLE_BARS` (3) candles (shown from the first candle after the flip; "wait" when the flip is on the latest candle); every MACD crossover gets a label with the MACD line's angle over the next `MACD_ANGLE_BARS` (3) candles, in ATRs. Panel titles show the latest of each. Labels: `FLIP_ANGLE_LABELS` 3 and `MACD_ANGLE_LABELS` 4 most recent per panel.
- Swing set-up callouts (daily and weekly price panels): the Heikin Ashi + Supertrend (10, 2) method taught by Harneet Singh Kharbanda (book *Swing Trading*, chapter on Heikin Ashi and Supertrend; rules taken from a summary of his course, the book itself has not been read). Buy: a red (down) Supertrend line turns green, then 1 to 3 bullish Heikin Ashi candles form above the line, the entry is on the 2nd or 3rd, and the line should turn up steeply. Sell/exit is the mirror. On the chart: candles numbered 1 to 3 (the flip candle is 1), BUY/SELL tags on 2 and 3, a check or cross on the flip angle, and a box saying where the latest flip stands. Settings in `ha_chart.py`: `SWING_CALLOUTS`, `SWING_TFS`, `SETUP_CANDLES` 3, `STEEP_ANGLE` 15 (a placeholder: the method says "steep" without a number), `MIN_PRIOR_TREND` 5, `SWING_FLIPS_SHOWN` 2, `STALE_AFTER` 3. No stop or target rule is drawn for this method because none was verified.
- Supertrend angle (title and a label at the end of the line): slope over the last `ANGLE_BARS` (10) candles since the latest flip, divided by ATR, in degrees (45 = one ATR per candle; + rising, - falling, ~0 flat). Comparable across stocks and timeframes; shows "wait" until 1 candle after a flip.
- Heikin Ashi open/close are not real prices: buy-above, stop, target and latest price are drawn as dotted lines.
- `candle_view.py` charts, formation detection and exit alerts still use REAL candles (formations are defined on real prices).

## Report behaviour
- `report.html` and `candle_report.html` link to each other per stock; the first "candlesticks" click opens a new window,
  later clicks reuse it and jump to that stock.
- Charts zoom in the candlestick report, the screener report and the lookup report (click a chart; wheel, drag to pan, double-click, Esc, +/-/Fit/100% buttons). The viewer code lives in `nse_screener.py` (`ZOOM_CSS`, `ZOOM_HTML`, copied from `candle_view.py`) and the lookup report reuses it. The portfolio report does not have it yet. Back-to-top button bottom right.
- Each stock has Google Finance and Yahoo Finance live-chart links (NSE data there can be delayed).

## Portfolio module (real money)
- Two broker snapshots are merged into one position per stock. ICICI codes are matched to NSE symbols by ISIN (NSE list; built-in guesses only as a flagged fallback). Fix mismatches with `map`.
- Holdings files have no buy dates. Trade history is optional and can be added any time: trades dated on or before the last import explain the file (real dates, FIFO lots, shares they cannot explain stay "before trade history"); trades dated after it are added on top.
- Price health (Trend intact / Watch / Weak) = simple warning count: below 200-day (2), below 50-day (1), 200-day falling (1), lagging Nifty 6m (1), far below 52w high (1), bearish candle formation (1), stop touched (3). A prompt to look, not a sell signal.
- Business quality re-checked every 30 days (cached); a change of verdict is flagged. ETFs skip it.
- Known data quirks seen in the first files: ITC Hotels cost differs between brokers (demerger), Shoonya shows average cost 0 for it. Use `cost` to correct.
- Splits, bonuses, demergers are not adjusted automatically.

## ETFs, mutual funds and gold (built, tested on made-up data only)
- Ranked list: trend (Uptrend = above 50- and 200-day averages with the 200-day rising; Downtrend = below both; Mixed = rest), then relative strength vs the Nifty over 6 and 12 months (same score as the screener). Ranked inside each group; `LIQUIDBEES` is left out (cash-like). The built-in ticker list at the top of `funds.py` is a starting guess: unknown tickers or ETFs with under about a year of prices are skipped with a message. Thin ETFs (median traded value under Rs 1 crore a day) are flagged and never suggested as a switch.
- Gold: gold ETFs (GOLDBEES and others) stand in for the gold price, shown against the Nifty. Sovereign gold bonds and physical gold are not covered.
- Mutual funds are not in the broker files: add them by hand. NAV data and search come from the free mfapi.in service (not run yet on real data). Equity funds are compared with the Nifty price index only (flattering, no dividends); debt funds are not. Funds are compared only inside their own category.
- Charts: best 3 per group plus every fund you hold, same Heikin Ashi layout (daily, weekly, monthly side by side) as the screener.

## Exit alerts (built)
- Formations counted: bearish engulfing, evening star, dark cloud cover, three black crows (a formation counts for 3 candles).
- Points: close below the formation candle's low 2, below 50-day 1, below support band 2, heavy-volume down day 1, weekly candle bearish 2, lagging Nifty 3m 1, at resistance 1, after a 20% run 1. WARNING from 2; EXIT needs the confirming close and 4 points; your stop touched is always EXIT.
- No repeat messages; escalation, a new formation, or an open EXIT (every 3 days) triggers a new one. WATCH is not sent unless `SEND_WATCH = True`.
- Thresholds are untested starting points: use `replay` on real stocks before relying on them.

## Redeploy ideas (built)
- Split options (Split A diversified, B strongest momentum, C quality first with smaller stops): 3 stocks each (`SPLIT_STOCKS`), the proceeds divided equally, whole shares at buy-above, one stock per sector inside a basket, baskets made different from each other; shows shares, cost, loss if every stop is hit and average correlation.
- Option 1: screener stock you do not hold, tight base, near breakout, STRONG before CHECK (never WEAK), sector not over 35% of what you keep, lowest correlation with what you keep. Option 2: strongest relative strength in the sold stock's sector (ETF: broad index ETF). Option 3: liquid ETF/fund, broad ETF, extended "wait for pullback" list.
- Tax rates in the file (20% / 12.5%) are assumptions to verify; baseline shares have unknown dates, so tax is shown as a range. Screening output, not advice.

## Preferences
- Large, easy-to-read text in charts and outputs; daily, weekly and monthly charts side by side.

## Parked ideas (not built)
- **Next for the portfolio:** gap analysis feeding the exit score, WhatsApp delivery if Telegram does not suit, "where could the money go" top-3 replacement ideas, paper-vs-real entry comparison, broker-specific trade-book layouts once sample files exist.
- **Read-only broker link.** User has an ICICI Direct account. Breeze API (official `breeze-connect`) offers holdings, funds,
  quotes, order-cost preview, trade list, websocket prices. Orders need a registered static IP; unknown whether read-only calls do.
  Daily session token via ICICI login page. Plan: wrapper exposing read calls only; token kept in memory; manual one-click login.
  First version would be live prices + holdings overlay. Historical data is not split/bonus adjusted, so Yahoo stays the screener source.
  Never store passwords or authenticator secrets in scripts.
- **Bearish side.** Avoid/exit list (mirror rules applied to holdings), weak-business list, market-health gauge, paper-only
  mirror-image short candidates. Real shorting needs futures or puts; delivery shorts are not possible for retail.
- **Futures and options.** User wants to learn first. Possible later: lot size and margin reference, separate derivatives paper log.
- **Selling at the target** in the paper tracker and backtest, as an option (would change results).

## Known limits
- Backtest uses today's Nifty 500 list (survivorship bias makes results look better) and tests price rules only.
- Fundamentals come from Yahoo's free statements and are sometimes late or missing (hence CHECK).
- Candle formations are weak signals on their own; the "watch next candle" lists are conditions, not predictions.
- Buy-above and stop-loss for stocks that fail the price rules (in lookups) are hypothetical.
