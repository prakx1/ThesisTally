# Independent review of Strategy D

**Neither holding period had enough filing coverage for a historical test.** The final archive supplies all four first-reported figures for **610 of 10,221** eligible Nifty 200 stock-quarters from January 2014 through the 20 November 2026 market close. It supplies original EPS for 925. No walk-forward window reached the preregistered 80% threshold. Both study events retain the status `proposed` and the evaluation `insufficient data`. [D40 register event](../data/discovery/register/results-d40/02-000001-study.json) · [D20 register event](../data/discovery/register/results-d20/02-000001-study.json)

| Test window | Complete stock-quarters / eligible | Coverage |
|---|---:|---:|
| 2017 | 0 / 804 | 0.00% |
| 2018 | 17 / 804 | 2.11% |
| 2019 | 31 / 804 | 3.86% |
| 2020 | 60 / 802 | 7.48% |
| 2021 | 66 / 800 | 8.25% |
| 2022 | 69 / 800 | 8.63% |
| 2023 | 66 / 801 | 8.24% |
| 2024 | 62 / 803 | 7.72% |
| 2025 | 102 / 800 | 12.75% |
| 2026 through 20 November | 137 / 801 | 17.10% |

An annual window uses its previous December and current March, June and September quarter-end constituents and information available by its last session. The overall count also includes the training years. The independent reconciliation uses 51 quarter ends, 3,190 actual sessions and the full historical membership file. Of 465 member ISINs, 448 appeared on a quarter end. The other 17 remained in the historical universe. The release and entry checks also use dated membership. [Coverage and issuer gaps](FUNDAMENTALS_COVERAGE.md)

## First disclosure and signal timing

I traced the official announcement time, reporting basis, quarter and accepted source fields. Later changes stay separate. Of 18 previously challenged issuer-quarter-basis clocks, six kept the verified original time and twelve had no verified first values. None used a later formal upload as the earlier release. Announcements after 15:30 IST and non-session announcements moved to the next session. The source census includes 15,525 of 15,577 requested original notices and 28,362 of 29,062 requested structured resources. The BSE market archive was inaccessible. The 30 discovered BSE document links supplied no independently verified earnings dissemination time, so an earlier BSE-only release can remain unknown. [Download reconciliation](../data/fundamentals/review/acquisition-decoded-a96533f7.json) · [First-release source audit](../data/fundamentals/review/POST_DOWNLOAD_AUDIT.md)

I checked the full modern original-page edition against the dated issuer PDFs and separate XBRL. All 1,155 fields reproduced from original page pixels and quarterly table cells across 637 filing-basis records. The final canonical ledger directly attributes 781 fields in 440 basis records to these optical witnesses. The legacy pass read 44,444 physical page occurrences across 5,029 packets and found zero additional fields. Forty of its 7,333 basis cases already had some verified original values. For the September 2012 Tech Mahindra result, independent renders and text recognition matched every one of the seven pages in its four original PDFs, including a page that rendered 2,401 pixels wide. [Modern source cells](../data/fundamentals/review/completed-modern-financials-673ac0fa.json) · [Canonical field links](../data/fundamentals/review/canonical-optical-0e93eb35.json) · [Legacy source counts](../data/fundamentals/review/completed-legacy-scope-306980ad.json) · [Tech Mahindra evidence](../data/fundamentals/review/techm-2012-original-physical-pages.json)

Four announcements had the 13 consecutive same-basis original EPS values needed to form the current and eight previous year-over-year changes. Two met the surprise and price-reaction conditions. L&T Technology Services had a +2.013 percentage-point excess reaction in July 2022 and Tata Communications had +0.274 points in July 2026. Each was the only score in its 90-day comparison group. The independent calculation used the four closing prices spanning the three completed reaction sessions, then checked the following open as the prospective entry. Both observations fall in excluded windows. [Replay evidence](../data/fundamentals/replays/80439cf03f306663141a/report.json)

## Execution, isolation and limits

The historical coverage gate excluded all 40 year, holding-period and book combinations before trade execution. No historical trade count, expectancy, drawdown, loss distribution, TRI comparison or after-tax result can be estimated from this study. The focused test suite passed 214 tests. It exercised actual shared next-open entry slippage, the fill-based 3 ATR stop, the strict closing-stop trigger and next tradable open, the 20- and 40-session scheduled exits, funded versus equal-risk rules, holder settlements, fees, benchmark liquidation and fiscal-year taxes on controlled data. Shared corporate-action tests cover successor shares, fractional cash, suspended holdings, rights and delisting. Those checks establish simulated bookkeeping behavior. [Focused tests](../data/fundamentals/review/final-focused-tests.txt)

The independent review surfaced and retested corrections to earlier release clocks, currency units, ambiguous PDF numerics, missing page reads and unsupported or erased optical cells. A real Muthoot Finance filing retained its original consolidated figures while altered copies with a missing page, changed EPS or all four values erased failed publication. A fabricated zero-page legacy completion failed its physical census. Direct registration now requires both completed optical cohorts and the exact source processing receipt. The study kept the preregistered signal and exit parameters. The A126 tag still resolves to `dc8e45c791c4f8485eaa869a6985f492805039b3`; the frozen config and original discovery-isolation test match the research baseline. The research runs used the separate worktree and research output directories. [Research log](RESEARCH_LOG.md) · [Preregistration](PREREGISTRATION.md)

Coverage measures what the signed source readers could establish. They omitted genuine printed facts when issuer, quarter, basis, currency, column or first-release timing could not be certified. The 90-day peer samples reflect these gaps. An ISIN-changing split also breaks the registered same-ISIN EPS sequence, as in Eicher Motors in 2020. Measuring Strategy D’s edge will require more complete first-disclosure data. [Numerical reconciliation](../data/fundamentals/review/final-0e93eb35.json)

The local review attestation binds this document, the numerical reconciliation, the source evidence, the test result and both registry events. Run `.venv/bin/python data/fundamentals/review/verify_attestation.py` in the research worktree to check the hashes and the Ed25519 signature. [Signed receipt](../data/fundamentals/review/final-attestation.json)
