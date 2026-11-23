# Historical research in ThesisTally

Start the terminal with `uv run radar`. The Reports tab opens locally saved studies. Collection and evaluation also run from the shell.

```sh
uv run radar data backfill --from 2014-01-01
uv run radar data check --from 2014-01-01 --to 2026-11-20
uv run radar backtest --walk-forward --train-years 3 --test-years 1
```

The backfill stores official NSE equity finals, index observations, corporate announcements and the source files used to reconstruct membership. The capital-market archive changes format in July 2024. Both formats retain the ISIN. The independent Kite check compares price shapes on the NSE close scale because Kite supplies adjusted history. Unavailable instruments and conflicting candles appear in `data/sources/kite-comparison.json`. Historical PDF tables require the Poppler `pdftotext` executable.

## Membership and prices

`data/universes/nifty200_membership.csv` has one row per symbol, ISIN and inclusive validity interval, with an official source URL. The reconstruction starts from monthly index-weight PDFs, applies effective changes from exchange circulars and compares each traded basket’s shares traded and rupee turnover with the exchange’s daily index totals. A symbol rename keeps its ISIN. Listed demerger entities have their own ISIN. Documented pre-listing index placeholders appear in the CSV with the ISIN resolved from the listing-day bhavcopy. They become eligible for scanning when the equity trades. The adjacent `membership-gaps.json` records unresolved periods and placeholder identities.

`data/sources/adjusted/` holds raw OHLCV, cumulative ex-date factors, common-unit price series and a quarantine ledger. Daily snapshots rebase the earlier bars using factors effective by that session. The ledger records unexplained equity moves over 20%, invalid prices and unresolved adjustments. On days when NSE published only the India VIX close, the archive preserves the close and leaves its intraday range unavailable. The regime rule uses the close.

Historical ASM/GSM flags and opening price bands can be unavailable. Archived context records missing surveillance as unknown and missing band limits as null. Each study lists affected dates. The shared portfolio engine places a setup with unknown surveillance in the shadow book. Missing opening limits prevent a simulated entry. These limitations affect the number of measurable trades in earlier windows.

## Evaluation

For r2, new paper sessions after the future rule freeze are the final out-of-sample test. The 2–20 November 2026 historical diagnostic is retired.

Each rolling test starts with fresh simulated capital. The preceding training dates establish the information available to the fixed rules. In the archived r1 methodology the final 18 months have a separate holdout ledger keyed by rule revision. An identical completed study can be read again. The journal prevents consuming a second formal holdout for the same revision. The older short replay remains accessible as a diagnostic.

The JSON report records the configuration and data hashes, source-code fingerprint, local source commit, checkout commit when available, parameters, and model and prompt versions. Each archived source commit can be inspected with `git -C data/provenance/source show <code_commit>`. It reports after-cost returns, the bootstrap interval, daily mark-to-market drawdown, exposure, and CAGR alongside Nifty 200 buy-and-hold. Strategy account curves attribute that strategy’s positions within the shared portfolio to the same starting capital. Regime, sector and year splits distinguish portfolio and shadow calls.

Qualification uses closed calls from sufficiently old test cohorts. An open call does not block a closed cohort from being assessed. Each criterion records its measured value, threshold and result, including the sample size for each test window. Only test windows contribute to the qualification decision. The paired blackout comparison runs over those same test windows with an independent account for each policy.

### Compulsory corporate actions

The simulation uses the `gross-entitlements-v1` convention. For a mandatory exchange of shares it holds the original purchase and risk in the journal, converts the outstanding quantity by the published ratio on the effective date and translates entry, stop and target into successor-share units. A non-trading effective date is recorded on the next exchange session. It carries the last traded position value through suspension. A closing-stop or holding-time breach queues an exit for the first eligible successor open, subject to liquidity and slippage. The cash ledger books gross entitlements when credited. Investor-specific withholding is recorded in the event notes.

Whole shares use integer division. Fractions receive the issuer's published proceeds per share when available. The flagged fallback is the first observed successor close on or after the effective date. A compulsory cash delisting uses the published offer, or the flagged last trade if the price is unavailable. The source URL, stage, dates and convention are available in each affected call's events. Reports show the difference when fractional proceeds are set to zero and when affected calls are excluded, holding all other fills and sizes fixed.

For Tata Motors DVR, NSE suspended the predecessor on 30 August 2024. The 7-for-10 conversion took effect on 1 September. Tata Motors confirmed completion of credit on 25 September. The model uses the next open on 26 September because the issuer did not specify that every shareholder could sell at the opening auction on the 25th. The trustee communicated individual cash details privately. The archive flags its fractional-cash estimate. [Suspension circular](https://nsearchives.nseindia.com/content/circulars/CML63585.pdf) · [Scheme FAQ](https://www.tatamotors.com/wp-content/uploads/2024/09/FAQs-Cancellation-of-A-Ordinary-Shares.pdf) · [Credit confirmation](https://static-assets.tatamotors.com/Production/www-tatamotors-com-NEW/wp-content/uploads/2024/09/NSEBSELETTER-250924.pdf)

The bundled action register is under `src/thesistally/revisions/compulsory-actions.json`. Additional documented terms can be supplied as an array with the same fields in `data/corporate_actions.json`. Cash-only delistings omit the successor and may specify `offer_price`. A conversion can specify the issuer’s `fraction_price`, `fraction_source` and a separate `cash_date`. When cash arrives after share credit, the journal carries a non-spendable receivable. If the shares are sold first, the call’s final return is recorded when the cash settles. The report fingerprints the supplied evidence. A funded position with an unresolved price or action records its call ID, ISIN and date at the point where valuation stopped. Other test windows continue with separate accounts. A partial window cannot qualify a strategy.

`uv run radar backtest --walk-forward --tests-only` runs the historical training and test periods. The archived r1 design reserves a separate 18-month historical holdout. R2 includes the full post-training history in test windows and can end with a partial final window. Its final out-of-sample test starts with new forward paper sessions after the freeze.

The sealed research-4 report covers all ten planned accounts, including the final holdout. A later data-reader correction retrieves full-exchange opening bands for positions that leave the index. The archived study had four shadow calls with deferred exits after index removal, one in MAHABANK and three in SUNDARMFIN. No funded call had a deferred exit outside the index. The sealed report retains the original shadow outcomes.

## Stage 2 baseline

The Stage 2 comparison runs both stored rule revisions on the same certified market archive. Each annual test starts with ₹10 lakh and an independent equal-risk book with ₹5,000 per eligible trade. Both years with zero purchases and the partial last calendar year remain in the results. The three preceding years supply historical context. With fixed rules, no parameters are estimated during training.

Run the paired replay on the prepared workspace.

```sh
uv run --frozen python -m thesistally.baseline_replay
```

The command prints the path to `replay.json`. Use that path to publish the numerical tables, then open the terminal.

```sh
uv run --frozen python scripts/publish_baseline.py data/baseline/runs/<run-id>/replay.json
uv run radar
```

Select **Reports** in the terminal. Each account and each revision retains its original calls, fill events, whole-share quantities and costs. A replay restarts from the last consecutive committed session in each book. A data or source revision produces a new run identity.

The baseline verdict and gross/net loss analysis use the versioned Stage 2 qualification configuration. Closed trades with a complete opportunity to reach their holding limit within their window contribute to qualification. A test window needs 20 such closes to contribute a trade or a positive vote. All scheduled windows count in the two-thirds requirement. The report also shows all actual closes, pending signals, open holdings and unmeasurable calls. [Qualification policy](../src/thesistally/validation/stage-2-v1.json)

The reviewed Stage 2 baseline includes both original revisions, funded and equal-risk diagnoses, and the historical data limits. To reproduce its narrative and independent-review section after reproducing the local ledgers, use the recorded study directory.

```sh
run=data/baseline/runs/e923bed5c9d9448d7340
uv run --frozen python scripts/publish_baseline.py "$run/replay.json" --interpretation "$run/interpretation.json" --review "$run/review.json"
```

[Reviewed baseline](BASELINE_REPORT.md) · [Full result JSON](../data/backtests/stage2-baseline-e923bed5c9d9448d7340.json)
