# Stage 3 walk-forward study

## Decisions

**A126 · does not qualify.** Beat the Nifty 200 total-return index in 1/10 scheduled windows. 10/10 windows reached the scheduled end. The observed net CAGR was 4.24% versus 14.28% for the index over the same observed sessions.

**A252 · does not qualify.** Beat the Nifty 200 total-return index in 1/10 scheduled windows. 10/10 windows reached the scheduled end. The observed net CAGR was 4.13% versus 14.28% for the index over the same observed sessions.

**B10 funded · not enough data.** 175 measured closes, 159 mature closes in counted windows. Their net mean was -0.03R, with a 90% date-clustered interval of [-0.13, 0.08]R.

**B10 equal risk · not enough data.** 602 measured closes, 579 mature closes in counted windows. Their net mean was -0.02R, with a 90% date-clustered interval of [-0.09, 0.06]R.

**B5 funded · not enough data.** 127 measured closes, 105 mature closes in counted windows. Their net mean was 0.19R, with a 90% date-clustered interval of [0.06, 0.31]R.

**B5 equal risk · not enough data.** 286 measured closes, 254 mature closes in counted windows. Their net mean was 0.06R, with a 90% date-clustered interval of [-0.04, 0.15]R.

The primary definitions are A126 and B10. A252 and B5 are the two registered sensitivities. Every window started with ₹10 lakh. January 2017 through 20 November 2026 supplied the ten scheduled tests, with three preceding years of history for each start. The latest year ends at the final available session. [Registered mechanics and thresholds](PREREGISTRATION.md)

For the primary dip rule, the equal-risk book supplied enough mature closes to count 3/10 windows (2024, 2025, 2026). Its profit factor was 0.96, against a requirement above 1.4. The number of trades and the number of independent market periods answer different questions.

The monthly primary held an average of 17.37% in shares. Both monthly portfolios spent 2017–2019 entirely in cash as their planned purchases lacked verified opening bands. Both beat the index in 2024.

Across all 602 measured equal-risk B10 closes, gross expectancy was +0.038R and the average cost was 0.046R, leaving -0.008R net. The tighter B5 funded setting had a positive confidence interval, but its counted mean of +0.192R fell below the +0.25R hurdle. It had a positive counted mean in 2/10 windows against seven required.

## Corrected execution and data

All 2,451 scheduled r2 test sessions were marked. The corrected accounts registered 67 slipped opening sales with unknown lower bands. INDHOTEL sold 552 shares at ₹145.927 on 23 July 2021. The 4 June 2024 ADANIPORTS close was verified at ₹1,248.95. The revised account carried no ADANIPORTS position that day, so a held-position regression separately exercised that mark. [Execution audit and exchange sources](STAGE3_PLUMBING.md)

The market receipt covers 3,190 sessions and 12 verified price-move releases. It records 39 rights issues. A holder declines subscription, keeps the existing share count and receives zero entitlement cash. An unknown opening lower band permits a slipped sale with an explicit journal flag. Positive volume and observed locked circuits still control execution. [Market receipt](../data/stage3/preflight.json) · [Corrected r2 accounts and case events](../data/stage3/r2-convention-replay/e722f88c7cc3e270/result.json)

| r2 funded window | Scheduled end | Sessions scanned / expected | Reached end |
| --- | --- | --- | --- |
| walk-1 | 2017-12-29 | 248 / 248 | Yes |
| walk-2 | 2018-12-31 | 246 / 246 | Yes |
| walk-3 | 2019-12-31 | 245 / 245 | Yes |
| walk-4 | 2020-12-31 | 252 / 252 | Yes |
| walk-5 | 2021-12-31 | 248 / 248 | Yes |
| walk-6 | 2022-12-30 | 248 / 248 | Yes |
| walk-7 | 2023-12-29 | 246 / 246 | Yes |
| walk-8 | 2024-12-31 | 249 / 249 | Yes |
| walk-9 | 2025-12-31 | 249 / 249 | Yes |
| walk-10 | 2026-11-20 | 220 / 220 | Yes |

## A · Steady momentum

The portfolio is marked daily. Annual windows compound by linking returns across fresh accounts. The comparator includes reinvested dividends. The cost estimate restores fees and adverse slippage on the same executed quantities. Turnover is one-way annualized. Any incomplete window contributes observations only through its last verifiable mark. Full-schedule interpretation requires every window to finish.

### A126

| Period | Sessions | Net % | TRI % | Net CAGR % | TRI CAGR % | Net DD % | TRI DD % | Net vol % | Net Sharpe | Exposure % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| All observed | 2451 | 50.82 | 274.33 | 4.24 | 14.28 | 10.92 | 38.03 | 7.93 | 0.57 | 17.37 |
| walk-1, 2017 | 248 | 0.00 | 35.21 | 0.00 | 35.35 | 0.00 | 4.64 | 0.00 | — | 0.00 |
| walk-2, 2018 | 246 | 0.00 | 0.32 | 0.00 | 0.31 | 0.00 | 15.16 | 0.00 | — | 0.00 |
| walk-3, 2019 | 245 | 0.00 | 10.03 | 0.00 | 10.04 | 0.00 | 11.07 | 0.00 | — | 0.00 |
| walk-4, 2020 | 252 | -3.58 | 16.83 | -3.57 | 16.79 | 5.28 | 38.03 | 3.25 | -1.11 | 3.59 |
| walk-5, 2021 | 248 | 12.79 | 28.88 | 12.80 | 28.90 | 2.55 | 10.17 | 4.60 | 2.68 | 13.17 |
| walk-6, 2022 | 248 | 0.32 | 4.92 | 0.32 | 4.93 | 1.18 | 16.55 | 1.62 | 0.21 | 1.77 |
| walk-7, 2023 | 246 | 2.44 | 24.66 | 2.45 | 24.75 | 0.95 | 8.43 | 2.16 | 1.15 | 2.95 |
| walk-8, 2024 | 249 | 37.74 | 14.75 | 37.41 | 14.63 | 9.95 | 11.11 | 19.99 | 1.72 | 72.72 |
| walk-9, 2025 | 249 | 0.03 | 9.57 | 0.03 | 9.57 | 10.92 | 11.29 | 12.11 | 0.06 | 59.41 |
| walk-10, 2026 | 220 | -2.07 | 1.30 | -2.32 | 1.46 | 6.99 | 14.76 | 6.04 | -0.37 | 19.76 |
| Prior gate off | 1144 | 21.51 | 48.66 | 4.38 | 9.13 | 9.44 | 37.75 | 7.68 | 0.60 | 13.39 |
| Prior gate on | 1307 | 24.13 | 151.81 | 4.26 | 19.49 | 8.83 | 11.77 | 8.15 | 0.55 | 20.86 |

The account stayed entirely in cash in 2017, 2018, 2019. The register below counts missed entries where the historical opening band could not be verified.

The benchmark had 16.24% annual volatility and a 0.92 Sharpe. Annual one-way turnover was 66.88%, fees were ₹18,573, and adverse slippage was ₹7,179. Costs reduced CAGR by 0.25 percentage points. The strategy lost money in 17/119 observed months and trailed the index in 65. Its peak-to-trough loss ran from 2025-06-30 to 2025-08-01. 55 of 161 sales realized a loss on shares before distributions.

Qualification requires seven wins, higher CAGR and drawdown ≤1.2× the index. The respective checks were 1/10, fail and pass.

Fills and exceptions. buy 161, sell 161, unknown_band_sales 20. missed/unknown_upper_band 661.

Corporate events. bonus_credited 2, bonus_entitlement 2, bonus_tradable 2, converted 2, credited 2, dividend 35, quarantine_release 1, suspended 2, tradable 2.

### A252

| Period | Sessions | Net % | TRI % | Net CAGR % | TRI CAGR % | Net DD % | TRI DD % | Net vol % | Net Sharpe | Exposure % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| All observed | 2451 | 49.28 | 274.33 | 4.13 | 14.28 | 14.38 | 38.03 | 8.57 | 0.52 | 19.55 |
| walk-1, 2017 | 248 | 0.00 | 35.21 | 0.00 | 35.35 | 0.00 | 4.64 | 0.00 | — | 0.00 |
| walk-2, 2018 | 246 | 0.00 | 0.32 | 0.00 | 0.31 | 0.00 | 15.16 | 0.00 | — | 0.00 |
| walk-3, 2019 | 245 | 0.00 | 10.03 | 0.00 | 10.04 | 0.00 | 11.07 | 0.00 | — | 0.00 |
| walk-4, 2020 | 252 | -3.15 | 16.83 | -3.15 | 16.79 | 5.28 | 38.03 | 3.25 | -0.97 | 3.65 |
| walk-5, 2021 | 248 | 3.98 | 28.88 | 3.99 | 28.90 | 3.32 | 10.17 | 3.48 | 1.16 | 12.40 |
| walk-6, 2022 | 248 | -1.82 | 4.92 | -1.83 | 4.93 | 3.60 | 16.55 | 2.92 | -0.63 | 3.84 |
| walk-7, 2023 | 246 | 7.32 | 24.66 | 7.34 | 24.75 | 2.34 | 8.43 | 2.97 | 2.45 | 10.14 |
| walk-8, 2024 | 249 | 34.64 | 14.75 | 34.34 | 14.63 | 14.10 | 11.11 | 21.76 | 1.49 | 75.44 |
| walk-9, 2025 | 249 | 8.39 | 9.57 | 8.40 | 9.57 | 6.74 | 11.29 | 12.27 | 0.73 | 63.52 |
| walk-10, 2026 | 220 | -3.60 | 1.30 | -4.04 | 1.46 | 11.50 | 14.76 | 8.06 | -0.48 | 26.70 |
| Prior gate off | 1144 | 9.73 | 48.66 | 2.07 | 9.13 | 15.13 | 37.75 | 8.37 | 0.29 | 15.46 |
| Prior gate on | 1307 | 36.05 | 151.81 | 6.11 | 19.49 | 9.80 | 11.77 | 8.74 | 0.72 | 23.13 |

The account stayed entirely in cash in 2017, 2018, 2019. The register below counts missed entries where the historical opening band could not be verified.

The benchmark had 16.24% annual volatility and a 0.92 Sharpe. Annual one-way turnover was 53.89%, fees were ₹15,555, and adverse slippage was ₹5,723. Costs reduced CAGR by 0.20 percentage points. The strategy lost money in 21/119 observed months and trailed the index in 66. Its peak-to-trough loss ran from 2024-09-23 to 2025-04-07. 43 of 173 sales realized a loss on shares before distributions.

Qualification requires seven wins, higher CAGR and drawdown ≤1.2× the index. The respective checks were 1/10, fail and pass.

Fills and exceptions. buy 147, sell 173, unknown_band_sales 24. missed/unknown_upper_band 647.

Corporate events. bonus_credited 2, bonus_entitlement 2, bonus_fraction_paid 1, bonus_tradable 2, converted 2, credited 2, dividend 42, quarantine_release 3, suspended 2, tradable 2.

Each annual row above is both a test window and a calendar-year breakdown. Gate subsets link the days following each corresponding signal-date gate and annualize on 252 observed sessions. Their returns cover different sequences of dates.

## B · Dip in steady risers

Each trade risks the distance from its slipped purchase to its initial three-ATR stop. Gross R restores fees and adverse slippage while keeping the actual orders. Trade summaries include every measured close. Qualification considers only completed holding windows in complete test windows with at least 20 mature closes. The equal-risk book assigns ₹5,000 to each eligible risk independently. Its marked profit or loss can be expressed relative to the initial ₹10 lakh reference.

| Run | Book | Plans in book | Bought | Closed | Mature | Win % | Gross R | Cost R | Net R | PF | Mean sessions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B10 | Funded | 2077 | 184 | 175 | 172 | 52.57 | 0.04 | 0.05 | -0.00 | 1.00 | 12.36 |
| B10 | Equal risk | 2853 | 625 | 602 | 599 | 50.00 | 0.04 | 0.05 | -0.01 | 0.98 | 12.94 |
| B5 | Funded | 1063 | 131 | 127 | 126 | 63.78 | 0.22 | 0.05 | 0.17 | 1.67 | 12.83 |
| B5 | Equal risk | 1304 | 295 | 286 | 285 | 52.80 | 0.09 | 0.05 | 0.04 | 1.14 | 13.18 |

### B10 · Funded

Average exposure was 4.97%. The largest within-window peak-to-trough loss was 5.28%. The journals contain 0 deferred-sale events and 9 slipped opening sales with unknown lower bands. Equal-risk exposure and peak-to-trough loss use the fixed capital reference.

| Window / signal year | Closed | Mature | Net R | Marked %¹ | Nifty TRI % | Sessions scanned / scheduled |
| --- | --- | --- | --- | --- | --- | --- |
| walk-1, 2017 | 0 | 0 | — | 0.00 | 35.21 | 248/248 |
| walk-2, 2018 | 0 | 0 | — | 0.00 | 0.32 | 246/246 |
| walk-3, 2019 | 0 | 0 | — | 0.00 | 10.03 | 245/245 |
| walk-4, 2020 | 4 | 3 | 0.67 | 1.34 | 16.83 | 252/252 |
| walk-5, 2021 | 5 | 5 | 0.04 | 0.14 | 28.88 | 248/248 |
| walk-6, 2022 | 0 | 0 | — | 0.00 | 4.92 | 248/248 |
| walk-7, 2023 | 5 | 5 | -0.00 | 0.05 | 24.66 | 246/246 |
| walk-8, 2024 | 69 | 68 | 0.13 | 4.12 | 14.75 | 249/249 |
| walk-9, 2025 | 70 | 70 | -0.14 | -4.38 | 9.57 | 249/249 |
| walk-10, 2026 | 22 | 21 | -0.10 | -1.32 | 1.30 | 220/220 |

¹ Funded shows window return after costs. Equal risk shows cumulative position P&L divided by the fixed ₹10 lakh reference. Benchmark returns start at the preceding session’s closing index.

| Gate | Signal-cohort closes | Signal-cohort net R | Exit-cohort closes | Exit-cohort net R | Marked sessions after gate | Marked P&L ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 | — | 54 | -0.33 | 1144 | 22786 |
| on | 175 | -0.00 | 121 | 0.15 | 1307 | -23293 |

| Exit reason | Closed | Losses | Gross R | Net R |
| --- | --- | --- | --- | --- |
| Close above pre-signal 10-session high | 53 | 0 | 0.84 | 0.79 |
| Closing stop | 31 | 31 | -1.18 | -1.22 |
| Maximum holding period | 7 | 3 | 0.25 | 0.21 |
| Scheduled maximum holding close | 84 | 49 | -0.03 | -0.07 |

| Excursion or timing | Count | p10 | p25 | Median | p75 | p90 |
| --- | --- | --- | --- | --- | --- | --- |
| Favorable excursion, R | 175 | 0.09 | 0.21 | 0.51 | 0.82 | 1.02 |
| Adverse excursion, R | 175 | -1.21 | -0.90 | -0.51 | -0.24 | -0.12 |
| Sessions to losing stop trigger | 31 | 4.00 | 7.50 | 10.00 | 11.50 | 14.00 |
| Sessions to losing stop sale | 31 | 5.00 | 8.50 | 11.00 | 12.50 | 15.00 |

1 of 83 losing trades first reached +1R while the position was open. Across all measured closes the average cost was 0.03R in fees and 0.01R in adverse slippage.

Entry and endpoint dispositions. active 9, closed 175, confirmed 9, invalidated 2, missed 1,882. Fill stop distance out of bounds 183, Open above signal close + 0.5 ATR(14) 6, Opening liquidity or upper price band 1,693.

| Qualification criterion | Measured value | Requirement | Result |
| --- | --- | --- | --- |
| bootstrap_lower_r | -0.130 | > 0 | Fail |
| closed_oos | 159 | >= 100 | Pass |
| completed_replays | 10 | == 10 | Pass |
| counted_window_sample | walk-10=21, walk-8=68, walk-9=70 | each >= 20 | Pass |
| mean_r | -0.026 | > 0.250 | Fail |
| positive_window_fraction | 0.100 | >= 0.667 | Fail |
| profit_factor | 0.922 | > 1.400 | Fail |

### B10 · Equal risk

Average exposure was 19.18%. The largest within-window peak-to-trough loss was 31.08%. The journals contain 0 deferred-sale events and 15 slipped opening sales with unknown lower bands. Equal-risk exposure and peak-to-trough loss use the fixed capital reference.

| Window / signal year | Closed | Mature | Net R | Marked %¹ | Nifty TRI % | Sessions scanned / scheduled |
| --- | --- | --- | --- | --- | --- | --- |
| walk-1, 2017 | 0 | 0 | — | 0.00 | 35.21 | 248/248 |
| walk-2, 2018 | 0 | 0 | — | 0.00 | 0.32 | 246/246 |
| walk-3, 2019 | 0 | 0 | — | 0.00 | 10.03 | 245/245 |
| walk-4, 2020 | 6 | 5 | 0.72 | 2.13 | 16.83 | 252/252 |
| walk-5, 2021 | 8 | 8 | 0.07 | 0.30 | 28.88 | 248/248 |
| walk-6, 2022 | 0 | 0 | — | 0.00 | 4.92 | 248/248 |
| walk-7, 2023 | 7 | 7 | -0.23 | -0.80 | 24.66 | 246/246 |
| walk-8, 2024 | 251 | 250 | 0.14 | 17.35 | 14.75 | 249/249 |
| walk-9, 2025 | 291 | 291 | -0.12 | -16.63 | 9.57 | 249/249 |
| walk-10, 2026 | 39 | 38 | -0.22 | -4.25 | 1.30 | 220/220 |

¹ Funded shows window return after costs. Equal risk shows cumulative position P&L divided by the fixed ₹10 lakh reference. Benchmark returns start at the preceding session’s closing index.

| Gate | Signal-cohort closes | Signal-cohort net R | Exit-cohort closes | Exit-cohort net R | Marked sessions after gate | Marked P&L ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 | — | 204 | -0.38 | 1144 | 74610 |
| on | 602 | -0.01 | 398 | 0.19 | 1307 | -93613 |

| Exit reason | Closed | Losses | Gross R | Net R |
| --- | --- | --- | --- | --- |
| Close above pre-signal 10-session high | 145 | 0 | 0.96 | 0.91 |
| Closing stop | 101 | 101 | -1.13 | -1.18 |
| Maximum holding period | 11 | 5 | 0.16 | 0.12 |
| Maximum holding period during compulsory suspension | 1 | 1 | -0.44 | -0.49 |
| Scheduled maximum holding close | 344 | 194 | -0.01 | -0.05 |

| Excursion or timing | Count | p10 | p25 | Median | p75 | p90 |
| --- | --- | --- | --- | --- | --- | --- |
| Favorable excursion, R | 602 | 0.08 | 0.22 | 0.55 | 0.87 | 1.16 |
| Adverse excursion, R | 602 | -1.22 | -0.98 | -0.55 | -0.28 | -0.12 |
| Sessions to losing stop trigger | 101 | 5.00 | 8.00 | 10.00 | 13.00 | 15.00 |
| Sessions to losing stop sale | 101 | 6.00 | 9.00 | 11.00 | 14.00 | 16.00 |

6 of 301 losing trades first reached +1R while the position was open. Across all measured closes the average cost was 0.03R in fees and 0.01R in adverse slippage.

Entry and endpoint dispositions. active 23, closed 602, confirmed 10, invalidated 5, missed 2,213. Fill stop distance out of bounds 261, Open above signal close + 0.5 ATR(14) 19, Opening liquidity or upper price band 1,933.

| Qualification criterion | Measured value | Requirement | Result |
| --- | --- | --- | --- |
| bootstrap_lower_r | -0.088 | > 0 | Fail |
| closed_oos | 579 | >= 100 | Pass |
| completed_replays | 10 | == 10 | Pass |
| counted_window_sample | walk-10=38, walk-8=250, walk-9=291 | each >= 20 | Pass |
| mean_r | -0.016 | > 0.250 | Fail |
| positive_window_fraction | 0.100 | >= 0.667 | Fail |
| profit_factor | 0.956 | > 1.400 | Fail |

### B5 · Funded

Average exposure was 3.82%. The largest within-window peak-to-trough loss was 2.94%. The journals contain 0 deferred-sale events and 6 slipped opening sales with unknown lower bands. Equal-risk exposure and peak-to-trough loss use the fixed capital reference.

| Window / signal year | Closed | Mature | Net R | Marked %¹ | Nifty TRI % | Sessions scanned / scheduled |
| --- | --- | --- | --- | --- | --- | --- |
| walk-1, 2017 | 0 | 0 | — | 0.00 | 35.21 | 248/248 |
| walk-2, 2018 | 0 | 0 | — | 0.00 | 0.32 | 246/246 |
| walk-3, 2019 | 0 | 0 | — | 0.00 | 10.03 | 245/245 |
| walk-4, 2020 | 3 | 2 | 0.68 | 0.99 | 16.83 | 252/252 |
| walk-5, 2021 | 3 | 3 | 0.06 | 0.09 | 28.88 | 248/248 |
| walk-6, 2022 | 0 | 0 | — | 0.00 | 4.92 | 248/248 |
| walk-7, 2023 | 4 | 4 | 0.04 | 0.12 | 24.66 | 246/246 |
| walk-8, 2024 | 55 | 55 | 0.33 | 8.54 | 14.75 | 249/249 |
| walk-9, 2025 | 50 | 50 | 0.04 | 0.89 | 9.57 | 249/249 |
| walk-10, 2026 | 12 | 12 | -0.04 | -0.42 | 1.30 | 220/220 |

¹ Funded shows window return after costs. Equal risk shows cumulative position P&L divided by the fixed ₹10 lakh reference. Benchmark returns start at the preceding session’s closing index.

| Gate | Signal-cohort closes | Signal-cohort net R | Exit-cohort closes | Exit-cohort net R | Marked sessions after gate | Marked P&L ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 | — | 36 | -0.17 | 1144 | 48419 |
| on | 127 | 0.17 | 91 | 0.31 | 1307 | 53623 |

| Exit reason | Closed | Losses | Gross R | Net R |
| --- | --- | --- | --- | --- |
| Close above pre-signal 10-session high | 38 | 0 | 0.98 | 0.94 |
| Closing stop | 15 | 15 | -1.16 | -1.20 |
| Maximum holding period | 5 | 1 | 0.43 | 0.39 |
| Scheduled maximum holding close | 69 | 30 | 0.08 | 0.04 |

| Excursion or timing | Count | p10 | p25 | Median | p75 | p90 |
| --- | --- | --- | --- | --- | --- | --- |
| Favorable excursion, R | 127 | 0.10 | 0.34 | 0.71 | 0.93 | 1.21 |
| Adverse excursion, R | 127 | -1.12 | -0.78 | -0.45 | -0.26 | -0.12 |
| Sessions to losing stop trigger | 15 | 5.80 | 7.50 | 9.00 | 11.50 | 14.00 |
| Sessions to losing stop sale | 15 | 6.80 | 8.50 | 10.00 | 12.50 | 15.00 |

0 of 46 losing trades first reached +1R while the position was open. Across all measured closes the average cost was 0.03R in fees and 0.01R in adverse slippage.

Entry and endpoint dispositions. active 4, closed 127, confirmed 6, invalidated 2, missed 924. Fill stop distance out of bounds 104, Open above signal close + 0.5 ATR(14) 3, Opening liquidity or upper price band 817.

| Qualification criterion | Measured value | Requirement | Result |
| --- | --- | --- | --- |
| bootstrap_lower_r | 0.065 | > 0 | Pass |
| closed_oos | 105 | >= 100 | Pass |
| completed_replays | 10 | == 10 | Pass |
| counted_window_sample | walk-8=55, walk-9=50 | each >= 20 | Pass |
| mean_r | 0.192 | > 0.250 | Fail |
| positive_window_fraction | 0.200 | >= 0.667 | Fail |
| profit_factor | 1.799 | > 1.400 | Pass |

### B5 · Equal risk

Average exposure was 9.19%. The largest within-window peak-to-trough loss was 16.19%. The journals contain 0 deferred-sale events and 9 slipped opening sales with unknown lower bands. Equal-risk exposure and peak-to-trough loss use the fixed capital reference.

| Window / signal year | Closed | Mature | Net R | Marked %¹ | Nifty TRI % | Sessions scanned / scheduled |
| --- | --- | --- | --- | --- | --- | --- |
| walk-1, 2017 | 0 | 0 | — | 0.00 | 35.21 | 248/248 |
| walk-2, 2018 | 0 | 0 | — | 0.00 | 0.32 | 246/246 |
| walk-3, 2019 | 0 | 0 | — | 0.00 | 10.03 | 245/245 |
| walk-4, 2020 | 4 | 3 | 0.85 | 1.67 | 16.83 | 252/252 |
| walk-5, 2021 | 5 | 5 | 0.05 | 0.12 | 28.88 | 248/248 |
| walk-6, 2022 | 0 | 0 | — | 0.00 | 4.92 | 248/248 |
| walk-7, 2023 | 4 | 4 | 0.04 | 0.09 | 24.66 | 246/246 |
| walk-8, 2024 | 119 | 119 | 0.28 | 15.95 | 14.75 | 249/249 |
| walk-9, 2025 | 135 | 135 | -0.14 | -8.91 | 9.57 | 249/249 |
| walk-10, 2026 | 19 | 19 | -0.29 | -2.55 | 1.30 | 220/220 |

¹ Funded shows window return after costs. Equal risk shows cumulative position P&L divided by the fixed ₹10 lakh reference. Benchmark returns start at the preceding session’s closing index.

| Gate | Signal-cohort closes | Signal-cohort net R | Exit-cohort closes | Exit-cohort net R | Marked sessions after gate | Marked P&L ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 | — | 104 | -0.35 | 1144 | 35650 |
| on | 286 | 0.04 | 182 | 0.27 | 1307 | 28047 |

| Exit reason | Closed | Losses | Gross R | Net R |
| --- | --- | --- | --- | --- |
| Close above pre-signal 10-session high | 67 | 0 | 1.03 | 0.99 |
| Closing stop | 44 | 44 | -1.14 | -1.19 |
| Maximum holding period | 7 | 2 | 0.33 | 0.28 |
| Scheduled maximum holding close | 168 | 89 | 0.03 | -0.02 |

| Excursion or timing | Count | p10 | p25 | Median | p75 | p90 |
| --- | --- | --- | --- | --- | --- | --- |
| Favorable excursion, R | 286 | 0.09 | 0.26 | 0.64 | 0.95 | 1.23 |
| Adverse excursion, R | 286 | -1.20 | -0.97 | -0.51 | -0.27 | -0.12 |
| Sessions to losing stop trigger | 44 | 7.00 | 8.00 | 10.50 | 13.00 | 14.00 |
| Sessions to losing stop sale | 44 | 8.00 | 9.00 | 11.50 | 14.00 | 15.00 |

2 of 135 losing trades first reached +1R while the position was open. Across all measured closes the average cost was 0.03R in fees and 0.01R in adverse slippage.

Entry and endpoint dispositions. active 9, closed 286, confirmed 6, invalidated 3, missed 1,000. Fill stop distance out of bounds 126, Open above signal close + 0.5 ATR(14) 10, Opening liquidity or upper price band 864.

| Qualification criterion | Measured value | Requirement | Result |
| --- | --- | --- | --- |
| bootstrap_lower_r | -0.040 | > 0 | Fail |
| closed_oos | 254 | >= 100 | Pass |
| completed_replays | 10 | == 10 | Pass |
| counted_window_sample | walk-8=119, walk-9=135 | each >= 20 | Pass |
| mean_r | 0.056 | > 0.250 | Fail |
| positive_window_fraction | 0.100 | >= 0.667 | Fail |
| profit_factor | 1.192 | > 1.400 | Fail |

## Reproduction and data limits

To open this study, run `uv run radar`, select **Reports** with **3**, and open **Stage 3**. The experiment register links the four decisions below. The earlier strategies are retired from new calls. All three historical detectors and their Stage 2 reports are preserved.

Entry permissions depend on dated exchange data. Historical opening ranges were recoverable for 147,270 of 639,334 member observations in the parent archive. Missing purchase bands particularly constrain the early years. Zero exposure in a rising market reduces portfolio returns and provides little direct evidence about the stocks ranked then. A rejected or inconclusive study tests the registered rule under these data and execution constraints. [Historical data gaps](DATA_GAPS.md)

Every eligible B purchase had a measurable path through closure or the end of its window. Six excluded shadow records across the two variants encountered unresolved corporate actions. They represent two CANBK setups rejected by the results blackout and one VEDL setup rejected by the regime gate. Neither book included those signals in its decision cohort. [Research and review log](RESEARCH_LOG.md)

Source files contain full-precision results, the scored periods and the execution receipts. [Monthly study JSON](../data/stage3/steady-momentum/51fd8295cc4c5919fe99/results.json) · [Swing study JSON](../data/backtests/stage3-dip-741213b1920a60c14e26.json) · [r2 coverage](../data/stage3/r2-convention-replay/e722f88c7cc3e270/result.json) · [Official TRI sources](../data/stage3/benchmark.json)

Registered definitions at `ad4dec1`. Market SHA-256 `e722f88c7cc3e270f38902fb2f531023a6d9f0955faf5d29fc3a1e2c0c08158b`. Study IDs `51fd8295cc4c5919fe9996b079f29a11670a94888786aa72944317ccdf0e846e` and `741213b1920a60c14e26f0f161544118f7270ed36946b9469b7c5571689cb1f4`. [Experiment register](EXPERIMENTS.md) · [Research and review log](RESEARCH_LOG.md)

To reproduce the numbers using the certified local market, use the source commits named in the monthly and swing receipts. Restore the recorded holder evidence, then run the corresponding study.

```sh
uv run --frozen python -m thesistally.steady_actions
uv run --frozen python -m thesistally.steady_replay
uv run --frozen python -m thesistally.stage3_dip
```
