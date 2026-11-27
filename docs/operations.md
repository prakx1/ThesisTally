# Running ThesisTally locally

`uv run radar` opens Prices, Calls, Reports and Paper Trading. The Calls view shows the original plan, lifecycle, feedback and available shadow AI verdicts. Reports includes the sealed walk-forward study and any completed ranking comparisons.

Press **4** for the forward paper account. The daily scan automatically attempts the next-open entry for each allocated call and tracks partial sales, stops, costs and exits. The account uses the saved rule version for the life of each call and runs independently of Taken or Skipped feedback. Cash includes settled proceeds and dividends. Equity also includes held positions and outstanding corporate-action receivables. Press **Enter** on a trade for fills and daily position values, or on a date for that session’s executions. Older sessions without a recorded valuation display a dash when equity cannot be established.

The Paper Trading tab estimates P&L on shares still held using the selected terminal price feed. Look for the source and exchange time beside each holding. When Radar cannot verify the security identity, a newer trading session and a matching previous close, it shows the recorded mark. Paper fills, costs and settled balances come from the daily journal.

## When to expect new calls

The **Calls** tab keeps the daily swing journal. Trend pullback, volatility breakout and 52-week-high momentum were retired after the baseline study. A completed scan can still detect those patterns and update earlier paper calls while issuing zero new calls. Check the scan date first. A missing session and a completed session with zero calls mean different things. [Retirement decision](../src/thesistally/revisions/retirements.json)

The active A126 paper portfolio ranks stocks at the last trading close of each month. Eligible purchases are simulated at the next session’s open if the allocation gate permits them. A126 has its own ledger. Run `uv run radar report --since-freeze` to see its progress. [Frozen portfolio](#frozen-a126-forward-paper-account)

## Terminal price sources

| What you want | Connection Radar uses | What you need |
| --- | --- | --- |
| Watch prices and estimated paper P&L | NSE Official cash-market feed. Radar polls every two minutes. NSE publishes quotes with a 1–3 minute delay. | Run `uv run --frozen radar`. No account or API key. |
| Watch the same screen with faster updates | Zerodha Kite Connect. Radar requests quotes every five seconds. | Run `uv run --frozen radar --quote-provider kite` with a Kite Connect session that has market-data access. [Login and status](#kite-login-and-status) |
| Generate daily swing calls and settle paper trades | NSE’s final equity price file (the *bhavcopy*) and index closing file. Kite supplies historical daily prices for indicators and a price cross-check. | NSE archives are public. Collecting a new swing session also requires Kite historical-data access. Verified sessions can be read offline. |

**Reading the Prices tab.** Check the exchange time in IST beside the stock. Press **R** to request the latest available snapshot. The two-minute timer keeps requests across the full index manageable. Polling faster can display a newly published quote sooner, but calls and paper fills follow the completed daily session. During an outage, Radar labels cached quotes and shows a verified closing price when available. To keep Kite as your terminal preference, set `export RADAR_QUOTE_PROVIDER=kite`. Run `unset RADAR_QUOTE_PROVIDER` to restore the NSE default.

### Where Radar connects

- **NSE quotes.** Radar calls `https://mcp.nseindia.in/cmmkt/mcp` directly. It requests stock quotes in groups with `cm_get_equity_stocks` and reads Nifty 50, Nifty 200 and India VIX with `cm_get_live_indices`. NSE provides this feed for informational, non-commercial use. [NSE Official MCP](https://www.nseindia.in/nse-mcp)
- **NSE files.** Radar reads the current Nifty 200 constituent list and, when needed, the equity security list from `nsearchives.nseindia.com` to match symbols to securities. For recent sessions, final equity files come from `/content/cm/` and index closes from `/content/indices/`. The daily scan checks for the final file from 15:45 IST on trading days. [Constituent list](https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv) · [Daily archive reader](../src/thesistally/market.py)
- **Kite Connect.** Radar calls `api.kite.trade/quote` for optional terminal quotes. The swing collector uses `/instruments/NSE` to identify each security and `/instruments/historical/{instrument_token}/day` for daily candles. The terminal provider setting only controls the displayed quotes and estimated paper P&L. [Kite quote API](https://kite.trade/docs/connect/v3/market-quotes/) · [Kite historical API](https://kite.trade/docs/connect/v3/historical/)

## Stock reports

In **Prices**, press **/**, type the NSE symbol (for example, **SBIN**), and press **Enter** to focus the results. Select the stock and press **Enter** to open its most recently generated report. Use the arrow keys or Page Down to read it. **A** runs a fresh analysis. You can close the report with **Esc** and keep using the terminal while the analysis runs.

From the project directory:

```sh
uv run --frozen radar research SBIN          # Generate and read a fresh report
uv run --frozen radar research SBIN --saved  # Read the most recent saved report offline
```

The report shows the market date, rating, analyst views, bull case, bear case, risks and cited filings. A fresh analysis uses the latest archived market session by default. Add `--date YYYY-MM-DD` to analyze a specific archived session or to read a saved report for that exact market date. Press **q** to leave the command-line pager. Saved reports stay in `data/research/<symbol>/`. For use in scripts, add `--json` or `--csv`.

## Kite login and status

In Kite mode, **Check status (F5)** verifies the session with Kite. A successful check shows **Connected to Kite** in green with the time of verification. When sign-in is needed, choose **Log in to Kite (F6)**, finish in the browser, then press **F5**. A network outage can leave the status unavailable even with a valid login.

Renew the session on each day you choose the Kite feed. Zerodha normally expires the access token at 06:00 IST the following day. Reading saved research reports works offline. [Kite Connect session lifetime](https://kite.trade/docs/connect/v3/user/#authentication-and-token-exchange)

From the shell, `uv run --frozen radar login` opens the helper and `uv run --frozen radar session` checks the session. The default helper address is `http://127.0.0.1:8000/login`. On a different installation, set `RADAR_KITE_LOGIN_URL` to your own running Kite Connect login helper. The helper completes the token exchange and saves the fresh session.

Point `RADAR_CREDENTIALS` or the ignored `.radar-credentials` symlink at the session file your helper refreshes. It can contain `api_key` and `access_token` at the top level or under `kite`. ThesisTally rereads the file for the next check. If you supply both `KITE_API_KEY` and `KITE_ACCESS_TOKEN` as environment variables, they take precedence and you must update them when you relaunch after login.

## Private notifications and unattended collection

Keep `uv run radar` open for the after-close scan and automatic research. For the full unattended scheduler, start `uv run radar run` in a persistent terminal. The scheduler checks the Kite session at 08:30 IST, caches instruments and dated exclusion lists, looks for the exchange final from 15:45, and advances the journal once per session. The exchange calendar includes Muhurat sessions. In December it requests the following year's published NSE calendar. Calendar files live under `data/calendars/` and include their official source. Daily Kite login is an operator step.

| Environment variable | Purpose |
| --- | --- |
| `KITE_API_KEY`, `KITE_ACCESS_TOKEN` | Read-only market data and session checks. The existing local Kite handoff also works. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Private destination for research plans, updates, summaries and warnings. |
| `RADAR_HEALTHCHECKS_JSON` | JSON object mapping `session`, `instruments`, `collect`, `scan`, `summary` and `backup` to individual Healthchecks ping URLs. Configure expected schedules and grace periods at the monitor. |
| `RADAR_BACKUP_DIR` | A directory outside the live data tree, preferably on the intended secondary disk. The 23:00 backup incrementally copies data and uses SQLite's backup API for the journal. |
| `RADAR_AI_PROVIDER` | Defaults to `codex`. Set `off` to disable research or `openai` to use the metered API. This environment variable overrides the saved data-directory setting. |
| `RADAR_CODEX_MODEL` | Codex model requested throughout the graph. Defaults to `gpt-6.2-sol`. |
| `OPENAI_API_KEY`, `RADAR_AI_DAILY_BUDGET_INR` | Enable metered OpenAI API research. The default budget is zero. An atomic local reservation limits combined spending across jobs. |
| `RADAR_AI_INR_PER_USD` | Accounting rate for model charges. The default is ₹85 per US dollar. Each request records its INR charge. A provider timeout retains the conservative reservation amount as a cost estimate. |
| `RADAR_AI_VETO_ENFORCED` | Defaults to false. An explicitly enabled veto can move a pending observation to shadow only after a positive comparison across at least 100 closed calls with pre-entry verdicts. |

The scan writes outgoing events to SQLite. One private setup message carries **Taken** and **Skip** buttons. The callback verifies the destination chat and writes the terminal's regular feedback event. Delivery retries after a Telegram outage. Telegram can receive a duplicate if the machine stops after posting and before acknowledging delivery in SQLite. Shadow plans appear in the daily count.

### Automatic Codex research

TradingAgents research is on by default. Keep `uv run radar` open and signed in to Kite and Codex. On exchange days the terminal checks for the NSE final every five minutes from 15:45 IST. Each allocated call from a forward scan goes into the research queue. The scan starts a background worker that finishes the queued reviews even if you close the terminal. The full scheduler and terminal can share one data directory. Reopening either recovers interrupted jobs.

Run `uv run radar research-mode off` to disable research for this data directory. Scanning continues. Existing queued reviews pause. An in-flight model request can finish, and the worker checks the setting before the next request. Run `uv run radar research-mode on` to resume with Codex, or `uv run radar research-mode` to see the effective setting. For a session-only override, use `RADAR_AI_PROVIDER=off uv run radar`.

The worker runs `codex exec` in a temporary read-only workspace with the dated evidence passed on standard input. ThesisTally supplies the market, macro and filings data to the TradingAgents analyst roles. Each CLI response is validated against its JSON schema. Filing extracts retain matching source quotations and page or XBRL references. Successfully extracted sections are cached by source hash, model and prompt version.

Codex runs use the signed-in ChatGPT allowance. The audit records provider, tokens and metered API spend. A zero metered API charge for a Codex request excludes the subscription price and any plan credits used. A missing login, exhausted allowance or failed CLI invocation is journalled as unavailable. The daily scan and its original trade plan continue independently.

## Queries and studies

```sh
uv run radar scan --date 2026-11-20 --json
uv run radar calls --open --strategy trend_pullback --csv
uv run radar calls --shadow --json
uv run radar call 390 --json
uv run radar report --by sector --csv
uv run radar data check --from 2014-01-01 --to 2026-11-20 --json
uv run radar data filings COFORGE --date 2026-11-20
uv run radar research 390
uv run radar research COFORGE --date 2026-11-20
uv run radar ai-audit --json
uv run --extra ranking radar ranker-study --json
```

The filing archive keeps original documents, filing timestamps, source URLs and SHA-256 hashes. Collection records failures separately for NSE and BSE. Dated PDFs are extracted in sections with page numbers. XBRL facts retain element and context references. Extracted facts must carry a supporting excerpt from the cited section. The research layer uses TradingAgents 0.2.5 with local prices, official filings, available RBI releases and institutional flows, and the Nifty 200 benchmark. Missing macro observations appear in the report context. The selected provider supplies filing extracts, the TradingAgents analysts and debate, the five-tier report and the shadow verdict. The metered API uses `gpt-4.1-mini-2025-04-14`. The AI audit pairs outcomes only with verdicts recorded before the original entry.

The ranking study trains LightGBM on detection-time inputs and resolved trade labels, with a holding-period gap between training and test. It compares probability times planned reward/risk with relative strength under the simulated portfolio limits. Test windows without sufficient prior labels are listed. The final holdout is reserved for the original walk-forward study. Per-strategy regime tables distinguish the funded and shadow books and show the evidence for testing a regime change.

## Frozen A126 forward paper account

Run `uv run radar report --since-freeze` to see the current candidate label, actual observed sessions, average stock exposure, net return, peak-to-trough drawdown and cumulative one-way turnover. Add `--json` for the machine report. It starts from ₹10 lakh cash after freeze, first ranks on a prospective month-end close and sends its paper purchases to the following session's open. Every mark uses the frozen A126 rules and advances once. A new rule revision needs a new freeze and ledger.

The comparator is the official Nifty 200 gross TRI, held from the freeze anchor. Its starting exposure is 100% and its subsequent trading turnover is zero. Both series cover exactly the same closing intervals. An unpublished TRI leaves comparison metrics pending and the paper account continues. An unverified holding mark leaves the last committed account visible and identifies the missing session. A zero-session report has no measured performance.

The normal observation scan and the dedicated `uv run radar forward-update` catch up completed sessions. The dedicated command uses only exchange data and the paper ledger. It requests NSE final bhavcopies, index closes, dated membership and corporate announcements, and records Kite agreement or unavailability as a separate check. The public collector can proceed without a Kite login. An unsupported issuer change or a missing session is reported and retried. The initial 400-session histories come from the certified anchor. A newly admitted ISIN's missing history is rebuilt from dated exchange prices and notices.

The macOS user service `com.thesistally.a126-paper` runs at login and every ten minutes while the machine is awake. The service uses its own copy of the tagged code and public data under `~/Library/Application Support/ThesisTally`. The repository terminal uses the same ledger through its local `data/forward/installation.json` pointer. Service messages go under `~/Library/Logs/ThesisTally`. To reinstall the tagged service run `uv run --frozen python scripts/install_a126_agent.py`. On another system, schedule `radar forward-update` against that host’s staged anchor and public data.
