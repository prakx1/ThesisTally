# R2 pilot mechanics

**Pipeline test only · not for decisions.** Both books completed all 718 NSE sessions from 1 January 2024 through 20 November 2026. The 12,143 investable-universe signals that passed liquidity exactly matched the archived r1 signals by date, ISIN, strategy, detector inputs and planned levels. On the same 545 signals r1 had allocated, the opening rule admitted 490 entries under r2 versus 116 under r1. These dates have already been examined. This run tests execution and accounting. It cannot qualify a strategy. [Machine-readable results](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/report.json) · [Signal and sizing audit](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/signal-and-edge-audit.json) · [Archived r1 pilot](PILOT_REPORT.md)

## Entry and the funded account

The signal close, pattern stop, planned 2R target and planned share quantity are retained. A raw opening price at or below the stop invalidates the call. With opening liquidity and an executable upper band, an open strictly above the signal close plus 0.5 × signal-day ATR(14) misses. The engine then applies the existing five basis points of buy slippage and measures the stop distance from that fill. A distance below 3%, above 10%, or above 2.5 × ATR(14) misses. The boundary values pass. An accepted order stores its actual quantity, fill and target. Its target equals fill + 2 × (fill − pattern stop). Portfolio quantities can fall to protect the preceding close’s reserved cash and risk. Slippage, fees, exits and same-bar stop priority use the shared execution engine. [Versioned r2 rules](../src/thesistally/revisions/r2.yaml) · [Shared execution](../src/thesistally/journal.py)

| Portfolio measure | Archived r1 | R2 |
| --- | ---: | ---: |
| Plans allocated at the close | 545 | 182 |
| Awaiting the next session at cutoff | 0 | 1 |
| Plans with an observed next session | 545 | 181 |
| Missed | 428 (78.53%) | 13 (7.18%) |
| Invalidated | 1 | 0 |
| Bought | 116 | 168 |
| Closed / still held | 116 / 0 | 161 / 7 |

All 428 r1 misses failed the fixed-target reward/risk check. R2 had 11 fill-distance misses and two chase misses. The two portfolios allocated different signals as positions, available capacity and equity evolved. Matching the **same 545 r1 allocations** against the r2 independent book gives 490 buys, 54 misses (9.91%) and one invalidation. Of those 490 buys, 488 closed and two had unmeasurable outcomes. This comparison isolates the change in entry admission.

For account reconciliation, the r2 portfolio started at ₹10,00,000 and finished marked at ₹8,99,106.38 with seven holdings. The 161 closed trades realised −₹1,08,065.93, averaging −0.199R after costs. Maximum daily marked drawdown was 11.93%. The closing balance includes the remaining holdings.

## From pattern detection to a funded trade

“Detected” counts pattern matches before the turnover check. “Valid stop” counts the liquid signals inside the signal-close stop bounds. Each liquid plan receives exactly one portfolio allocation or one primary shadow reason. A funded trade has an actual simulated purchase. A missed entry has no purchase.

| Strategy | Detected | Passed liquidity | Valid stop | Allocated | Shadow | Missed after allocation | Funded | Closed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend pullback | 10,634 | 10,633 | 5,939 | 178 | 10,455 | 11 | 166 | 159 |
| Volatility breakout | 1,499 | 1,498 | 811 | 4 | 1,494 | 2 | 2 | 2 |
| 52-week-high momentum | 12 | 12 | 2 | 0 | 12 | 0 | 0 | 0 |
| **Total** | **12,145** | **12,143** | **6,752** | **182** | **11,961** | **13** | **168** | **161** |

| Why the portfolio recorded a shadow | Pullback | Volatility breakout | Momentum |
| --- | ---: | ---: | ---: |
| Signal stop out of bounds | 4,694 | 687 | 10 |
| Surveillance unknown | 1,929 | 197 | 0 |
| Known surveillance exclusion | 60 | 4 | 0 |
| Regime gate failed | 1,750 | 232 | 0 |
| Upcoming results | 329 | 66 | 1 |
| Position, ISIN or sector limit | 1,516 | 249 | 1 |
| Drawdown pause | 176 | 59 | 0 |
| Available capital or risk | 1 | 0 | 0 |
| **Total** | **10,455** | **1,494** | **12** |

| Opening miss reason | Pullback | Volatility breakout | Momentum |
| --- | ---: | ---: | ---: |
| Allocated · fill stop out of bounds | 9 | 2 | 0 |
| Allocated · chase ceiling | 2 | 0 | 0 |
| Shadow · fill stop out of bounds | 3,114 | 553 | 9 |
| Shadow · chase ceiling | 263 | 68 | 2 |
| Shadow · unavailable opening liquidity or upper band | 3,030 | 358 | 0 |

### Why momentum had zero funded trades

All 12 detected patterns passed liquidity. Ten failed the 2.5 ATR signal-stop limit, even though their percentage distances were inside 3–10%. TATASTEEL on 13 May 2026 passed the stop check and fell inside the results blackout. BANKBARODA on 1 December 2025 passed the strategy gates and hit a portfolio position limit. Its independent order reached the next open at ₹297, below the ₹298.62 chase ceiling. At a modeled fill of ₹297.1485 and a stop of ₹280.70, the risk was 5.54% of entry but **2.679 ATR**, above the permitted 2.5. Neither book had an eligible momentum purchase. One signal-day disqualified momentum shadow eventually closed. Its outcome belongs to the excluded-call record. The 12 cases reconcile without a detector or allocation change. [All 12 momentum plans](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/signal-and-edge-audit.json)

## Drawdown pause and recovery

The archived r1 pause **did release** on 5 and 10 March 2026 when drawdown briefly moved inside 10%. The regime gate failed on both dates. R1 paused again on 16 March and stayed paused for the last 170 sessions.

R2 checks the gate on each completed session **after** a pause. A failed gate resets the count. On the tenth consecutive passing close, the account can reserve orders at 0.25% risk for the next session. A fresh downward crossing of 10% can pause it again. A strictly higher equity peak restores full 0.5% risk. The journal records the date, old and new state, gate count, equity, peak, drawdown and risk fraction on every transition.

| Pause at close | Drawdown | Resume at close | Drawdown | Passing closes | Resumed risk |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 Aug 2025 | 10.21% | 23 Sep 2025 | 10.12% | 10 | 0.25% |
| 14 Oct 2025 | 10.24% | 29 Oct 2025 | 8.87% | 10 | 0.25% |
| 4 Mar 2026 | 10.15% | 13 Aug 2026 | 10.35% | 10 | 0.25% |

Each of the three resume closes allocated at least one plan that subsequently bought. The pilot recorded no new peak after the first pause, so it ended at half risk. Automated tests exercise restoration to full risk, streak resets and repeated sessions. [Recorded account transitions](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/report.json)

## Strategy results without portfolio limits

Each eligible call receives an independent ₹5,000 risk budget, 0.5% of the fixed ₹10 lakh reference. The quantity is the whole number of shares that fits that budget at the actual fill and pattern stop. Repeated signals in the same ISIN can overlap. There are no cash, sector, position, aggregate-risk or drawdown limits. Dated membership, liquidity, surveillance, regime, results, stop bounds, fill rules, fees and corporate-action handling still apply. Returns below use only closed trades. An unmeasurable position has no assigned P&L. Adding independent trade P&Ls does not describe a single investable ₹10 lakh account.

| Strategy | Eligible plans | Bought | Closed | Open | Awaiting entry | Missed | Invalidated | Unmeasurable | Win rate | Mean net R | Profit factor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend pullback | 1,871 | 1,683 | 1,625 | 53 | 16 | 165 | 7 | 5 | 35.82% | −0.245 | 0.63 |
| Volatility breakout | 312 | 235 | 220 | 15 | 3 | 73 | 1 | 0 | 32.27% | −0.328 | 0.49 |
| 52-week-high momentum | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | — | — | — |

Both measurable strategy averages were negative in this pilot. The 1,845 closed outcomes include overlapping trades and signals from the same market sessions. Historical data exclusions and the five unmeasurable positions also limit interpretation. No parameter was selected on these outcomes.

## The three unmeasurable r1 shadows

| Archived call | What happened | Treatment |
| --- | --- | --- |
| TATACONSUM #3638 | Of 139 shares bought, 70 remained on 26 July 2024. The issuer offered one right per 26 shares at ₹818. | The holder could subscribe, sell rights or let them lapse. The research convention has no rights election or additional-capital policy. The call retains its fills and an unknown return. [Issuer letter of offer](https://www.tataconsumer.com/sites/g/files/gfwrlq316/files/2024-07/ABRIDGED-LETTER-OF-OFFER.pdf) |
| TVSMOTOR #7598 | Of 23 ordinary shares bought, 12 remained on 25 August 2025. The holder became entitled to 48 separately issued redeemable preference shares, four per ordinary share. | The ordinary shares survive. A realised result needs a valuation and disposition for the second security. Its ₹10 face value cannot establish the holder’s sale price. [Issuer scheme document](https://www.tvsmotor.com/api/InvestorDownloadData?ItemId=8429BC9A7BD447A4A5DDA68BB26B6AF3) |
| POLICYBZR #11716 | The call held 39 shares. NSE closed the same ISIN at ₹1,886.30 on 23 September 2026 and ₹1,207.20 on 24 September, a 36.00% drop. There was no pending opening exit or confirmed explanatory action. | The closing move exceeded the 20% quarantine threshold. The engine preserved the existing holding and recorded the valuation gap. The later close cannot justify a retroactive opening sale. [NSE cash-market final](https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20260924_F_0000.csv.zip) |

These are one market-data gap and two types of shareholder entitlement outside the agreed settlement rules. The explanations support keeping all three censored. The underlying published instruments, election and price evidence would be needed to establish outcomes.

R2’s different entries exposed **15 unmeasurable portfolio-excluded shadows** (14 pullbacks and one volatility breakout) and **five unmeasurable independent calls** (all pullbacks). The two books can cover the same signal, so the counts are reported separately. Zero funded calls were unmeasurable.

| Event | Excluded shadows in r2 | Eligible independent calls in r2 |
| --- | ---: | ---: |
| Ordinary bonus issues · ASHOKLEY, BSE, MOTHERSON, WIPRO | 7 | 3 (BSE twice, MOTHERSON once) |
| Demergers · ITC, VEDL | 2 | 0 |
| TATACONSUM rights | 2 | 2 |
| TVSMOTOR preference shares | 2 | 0 |
| POLICYBZR closing quarantine | 2 | 0 |

For bonus issues the original stock can continue trading while a holder awaits separately credited units. Published ratios alone do not establish how many units could be sold at each intervening open. For demergers, valuing the distributed security also requires dated evidence. Censored outcomes stay visible and are excluded from closed-trade statistics. [Data gaps](DATA_GAPS.md)

## Verification and the stage gate

The independent Codex reviewer checked point-in-time ISIN membership, official NSE prices, the absence of new detector signals, entry and exit timing, costs, drawdown transitions and closed cash flows. The nine previously documented nontrading placeholders explain membership-versus-traded-basket differences on 166 dates. All 6,537 buys across the funded, excluded-shadow and equal-risk books used the slipped next open. All 14,080 fill fees and 6,327 closed net rupee/R outcomes reconciled. The independent account reconstruction matched every closing balance within ₹0.000001. Both actual same-bar stop/target collisions sold the full position at the next open. An independent sizing check reconciled all 1,918 equal-risk buys. [Run and source fingerprints](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/report.json) · [Data preparation](PILOT_REPORT.md)

The frozen-dependency suite passed 102 tests. A second invocation on the completed r2 books preserved the SHA-256 of the SQLite journal, report and timing evidence. The r1 journal and sealed 18-month historical artifact retain their verified hashes. [Repeat verification](../data/pilot/runs/pilot-38cd9375ea5dde47c7cd/repeat.json)

On the prepared local archive, reproduce the r2 pilot with the following command.

```sh
uv run --frozen python -m thesistally.pilot --revision r2
```

The same command accepts `--revision r1` for the archived rule design. The historical 2–20 November 2026 holdout is retired. The final out-of-sample test will use new forward paper sessions after the rule freeze. Stage 2 requires separate approval. [Research log](RESEARCH_LOG.md)
