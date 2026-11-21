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

Each rolling test starts with fresh simulated capital. The preceding training dates establish the information available to the fixed rules. The final 18 months have a separate holdout ledger keyed by rule revision. An identical completed study can be read again. The journal prevents consuming a second formal holdout for the same revision. The older short replay remains accessible as a diagnostic.

The JSON report records the configuration and data hashes, source-code fingerprint, local source commit, checkout commit when available, parameters, and model and prompt versions. Each archived source commit can be inspected with `git -C data/provenance/source show <code_commit>`. It reports after-cost returns, the bootstrap interval, daily mark-to-market drawdown, exposure, and CAGR alongside Nifty 200 buy-and-hold. Strategy account curves attribute that strategy’s positions within the shared portfolio to the same starting capital. Regime, sector and year splits distinguish portfolio and shadow calls.

Qualification uses closed calls from sufficiently old test cohorts. An open call does not block a closed cohort from being assessed. Each criterion records its measured value, threshold and result, including the sample size for each test window. Only test windows contribute to the qualification decision. The paired blackout comparison runs over those same test windows with an independent account for each policy.

### Compulsory corporate actions

The simulation uses the `gross-entitlements-v1` convention. For a mandatory exchange of shares it holds the original purchase and risk in the journal, converts the outstanding quantity by the published ratio on the effective date and translates entry, stop and target into successor-share units. A non-trading effective date is recorded on the next exchange session. It carries the last traded position value through suspension. A closing-stop or holding-time breach queues an exit for the first eligible successor open, subject to liquidity and slippage. The cash ledger books gross entitlements when credited. Investor-specific withholding is recorded in the event notes.

Whole shares use integer division. Fractions receive the issuer's published proceeds per share when available. The flagged fallback is the first observed successor close on or after the effective date. A compulsory cash delisting uses the published offer, or the flagged last trade if the price is unavailable. The source URL, stage, dates and convention are available in each affected call's events. Reports show the difference when fractional proceeds are set to zero and when affected calls are excluded, holding all other fills and sizes fixed.

For Tata Motors DVR, NSE suspended the predecessor on 30 August 2024. The 7-for-10 conversion took effect on 1 September. Tata Motors confirmed completion of credit on 25 September. The model uses the next open on 26 September because the issuer did not specify that every shareholder could sell at the opening auction on the 25th. The trustee communicated individual cash details privately. The archive flags its fractional-cash estimate. [Suspension circular](https://nsearchives.nseindia.com/content/circulars/CML63585.pdf) · [Scheme FAQ](https://www.tatamotors.com/wp-content/uploads/2024/09/FAQs-Cancellation-of-A-Ordinary-Shares.pdf) · [Credit confirmation](https://static-assets.tatamotors.com/Production/www-tatamotors-com-NEW/wp-content/uploads/2024/09/NSEBSELETTER-250924.pdf)

The bundled action register is under `src/thesistally/revisions/compulsory-actions.json`. Additional documented terms can be supplied as an array with the same fields in `data/corporate_actions.json`. Cash-only delistings omit the successor and may specify `offer_price`. A conversion can specify the issuer’s `fraction_price`, `fraction_source` and a separate `cash_date`. When cash arrives after share credit, the journal carries a non-spendable receivable. If the shares are sold first, the call’s final return is recorded when the cash settles. The report fingerprints the supplied evidence. A funded position with an unresolved price or action records its call ID, ISIN and date at the point where valuation stopped. Other test windows continue with separate accounts. A partial window cannot qualify a strategy.

`uv run radar backtest --walk-forward --tests-only` runs the training and test periods and preserves the final holdout. The final test period can be shorter than a year so every post-training trading session belongs to a test or the holdout.

The sealed research-4 report covers all ten planned accounts, including the final holdout. A later data-reader correction retrieves full-exchange opening bands for positions that leave the index. The archived study had four shadow calls with deferred exits after index removal, one in MAHABANK and three in SUNDARMFIN. No funded call had a deferred exit outside the index. The sealed report retains the original shadow outcomes.
