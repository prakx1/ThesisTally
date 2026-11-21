# Swing Trading Radar — One-Page Project Plan

**Objective:** Build an automated, multi-strategy swing-trading system on Zerodha Kite APIs that finds high-quality setups in Nifty 200 stocks for a **2–4 week holding period**, sends Telegram signals, and **tracks every call to its outcome**. Rules generate signals; AI filters and researches them; nothing goes live until backtests and paper trading prove an edge.

### 1. Core Functionality

- **Daily scan:** Nifty 200 (point-in-time membership, liquidity-filtered) on adjusted daily candles.
- **Opportunity detection:** Strategy rules gated by a market-regime filter (Nifty trend, breadth, India VIX).
- **Live monitoring:** Shortlist tracked via KiteTicker WebSocket for triggers, stops and invalidations.
- **Ranking:** ML success probability × reward-to-risk, within portfolio limits.
- **Telegram signals:** Entry, stop, target, size, reasoning, AI verdict, with **Taken / Skip** buttons.
- **Terminal CLI (`radar`):** scan, backtest, calls, reports, AI audit. No web UI.
- **Call tracking:** Every signal stored and followed automatically to its outcome, including skipped and vetoed calls.
- **Backtesting:** Every strategy must pass a realistic backtest before it can alert.

### 2. Strategies

| Strategy | Detection logic | Phase |
|---|---|---|
| Trend pullback | Close > 50 EMA > 200 EMA, pullback within 1 ATR of 20 EMA, close above prior high | MVP |
| 52-week-high momentum | Top-20% relative strength, 3–8 week base within 5% of yearly high, volume breakout | MVP |
| VCP breakout | 2–4 shrinking contractions, falling volume, breakout above pivot on 1.5× volume | MVP |
| Sector rotation | Leading sectors + outperforming stocks | Phase 4 |
| Post-earnings momentum | Positive results surprise (LLM-read filings) + sustained strength | Phase 4 |
| Mean reversion | Oversold within a longer-term uptrend | Phase 4 |

**Exits:** closing-basis stop · half off at 2R · trail on 20 EMA / 10-day low · time stop (10 days without 1R, 20 days max). All parameters versioned in YAML.

### 3. Automated Schedule (IST, NSE trading days only)

| Time | Activity |
|---|---|
| 8:30 AM | Check Kite session; Telegram the login link if needed (daily login is manual) |
| 8:45 AM | Instruments, exclusion lists (ASM/GSM, upcoming results), watchlist |
| 9:00 AM | Verify yesterday's closes against NSE bhavcopy |
| 9:15 AM–3:30 PM | Live monitoring; provisional trigger signals |
| **3:45 PM** | **Full scan on completed daily candles; update open calls** |
| 4:00 PM | AI layer, ranking, Telegram summary |

### 4. Architecture

**Kite + NSE data → Market Data Store → Indicators & Detectors → Risk & Ranking (+ AI layer) → Call Journal → Telegram / CLI / Execution**

The backtest runner replays history through the same detectors, risk rules and journal, so backtest, paper and live share one code path.

**Stack:** Python, Polars/pandas · Kite Connect (₹500/month) + KiteTicker · DuckDB + Parquet (candles) · SQLite (call journal) · TA-Lib, pandas-ta-classic · vectorbt + own event loop, quantstats · LightGBM · OpenAI (Structured Outputs) · python-telegram-bot · Typer + Rich (CLI) · APScheduler · Docker on a Mumbai VPS with static IP.

### 5. AI & Open-Source Research

| Component | Role | Indian-market readiness |
|---|---|---|
| ML success model (LightGBM) | Probability a setup reaches +2R; ranks signals | Trained on our own Nifty 200 backtest calls |
| LLM filings reader (OpenAI) | Extracts results numbers; flags red flags (auditor exit, pledges, regulator orders) | Reads NSE/BSE filings directly |
| **TradingAgents** (open source) | Multi-agent research report on each confirmed signal | Runs on `.NS` tickers, but needs Indian data adapters: Kite prices, NSE/BSE filings, Indian news, RBI / India VIX / FII flows, Nifty benchmark |
| **Qlib + RD-Agent** (open source) | LLM proposes new ranking features; Qlib backtests them | No India dataset — load our candles via `dump_bin`, add NSE calendar, Indian costs and circuit limits |
| **Kite MCP server** (Zerodha) | Ad-hoc AI questions about account and market data | Native; read-only use, never orders |
| **OpenAlgo** (open source) | Optional multi-broker execution layer | Native; AGPL licence |

**Rules:** AI can approve, downgrade or veto — never create a call or change entry, stop or size. Every AI report is stored (`research_reports`, full report as a file) and scored against the call's outcome; any AI layer that doesn't beat rules-only after 100 calls is switched off. Each open-source tool sits behind our own adapter with a pinned version.

### 6. Signal Lifecycle & Tracking

**Detected → Watching → Confirmed → Active → Closed**, with side exits **Invalidated / Expired / Missed**.

Every call stores: stock, strategy version, regime, detection-time feature snapshot, entry/stop/target, ML score, LLM verdict, research report, action taken, every status change (append-only), fills, and outcome (R, ₹ after costs, MAE/MFE, days held). One unique key per setup prevents duplicates. Backtest, paper and live calls share the same tables.

### 7. Risk Management & Validation

- 0.5% risk per trade · minimum 2:1 reward-to-risk · max 8 positions · 4% total open risk · max 2 per sector.
- Drawdown brakes: halve risk at −6%, pause at −10%.
- Backtests: point-in-time universe, full Indian costs, slippage, gap fills, walk-forward testing, final hold-out.
- **Pass bar:** expectancy > +0.25R and profit factor > 1.4 out of sample.
- Alerts and paper trading first; live execution only after validation.

### 8. Implementation Phases (each ends with a gate)

**Phase 1 — Data foundation:** Kite ingestion, backfill, adjustments, point-in-time Nifty 200, data checks. *Gate: data verified.*

**Phase 2 — Detectors, backtest, call journal:** 3 strategies with exits and regime filter. *Gate: pass bar met.*

**Phase 3 — Paper trading + ML:** Telegram signals, CLI, tracking, LightGBM ranker, TradingAgents reports with Indian adapters. *Gate: 100+ paper calls in line with backtest.*

**Phase 4 — Intelligence:** filings reader, red-flag veto, Qlib + RD-Agent feature research, regime model, new strategies. *Gate: each AI layer beats rules-only.*

**Phase 5 — Live execution:** static IP, Algo ID, broker safeguards, kill switch, start at quarter size.

---

### End Goal

A **data-driven swing-trading intelligence platform** that identifies trades, tracks every call, and continuously measures which strategies — and which AI layers — work across stocks, sectors and market regimes.

**Guiding principle:** Detect → Monitor → Validate → Rank → Alert → Track → Measure → Improve.
