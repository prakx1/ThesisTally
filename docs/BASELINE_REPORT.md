# ThesisTally Stage 2 baseline

Fixed r2 and r1 rules on 3,190 NSE sessions from 2014-01-01 through 2026-11-20. R2 is primary. Each test uses the preceding three calendar years as training history and starts its own uninvested account. Fixed detectors need no parameter fitting. The last test ends on the last verified session. The final out-of-sample test begins with new paper sessions after a future rule freeze.

## Verdicts

| Revision | Book | Strategy | All closed | Mature | In 20+ windows | Mature mean R¹ | 90% CI¹ | PF¹ | Positive windows / planned | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 249 | 233 | 152 | -0.016 | -0.194 to 0.170 | 0.94 | 1/10 | not enough data |
| r2 | Funded | Volatility breakout | 12 | 11 | 0 | — | — | — | 0/10 | not enough data |
| r2 | Funded | 52-week-high momentum | 0 | 0 | 0 | — | — | — | 0/10 | not enough data |
| r2 | Equal risk | Trend pullback | 2953 | 2862 | 2847 | -0.075 | -0.153 to 0.007 | 0.87 | 2/10 | not enough data |
| r2 | Equal risk | Volatility breakout | 350 | 328 | 314 | -0.133 | -0.265 to 0.010 | 0.77 | 1/10 | not enough data |
| r2 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | — | — | — | 0/10 | not enough data |
| r1 | Funded | Trend pullback | 125 | 117 | 77 | -0.271 | -0.535 to 0.008 | 0.64 | 0/10 | not enough data |
| r1 | Funded | Volatility breakout | 10 | 10 | 0 | — | — | — | 0/10 | not enough data |
| r1 | Funded | 52-week-high momentum | 0 | 0 | 0 | — | — | — | 0/10 | not enough data |
| r1 | Equal risk | Trend pullback | 610 | 586 | 556 | -0.166 | -0.340 to 0.010 | 0.78 | 0/10 | not enough data |
| r1 | Equal risk | Volatility breakout | 70 | 64 | 54 | 0.098 | -0.223 to 0.430 | 1.18 | 1/10 | not enough data |
| r1 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | — | — | — | 0/10 | not enough data |

¹ Qualification uses closed trades whose complete 20-session opportunity was observable within their test window. A window contributes trades to the qualification sample only with at least 20 mature closes. All planned windows remain in the denominator for the two-thirds positive-window requirement. The all-closed tables below also include trades that actually completed inside younger cohorts. The fixed qualification policy requires at least 100 counted trades, mean after-cost R above +0.25, a positive lower 90% confidence bound, cash profit factor above 1.4, and positive mean R in at least two thirds of test windows. The bootstrap resamples whole signal dates. Simultaneous trades move together in a draw.

The equal-risk verdict answers whether eligible setups showed an edge. The funded verdict adds ranking, cash, concentration and drawdown controls. “No edge” requires at least 100 counted mature trades and an entire 90% interval at or below zero. A small sample or an interval compatible with competing conclusions gives “not enough data”. A negative verdict is limited to dates on which the strategy could be measured.

### What the baseline supports

**Trend pullback — not enough data for an edge verdict.** At equal risk, r2 closed 2,953 trades. On the 2,847 mature trades from sufficiently sampled windows, mean net expectancy was −0.075R with a 90% interval of −0.153R to +0.007R. Two of ten planned windows contributed a positive vote. The upper bound is well below the required +0.25R. Across all closes the signal lost 0.027R before costs, and costs took another 0.075R. Of 1,780 losers, 323 reached +1R while held. The median losing price stop triggered on session six and exited on session seven.

**Volatility breakout — not enough data for an edge verdict.** R2 brought 350 equal-risk trades to a measured close. Its 314 counted mature trades averaged −0.133R, with a 90% interval of −0.265R to +0.010R. One of ten windows contributed a positive vote. Across all closes, mean gross R was −0.081 and costs averaged 0.065R. Only 31 of 222 losers reached +1R. The 12 observed funded closes averaged +0.712R after costs; that funded sample is too small to qualify the strategy.

**52-week-high momentum — not enough data.** There were 41 detections across training and testing. All passed liquidity and 35 failed the signal stop bounds. Every one of those 35 failed the 2.5 ATR ceiling, and one also exceeded the percentage limit. Five of the 37 test-period plans passed the signal stop. Two reached a funded allocation and neither could be bought at the next open. No revision or book closed a momentum trade. The conflict between the base-low stop and the ATR ceiling is documented for Stage 3 consideration.

**When the losses occurred.** Both r2 equal-risk strategies lost money on average in each of 2024, 2025 and 2026 through 20 November. These three signal years contributed 2,789 of 2,953 closed pullbacks and 335 of 350 closed breakouts. In 2023 they were positive on 54 and eight closes respectively. All eligible entries came from gate-on signals. Pullbacks exiting on a gate-off day averaged −0.558R across 901 closes versus +0.099R across 2,052 gate-on closes. Breakouts averaged −0.569R across 116 gate-off closes and +0.063R across 234 gate-on closes. The exit-day gate uses that session’s close. This association cannot establish what an earlier exit would have earned.

**What r1 adds.** R1 had 610 observed equal-risk pullback closes averaging −0.168R net and 70 breakout closes averaging −0.099R net. Its momentum count was also zero. R2 admitted substantially more trades. Both versions failed qualification under the predeclared criteria. The final out-of-sample evidence will come from future paper sessions after a freeze.

### Three funded windows with an unpriced position

All ten test windows ran in both revisions and both books. Thirty-seven of the 40 book-windows reached their scheduled end. Each equal-risk book reached all ten ends, with any unmeasurable call retained explicitly. A funded account requires a defensible daily valuation of every position. When that failed the entire session rolled back, and only the previously committed prefix is reported. The following annual window began independently with ₹10 lakh. The three shortened funded book-windows cannot contribute trades or votes to qualification.

In 2021 the r2 funded portfolio bought 566 INDHOTEL shares on 24 June. INDHOTEL entered derivatives trading on 25 June. Its time exit triggered on 22 July, but all 75 subsequent openings through 10 November had a dynamic band with no verified lower limit. On 11 November a 1-for-9 rights issue entitled the position to 62 independently tradable rights. A holder could sell, subscribe or let them lapse; the frozen rules specify no election. R2 therefore committed 212 of 248 sessions. The rights first traded on 24 November. Their later trading price could not supply a pre-open valuation or restore an earlier sale. [NSE derivatives circular](https://archives.nseindia.com/content/circulars/FAOP48731.pdf) · [Issuer’s offer, page 274](https://www.sebi.gov.in/sebi_data/attachdocs/nov-2021/1637557043074.pdf) · [NSE rights listing](https://archives.nseindia.com/content/circulars/CML50361.pdf)

In 2024 both funded accounts held ADANIPORTS when it closed at ₹1,248.95 on 4 June, down 21.15% from ₹1,583.95. The dated NSE finals confirm the move. No corporate action explained it, so the mandatory closing quarantine prevented the account mark. Both accounts committed 105 of 249 sessions, ending on 3 June. Their 2024 returns in the benchmark table cover that observed period. [3 June NSE equity final](https://nsearchives.nseindia.com/content/historical/EQUITIES/2024/JUN/cm03JUN2024bhav.csv.zip) · [4 June NSE equity final](https://nsearchives.nseindia.com/content/historical/EQUITIES/2024/JUN/cm04JUN2024bhav.csv.zip)

**What remains unknown.** Opening ranges were verified for 147,270 of 639,334 traded member observations. The missing ranges can prevent a purchase or keep a pending sale open. In the r2 funded book, missing lower limits delayed 56 pullback positions and four breakout positions. Some delays spanned months. Sparse early results and the shortened accounts limit any comparison of allocation effects. No historical parameter experiment was run.

## Sample and missing outcomes

| Revision | Book | Strategy | Plans | Bought | Closed | Holding | Pending | Missed | Invalid | Unknown outcome | Awaiting cash |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 3790 | 276 | 249 | 27 | 30 | 3476 | 8 | 0 | 0 |
| r2 | Funded | Volatility breakout | 277 | 13 | 12 | 1 | 2 | 262 | 0 | 0 | 0 |
| r2 | Funded | 52-week-high momentum | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| r2 | Equal risk | Trend pullback | 10780 | 3076 | 2953 | 98 | 66 | 7615 | 23 | 25 | 0 |
| r2 | Equal risk | Volatility breakout | 1625 | 371 | 350 | 19 | 11 | 1240 | 3 | 2 | 0 |
| r2 | Equal risk | 52-week-high momentum | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| r1 | Funded | Trend pullback | 4992 | 131 | 125 | 6 | 39 | 4818 | 4 | 0 | 0 |
| r1 | Funded | Volatility breakout | 379 | 11 | 10 | 1 | 2 | 366 | 0 | 0 | 0 |
| r1 | Funded | 52-week-high momentum | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| r1 | Equal risk | Trend pullback | 10780 | 653 | 610 | 38 | 66 | 10055 | 6 | 5 | 0 |
| r1 | Equal risk | Volatility breakout | 1625 | 84 | 70 | 14 | 11 | 1528 | 2 | 0 | 0 |
| r1 | Equal risk | 52-week-high momentum | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |

Both versions used the same market fingerprint. The archive records 465 traded ISINs and 597 constituent intervals. Surveillance or other unobserved controls retain their unknown status. Unsupported settlements or closing valuations remain recorded gaps. Sector splits preserve Unknown. [Dated data coverage](DATA_GAPS.md)

Before the public absolute-limit archive, some ordinary EQ opening ranges could be reconstructed from the previously published static percentage and closing price. Both boundaries had to land exactly on the applicable ₹0.05 tick. Dynamic-band names, ex-dates and unresolved cases remain unknown. Earlier fills therefore cover a selected subset of securities and dates. [NSE price-band definition](https://nsearchives.nseindia.in/web/sites/default/files/2023-02/Basic%20FAQs%20on%20Secondary%20Market.pdf) · [NSE tick circular](https://nsearchives.nseindia.in/content/circulars/CMTR62174.pdf)

### Exchange controls by calendar year

Each cell gives sessions with at least one unknown member and total unknown member-session observations. A known surveillance exclusion differs from an unavailable classification. A null opening limit prevents a fill.

| Year | Sessions | Surveillance · days / observations | Opening band · days / observations | Results calendar · days / observations |
| --- | --- | --- | --- | --- |
| 2014 | 244 | 0 / 0 | 244 / 48,799 | 0 / 0 |
| 2015 | 248 | 0 / 0 | 248 / 49,599 | 0 / 0 |
| 2016 | 247 | 0 / 0 | 247 / 49,586 | 0 / 0 |
| 2017 | 248 | 45 / 9,045 | 248 / 49,848 | 0 / 0 |
| 2018 | 246 | 156 / 31,297 | 246 / 49,446 | 0 / 0 |
| 2019 | 245 | 156 / 31,273 | 245 / 49,228 | 0 / 0 |
| 2020 | 252 | 30 / 5,198 | 252 / 47,514 | 0 / 0 |
| 2021 | 248 | 22 / 4,366 | 248 / 47,619 | 0 / 0 |
| 2022 | 248 | 75 / 14,383 | 248 / 48,159 | 0 / 0 |
| 2023 | 246 | 32 / 5,984 | 246 / 47,426 | 0 / 0 |
| 2024 | 249 | 9 / 9 | 26 / 4,840 | 0 / 0 |
| 2025 | 249 | 3 / 3 | 0 / 0 | 0 / 0 |
| 2026 | 220 | 1 / 1 | 0 / 0 | 0 / 0 |

### Why a test window had few or no funded trades

The first four counts describe primary signal-day dispositions from r2. Stop and surveillance gates precede portfolio allocation. The last columns show allocations and opening liquidity or band misses after allocation under each revision. The unknown-upper-band column is a subset of those misses, matched by dated security ISIN at the open.

| Window | Liquid plans | Stop out of bounds | Surveillance unknown | Surveillance flagged | r2 allocated | r2 band/liquidity misses | r2 with unknown upper band | r1 allocated | r1 band/liquidity misses | r1 with unknown upper band |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| walk-1 | 5127 | 2249 | 509 | 0 | 1202 | 1191 | 1191 | 1202 | 1192 | 1192 |
| walk-2 | 2893 | 1185 | 1064 | 7 | 215 | 210 | 210 | 215 | 210 | 210 |
| walk-3 | 2755 | 1364 | 877 | 0 | 166 | 163 | 163 | 166 | 163 | 163 |
| walk-4 | 3473 | 1290 | 196 | 21 | 408 | 381 | 381 | 624 | 586 | 586 |
| walk-5 | 5604 | 2048 | 261 | 90 | 701 | 665 | 665 | 1003 | 953 | 952 |
| walk-6 | 3516 | 1429 | 642 | 32 | 437 | 422 | 422 | 486 | 469 | 469 |
| walk-7 | 5303 | 3061 | 231 | 2 | 625 | 582 | 582 | 822 | 761 | 761 |
| walk-8 | 2753 | 1021 | 0 | 68 | 151 | 92 | 92 | 413 | 164 | 164 |
| walk-9 | 3416 | 1793 | 0 | 11 | 110 | 0 | 0 | 298 | 0 | 0 |
| walk-10 | 2872 | 1253 | 0 | 15 | 54 | 0 | 0 | 144 | 0 | 0 |

### How often an intended sale was delayed

An exit order needs a known lower band. Each deferred session is attributed using the held ISIN and opening evidence. The median and maximum count blocked sessions per affected trade within the window. A position can still be open when observation ends. Sparse historical limits can lengthen holding time and change measured losses.

| Revision | Book | Strategy | Trades delayed | Unknown floor days | Lower circuit days | Zero volume days | Missing quote days | Median delay | Maximum delay |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 56 | 634 | 0 | 0 | 0 | 3.0 | 217 |
| r2 | Funded | Volatility breakout | 4 | 67 | 0 | 0 | 0 | 5.0 | 56 |
| r2 | Funded | 52-week-high momentum | 0 | 0 | 0 | 0 | 0 | — | — |
| r2 | Equal risk | Trend pullback | 136 | 1563 | 0 | 0 | 0 | 3.0 | 217 |
| r2 | Equal risk | Volatility breakout | 12 | 109 | 0 | 0 | 0 | 4.5 | 56 |
| r2 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | 0 | 0 | — | — |
| r1 | Funded | Trend pullback | 10 | 57 | 0 | 0 | 0 | 4.5 | 20 |
| r1 | Funded | Volatility breakout | 0 | 0 | 0 | 0 | 0 | — | — |
| r1 | Funded | 52-week-high momentum | 0 | 0 | 0 | 0 | 0 | — | — |
| r1 | Equal risk | Trend pullback | 18 | 275 | 0 | 0 | 0 | 4.0 | 196 |
| r1 | Equal risk | Volatility breakout | 0 | 0 | 0 | 0 | 0 | — | — |
| r1 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | 0 | 0 | — | — |

## Funded accounts and same-period benchmark

Each window starts independently at ₹10 lakh. Returns and drawdowns use marked end-of-day equity including open holdings. The Nifty 200 price-index return uses the same start and last observed session as that row. A shortened row measures only the period through the date shown. Exposure is the average invested percentage. Equal-risk calls can overlap without a pooled capital ceiling. Their individual returns and marks per ₹10 lakh reference are available in the JSON.

| Window | Revision | Scheduled test period | Measured through | Sessions observed / scheduled | Funded return % | Maximum drawdown % | Mean exposure % | Nifty 200 over observed sessions % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| walk-1 | r2 | 2017-01-02 – 2017-12-29 | 2017-12-29 | 248 / 248 | 0.00 | 0.00 | 0.00 | 33.20 |
| walk-1 | r1 | 2017-01-02 – 2017-12-29 | 2017-12-29 | 248 / 248 | 0.00 | 0.00 | 0.00 | 33.20 |
| walk-2 | r2 | 2018-01-01 – 2018-12-31 | 2018-12-31 | 246 / 246 | 0.00 | 0.00 | 0.00 | -0.30 |
| walk-2 | r1 | 2018-01-01 – 2018-12-31 | 2018-12-31 | 246 / 246 | 0.00 | 0.00 | 0.00 | -0.30 |
| walk-3 | r2 | 2019-01-01 – 2019-12-31 | 2019-12-31 | 245 / 245 | 0.00 | 0.00 | 0.00 | 8.34 |
| walk-3 | r1 | 2019-01-01 – 2019-12-31 | 2019-12-31 | 245 / 245 | 0.00 | 0.00 | 0.00 | 8.34 |
| walk-4 | r2 | 2020-01-01 – 2020-12-31 | 2020-12-31 | 252 / 252 | 6.41 | 4.47 | 16.09 | 15.49 |
| walk-4 | r1 | 2020-01-01 – 2020-12-31 | 2020-12-31 | 252 / 252 | -0.45 | 0.95 | 0.68 | 15.49 |
| walk-5 | r2 | 2021-01-01 – 2021-12-31 | 2021-11-10 | 212 / 248 | 13.89 | 4.26 | 16.26 | 32.01 |
| walk-5 | r1 | 2021-01-01 – 2021-12-31 | 2021-12-31 | 248 / 248 | -1.30 | 2.40 | 1.97 | 26.92 |
| walk-6 | r2 | 2022-01-03 – 2022-12-30 | 2022-12-30 | 248 / 248 | 1.97 | 1.06 | 5.00 | 2.21 |
| walk-6 | r1 | 2022-01-03 – 2022-12-30 | 2022-12-30 | 248 / 248 | -0.56 | 0.73 | 0.33 | 2.21 |
| walk-7 | r2 | 2023-01-02 – 2023-12-29 | 2023-12-29 | 246 / 246 | 11.63 | 4.21 | 18.97 | 22.91 |
| walk-7 | r1 | 2023-01-02 – 2023-12-29 | 2023-12-29 | 246 / 246 | 1.92 | 2.11 | 6.93 | 22.91 |
| walk-8 | r2 | 2024-01-01 – 2024-12-31 | 2024-06-03 | 105 / 249 | 10.30 | 5.28 | 51.20 | 11.56 |
| walk-8 | r1 | 2024-01-01 – 2024-12-31 | 2024-06-03 | 105 / 249 | -1.67 | 4.66 | 30.63 | 11.56 |
| walk-9 | r2 | 2025-01-01 – 2025-12-31 | 2025-12-31 | 249 / 249 | -4.51 | 9.45 | 34.21 | 7.96 |
| walk-9 | r1 | 2025-01-01 – 2025-12-31 | 2025-12-31 | 249 / 249 | -4.43 | 7.30 | 27.24 | 7.96 |
| walk-10 | r2 | 2026-01-01 – 2026-11-20 | 2026-11-20 | 220 / 220 | -6.24 | 7.14 | 16.92 | 0.19 |
| walk-10 | r1 | 2026-01-01 – 2026-11-20 | 2026-11-20 | 220 / 220 | -1.64 | 4.88 | 12.31 | 0.19 |

## What each closed trade earned and cost

Gross R reverses modeled slippage and actual simulated fees on the executed quantities. Corporate cash is included. Results are before holder-specific income taxes. All four terms use initial filled share risk times quantity. Gross R less slippage R and fee R equals net R on every closed trade. Reversing slippage holds the executed trades fixed. Profit factor divides net rupee gains by absolute net rupee losses.

| Revision | Book | Strategy | Closed | Wins | Mean sessions held | Gross R | Slippage R | Fee R | Total cost R | Net R | PF |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 249 | 45.4% | 13.2 | 0.141 | 0.021 | 0.052 | 0.074 | 0.067 | 1.14 |
| r2 | Funded | Volatility breakout | 12 | 58.3% | 16.1 | 0.785 | 0.020 | 0.052 | 0.072 | 0.712 | 1.90 |
| r2 | Funded | 52-week-high momentum | 0 | — | — | — | — | — | — | — | — |
| r2 | Equal risk | Trend pullback | 2953 | 39.7% | 12.3 | -0.027 | 0.022 | 0.053 | 0.075 | -0.102 | 0.83 |
| r2 | Equal risk | Volatility breakout | 350 | 36.6% | 12.6 | -0.081 | 0.019 | 0.046 | 0.065 | -0.146 | 0.75 |
| r2 | Equal risk | 52-week-high momentum | 0 | — | — | — | — | — | — | — | — |
| r1 | Funded | Trend pullback | 125 | 33.6% | 12.3 | -0.137 | 0.029 | 0.071 | 0.100 | -0.237 | 0.71 |
| r1 | Funded | Volatility breakout | 10 | 40.0% | 12.3 | -0.070 | 0.027 | 0.065 | 0.092 | -0.162 | 0.42 |
| r1 | Funded | 52-week-high momentum | 0 | — | — | — | — | — | — | — | — |
| r1 | Equal risk | Trend pullback | 610 | 35.9% | 12.0 | -0.075 | 0.028 | 0.066 | 0.094 | -0.168 | 0.78 |
| r1 | Equal risk | Volatility breakout | 70 | 41.4% | 12.7 | -0.026 | 0.022 | 0.052 | 0.073 | -0.099 | 0.83 |
| r1 | Equal risk | 52-week-high momentum | 0 | — | — | — | — | — | — | — | — |

## How losing trades unfolded

Excursions are in units of initial filled risk. The three values in a distribution are the 10th percentile, median and 90th percentile. Positive excursion uses highs while shares were still held. Negative excursion uses lows, with separately reported bounds for a single-share target sale where the same daily bar does not establish whether the low preceded the fill. The +1R count tracks the first recorded reach while shares were held. The before/on/after split counts those who ultimately exited on a price stop. The tie column identifies cases where the same bar also touched both stop and target. The stop has priority. Time exits have their own exit group. Days to stop count exchange sessions from the purchase, with entry day counted as one.

| Revision | Book | Strategy | MFE R p10/50/90 | MAE R p10/50/90 | Losers reaching +1R | First +1R before / on / after price-stop session | Same-bar ties | Losses with price stop | Sessions to trigger p10/50/90 | Sessions to exit p10/50/90 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 0.12 / 0.83 / 3.42 | -1.87 / -0.93 / -0.24 | 22/136 (16.2%) | 11 / 0 / 0 | 0 | 79 | 2.00 / 6.00 / 9.20 | 4.00 / 7.00 / 12.40 |
| r2 | Funded | Volatility breakout | 0.01 / 1.93 / 3.12 | -1.38 / -0.58 / -0.09 | 0/5 (0.0%) | 0 / 0 / 0 | 0 | 2 | 5.20 / 6.00 / 6.80 | 6.30 / 7.50 / 8.70 |
| r2 | Funded | 52-week-high momentum | — | — | 0/0 (—) | 0 / 0 / 0 | 0 | 0 | — | — |
| r2 | Equal risk | Trend pullback | 0.09 / 0.78 / 3.07 | -1.76 / -1.04 / -0.27 | 323/1780 (18.1%) | 218 / 1 / 0 | 0 | 1135 | 2.00 / 6.00 / 12.00 | 3.00 / 7.00 / 13.00 |
| r2 | Equal risk | Volatility breakout | 0.06 / 0.56 / 2.75 | -1.63 / -0.97 / -0.22 | 31/222 (14.0%) | 18 / 0 / 0 | 0 | 120 | 2.00 / 6.50 / 14.10 | 3.00 / 7.50 / 15.10 |
| r2 | Equal risk | 52-week-high momentum | — | — | 0/0 (—) | 0 / 0 / 0 | 0 | 0 | — | — |
| r1 | Funded | Trend pullback | 0.16 / 0.86 / 3.16 | -1.99 / -1.14 / -0.36 | 21/83 (25.3%) | 14 / 0 / 0 | 0 | 54 | 1.00 / 5.00 / 13.40 | 3.00 / 6.00 / 14.40 |
| r1 | Funded | Volatility breakout | 0.22 / 0.42 / 2.23 | -1.56 / -1.02 / -0.46 | 1/6 (16.7%) | 1 / 0 / 0 | 0 | 2 | 6.30 / 7.50 / 8.70 | 7.30 / 8.50 / 9.70 |
| r1 | Funded | 52-week-high momentum | — | — | 0/0 (—) | 0 / 0 / 0 | 0 | 0 | — | — |
| r1 | Equal risk | Trend pullback | 0.14 / 0.90 / 3.53 | -2.14 / -1.15 / -0.26 | 100/391 (25.6%) | 73 / 2 / 0 | 0 | 275 | 1.00 / 5.00 / 13.00 | 2.00 / 6.00 / 14.00 |
| r1 | Equal risk | Volatility breakout | 0.08 / 0.75 / 3.07 | -1.64 / -1.04 / -0.22 | 7/41 (17.1%) | 4 / 0 / 0 | 0 | 25 | 1.40 / 6.00 / 9.60 | 2.40 / 7.00 / 10.60 |
| r1 | Equal risk | 52-week-high momentum | — | — | 0/0 (—) | 0 / 0 / 0 | 0 | 0 | — | — |

### Exit reasons

| Revision | Book | Strategy | Journal exit reason | Closed | Losers | Mean gross R | Mean net R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | Closing stop | 98 | 79 | -0.743 | -0.820 |
| r2 | Funded | Trend pullback | Maximum holding period | 16 | 2 | 2.105 | 2.038 |
| r2 | Funded | Trend pullback | Scheduled maximum holding close | 51 | 9 | 1.422 | 1.347 |
| r2 | Funded | Trend pullback | Time stop without 1R | 84 | 46 | 0.021 | -0.050 |
| r2 | Funded | Volatility breakout | Closing stop | 4 | 2 | -0.269 | -0.335 |
| r2 | Funded | Volatility breakout | Maximum holding period | 2 | 0 | 2.583 | 2.504 |
| r2 | Funded | Volatility breakout | Scheduled maximum holding close | 3 | 0 | 2.057 | 1.964 |
| r2 | Funded | Volatility breakout | Time stop without 1R | 3 | 3 | -0.281 | -0.336 |
| r2 | Funded | 52-week-high momentum | No closed trades | 0 | 0 | — | — |
| r2 | Equal risk | Trend pullback | Closing stop | 1383 | 1135 | -0.818 | -0.895 |
| r2 | Equal risk | Trend pullback | Maximum holding period | 27 | 3 | 2.252 | 2.177 |
| r2 | Equal risk | Trend pullback | Scheduled maximum holding close | 679 | 101 | 1.557 | 1.479 |
| r2 | Equal risk | Trend pullback | Time stop during compulsory suspension | 1 | 1 | -1.988 | -2.053 |
| r2 | Equal risk | Trend pullback | Time stop without 1R | 863 | 540 | -0.074 | -0.143 |
| r2 | Equal risk | Volatility breakout | Closing stop | 132 | 120 | -1.019 | -1.086 |
| r2 | Equal risk | Volatility breakout | Maximum holding period | 9 | 1 | 2.371 | 2.301 |
| r2 | Equal risk | Volatility breakout | Scheduled maximum holding close | 77 | 12 | 1.356 | 1.288 |
| r2 | Equal risk | Volatility breakout | Time stop without 1R | 132 | 89 | -0.150 | -0.210 |
| r2 | Equal risk | 52-week-high momentum | No closed trades | 0 | 0 | — | — |
| r1 | Funded | Trend pullback | Closing stop | 65 | 54 | -0.830 | -0.947 |
| r1 | Funded | Trend pullback | Maximum holding period | 6 | 0 | 1.879 | 1.801 |
| r1 | Funded | Trend pullback | Maximum holding period during compulsory suspension | 1 | 0 | 5.402 | 5.272 |
| r1 | Funded | Trend pullback | Scheduled maximum holding close | 25 | 7 | 1.068 | 0.982 |
| r1 | Funded | Trend pullback | Time stop without 1R | 28 | 22 | -0.231 | -0.308 |
| r1 | Funded | Volatility breakout | Closing stop | 2 | 2 | -1.281 | -1.401 |
| r1 | Funded | Volatility breakout | Scheduled maximum holding close | 2 | 0 | 2.028 | 1.868 |
| r1 | Funded | Volatility breakout | Time stop without 1R | 6 | 4 | -0.366 | -0.425 |
| r1 | Funded | 52-week-high momentum | No closed trades | 0 | 0 | — | — |
| r1 | Equal risk | Trend pullback | Closing stop | 318 | 275 | -0.969 | -1.070 |
| r1 | Equal risk | Trend pullback | Maximum holding period | 8 | 0 | 1.879 | 1.798 |
| r1 | Equal risk | Trend pullback | Maximum holding period during compulsory suspension | 2 | 1 | 1.462 | 1.342 |
| r1 | Equal risk | Trend pullback | Scheduled maximum holding close | 153 | 24 | 1.741 | 1.648 |
| r1 | Equal risk | Trend pullback | Time stop without 1R | 129 | 91 | -0.169 | -0.244 |
| r1 | Equal risk | Volatility breakout | Closing stop | 28 | 25 | -0.922 | -0.997 |
| r1 | Equal risk | Volatility breakout | Maximum holding period | 1 | 0 | 0.523 | 0.455 |
| r1 | Equal risk | Volatility breakout | Scheduled maximum holding close | 21 | 3 | 1.368 | 1.287 |
| r1 | Equal risk | Volatility breakout | Time stop without 1R | 20 | 13 | -0.264 | -0.326 |
| r1 | Equal risk | 52-week-high momentum | No closed trades | 0 | 0 | — | — |

## Where the trades occurred

Year and regime refer to the signal close. Both rule revisions require the gate to pass before opening a funded or equal-risk plan. A blank gate-off expectancy means there were no such entries. Open positions can cross into a gate-off period. The machine-readable report includes excluded shadows and window marks. Every cell below shows closed trades and their mean net R.

### r2 by signal year

| Signal Year | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| 2017 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2018 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2019 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2020 | 14 / -0.036 | 1 / -2.388 | 0 / — | 34 / -0.116 | 2 / -1.264 | 0 / — |
| 2021 | 20 / 0.733 | 0 / — | 0 / — | 60 / 0.479 | 3 / 0.204 | 0 / — |
| 2022 | 9 / 0.046 | 1 / 3.851 | 0 / — | 16 / -0.283 | 2 / 1.600 | 0 / — |
| 2023 | 28 / 0.619 | 1 / 1.157 | 0 / — | 54 / 0.488 | 8 / 1.225 | 0 / — |
| 2024 | 45 / 0.260 | 1 / -0.036 | 0 / — | 1909 / -0.043 | 208 / -0.082 | 0 / — |
| 2025 | 92 / -0.097 | 6 / 0.830 | 0 / — | 647 / -0.160 | 98 / -0.251 | 0 / — |
| 2026 | 41 / -0.437 | 2 / 0.492 | 0 / — | 233 / -0.694 | 29 / -0.709 | 0 / — |

### r2 by signal-day gate

| Signal-Day Gate | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| on | 249 / 0.067 | 12 / 0.712 | 0 / — | 2953 / -0.102 | 350 / -0.146 | 0 / — |

### r2 by exit-day gate

| Exit-Day Gate | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| off | 84 / -0.324 | 4 / 1.183 | 0 / — | 901 / -0.558 | 116 / -0.569 | 0 / — |
| on | 165 / 0.267 | 8 / 0.477 | 0 / — | 2052 / 0.099 | 234 / 0.063 | 0 / — |

### r1 by signal year

| Signal Year | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| 2017 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2018 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2019 | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| 2020 | 2 / -0.536 | 0 / — | 0 / — | 3 / 0.150 | 0 / — | 0 / — |
| 2021 | 6 / -0.282 | 1 / -1.232 | 0 / — | 12 / 0.009 | 1 / -1.229 | 0 / — |
| 2022 | 1 / -2.467 | 0 / — | 0 / — | 3 / 0.177 | 0 / — | 0 / — |
| 2023 | 10 / 0.372 | 0 / — | 0 / — | 12 / 0.480 | 1 / 0.455 | 0 / — |
| 2024 | 26 / -0.290 | 1 / 0.421 | 0 / — | 368 / -0.146 | 38 / 0.028 | 0 / — |
| 2025 | 58 / -0.192 | 6 / -0.009 | 0 / — | 145 / -0.027 | 20 / -0.006 | 0 / — |
| 2026 | 22 / -0.427 | 2 / -0.377 | 0 / — | 67 / -0.774 | 10 / -0.714 | 0 / — |

### r1 by signal-day gate

| Signal-Day Gate | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| off | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — | 0 / — |
| on | 125 / -0.237 | 10 / -0.162 | 0 / — | 610 / -0.168 | 70 / -0.099 | 0 / — |

### r1 by exit-day gate

| Exit-Day Gate | Funded pullback | Funded breakout | Funded momentum | Equal-risk pullback | Equal-risk breakout | Equal-risk momentum |
| --- | --- | --- | --- | --- | --- | --- |
| off | 43 / -0.271 | 5 / -0.912 | 0 / — | 210 / -0.688 | 24 / -0.738 | 0 / — |
| on | 82 / -0.219 | 5 / 0.588 | 0 / — | 400 / 0.105 | 46 / 0.234 | 0 / — |

### While positions were exposed

This split attributes each session’s change in marked wealth to that close’s gate. It includes open holdings at window-end. A position sold at the open can contribute P&L on a day ending in cash. Equal-risk values sum independently sized holdings and exclude positions with unresolved valuations; their rupee totals reflect the larger number of simultaneous trades.

| Revision | Book | Strategy | Gate on · invested closes | Gate on · net marked ₹ | Gate off · invested closes | Gate off · net marked ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 700 | 555,489 | 362 | -239,951 |
| r2 | Funded | Volatility breakout | 164 | 33,422 | 49 | -14,396 |
| r2 | Funded | 52-week-high momentum | 0 | 0 | 0 | 0 |
| r2 | Equal risk | Trend pullback | 843 | 2,971,695 | 439 | -4,046,949 |
| r2 | Equal risk | Volatility breakout | 526 | 342,923 | 176 | -555,586 |
| r2 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | 0 |
| r1 | Funded | Trend pullback | 358 | 44,754 | 141 | -114,434 |
| r1 | Funded | Volatility breakout | 87 | 4,711 | 9 | -16,356 |
| r1 | Funded | 52-week-high momentum | 0 | 0 | 0 | 0 |
| r1 | Equal risk | Trend pullback | 643 | 765,764 | 243 | -1,087,287 |
| r1 | Equal risk | Volatility breakout | 283 | 143,824 | 67 | -139,304 |
| r1 | Equal risk | 52-week-high momentum | 0 | 0 | 0 | 0 |

## 52-week-high momentum funnel

The initial 739 training sessions contributed detector and signal-eligibility counts. Funding starts in the first out-of-sample window. A plan gets one primary allocation or exclusion in each portfolio. An eligible training plan has no portfolio disposition.

| Stage | Training only | OOS signals | Full-history count |
| --- | --- | --- | --- |
| Detected | 4 | 37 | 41 |
| Passed liquidity | 4 | 37 | 41 |
| Invalid geometry | 0 | 0 | 0 |
| Valid signal stop | 1 | 5 | 6 |

The full detector census includes dates after an interrupted funded account using the complete independent scan tape. Actual funding and rejection counts below stop with the corresponding account.

| Primary signal disposition | Training only | r1 OOS | r2 OOS |
| --- | --- | --- | --- |
| portfolio | 0 | 2 | 2 |
| position_limit | 0 | 1 | 1 |
| regime | 1 | 0 | 0 |
| stop_out_of_bounds | 3 | 32 | 32 |
| surveillance_unknown | 0 | 1 | 1 |
| upcoming_results | 0 | 1 | 1 |

The pattern stop is the lowest low of the qualifying 15–40-session base. Its distance from an eventual breakout can exceed 2.5 ATR even when the percentage distance is admissible. Each excluded plan appears once in the combinations below. This documents the conflict for a possible Stage 3 experiment. The ceiling stayed fixed throughout the baseline.

| Stop-bound failures on the same signal | Training only | OOS signals |
| --- | --- | --- |
| above_max_atr | 3 | 31 |
| above_max_pct + above_max_atr | 0 | 1 |

| Revision | Book | Allocated | Bought | Closed | Awaiting open | Missed, invalidated or censored by reason |
| --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | 2 | 0 | 0 | 0 | missed: Opening liquidity or upper price band (2) |
| r2 | Equal risk | 3 | 0 | 0 | 0 | missed: Opening liquidity or upper price band (2), missed: Fill stop distance out of bounds (1) |
| r1 | Funded | 2 | 0 | 0 | 0 | missed: Opening liquidity or upper price band (2) |
| r1 | Equal risk | 3 | 0 | 0 | 0 | missed: Opening liquidity or upper price band (2), missed: Opening gap or slippage reduced reward/risk (1) |

## Qualification and sensitivity

| Revision | Book | Strategy | Counted windows | Replay complete | ≥100 mature | ≥20 in counted windows | >+0.25R | 90% lower >0 | PF >1.4 | Positive in ≥⅔ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 3 | fail | pass | pass | fail | fail | fail | fail |
| r2 | Funded | Volatility breakout | 0 | fail | fail | fail | fail | fail | fail | fail |
| r2 | Funded | 52-week-high momentum | 0 | fail | fail | fail | fail | fail | fail | fail |
| r2 | Equal risk | Trend pullback | 6 | pass | pass | pass | fail | fail | fail | fail |
| r2 | Equal risk | Volatility breakout | 3 | pass | pass | pass | fail | fail | fail | fail |
| r2 | Equal risk | 52-week-high momentum | 0 | pass | fail | fail | fail | fail | fail | fail |
| r1 | Funded | Trend pullback | 2 | fail | fail | pass | fail | fail | fail | fail |
| r1 | Funded | Volatility breakout | 0 | fail | fail | fail | fail | fail | fail | fail |
| r1 | Funded | 52-week-high momentum | 0 | fail | fail | fail | fail | fail | fail | fail |
| r1 | Equal risk | Trend pullback | 3 | pass | pass | pass | fail | fail | fail | fail |
| r1 | Equal risk | Volatility breakout | 2 | pass | fail | pass | fail | fail | fail | fail |
| r1 | Equal risk | 52-week-high momentum | 0 | pass | fail | fail | fail | fail | fail | fail |

Portfolio exclusions have their own hypothetical fills. Some failed the strategy’s eligibility rules. Their average cannot be substituted for the equal-risk result, which includes only admissible signals.

| Revision | Strategy | Allocated plans | Funded closed | Funded R | Excluded plans | Excluded closed | Excluded R | Excluded unmeasurable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Trend pullback | 3790 | 249 | 0.067 | 28938 | 3795 | -0.159 | 16 |
| r2 | Volatility breakout | 277 | 12 | 0.712 | 4670 | 443 | -0.127 | 1 |
| r2 | 52-week-high momentum | 2 | 0 | — | 35 | 1 | 0.853 | 0 |
| r1 | Trend pullback | 4992 | 125 | -0.237 | 28214 | 1537 | -0.155 | 3 |
| r1 | Volatility breakout | 379 | 10 | -0.162 | 4619 | 232 | -0.076 | 0 |
| r1 | 52-week-high momentum | 2 | 0 | — | 35 | 4 | -0.226 | 0 |

The corporate-action comparison holds every other execution fixed. Affected calls include dividend entitlements and compulsory settlements. Separate counts for compulsory exchanges, dividends and unresolved actions are in the JSON.

| Revision | Book | Strategy | With action · closed | With action · R | Without action · closed | Without action · R | Unmeasurable action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | 13 | -0.063 | 236 | 0.075 | 0 |
| r2 | Funded | Volatility breakout | 3 | 1.291 | 9 | 0.520 | 0 |
| r2 | Funded | 52-week-high momentum | 0 | — | 0 | — | 0 |
| r2 | Equal risk | Trend pullback | 203 | 0.356 | 2750 | -0.136 | 15 |
| r2 | Equal risk | Volatility breakout | 22 | 0.436 | 328 | -0.185 | 0 |
| r2 | Equal risk | 52-week-high momentum | 0 | — | 0 | — | 0 |
| r1 | Funded | Trend pullback | 12 | 0.631 | 113 | -0.329 | 0 |
| r1 | Funded | Volatility breakout | 1 | 3.635 | 9 | -0.584 | 0 |
| r1 | Funded | 52-week-high momentum | 0 | — | 0 | — | 0 |
| r1 | Equal risk | Trend pullback | 41 | 0.505 | 569 | -0.217 | 2 |
| r1 | Equal risk | Volatility breakout | 4 | 1.586 | 66 | -0.202 | 0 |
| r1 | Equal risk | 52-week-high momentum | 0 | — | 0 | — | 0 |

<details>
<summary>Sector results for every group with a closed trade</summary>

| Revision | Book | Strategy | Signal-day sector | Plans | Closed | Mean net R | Net ₹ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r2 | Funded | Trend pullback | AUTOMOBILE | 139 | 3 | 0.215 | 3,392 |
| r2 | Funded | Trend pullback | Automobile and Auto Components | 73 | 10 | -0.395 | -18,116 |
| r2 | Funded | Trend pullback | CEMENT & CEMENT PRODUCTS | 77 | 2 | 0.060 | -1,120 |
| r2 | Funded | Trend pullback | COMPUTERS - SOFTWARE | 28 | 1 | 4.992 | 25,248 |
| r2 | Funded | Trend pullback | CONSTRUCTION | 141 | 3 | -0.940 | -14,074 |
| r2 | Funded | Trend pullback | CONSUMER GOODS | 224 | 4 | 0.390 | 7,725 |
| r2 | Funded | Trend pullback | Capital Goods | 140 | 14 | 0.506 | 33,036 |
| r2 | Funded | Trend pullback | Chemicals | 30 | 4 | -0.696 | -10,931 |
| r2 | Funded | Trend pullback | Construction Materials | 27 | 2 | -0.078 | 1,296 |
| r2 | Funded | Trend pullback | Consumer Durables | 22 | 2 | -0.178 | -1,648 |
| r2 | Funded | Trend pullback | Consumer Services | 50 | 9 | 0.695 | 32,528 |
| r2 | Funded | Trend pullback | FERTILISERS & PESTICIDES | 35 | 2 | 0.871 | 8,587 |
| r2 | Funded | Trend pullback | FINANCIAL SERVICES | 266 | 6 | -0.236 | -6,232 |
| r2 | Funded | Trend pullback | Fast Moving Consumer Goods | 69 | 9 | 0.096 | 5,899 |
| r2 | Funded | Trend pullback | Financial Services | 211 | 44 | 0.246 | 37,874 |
| r2 | Funded | Trend pullback | HEALTHCARE SERVICES | 41 | 1 | 0.840 | 4,385 |
| r2 | Funded | Trend pullback | Healthcare | 105 | 18 | -0.439 | -32,953 |
| r2 | Funded | Trend pullback | IT | 168 | 3 | -0.409 | -5,901 |
| r2 | Funded | Trend pullback | Information Technology | 49 | 8 | -1.070 | -36,078 |
| r2 | Funded | Trend pullback | METALS | 150 | 1 | 1.064 | 5,984 |
| r2 | Funded | Trend pullback | Metals & Mining | 55 | 14 | -0.546 | -24,787 |
| r2 | Funded | Trend pullback | OIL & GAS | 57 | 2 | 1.210 | 12,094 |
| r2 | Funded | Trend pullback | Oil, Gas & Consumable Fuels | 55 | 10 | -0.565 | -24,474 |
| r2 | Funded | Trend pullback | PHARMA | 124 | 2 | -1.076 | -10,728 |
| r2 | Funded | Trend pullback | POWER | 92 | 7 | 1.486 | 49,561 |
| r2 | Funded | Trend pullback | Power | 58 | 11 | 0.679 | 38,271 |
| r2 | Funded | Trend pullback | Realty | 50 | 5 | 0.479 | 10,013 |
| r2 | Funded | Trend pullback | SERVICES | 62 | 1 | 0.311 | 1,616 |
| r2 | Funded | Trend pullback | Services | 24 | 2 | -1.988 | -19,310 |
| r2 | Funded | Trend pullback | Telecommunication | 29 | 6 | -0.421 | -10,817 |
| r2 | Funded | Trend pullback | Unknown | 141 | 43 | 0.207 | 25,369 |
| r2 | Funded | Volatility breakout | AUTOMOBILE | 13 | 1 | 1.157 | 6,229 |
| r2 | Funded | Volatility breakout | Capital Goods | 6 | 2 | 1.586 | 6,947 |
| r2 | Funded | Volatility breakout | FINANCIAL SERVICES | 22 | 1 | -2.388 | -12,668 |
| r2 | Funded | Volatility breakout | Fast Moving Consumer Goods | 5 | 1 | -1.322 | -6,621 |
| r2 | Funded | Volatility breakout | Financial Services | 19 | 3 | 1.774 | 19,142 |
| r2 | Funded | Volatility breakout | Metals & Mining | 6 | 1 | 0.892 | 4,444 |
| r2 | Funded | Volatility breakout | Power | 2 | 1 | -0.036 | -188 |
| r2 | Funded | Volatility breakout | Unknown | 10 | 2 | 0.876 | 4,081 |
| r2 | Equal risk | Trend pullback | AUTOMOBILE | 322 | 14 | 0.266 | 18,583 |
| r2 | Equal risk | Trend pullback | Automobile and Auto Components | 302 | 155 | -0.095 | -76,016 |
| r2 | Equal risk | Trend pullback | CEMENT & CEMENT PRODUCTS | 191 | 3 | -0.191 | -2,874 |
| r2 | Equal risk | Trend pullback | CHEMICALS | 76 | 1 | -1.218 | -6,021 |
| r2 | Equal risk | Trend pullback | COMPUTERS - SOFTWARE | 35 | 2 | 4.637 | 45,577 |
| r2 | Equal risk | Trend pullback | CONSTRUCTION | 282 | 23 | -0.876 | -100,668 |
| r2 | Equal risk | Trend pullback | CONSUMER GOODS | 631 | 19 | 0.405 | 38,441 |
| r2 | Equal risk | Trend pullback | Capital Goods | 371 | 158 | 0.311 | 242,457 |
| r2 | Equal risk | Trend pullback | Chemicals | 142 | 77 | -0.248 | -95,236 |
| r2 | Equal risk | Trend pullback | Construction | 19 | 11 | -0.258 | -14,349 |
| r2 | Equal risk | Trend pullback | Construction Materials | 129 | 69 | -0.322 | -107,350 |
| r2 | Equal risk | Trend pullback | Consumer Durables | 114 | 61 | 0.528 | 159,519 |
| r2 | Equal risk | Trend pullback | Consumer Services | 224 | 135 | 0.092 | 60,847 |
| r2 | Equal risk | Trend pullback | FERTILISERS & PESTICIDES | 90 | 3 | 1.209 | 17,999 |
| r2 | Equal risk | Trend pullback | FINANCIAL SERVICES | 792 | 20 | -0.233 | -23,344 |
| r2 | Equal risk | Trend pullback | Fast Moving Consumer Goods | 218 | 93 | -0.316 | -146,897 |
| r2 | Equal risk | Trend pullback | Financial Services | 1116 | 577 | -0.155 | -445,629 |
| r2 | Equal risk | Trend pullback | HEALTHCARE SERVICES | 77 | 7 | -0.224 | -7,734 |
| r2 | Equal risk | Trend pullback | Healthcare | 467 | 253 | -0.024 | -28,584 |
| r2 | Equal risk | Trend pullback | INDUSTRIAL MANUFACTURING | 160 | 6 | -0.558 | -16,728 |
| r2 | Equal risk | Trend pullback | IT | 362 | 5 | -0.689 | -16,996 |
| r2 | Equal risk | Trend pullback | Information Technology | 190 | 92 | 0.036 | 17,390 |
| r2 | Equal risk | Trend pullback | METALS | 258 | 2 | 0.195 | 1,946 |
| r2 | Equal risk | Trend pullback | Media, Entertainment & Publication | 33 | 10 | 0.486 | 24,214 |
| r2 | Equal risk | Trend pullback | Metals & Mining | 271 | 162 | -0.393 | -317,025 |
| r2 | Equal risk | Trend pullback | OIL & GAS | 152 | 6 | 1.317 | 39,471 |
| r2 | Equal risk | Trend pullback | Oil, Gas & Consumable Fuels | 296 | 181 | -0.274 | -247,208 |
| r2 | Equal risk | Trend pullback | PHARMA | 338 | 10 | 0.499 | 24,150 |
| r2 | Equal risk | Trend pullback | PLASTIC AND PLASTIC PRODUCTS | 11 | 10 | -0.681 | -32,962 |
| r2 | Equal risk | Trend pullback | POWER | 215 | 33 | 0.054 | 8,807 |
| r2 | Equal risk | Trend pullback | Power | 200 | 98 | -0.058 | -28,373 |
| r2 | Equal risk | Trend pullback | Realty | 168 | 71 | -0.221 | -78,466 |
| r2 | Equal risk | Trend pullback | SERVICES | 130 | 2 | -0.502 | -4,991 |
| r2 | Equal risk | Trend pullback | Services | 93 | 42 | -0.048 | -10,274 |
| r2 | Equal risk | Trend pullback | TELECOM | 44 | 2 | -1.726 | -17,066 |
| r2 | Equal risk | Trend pullback | Telecommunication | 120 | 59 | -0.265 | -77,821 |
| r2 | Equal risk | Trend pullback | Textiles | 15 | 8 | -0.115 | -3,187 |
| r2 | Equal risk | Trend pullback | Unknown | 766 | 473 | -0.124 | -290,061 |
| r2 | Equal risk | Volatility breakout | AUTOMOBILE | 58 | 2 | 1.043 | 10,398 |
| r2 | Equal risk | Volatility breakout | Automobile and Auto Components | 47 | 15 | -0.330 | -25,832 |
| r2 | Equal risk | Volatility breakout | CONSTRUCTION | 33 | 3 | -1.084 | -16,259 |
| r2 | Equal risk | Volatility breakout | CONSUMER GOODS | 98 | 1 | -0.611 | -3,050 |
| r2 | Equal risk | Volatility breakout | Capital Goods | 47 | 19 | 0.337 | 32,027 |
| r2 | Equal risk | Volatility breakout | Chemicals | 36 | 10 | 0.108 | 5,059 |
| r2 | Equal risk | Volatility breakout | Construction | 3 | 1 | -0.828 | -4,111 |
| r2 | Equal risk | Volatility breakout | Construction Materials | 16 | 7 | -1.009 | -34,945 |
| r2 | Equal risk | Volatility breakout | Consumer Durables | 25 | 9 | -0.192 | -7,886 |
| r2 | Equal risk | Volatility breakout | Consumer Services | 33 | 11 | -0.806 | -44,089 |
| r2 | Equal risk | Volatility breakout | FINANCIAL SERVICES | 135 | 4 | -0.513 | -10,179 |
| r2 | Equal risk | Volatility breakout | Fast Moving Consumer Goods | 32 | 15 | -0.672 | -50,153 |
| r2 | Equal risk | Volatility breakout | Financial Services | 193 | 80 | -0.127 | -51,327 |
| r2 | Equal risk | Volatility breakout | Healthcare | 79 | 27 | -0.341 | -44,970 |
| r2 | Equal risk | Volatility breakout | Information Technology | 49 | 19 | 0.434 | 40,707 |
| r2 | Equal risk | Volatility breakout | Media, Entertainment & Publication | 5 | 1 | 0.266 | 1,324 |
| r2 | Equal risk | Volatility breakout | Metals & Mining | 37 | 15 | 0.009 | 607 |
| r2 | Equal risk | Volatility breakout | OIL & GAS | 23 | 1 | -0.139 | -695 |
| r2 | Equal risk | Volatility breakout | Oil, Gas & Consumable Fuels | 35 | 14 | -0.218 | -15,124 |
| r2 | Equal risk | Volatility breakout | PLASTIC AND PLASTIC PRODUCTS | 2 | 2 | 0.295 | 2,824 |
| r2 | Equal risk | Volatility breakout | POWER | 29 | 2 | 2.291 | 22,906 |
| r2 | Equal risk | Volatility breakout | Power | 18 | 7 | -0.815 | -28,470 |
| r2 | Equal risk | Volatility breakout | Realty | 20 | 7 | 0.142 | 4,833 |
| r2 | Equal risk | Volatility breakout | Telecommunication | 23 | 7 | -0.490 | -16,991 |
| r2 | Equal risk | Volatility breakout | Unknown | 131 | 71 | -0.063 | -21,732 |
| r1 | Funded | Trend pullback | Automobile and Auto Components | 99 | 1 | 0.665 | 2,997 |
| r1 | Funded | Trend pullback | CEMENT & CEMENT PRODUCTS | 90 | 1 | 2.705 | 9,593 |
| r1 | Funded | Trend pullback | CONSTRUCTION | 166 | 1 | -0.606 | -2,483 |
| r1 | Funded | Trend pullback | CONSUMER GOODS | 299 | 2 | -1.501 | -14,425 |
| r1 | Funded | Trend pullback | Capital Goods | 201 | 5 | 1.292 | 30,155 |
| r1 | Funded | Trend pullback | Chemicals | 43 | 5 | -0.434 | -7,087 |
| r1 | Funded | Trend pullback | Construction Materials | 39 | 1 | 0.792 | 3,376 |
| r1 | Funded | Trend pullback | Consumer Durables | 38 | 2 | 0.564 | 4,423 |
| r1 | Funded | Trend pullback | Consumer Services | 79 | 4 | -1.746 | -16,076 |
| r1 | Funded | Trend pullback | FINANCIAL SERVICES | 346 | 2 | -0.814 | -6,352 |
| r1 | Funded | Trend pullback | Fast Moving Consumer Goods | 98 | 4 | -0.867 | -14,182 |
| r1 | Funded | Trend pullback | Financial Services | 360 | 25 | -0.128 | -20,830 |
| r1 | Funded | Trend pullback | HEALTHCARE SERVICES | 53 | 1 | -0.410 | -1,923 |
| r1 | Funded | Trend pullback | Healthcare | 169 | 15 | -0.523 | -26,236 |
| r1 | Funded | Trend pullback | IT | 220 | 1 | 0.175 | 824 |
| r1 | Funded | Trend pullback | Information Technology | 73 | 7 | 0.146 | -2,428 |
| r1 | Funded | Trend pullback | Metals & Mining | 82 | 7 | -0.297 | -8,896 |
| r1 | Funded | Trend pullback | Oil, Gas & Consumable Fuels | 95 | 6 | -0.577 | -15,108 |
| r1 | Funded | Trend pullback | POWER | 130 | 2 | -1.519 | -12,034 |
| r1 | Funded | Trend pullback | Power | 75 | 3 | 2.046 | 28,586 |
| r1 | Funded | Trend pullback | Realty | 65 | 4 | 0.081 | 2,108 |
| r1 | Funded | Trend pullback | Telecommunication | 53 | 4 | -0.501 | -6,282 |
| r1 | Funded | Trend pullback | Unknown | 280 | 22 | -0.413 | -31,794 |
| r1 | Funded | Volatility breakout | Automobile and Auto Components | 15 | 1 | 0.101 | 370 |
| r1 | Funded | Volatility breakout | CONSUMER GOODS | 18 | 1 | -1.232 | -2,742 |
| r1 | Funded | Volatility breakout | FINANCIAL SERVICES | 32 | 1 | -0.883 | -4,259 |
| r1 | Funded | Volatility breakout | Fast Moving Consumer Goods | 9 | 2 | -1.063 | -8,786 |
| r1 | Funded | Volatility breakout | Financial Services | 29 | 1 | -1.004 | -2,239 |
| r1 | Funded | Volatility breakout | Healthcare | 18 | 1 | 3.635 | 6,221 |
| r1 | Funded | Volatility breakout | Power | 3 | 1 | 0.128 | 525 |
| r1 | Funded | Volatility breakout | Telecommunication | 5 | 1 | 0.421 | 1,877 |
| r1 | Funded | Volatility breakout | Unknown | 24 | 1 | -0.659 | -3,191 |
| r1 | Equal risk | Trend pullback | AUTOMOBILE | 322 | 4 | 0.401 | 7,995 |
| r1 | Equal risk | Trend pullback | Automobile and Auto Components | 302 | 30 | -0.309 | -44,754 |
| r1 | Equal risk | Trend pullback | CEMENT & CEMENT PRODUCTS | 191 | 1 | 2.706 | 13,436 |
| r1 | Equal risk | Trend pullback | CONSTRUCTION | 282 | 4 | -1.452 | -29,023 |
| r1 | Equal risk | Trend pullback | CONSUMER GOODS | 631 | 4 | -0.473 | -9,466 |
| r1 | Equal risk | Trend pullback | Capital Goods | 371 | 19 | 0.287 | 26,548 |
| r1 | Equal risk | Trend pullback | Chemicals | 142 | 14 | -0.123 | -8,915 |
| r1 | Equal risk | Trend pullback | Construction | 19 | 1 | -1.128 | -5,632 |
| r1 | Equal risk | Trend pullback | Construction Materials | 129 | 11 | -0.472 | -25,484 |
| r1 | Equal risk | Trend pullback | Consumer Durables | 114 | 13 | 1.002 | 64,522 |
| r1 | Equal risk | Trend pullback | Consumer Services | 224 | 24 | -0.366 | -44,069 |
| r1 | Equal risk | Trend pullback | FINANCIAL SERVICES | 792 | 4 | -0.381 | -7,564 |
| r1 | Equal risk | Trend pullback | Fast Moving Consumer Goods | 218 | 30 | -0.495 | -74,481 |
| r1 | Equal risk | Trend pullback | Financial Services | 1116 | 144 | -0.056 | -41,481 |
| r1 | Equal risk | Trend pullback | HEALTHCARE SERVICES | 77 | 1 | -0.410 | -2,045 |
| r1 | Equal risk | Trend pullback | Healthcare | 467 | 51 | -0.172 | -43,427 |
| r1 | Equal risk | Trend pullback | IT | 362 | 1 | 0.175 | 874 |
| r1 | Equal risk | Trend pullback | Information Technology | 190 | 25 | 0.167 | 20,768 |
| r1 | Equal risk | Trend pullback | Media, Entertainment & Publication | 33 | 2 | 1.012 | 10,077 |
| r1 | Equal risk | Trend pullback | Metals & Mining | 271 | 28 | -0.389 | -54,427 |
| r1 | Equal risk | Trend pullback | OIL & GAS | 152 | 2 | 2.161 | 21,592 |
| r1 | Equal risk | Trend pullback | Oil, Gas & Consumable Fuels | 296 | 31 | -0.627 | -96,857 |
| r1 | Equal risk | Trend pullback | PLASTIC AND PLASTIC PRODUCTS | 11 | 3 | 3.491 | 51,866 |
| r1 | Equal risk | Trend pullback | POWER | 215 | 3 | -0.593 | -8,896 |
| r1 | Equal risk | Trend pullback | Power | 200 | 21 | 0.152 | 16,120 |
| r1 | Equal risk | Trend pullback | Realty | 168 | 16 | -0.710 | -56,579 |
| r1 | Equal risk | Trend pullback | Services | 93 | 8 | -0.566 | -22,648 |
| r1 | Equal risk | Trend pullback | Telecommunication | 120 | 10 | 0.008 | 407 |
| r1 | Equal risk | Trend pullback | Textiles | 15 | 5 | -0.390 | -9,256 |
| r1 | Equal risk | Trend pullback | Unknown | 766 | 100 | -0.325 | -161,157 |
| r1 | Equal risk | Volatility breakout | Automobile and Auto Components | 47 | 2 | 0.385 | 2,813 |
| r1 | Equal risk | Volatility breakout | CONSUMER GOODS | 98 | 1 | -1.229 | -6,065 |
| r1 | Equal risk | Volatility breakout | Capital Goods | 47 | 3 | 0.650 | 9,770 |
| r1 | Equal risk | Volatility breakout | Construction | 3 | 1 | -0.828 | -4,111 |
| r1 | Equal risk | Volatility breakout | Consumer Durables | 25 | 1 | 1.301 | 6,451 |
| r1 | Equal risk | Volatility breakout | Consumer Services | 33 | 2 | -1.116 | -11,106 |
| r1 | Equal risk | Volatility breakout | FINANCIAL SERVICES | 135 | 1 | -0.883 | -4,411 |
| r1 | Equal risk | Volatility breakout | Fast Moving Consumer Goods | 32 | 5 | -0.893 | -22,183 |
| r1 | Equal risk | Volatility breakout | Financial Services | 193 | 16 | -0.061 | -4,505 |
| r1 | Equal risk | Volatility breakout | Healthcare | 79 | 7 | 0.107 | 3,689 |
| r1 | Equal risk | Volatility breakout | Information Technology | 49 | 5 | 0.766 | 19,052 |
| r1 | Equal risk | Volatility breakout | Metals & Mining | 37 | 3 | 0.769 | 11,523 |
| r1 | Equal risk | Volatility breakout | Oil, Gas & Consumable Fuels | 35 | 4 | -0.671 | -13,389 |
| r1 | Equal risk | Volatility breakout | PLASTIC AND PLASTIC PRODUCTS | 2 | 1 | 1.019 | 4,907 |
| r1 | Equal risk | Volatility breakout | Power | 18 | 1 | 0.129 | 645 |
| r1 | Equal risk | Volatility breakout | Realty | 20 | 3 | 0.476 | 7,114 |
| r1 | Equal risk | Volatility breakout | Telecommunication | 23 | 3 | -0.470 | -7,017 |
| r1 | Equal risk | Volatility breakout | Unknown | 131 | 11 | -0.521 | -28,704 |

The JSON also retains sectors with signals and zero closed trades.

</details>

## Reproducibility

The replay data SHA-256 is `8b5d16971f271232e9ca8a2803e46da109409ee714738b871b4dac515f175102`. The source snapshot commit is `c38db1b38e1ae987581bfa807b154f5219ad83cc`, and the checkout commit is `a4ad0fdd54dfefe719e618f04674aa9076c75ef8`. The JSON includes the exact rule and qualification hashes, daily-coverage counts, every criterion with its threshold and result, window/sector/regime splits, and corporate-action sensitivity.

[Complete baseline JSON](../data/backtests/stage2-baseline-e923bed5c9d9448d7340.json) · [Validation policy](../src/thesistally/validation/stage-2-v1.json) · [Research log](RESEARCH_LOG.md)

## Independent review

An independent Codex reviewer reconstructed 639,334 dated member-price observations, checked all ten replay ledgers, and recalculated 23,951 fill fees and 10,391 closed outcomes across the funded, equal-risk and excluded-shadow cohorts. The audit reproduced all twelve qualification verdicts, confirmed eight holdings conversions, and found a maximum funded-account discrepancy below ₹0.000001. Independent next-open checks reproduced 88,777 missed or invalidated calls. Three funded account-windows stop at their last supportable valuation. The early archive’s sparse price bands and the heavy concentration of measured closes in 2024–2026 limit historical inference.
