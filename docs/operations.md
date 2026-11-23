# Running ThesisTally locally

`uv run radar` opens Prices, Calls, Reports and Paper Trading. The Calls view shows the original plan, lifecycle, feedback and available shadow AI verdicts. Reports includes the sealed walk-forward study and any completed ranking comparisons.

Press **4** for the forward paper account. The daily scan automatically attempts the next-open entry for each allocated call and tracks partial sales, stops, costs and exits. The account uses the saved rule version for the life of each call and runs independently of Taken or Skipped feedback. Cash includes settled proceeds and dividends. Equity also includes held positions and outstanding corporate-action receivables. Press **Enter** on a trade for fills and daily position values, or on a date for that session’s executions. Older sessions without a recorded valuation display a dash when equity cannot be established.

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
