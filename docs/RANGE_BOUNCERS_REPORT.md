# Range Bouncers

One preregistered experiment tested a monthly range-stock screen followed by a daily oversold entry. The primary sold after a close at the old midpoint. Its only sensitivity waited for the boundary of the old top fifth. Each test year opened a fresh ₹10 lakh simulated account. The last year ends on 2026-11-20. [Registered definition](PREREGISTRATION.md#c--range-bouncers)

## Did the strategy qualify?

**Midpoint.** Neither book qualified. The funded book recorded 25 mature closed trades and the equal-risk book recorded 48. No coverage-eligible window supplied the required 20 trades.

**Top fifth.** Neither book qualified. The funded book recorded 25 mature closed trades and the equal-risk book recorded 48. No coverage-eligible window supplied the required 20 trades.

| Target | Book | All mature trades | All mean net R | Counted trades | Counted mean net R | 90% date-cluster CI | Cash profit factor | Positive counted / coverage-eligible windows | Qualified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Midpoint | Funded | 25 | +0.142 | 0 | — | —, — | — | 0/7 | No |
| Midpoint | Range, equal risk | 48 | +0.446 | 0 | — | —, — | — | 0/7 | No |
| Top fifth | Funded | 25 | +0.261 | 0 | — | —, — | — | 0/7 | No |
| Top fifth | Range, equal risk | 48 | +0.524 | 0 | — | —, — | — | 0/7 | No |

R is the risk between the actual entry fill and the original stop. Net R includes slippage and trading costs. Independent calls each risk up to ₹5,000 and can overlap. Funded trades also face cash, position, sector and drawdown limits. A window contributes to the counted mean with at least 20 mature closes, a completed replay and at most 10% of planned entries blocked by data. Every coverage-eligible window is in the breadth denominator. Qualification also requires 100 counted trades, mean net R above +0.25, a positive 90% lower confidence bound, cash profit factor above 1.4, and positive mean R in at least two thirds of eligible windows.

| Target | Book | Finished replays | Years excluded by the data gate | Years with 20+ mature closes that counted |
| --- | --- | --- | --- | --- |
| Midpoint | Funded | 10/10 | 2018, 2019, 2022 | None |
| Midpoint | Range, equal risk | 10/10 | 2018, 2019, 2022 | None |
| Top fifth | Funded | 10/10 | 2018, 2019, 2022 | None |
| Top fifth | Range, equal risk | 10/10 | 2018, 2019, 2022 | None |

Only windows meeting the trade floor can earn a positive-window vote. An eligible year with fewer than 20 mature trades earns zero votes even when its trades made money. A dash means the qualifying sample was too small to estimate the statistic.

## Do past range stocks bounce again?

**Midpoint.** Range stocks had the higher target-hit rate in 4 of 8 windows with observations in both groups.

**Top fifth.** Range stocks had the higher target-hit rate in 2 of 8 windows with observations in both groups.

| Target | Pooled sample | Windows | Range hits / fills | Other hits / fills | Gap (percentage points) |
| --- | --- | --- | --- | --- | --- |
| Midpoint | All observed | 10 | 7/48 (14.6%) | 330/1,832 (18.0%) | -3.4 |
| Midpoint | Both data gates pass | 3 | 3/13 (23.1%) | 120/666 (18.0%) | +5.1 |
| Top fifth | All observed | 10 | 2/48 (4.2%) | 103/1,832 (5.6%) | -1.5 |
| Top fifth | Both data gates pass | 3 | 1/13 (7.7%) | 40/666 (6.0%) | +1.7 |

| Year | Target | Range hits / observable fills | Other hits / observable fills | Gap (pp) | Both data gates pass |
| --- | --- | --- | --- | --- | --- |
| 2017 | Midpoint | 0/6 (0.0%) | 68/329 (20.7%) | -20.7 | No |
| 2017 | Top fifth | 0/6 (0.0%) | 19/329 (5.8%) | -5.8 | No |
| 2018 | Midpoint | 0/0 | 5/64 (7.8%) | — | No |
| 2018 | Top fifth | 0/0 | 0/64 (0.0%) | — | No |
| 2019 | Midpoint | 1/5 (20.0%) | 5/66 (7.6%) | +12.4 | No |
| 2019 | Top fifth | 0/5 (0.0%) | 1/66 (1.5%) | -1.5 | No |
| 2020 | Midpoint | 0/8 (0.0%) | 29/153 (19.0%) | -19.0 | No |
| 2020 | Top fifth | 0/8 (0.0%) | 10/153 (6.5%) | -6.5 | No |
| 2021 | Midpoint | 2/2 (100.0%) | 65/294 (22.1%) | +77.9 | No |
| 2021 | Top fifth | 0/2 (0.0%) | 20/294 (6.8%) | -6.8 | No |
| 2022 | Midpoint | 0/10 (0.0%) | 15/121 (12.4%) | -12.4 | No |
| 2022 | Top fifth | 0/10 (0.0%) | 1/121 (0.8%) | -0.8 | No |
| 2023 | Midpoint | 1/4 (25.0%) | 23/139 (16.5%) | +8.5 | No |
| 2023 | Top fifth | 1/4 (25.0%) | 12/139 (8.6%) | +16.4 | No |
| 2024 | Midpoint | 1/10 (10.0%) | 87/401 (21.7%) | -11.7 | Yes |
| 2024 | Top fifth | 1/10 (10.0%) | 34/401 (8.5%) | +1.5 | Yes |
| 2025 | Midpoint | 2/3 (66.7%) | 29/136 (21.3%) | +45.3 | Yes |
| 2025 | Top fifth | 0/3 (0.0%) | 5/136 (3.7%) | -3.7 | Yes |
| 2026 | Midpoint | 0/0 | 4/129 (3.1%) | — | Yes |
| 2026 | Top fifth | 0/0 | 1/129 (0.8%) | — | Yes |

A hit is the first qualifying held close before the stop or time exit. The entry, tradability and eligibility checks are identical for both groups. The comparison includes overlapping independent calls and weights each observed trade equally. Only filled calls with a full 20-session observation horizon and a resolved price path enter the denominator. Unknown classifications, short follow-up and early compulsory exits are kept separately. The same stock can signal on adjacent days, and each group can trade on different dates. Overlapping trades and small range-stock samples limit how confidently these observed differences can be attributed to past bounces.

## What happened to trades that timed out?

**Midpoint.** The 20-session time limit ended 29 of 48 mature equal-risk trades. They averaged +0.859R after costs.

**Top fifth.** The 20-session time limit ended 34 of 48 mature equal-risk trades. They averaged +1.015R after costs.

## Trade outcomes by year

| Year | Target | Funded trades | Gross R | Net R | Equal-risk trades | Gross R | Net R | Funded return % | Nifty 200 TRI % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2017 | Midpoint | 3 | -1.146 | -1.221 | 6 | -0.808 | -0.881 | -1.78 | +35.21 |
| 2017 | Top fifth | 3 | -1.146 | -1.221 | 6 | -0.808 | -0.881 | -1.78 | +35.21 |
| 2018 | Midpoint | 0 | — | — | 0 | — | — | +0.00 | +0.32 |
| 2018 | Top fifth | 0 | — | — | 0 | — | — | +0.00 | +0.32 |
| 2019 | Midpoint | 4 | +0.036 | -0.045 | 5 | +0.369 | +0.287 | -0.13 | +10.03 |
| 2019 | Top fifth | 4 | +0.036 | -0.045 | 5 | +0.483 | +0.401 | -0.13 | +10.03 |
| 2020 | Midpoint | 3 | -0.755 | -0.829 | 8 | +0.131 | +0.051 | -1.23 | +16.83 |
| 2020 | Top fifth | 3 | -0.755 | -0.829 | 8 | +0.131 | +0.051 | -1.23 | +16.83 |
| 2021 | Midpoint | 2 | +2.357 | +2.300 | 2 | +2.357 | +2.300 | +2.15 | +28.88 |
| 2021 | Top fifth | 2 | +2.281 | +2.224 | 2 | +2.281 | +2.224 | +2.11 | +28.88 |
| 2022 | Midpoint | 5 | -0.023 | -0.099 | 10 | +0.600 | +0.527 | -0.83 | +4.92 |
| 2022 | Top fifth | 5 | -0.023 | -0.099 | 10 | +0.600 | +0.527 | -0.83 | +4.92 |
| 2023 | Midpoint | 2 | +1.457 | +1.369 | 4 | +1.742 | +1.657 | +1.37 | +24.66 |
| 2023 | Top fifth | 2 | +2.794 | +2.704 | 4 | +2.410 | +2.325 | +2.71 | +24.66 |
| 2024 | Midpoint | 5 | +0.482 | +0.403 | 10 | +0.537 | +0.457 | +0.99 | +14.75 |
| 2024 | Top fifth | 5 | +0.574 | +0.494 | 10 | +0.583 | +0.503 | +1.22 | +14.75 |
| 2025 | Midpoint | 1 | +1.095 | +1.015 | 3 | +1.355 | +1.267 | +0.50 | +9.57 |
| 2025 | Top fifth | 1 | +1.095 | +1.015 | 3 | +1.410 | +1.322 | +0.50 | +9.57 |
| 2026 | Midpoint | 0 | — | — | 0 | — | — | +0.56 | +1.30 |
| 2026 | Top fifth | 0 | — | — | 0 | — | — | +0.56 | +1.30 |

Trade columns use all observed mature closes by signal year, including coverage-excluded windows. The funded return marks capital through the last verified session and includes open holdings. The gross total-return index uses the same observation dates and the prior session’s closing anchor. Exact dates, exposure, drawdown, censored positions, and exit diagnostics appear in the JSON.

## Sample coverage

| Year | Member-days | Daily signal unobservable | Raw setups | Range | Other | Monthly class unknown | Ineligible |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2017 | 49848 | 630 | 1518 | 10 | 1106 | 200 | 202 |
| 2018 | 49446 | 597 | 3354 | 53 | 2557 | 511 | 233 |
| 2019 | 49245 | 1004 | 2773 | 111 | 2037 | 265 | 360 |
| 2020 | 50520 | 1774 | 2510 | 66 | 1947 | 237 | 260 |
| 2021 | 49600 | 300 | 1598 | 8 | 1313 | 276 | 1 |
| 2022 | 49600 | 70 | 2886 | 106 | 2329 | 440 | 11 |
| 2023 | 49275 | 210 | 1923 | 114 | 1619 | 185 | 5 |
| 2024 | 49965 | 72 | 2131 | 31 | 1929 | 164 | 7 |
| 2025 | 49822 | 330 | 2830 | 62 | 2457 | 311 | 0 |
| 2026 | 44027 | 133 | 3024 | 143 | 2488 | 393 | 0 |

An unobservable member-day has insufficient daily inputs to establish whether the entry signal fired. A monthly class unknown is an observed entry signal whose range-stock classification cannot be reconstructed. The control group requires a resolved classification and entry signal. Some historical NSE dividend records give only an ex-date, so the timestamp of a later correction can remain uncertain. [Historical classification and dividend gaps](DATA_GAPS.md#range-bouncers-historical-classification)

| Year | Target | Range exclusions (pre / no fill / pending / filled censored) | Other exclusions (pre / no fill / pending / filled censored) |
| --- | --- | --- | --- |
| 2017 | Midpoint | 3 / 1 / 0 / 0 | 645 / 89 / 1 / 42 |
| 2018 | Midpoint | 52 / 1 / 0 / 0 | 2296 / 177 / 1 / 19 |
| 2019 | Midpoint | 92 / 13 / 0 / 1 | 1745 / 219 / 3 / 4 |
| 2020 | Midpoint | 56 / 2 / 0 / 0 | 1754 / 39 / 0 / 1 |
| 2021 | Midpoint | 6 / 0 / 0 / 0 | 962 / 57 / 0 / 0 |
| 2022 | Midpoint | 82 / 8 / 1 / 5 | 2077 / 81 / 3 / 47 |
| 2023 | Midpoint | 110 / 0 / 0 / 0 | 1408 / 56 / 1 / 15 |
| 2024 | Midpoint | 21 / 0 / 0 / 0 | 1430 / 46 / 12 / 40 |
| 2025 | Midpoint | 59 / 0 / 0 / 0 | 2306 / 14 / 1 / 0 |
| 2026 | Midpoint | 138 / 1 / 0 / 4 | 2305 / 23 / 3 / 28 |
| 2017 | Top fifth | 3 / 1 / 0 / 0 | 645 / 89 / 1 / 42 |
| 2018 | Top fifth | 52 / 1 / 0 / 0 | 2296 / 177 / 1 / 19 |
| 2019 | Top fifth | 92 / 13 / 0 / 1 | 1745 / 219 / 3 / 4 |
| 2020 | Top fifth | 56 / 2 / 0 / 0 | 1754 / 39 / 0 / 1 |
| 2021 | Top fifth | 6 / 0 / 0 / 0 | 962 / 57 / 0 / 0 |
| 2022 | Top fifth | 82 / 8 / 1 / 5 | 2077 / 81 / 3 / 47 |
| 2023 | Top fifth | 110 / 0 / 0 / 0 | 1408 / 56 / 1 / 15 |
| 2024 | Top fifth | 21 / 0 / 0 / 0 | 1430 / 46 / 12 / 40 |
| 2025 | Top fifth | 59 / 0 / 0 / 0 | 2306 / 14 / 1 / 0 |
| 2026 | Top fifth | 138 / 1 / 0 / 4 | 2305 / 23 / 3 / 28 |

“Pre” counts rule or allocation exclusions. “No fill” counts data blocks and other opening failures. “Pending” counts orders with no observed opening decision. “Filled censored” counts fills without sufficient follow-up or a classifiable target outcome. The JSON gives each blocked reason and marks each book’s coverage decision.

[Machine-readable counts, criteria, ledger fingerprints and exclusions](range-bouncers-results.json)
