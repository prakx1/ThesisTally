# Stage 3 experiment register

The accepted Stage 2 strategies are retired by owner decision. Their original statistical verdicts, configurations and results remain archived. The redefined experiment budget has four entries, comprising two primary hypotheses and one prescribed sensitivity for each. No experiment uses the retired detectors. Changes required to restore execution or a dated corporate action are recorded separately in the research log.

## Superseded initial execution

All results in the following table used the original data convention, which blocked purchases when the upper price band was unknown. The owner superseded all four of these results for decisions.

| ID | Hypothesis or sensitivity | Fixed change | Registration | Result |
| --- | --- | --- | --- | --- |
| A126 | Smooth positive trends can outperform Nifty 200 after costs | 126-close score, monthly portfolio | ad4dec1 | 1/10 index wins. Net CAGR 4.24% vs 14.28%. Net drawdown 10.92%. Does not qualify. |
| A252 | Sensitivity to the slope horizon | A126 with 252 regression closes | ad4dec1 | 1/10 index wins. Net CAGR 4.13% vs 14.28%. Net drawdown 14.38%. Does not qualify. |
| B10 | A short oversold dip in a steady riser may have positive expectancy | Top 40, RSI(2) below 10 | ad4dec1 | Funded -0.026R (159 counted). Equal risk -0.016R (579 counted). Not enough data to qualify. |
| B5 | Sensitivity to the oversold threshold | B10 with RSI(2) below 5 | ad4dec1 | Funded +0.192R (105 counted). Equal risk +0.056R (254 counted). Not enough data to qualify. |

[Pre-registered mechanics and qualification](PREREGISTRATION.md)

**Four of four registered experiments are complete.** The two primary definitions determine the verdicts. Both sensitivities are reported. Neither primary nor sensitivity qualified. The audit reruns repaired shareholder evidence under the same definitions. [Full results and loss diagnoses](STAGE3_REPORT.md) · [Research log](RESEARCH_LOG.md)

## Opening-band data-revision rerun

The owner authorized repeating A126, A252, B10 and B5 with flagged unknown-band buys and a per-window data-coverage gate. This is one data-revision rerun of four existing settings. Total registered strategy experiments remain four. [Fixed revision and coverage measurements](STAGE3_ENTRY_REVISION.md)

| Existing ID | Revised outcome |
| --- | --- |
| A126 | Inconclusive. Won 4 of 7 complete windows. Net CAGR 21.44% versus matched Nifty 200 TRI 14.64%. Average exposure across observed sessions rose to 70.61%. Five wins were required on the seven complete windows. |
| A252 | Inconclusive. Won 4 of 7 complete windows. Net CAGR 20.30% versus matched TRI 16.06%. The portfolio’s 25.01% drawdown exceeded 1.2 times the benchmark’s 17.40% drawdown. |
| B10 | Funded and equal-risk counted mature cohorts averaged +0.026R (172 trades) and +0.042R (1,175). The data gate admitted three and five windows, respectively. Both missed the required +0.25R. |
| B5 | Funded and equal-risk counted mature cohorts averaged +0.158R (146 trades) and +0.072R (515). The data gate admitted four and five windows. Both missed the required +0.25R. |

The two A settings have different eligible dates. Their matched benchmark CAGRs therefore differ. Three physical valuation gaps remain in each A run. All forty B book-windows finished. In both strategies, earlier results are superseded due to the unknown-band entry artifact. [Revised yearly results, data blocks and decisions](STAGE3_ENTRY_REPORT.md)

## Owner disposition and A closing-price verification

B10 and B5 are parked as **small gross edge, below hurdle after costs**. Both funded and equal-risk books had positive gross mean R over observed closes. The original qualifying-sample after-cost results remain below +0.25R in all four cases. The owner authorized no further B experiments. The A126/A252 closing-price rerun repeats the same two registrations. Total strategy experiment count remains four.

| Existing setting | Complete windows | TRI wins / required | Net CAGR / TRI | Max drawdown / TRI | Average exposure | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A126 | 10/10 | 5 / 7 | 19.00% / 14.28% | 25.64% / 38.03% | 68.02% | Candidate, under observation. The CAGR and drawdown criteria passed. The window-win criterion failed. |
| A252 | 10/10 | 5 / 7 | 14.43% / 14.28% | 34.68% / 38.03% | 70.35% | Sensitivity does not qualify. The window-win criterion failed. |

The independent-closing-price rerun supersedes the earlier incomplete momentum verdict. The report-only continuous tax comparison gives A126 an investor XIRR of 13.62% and A252 11.51%, versus 13.34% for the single-sale Nifty 200 TRI proxy. The prospective primary is frozen in the annotated Git tag `a126-forward-2026-11-21` (dc8e45c). No parameter experiments were added. [Annual costs, turnover and results](A126_CANDIDATE_REPORT.md) · [Tax rates and financial-year comparison](A_MOMENTUM_TAX.md)

## C · Range Bouncers

One owner-authorized experiment, registered before calculation on 22 November 2026. The hypothesis is that repeated bottom-to-top moves identify stocks more likely to bounce again on the same oversold signal. The primary exits at the frozen midpoint; its only sensitivity exits at the top-fifth boundary. The persistence comparison is a control within this experiment. The cumulative register now contains five experiments under the existing counting convention. [Locked definition](PREREGISTRATION.md#c--range-bouncers)

**Decision — insufficient evidence to qualify Range Bouncers.** All ten annual windows completed, ending on 20 November 2026. Both targets had 25 mature funded trades and 48 independent range-stock trades. Mean after-cost R was +0.142 funded and +0.446 equal-risk for the midpoint, and +0.261 and +0.524 for the top fifth. Each set of 48 comes from the same entry cohort. Seven windows met the strategy data gate, and none had the 20 mature trades required to count toward the 100-trade minimum.

With the midpoint target, the range stocks bounced on 7 of 48 observable fills, compared with 330 of 1,832 for the other stocks. The top-fifth result was 2 of 48 versus 103 of 1,832. The difference switched sign in the three windows where both groups passed the data gate, which contained just 13 range calls. Persistence is inconclusive. The GAIL and L&T bonus-evidence repair completed the same registered test under holder-data revision `babf7a7862a0fb38e23e`. Range Bouncers remains an unqualified research strategy. Cumulative experiment count is five. [Annual results and comparison groups](RANGE_BOUNCERS_REPORT.md)

## Strategy discovery engine · one experiment family

The discovery family tested seven precommitted filters of the r2 trend-pullback and Strategy B equal-risk books. The 2017–2020 development period generated the cards. The 2021–2026 annual windows were reserved for rolling comparative validation. The 935 full-history exploratory cells were released after the seven-card lock in `fbf15ec`. All seven count in one 5% Benjamini–Hochberg correction. Five earlier strategy experiments remain on the register. This entry adds one discovery family covering seven hypotheses.

**Decision.** Zero hypotheses passed. The strongest window count was three of six for pullbacks on breadth above 60%, against four required. The lowest adjusted p-value was 0.969. The conditional joint-null simulation averaged 0.0417 corrected statistical discoveries per family; 3.35% of simulated families had any. Evidence-level and data-coverage failures remain visible for every hypothesis. [Results and method](DISCOVERY.md) · [Family evidence](../data/discovery/families/discovery-v1/validation.json) · [Permanent register](../data/discovery/register)

## D · Post-earnings momentum

One preregistered experiment with a 40-session primary horizon and a single 20-session sensitivity. Both use the same original quarterly EPS surprise, three-session relative price reaction, U+2 opening order and three-ATR closing stop. Every annual result needs the 80% eligible stock-quarter filing gate. Evidence is kept separately for the funded and independent equal-risk books. Registration commit `af36fef`. [Definition](PREREGISTRATION.md#d--post-earnings-momentum) · [Primary card](../data/discovery/register/results-d40/01-proposed.json) · [Sensitivity card](../data/discovery/register/results-d20/01-proposed.json)

**Coverage determination while original statements are being read.** The frozen source checks cannot bring any of the ten years to the 80% threshold. The reviewer established this using completed original-packet receipts, while granting every remaining case all four fields. The upper bound for the 2026 filing season fell to 632 of 801 against a requirement of 641. This established the coverage shortfall before the final numerical counts were available. [Independent upper bounds by year](../data/fundamentals/review/2026-below-coverage-bound.json)

**D40 and D20 both have insufficient data.** The completed filing census verifies all four original figures for 610 of 10,221 eligible stock-quarters (5.97%) and original EPS for 925. None of the ten walk-forward test years reached 80%. Even the best-covered year, 2026 through 20 November, had only 137 of 801 quarters (17.1%). Four announcements had sufficient same-basis history for a surprise score. Two passed the price-reaction check, L&T Technology Services in July 2022 and Tata Communications in July 2026. In both cases the company was the only scored announcement in its 90-day comparison group. All annual windows were excluded before allocating trades. Funded and equal-risk gross and net R, confidence interval, drawdown, excess over Nifty 200 TRI, after-tax results and loss diagnosis are consequently unavailable. This family contains one primary definition and one preregistered sensitivity. [Coverage by year and stock](FUNDAMENTALS_COVERAGE.md) · [Annual decisions and qualification evidence](../data/fundamentals/replays/80439cf03f306663141a/report.json)

Both discovery cards remain proposed with the evaluation “insufficient data.” Each card points to the same signed study and records the funded and equal-risk decisions. [D40 register event](../data/discovery/register/results-d40/02-000001-study.json) · [D20 register event](../data/discovery/register/results-d20/02-000001-study.json)

### D2 · First official XBRL coverage gate

**Parked on the signed historical filing archive.** The rule required five annual windows with at least 80% of eligible stock-quarters covered. Two of ten passed. In 2025 the count was 711/800 (88.9%). In 2026, through 20 November, it was 772/801 (96.4%). Allowing any later complete XML in the same window still produces only those two years. Historical D2 performance remains unmeasured. This was a source-coverage check and adds no trading experiment. The owner declined the proposed AI/PDF extraction. Forward collection of original exchange filings and their publication times began on 23 November 2026. [Annual coverage](STRATEGY_D2_XBRL_COVERAGE.md) · [Recorded decision](../data/fundamentals/d2-xbrl/decision.json)

### D2 · Final coverage decision across both exchanges

**Parked.** Using the owner-confirmed first XBRL publication clock across NSE and BSE, zero of ten windows had verified coverage of at least 80%. Five were required. Only 2025 and 2026 could reach the threshold from figures retrieved, even if unresolved publication ordering were resolved favorably. This result supersedes the two-window count from the earlier NSE-only source edition. D40 and D20 have no historical performance result under D2. The coverage count adds no strategy experiment. The separate forward service captured its first new exchange XBRL on 23 November 2026. [Final numbers and independent audit](STRATEGY_D2_XBRL_COVERAGE.md) · [Decision receipt](../data/fundamentals/d2-bse/decision.json) · [Live filing receipt](../data/fundamentals/d2-forward/first-live-filing-20261123.json)
