# ThesisTally

ThesisTally scans the Nifty 200 after the close and follows each detected swing setup through a local research journal. The November 20, 2026 scan found 24 setups. Eight fit the simulated portfolio limits, and the journal tracks the other 16 independently.

All three strategies are still in research. In the sealed 18-month holdout, 74 simulated trades closed with a mean of −0.270R after costs and a profit factor of 0.56. The Reports tab shows the results by window and the criteria each strategy missed. [Architecture guide](docs/current-design.md) · [Illustrated PDF](output/pdf/ThesisTally-Architecture.pdf) · [Current operations](docs/operations.md)

The historical research archive covers 3,190 completed sessions from 1 January 2014 through 20 November 2026. The local membership export identifies each constituent by ISIN, symbol, validity dates and official evidence. Research rules include bounded stops, paired earnings-blackout policies, rolling evaluation and a sample-size and confidence-interval qualification gate. [Historical research guide](docs/historical-research.md)

## Run the radar

From the project directory, open the live terminal with one command (Python 3.11+ and uv).

```sh
uv run radar
```

| Key | View or action |
| --- | --- |
| **1** | Prices for the Nifty 200 universe and three benchmarks. Press **/** to find a symbol or sector. |
| **2** | Calls. Choose Observations or a historical research run. Open a row to inspect levels, events, fills and journal feedback. |
| **3** | Saved reports. Open a backtest to see its folds, outcomes and qualification results. |
| **4** | Paper Trading. See pending entries, holdings, closed trades and the daily account balance. Open a trade for its fills and daily valuations, or a session for its executions. |
| **Enter / Esc** | Open the selected row / close its details. |
| **R / D / Q** | Refresh, collect and scan today's completed session, or quit. |

The Prices tab polls Kite in one batch every five seconds and shows the exchange timestamp in IST. After market hours it shows the last exchange quotes. On a connection failure the terminal identifies cached quotes or dated end-of-day closes. While the terminal is open, it checks for the published NSE final from 15:45 IST on trading days, scans the session and automatically reviews allocated calls with your signed-in Codex CLI. Press **D** to trigger the scan immediately. Use `uv run radar research-mode off` to disable AI research or `uv run radar research-mode on` to resume. Calls and reports load independently of the feed.

The individual commands below also support scripted use.

`session` checks the current Kite token. This workspace has a Git-ignored local link to the user-supplied handoff. On another machine, or after the manual daily Kite login, point `RADAR_CREDENTIALS` at the current handoff file. Its `kite` object supplies `api_key` and `access_token`. The research commands use read-only market and profile requests.

`daily` archives the exchange final and advances the observation journal in one command. After the initial download, previously verified sessions can be inspected and scanned offline. For unattended collection, notifications and nightly backups, use `uv run radar run`. The scheduler follows the exchange calendar, including special weekend sessions. Daily Kite login and private delivery settings are described in the [operations guide](docs/operations.md).

| Command | Result |
| --- | --- |
| `uv run radar sync` | Download a completed session and its indicator history |
| `uv run radar verify --day 2026-11-20` | Verify the archived inputs, exchange context and all 200 closes |
| `uv run radar scan --day 2026-11-20` | Evaluate the rules and advance the journal once for the session |
| `uv run radar calls --call-id ID` | Inspect the original levels, features, fills, entitlements and event history |
| `uv run radar action ID skip` | Record a skip decision and continue measuring the original hypothetical outcome |
| `uv run radar reports --run NAME` | Compare realized portfolio and shadow outcomes |
| `uv run pytest -q` | Run the offline regression suite |

Set `RADAR_DATA` to choose a data directory. The default is `./data`. SQLite stores calls and fills in `journal.sqlite`. Session directories hold Parquet history, instrument identities, original exchange files and reconciliation evidence. Each snapshot publishes atomically. Calling the same scan twice preserves a single set of journal events.

## Reproduce the first evaluation

The included membership file covers the **inclusive October 1 through November 20, 2026 interval**. The reconstruction uses a dated public archive of the October 1 symbol list, checks all 200 symbols against the November 20 NSE list, and reviews intervening equity index notices. Each traded session also checks the exchange ISIN and security ID. Industry labels come from the November list. The [input provenance](inputs/nifty200-2026-10-01-to-11-20.sources.json) identifies the exact archive revision and exchange publications.

The prepared local workspace can replay the last command directly. For a fresh data directory, run the sequence with a valid Kite historical-data session.

```sh
uv run radar import-universe inputs/nifty200-2026-10-01-to-11-20.csv 2026-10-01 2026-11-20 inputs/nifty200-2026-10-01-to-11-20.sources.json
uv run radar sync --day 2026-11-20 --start 2024-11-20
uv run radar backfill 2026-10-01 2026-11-20 --anchor 2026-11-20
uv run radar backtest 2026-10-01 2026-11-20 2026-11-02 --folds 2
```

A run fixes the YAML revision, starts each chronological fold with a fresh simulated account, and reserves the final period as a holdout. Scan and replay use the same detector, sizing, fee and lifecycle functions. The JSON report under `data/backtests` includes all calls and a separate maturity check. Qualification uses only closed calls from detection dates old enough to complete their holding window. Active calls remain visible in their original books.

The November 2–20 holdout produced 29 portfolio candidates and five closed trades. Mean realized expectancy was **+0.134R**, profit factor was **1.45** on after-cost rupee P&L, and eight calls were still open or awaiting an entry. The earlier folds had no portfolio allocations. The sample is shorter than a full holding window in each fold. The longer point-in-time archive and its rolling evaluation supersede this initial diagnostic for validation.

## How prices and fills work

- The daily scan uses the NSE final OHLCV for equities and official index closes for Nifty 50, Nifty 200 and India VIX. Kite supplies adjusted daily history for indicator warm-up. In the November 20 capture, 190 latest equity closes differed from the exchange final and all 200 previous-session closes matched exactly.
- Each historical session uses its own dated membership, bhavcopy, price bands, ASM/GSM indicators and corporate events. Historical OHLC comparisons account for subsequent provider price adjustments. The earnings exclusion uses the announcement timestamp and the meeting date. Missing membership prevents replay for that date.
- The three rules detect trend pullbacks, 52-week-high momentum and volatility contraction breakouts. The YAML defines the evaluation assumptions, including a ₹10 lakh simulated account, ₹5 crore median daily turnover, a five-calendar-day results exclusion and five basis points of modeled slippage. Relative strength ranks candidates before the position and sector limits are applied. [Versioned rules](src/thesistally/rules.yaml)
- A confirmed setup attempts entry at the next session open. The simulation checks the original stop, target, opening band and minimum reward/risk after slippage. A closing stop schedules a sale for the following open. A target touch takes half the position off, and the remainder trails the 20 EMA or 10-session low. The maximum holding close is scheduled in advance. A locked lower opening band can defer an exit.
- Call outcomes include fills, modeled delivery fees, dividend entitlements, net rupees, R, holding sessions and daily-bar estimates of adverse/favorable excursion. Fees use the configured resident NSE delivery schedule. Exact order-book fills, intraday price paths and account-specific charges can differ. Documented compulsory conversions freeze positions until credited and settle fractional cash. A valuation gap stops the affected account and appears in its report. [Zerodha charges](https://zerodha.com/charges/)
- The portfolio journal reserves capital and risk for accepted candidates. Filtered candidates retain a separate hypothetical outcome. Taking or skipping a call records feedback in the event history. A later scan must process the next exchange session so missed days cannot silently change a trade's outcome.

The formal 2014–2026 walk-forward study completed nine test windows and a separate 375-session holdout. The holdout closed 74 simulated trades at −0.270R after costs and a 0.56 profit factor. No strategy qualified. Older dates without surveillance coverage produced shadow observations. On the only test window with sufficient prior labels, relative strength closed nine trades at −0.140R and LightGBM closed ten at −0.618R. The terminal continues to rank by relative strength.

TradingAgents automatically researches allocated calls through the signed-in Codex CLI. A metered OpenAI API provider is also available. The Calls tab shows the journalled verdict. Private Telegram delivery, official filings and ranking studies are also available. [Operations and configuration](docs/operations.md)
