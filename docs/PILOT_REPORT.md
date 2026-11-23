# Stage 1 pilot · Nifty 200 swing research

**Performance label — not for decisions.** This is one continuous ₹10 lakh paper account from 1 January 2024 through 20 November 2026. Its purpose is to verify whether signals, allocations, fills and cash can be reconstructed from dated evidence. The period overlaps the previously studied holdout. It cannot supply a fresh out-of-sample estimate of strategy performance.

## Account results · not for decisions

The scan found **12,143 plans**. The funded account allocated 545, bought and closed 116, missed 428 because opening prices and slippage reduced the reward-to-risk ratio, and invalidated one because the opening fill crossed the stop. It ended in cash at **₹9,46,409.72**, down **₹53,590.28 (5.36%)**. Expectancy was **−0.158R per closed trade**. Maximum drawdown from the account's running peak was **10.20%**. Average invested exposure was **13.41%**. The Nifty 200 price index rose 23.44% over the same period. That benchmark assumes continuous exposure and excludes dividends.

| Funded strategy | Plans allocated | Trades closed | Win rate | Mean net R | Profit factor | Net P&L |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend pullback | 495 | 108 | 35.19% | −0.162 | 0.80 | −₹42,068.15 |
| Volatility breakout | 50 | 8 | 37.50% | −0.104 | 0.23 | −₹11,522.13 |
| 52-week-high momentum | 0 | 0 | — | — | — | — |
| **Total** | **545** | **116** | **35.34%** | **−0.158** | **0.77** | **−₹53,590.28** |

The same executed quantities lost ₹23,321.78 when valued at the un-slipped prices before fees. Modelled adverse slippage cost another ₹8,728.82, and explicit charges including DP cost ₹21,539.68. The net result already includes ₹3,074.90 of dividends and ₹218.53 of fractional cash. These are pre-tax results under the historical simulation's specified fee schedule, with investor-specific taxes excluded.

Two periods restricted the funded sample. Surveillance and opening bands were unavailable for every constituent through 5 July 2024. On 4 March 2026 the account first crossed the existing 10% drawdown threshold. The last funded sale was 17 March. With the account in cash, the drawdown rule subsequently routed 190 otherwise eligible signals to shadow through 20 November. The dates overlap an earlier holdout, and very few funded trades came from either breakout strategy. The pilot supplies no basis for qualifying or tuning a strategy.

### Shadow outcomes

| Strategy | Shadow plans | Entered | Closed | Mean net R on closures |
| --- | ---: | ---: | ---: | ---: |
| Trend pullback | 10,138 | 1,735 | 1,669 | −0.310 |
| Volatility breakout | 1,448 | 263 | 237 | −0.161 |
| 52-week-high momentum | 12 | 4 | 4 | −0.230 |
| **Total** | **11,598** | **2,002** | **1,910** | **−0.291** |

At cutoff, 89 shadows were active, 24 were awaiting an entry decision, 9,492 missed and 80 were invalidated. Three entered shadows were unmeasurable. TATACONSUM issued voluntary rights, TVSMOTOR issued a separate class of redeemable preference shares, and POLICYBZR had a quarantined closing price. The first two instruments require settlement or trading decisions beyond the documented single-successor model. No return was assigned to any of the three. Shadow calls can overlap and use independent hypothetical capital.

## Input coverage and limits

NSE finals covered 143,814 tradable constituents by date across 276 ISINs and all 718 sessions. Each day's reconstructed basket reconciled to both published Nifty 200 turnover and share volume. The 302 pilot intervals use contemporaneous sources and document subsequent ISIN identification separately. The canonical CSV preserves merged, removed and formerly listed members. There were 26 changes to the traded basket. The historical 400-session warm-up begins 24 May 2022. [Canonical membership](../data/universes/nifty200_membership.csv) · [Per-interval receipts](pilot-membership-provenance.csv)

Thirty-five source sessions used a Kite warm-up in the earlier archive. Their pilot replacements use NSE history. Of 143,814 equity observations, 606 lack an independent Kite comparison. Kite and NSE differed on candle shape in 422 comparable observations and by over 10 basis points in 408 of 142,927 comparable adjacent close returns. Missing control coverage includes 25,735 member-days for surveillance and 25,728 for opening bands. An additional 27,752 member-days have unknown sector. Those stocks share the existing unknown-sector allocation bucket. All 42 previously incomplete December earnings windows were recovered from dated notices. Each limit and its effect on the simulator are documented in the gap register. [Data gaps](DATA_GAPS.md) · [Verified preflight receipt](../data/pilot/preflight.json)

The Bharat Dynamics split needed an identifier correction. The 24 May 2024 bhavcopy quoted split-adjusted prices under the old ISIN, and the new ISIN first appeared on 27 May. Joining those records with one two-for-one adjustment restored 399 full indicator histories. The final audit checked all 16 same-symbol ISIN switches among pilot names and verified each published ratio. Across the period, 12,546 member-date lookbacks contained fewer than 400 prices. The gap report distinguishes young price histories from documented or quarantined breaks. [BDL exchange circular](https://nsearchives.nseindia.com/content/circulars/CML62006.pdf)

## What the pilot measures

The original `research-4` parameter file drives all three detectors. Each session uses its own tradable index members and trailing prices. A plan enters the funded book only after the existing risk, regime, surveillance and portfolio checks. At the next session's opening auction the engine applies the original reward-to-risk filter, tradability and adverse slippage. Shadow calls record excluded plans separately. A signal printed on the final date stays pending until there is an observable next auction.

The engine books 5 basis points of adverse slippage and the configured explicit exchange, statutory and depository charges. R uses the original filled risk in rupees, even after a conversion. The trading-account convention excludes holder-specific tax. For a bar that touches both the outstanding stop and target, the engine queues the full quantity for the next tradable open. A lower-band lock defers that sale. A stop-only bar uses the existing closing-stop rule. [Rule configuration](../src/thesistally/rules.yaml)

## Checks on the shared implementation

- Appending future prices left earlier strategy signals, indicator values, ranks and regime unchanged in the regression fixtures. An independent replay of the 20 November 2026 NSE basket reproduced the COFORGE pullback with entry ₹2,086.70, stop ₹2,011.20, target ₹2,237.70 and ATR(14) 68.79333. The other two strategies have fixed breakout cases.
- Stop distances below 3%, above 10%, or above 2.5 ATR(14) create a shadow call with reason `stop_out_of_bounds`. Exact boundaries are inclusive. Regression tests found and corrected floating-point rejection at the boundaries.
- A prior order at the opening auction cannot use a jump first observable at that day's close. A sale already due can fill at a verified open. A funded position that still requires a quarantined mark stops the replay at that date. A shadow position with that gap is labelled unmeasurable.
- Positions leaving the index continue by ISIN on the official equity tape, including the BE and BZ series. The quoted series determines which exchange bands apply. Frozen positions retain the original levels and translate levels into the successor's units. Frozen stop, collision and time breaches have dated journal events.
- The independent reviewer matched all 2,154 same-session benchmark closes for Nifty 50, Nifty 200 and India VIX to the official closing files. The NSE monthly calendar omits the special 2 March 2024 session. Both the exchange's final cash-market files and the Kite benchmark establish that session.

## Corporate-action checks

**Tata Motors DVR.** The predecessor stopped trading on 30 August 2024. The 7-for-10 conversion took effect on Sunday 1 September. The successor closed at ₹1,092.65 on 2 September. The issuer confirmed credit after market hours on 25 September, so the first supported sale is the 26 September open of ₹970.80. The trustee disclosed the quantity sold for settlement. Its published material did not give us a rupee price per fractional share. The simulation uses the flagged first-close fallback and excludes individual withholding. The independent reviewer verified the five archived document checksums. [NSE suspension](https://nsearchives.nseindia.com/content/circulars/CML63585.pdf) · [Issuer credit filing](https://static-assets.tatamotors.com/Production/www-tatamotors-com-NEW/wp-content/uploads/2024/09/NSEBSELETTER-250924.pdf)

The separately seeded 209-share check converted to 146 ordinary shares and ₹327.80 for the 0.3 fraction. Its original ₹700 stop became ₹1,000. The successor closed below that stop on 11 September during suspension, and the journal queued the eventual sale. All 146 shares sold for ₹970.3146 each after slippage on 26 September. Assigning zero cash to the fraction reduces this fixture's P&L by ₹327.80. The fixture is excluded from the strategy and account figures. [Dated fixture events and sensitivity](../data/pilot/fixtures/dvr-209-uta9d03h/trace.json)

**COFORGE.** An earlier pilot run stopped on 4 June 2025 because the portfolio held the disappearing ISIN. NSE made the new five-for-one share units effective for market trading on 4 June. The issuer separately confirms demat adjustments were completed on 5 June. Using the first open after that confirmed date, a holder of the predecessor could sell on 6 June. A regression covers a holder who sold two of five original shares before the split, retained three, and received 15 successor shares. It preserves the original cost and risk, carries a frozen value of ₹25,497, queues the 20-session exit on 5 June and sells at the slipped open of ₹1,778.61025 on 6 June. The interrupted ledger and its report were preserved. [NSE change of ISIN](https://nsearchives.nseindia.com/content/circulars/CML68318.pdf) · [Issuer Annual Report, Directors’ Report](https://www.coforge.com/hubfs/Annual-Report-2025.pdf)

**Measured holdings.** The funded DVR call bought 216 shares. It converted to 151 ordinary shares and 0.2 of a share, received ₹218.53, and settled for a net loss of ₹10,944.74. Holding other fills fixed, zero fractional cash would reduce the account's ending equity by ₹218.53. The funded COFORGE call closed for a ₹5,938.61 gain on its original five-share risk. Excluding the two funded corporate-action trades leaves 114 closed trades, −₹48,584.16 and −0.186R per trade. This sensitivity holds every other fill and quantity fixed. One Adani Power shadow held 77 shares through a five-for-one split, received 385 shares and exited on 24 September 2025.

## Independent reconciliation

A Codex reviewer who wrote none of the implementation recalculated fees and DP charges directly from the rates on all 4,571 funded and shadow fills, and reconstructed all 718 funded daily marks from cash flows, dated exchange closes, frozen prices and recorded share ratios. Every value reconciled within ₹0.000001. The minimum verified opening cash was ₹82,902.84. The reviewer independently found exactly two eligible bars touching both outstanding levels across 17,004 non-corporate holding bars checked. Both were shadow calls, and both sold the full position at the next tradable open. A regression also exercises the lower-band deferral case.

Zero of the 254 funded fills overlapped a flagged Kite discrepancy. Shadow fills overlapped 30 distinct close-return exceptions and 25 candle-shape exceptions. One funded and 24 shadow holding-days were outside the index. None required an off-index BE or BZ price in this sample. Seven pending shadow buys coincided with close-discovered quarantines. Six missed under the opening liquidity or band checks, and one crossed the original stop at the open. The remaining held POLICYBZR shadow became unmeasurable at the close. [Exposure and timing audit](../data/pilot/runs/pilot-7c67139d16a8cd5283d1/exposure-audit.json)

A second actual invocation reused the same journal without a new session. All 718 scans, 12,143 calls, 56,877 events, 4,571 fills and 2,872 marks were preserved. The SQLite file, JSON report and timing file were byte-identical. [Repeat-run proof](../data/pilot/runs/pilot-7c67139d16a8cd5283d1/idempotence.json)

A separate full preparation of the 718 sessions ran on an isolated filesystem copy in 183.0 seconds. It reproduced all 381,360 bytes of the receipt, the independently calculated market fingerprint, the membership CSV and the interval provenance map. The reviewer closed the Stage 1 audit after this check. [Repeat-preflight proof](../data/pilot/preflight-repeat.json)

## Runtime and storage

| Year | Sessions | Scan and journal seconds | Logical market snapshot MiB | Journal growth MiB |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 249 | 23.5 | 591.5 | 6.35 |
| 2025 | 249 | 27.4 | 650.0 | 4.02 |
| 2026 | 220 | 20.0 | 581.8 | 3.46 |

The cached preflight took 190 seconds and the shared replay loop took 70.9 seconds. Audit and report-writing time are outside that loop. The final journal occupies 14.00 MiB including schema overhead. Logical market size counts each dated lookback and the files referenced by a snapshot. Shared source files and APFS copies make incremental physical disk use different from that sum.

## Full-backfill estimate

On this machine the existing archive has 3,445 NSE daily packages, including warm-up dates. Their file publication timestamps span 17 minutes. Kite history for 372 ISINs spans 28 minutes, and the NSE reference archives span 37 minutes. These jobs overlapped. The first daily archive file and the reconstructed membership output were written about 43 minutes apart. Budget **45–90 minutes for an uninterrupted cold collection and reconstruction** with the same archive availability and Kite entitlement. This is a planning estimate from file timestamps. Exchange outages, unmapped securities and manual reconciliation can extend it. Cached inputs avoid that network work. The original session and raw-source directories occupy about 6.6 GiB and 1.6 GiB respectively.

At the pilot's throughput, validating and replaying 3,190 archived sessions once would take roughly 14 minutes for cached checks and 5¼ minutes for the replay loop. A complete walk-forward study may process additional windows and write additional reports. This extrapolation does not include resolving older historical data gaps or re-running after corrections.

## Reproduction

The commands below assume the dated local source warehouse is present. The pilot uses its own archive view and SQLite journal. The data command verifies source-backed membership, dates, prices, controls and discontinuities before issuing a preflight receipt. The replay verifies the receipt and resumes a consecutive prefix for the same rules, source and archive. Actual corporate-action and scan events are available in the run's journal.

```sh
uv run --frozen --all-extras pytest -q
uv run --frozen python -m thesistally.pilot_data --end 2026-11-20
uv run --frozen python -m thesistally.pilot
```

The run used revision `research-4-c8007c0a59b4`, repository commit `3ca083baca57607ca2b2a8fbe3b9ec8650179d46`, and archived execution-source commit `024ee15f05734efd536372f675cf29d989abcb4e`. Config SHA-256 is `c8007c0a59b474484aed340d02ed8cf4029bca54b1d4406b88b61da39585be28`. Snapshot SHA-256 is `462665a5388bd9938f6bd9fa9b557e9943953f5e9ccba01164d6ce0fdcaf31a9`. The JSON includes the supplementary evidence digest and detailed reconciliation checks. [Machine-readable pilot report](../data/pilot/runs/pilot-7c67139d16a8cd5283d1/report.json)
