# Quarterly results coverage

The coverage ledger follows Nifty 200 membership by ISIN from 1 January 2014 through the last certified session on 20 November 2026. Across 51 completed calendar quarters there are 10,221 eligible security-quarters and 448 distinct quarter-end securities. The stock table also lists 17 historical ISINs that joined and left between quarter ends, giving 465 ISINs in total. Several quarters had 201 securities in the official membership ledger. A share class holds its own ISIN. [All stocks](../data/fundamentals/stock-coverage.csv) · [Each stock by calendar year](../data/fundamentals/stock-year-coverage.csv) · [Every stock-quarter and gap](../data/fundamentals/quarter-coverage.csv)

## Which quarter counts

Take each constituent on the last actual NSE session at or before the calendar quarter end, including issuers that later merged or delisted. A security-quarter counts once when an original consolidated or standalone filing has a known release time, an official source hash and all four usable quarterly figures. Both accounting bases retain their own first-release records. The quarter CSV gives the earliest verified timestamp, accounting basis and source of each known field even when only part of the statement is covered. A complete set still requires one basis. A later consolidated release has its own availability time. Unresolved earlier releases of the same basis prevent a later copy from qualifying as the original. Revisions, repeated annual totals and comparative columns never fill missing first-release values. [Membership ledger](../data/universes/nifty200_membership.csv) · [Certified trading sessions](../data/baseline/preflight.json)

Calendar-year coverage groups by quarter end, as observed through the snapshot close. Walk-forward coverage aligns with filing seasons instead. Each annual trading window uses December of the previous year and March, June and September of the current year. It counts releases usable by the window's last actual session. Coverage of at least 80% allows the window to proceed to the trading-data checks. The threshold is computed using integer counts. A stock that joins between quarter end and the announcement can still be screened at the registered signal time. The quarter-end denominator audits data completeness. Off-cycle source periods remain dated in the filing ledger. Mphasis, for example, reported a quarter ending 31 January 2014 followed by a two-month transition to 31 March 2014. The shorter March stub cannot establish a full calendar-quarter result.

A timestamp of exactly 15:30:00 IST is usable that session. A later timestamp moves to the next actual session, as does a weekend or exchange holiday. An original PDF can precede its structured submission. Each first-reported field keeps its own public timestamp. When earnings were disclosed in stages, the EPS clock determines when Strategy D can form its signal. The four-field coverage clock is the latest first publication among revenue, operating profit, net profit and EPS. An unidentified intervening release can prevent a later table from establishing an original figure. A delayed filing can establish an earlier clock only with evidence linking the earlier announcement to the particular facts. A reconciliation of a delayed four-field table requires the same accounting basis, period, monetary unit and first quarterly column. An extracted PDF field requires an original statement label, current-quarter column, reporting basis and units, plus corroboration of the printed amount. A PDF text layer or OCR result on its own can misread digits. The reviewer found such errors in the provisional acquisition checkpoint at commit `30239fd`, and its coverage counts were withdrawn. The coverage threshold still requires all four original figures on one accounting basis. Unreadable fields and ambiguous layouts retain their gaps. Announcements outside the certified trading calendar retain their actual timestamps. [Original filing procedure published by BSE](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=6b163bb8-fbe3-4e14-be9a-47b99f2cfaaf&id=20250523-40)

## What the figures measure

Revenue, operating profit and net profit are recorded in rupees, with the reported unit scaled to INR. EPS is the reported basic rupees per share. For industrial issuers the source uses operating revenue. Operating profit can be reported directly or calculated as operating revenue less total expenses plus finance costs. This retains depreciation and removes financing charges. A bank's revenue uses reported total income, including interest income. Its operating profit is the reported amount before provisions and contingencies. These measures reflect different businesses. The ledger's fact tags identify each source or arithmetic derivation.

Net profit is the total profit for the reporting period. For a consolidated issuer it can include non-controlling interests. Basic EPS uses the issuer's published shareholder earnings calculation. For example, TCS's September 2024 consolidated statement reports ₹11,955 crore of total profit and ₹32.92 basic EPS. Its ₹64,259 crore operating revenue, ₹48,956 crore total expenses and ₹162 crore finance costs imply ₹15,465 crore operating profit. In the September 2024 Kotak Mahindra Bank standalone statement, operating profit before provisions is reported directly at ₹5,099.25 crore. The bank reported ₹15,900.46 crore total income and ₹3,343.72 crore net profit, with basic EPS of ₹16.82. [TCS, page 7](https://nsearchives.nseindia.com/corporate/SEIntfinancials_10102024160639.pdf) · [Kotak, page 4](https://nsearchives.nseindia.com/corporate/KOTAKBANK_19102024122109_OutcomeofBMOct192024.pdf)

## Exchange coverage and preservation

The NSE catalogue includes legacy results tables and XBRL instances. We rebuilt the exchange-wide announcement tape from January 2011 through 20 November 2026 across 191 months. The 192 date blocks contain 1,538,926 announcements. Independently requested early-period portions reproduced 245,671 records. Screening for results and board outcomes retained 42,515 potential notices for 397 historical symbols. Where per-issuer archive queries initially failed or returned empty, shorter queries with matched overlaps recovered 158 histories. These checks establish the bulletin coverage. Every quarter counted in the numerator still requires original numerical evidence. [Market-wide receipts](../data/fundamentals/nse/notice-market-manifest.json) · [Issuer receipts](../data/fundamentals/nse/notice-fallback-manifest.json)

The original screening vocabulary missed legacy NSE results summaries and some issuer press releases. A second scan retained 1,729 quarterly summaries and 645 annual summaries, plus 1,120 attached potential earnings releases. The exact first-quarter amounts in the legacy summary come from the original exchange response, preserved with its hash and the accession fingerprint. A quarterly summary can establish its period’s reported net profit. “Net Sales” or bank interest earned alone does not identify total operating revenue or total bank income. Only a complete body with its issuer, basis, duration, numerical clauses and absence of attachments verified can establish that EPS or operating profit first appeared elsewhere. [Supplemental notice evidence](../data/fundamentals/nse/notice-language-manifest.json)

BSE's current app identified its official public API, which returned HTTP 403 for the results catalogue, announcement archive, dated TCS request and issuer master during collection. The latest BSE financial-results RSS returned zero items and supplies no historical census. The fallback contributed zero verified first filings. The recorded clock is the earliest verified NSE disclosure available for that filing. An issuer may have released the result on BSE earlier. A BSE announcement before 15:30 and an NSE release after 15:30 could shift the first usable session, the measured price reaction, and the entry by one trading session. The magnitude of that effect is unknown. Saved responses include the requested URL, HTTP status, date and SHA-256. [BSE access evidence](../data/fundamentals/bse/source-access.json) · [NSE filing receipt](../data/fundamentals/nse/filings-receipt.json)

The historical BSE results and corporate-announcements landing pages returned client shells without public tabular results in their HTML. The separately advertised BSE Corporate Data feed requires registration, pricing agreements and payment. Direct public BSE attachments can still corroborate a known document. TCS's September 2024 BSE filing was byte-identical to both NSE originals. APL Apollo's December 2024 BSE board-result PDF was byte-identical to the later of its two NSE notices, and the issuer signed it before the first NSE notification. Max Healthcare's BSE integrated filing for the same quarter was signed hours after the first NSE notification. The board signature and the HTTP file timestamp are distinct from an exchange disclosure timestamp. These examples contributed no additional first-publication clocks. Among 434,860 announcements for the study tickers in the complete NSE bulletin, nine mentioned the BSE domain and none linked directly to a BSE results document. [Public landing-page receipts](../data/fundamentals/bse/alternate-route-receipts.json) · [Original PDF comparisons](../data/fundamentals/bse/cross-exchange-check.json) · [BSE data feed terms](https://marketdata.bseindia.com/)

Bharat Petroleum’s 31 January 2022 original standalone statement shows December-quarter operating revenue of ₹1,18,536.76 crore on page 4. Its searchable text lost the rupee glyph. At three render sizes an unrestricted Hindi recognizer found ₹ in the legend, and an English recognizer read “in Crores” on the same pixels. The English recognizer read the glyph itself as “2”. An independent reviewer verified the printed ₹ on the original. The revenue cell, period and issuer also agree with the separate exchange XBRL. This evidence certifies one revenue figure. The other three fields require their own evidence. [Original filing](https://nsearchives.nseindia.in/corporate/resultsdec211_31012022172039.pdf) · [Printed legend](../data/fundamentals/nse/targeted-currency/legend-3200.png) · [Three optical readings](../data/fundamentals/nse/targeted-currency/evidence.json)

### Why a readable legacy table could still lack an original

The reviewed interim contained 4,339 initial HTML filing editions on the two accounting bases for 2,931 eligible security-quarters. An earlier exchange packet existed for 4,334 editions. Two more followed an exhaustive exchange summary and then another packet before the formal table. SBI’s intervening filing followed its summary by two seconds. None of these fiscal-year cohorts contained an exhaustive earlier summary followed immediately by a clean, first structured table. The audit checked the actual earlier document for one four-value HTML filing in each fiscal year, selecting the smallest SHA-256 of its filing ID. CYIENT, Unitech, Idea and DLF had image-only packets of 4, 16, 15 and 17 pages respectively. RBL Bank’s packet had 49 pages and no machine-readable line labelled “basic” in its text layer. A later table can help verify a printed original; it cannot supply a field that may have changed in between. [Edition census and sample evidence](../data/fundamentals/nse/legacy-html-audit.json)

The three initially apparent exceptions were one standalone GSK Consumer filing and two Natco Pharma filings. GSK's preceding dividend-category attachment contained the March quarter results. Natco's preceding record-date attachment stated that both sets of results were enclosed, but the archived ZIP contained only the cover letter. A later same-day consolidated press release and the formal HTML table reported different profit figures. No candidate remained that could safely acquire a first-publication label solely from the HTML broadcast. [Three earlier official bulletins and source hashes](../data/fundamentals/nse/notice-case-manifest.json)

We also tested the physical tables in ten archived results packets, two per period-end year from 2013 through 2017. The sample contained 72 pages and twelve candidate filing editions. The March 2015 ITC first packet had a clearly bounded standalone quarter column at four rendering sizes. The scale was legible as crores, but the amount currency and basic EPS currency could not be read reliably. The other packets did not establish issuer, basis and quarterly column together under this grouped-header method. This test added zero verified original fields. The source hashes, dates and optical page hashes are recorded for each packet. [Legacy image sample](../data/fundamentals/nse/legacy-grouped-raster-study.json)

### Current-quarter figures in recent filings

We sampled five different issuers per year from 2021 through 2026 whose original document was archived and whose later XBRL contained all four values. The sample was selected by filing-ID hash. In 13 of the 30 originals the native text did not establish the legal issuer on a statement page, and in seven it did not establish the quarter and comparator columns. Thirteen includes cases where the bulletin contained a readable letter and the accounting table itself was scanned. The earlier native-text methods confirmed at least one field for two of the 30. [Per-issuer evidence and failure counts](../data/fundamentals/nse/native-row-study/index.json)

A second reading checks the printed legal name, accounting basis, explicit currency, quarter heading, first and comparative dates, and individual current-quarter cells. It handles a wrapped company name or year, and finance-cost labels with a footnote. An amount must agree exactly with the independently filed XBRL. Across the initial 30 originals this added six fields for three issuers, including the missing operating profit in UPL’s March 2022 consolidated statement. Applied unchanged to another 120 distinct issuers, it added five fields for four issuers, including one more complete set on combining the two methods. The signed amounts and source-page references are available for each additional field. These figures describe archive recovery. Qualification uses the separate annual coverage calculation. [120-issuer check](../data/fundamentals/nse/native-row-study/holdback.json)

### Earlier modern originals in the downloaded archive

An independent audit of the first numerical edition after the download found 571 uncovered stock-quarters in the 2025 filing season and 589 in 2026 with both a downloaded earlier original and four figures in a later XBRL available by the window close. Some earlier originals include the complete first statement even when their searchable text is garbled. Counting each eligible stock-quarter just once across the legacy, disputed-cell and modern lists gives optimistic ceilings of 685 of 800 for 2025 and 732 of 801 for 2026. Both years could reach 80% if enough figures can be verified. All eight earlier years remain below 80% even under those assumptions. The experiment counts only figures verified in the original release. [Independent year-by-year census and source example](../data/fundamentals/review/POST_DOWNLOAD_AUDIT.md)

The complete modern plan contains 5,837 earlier original packets and 11,008 comparative accessions. The selector nominated 36,781 page reads out of 124,821 pages, with no nominated page for 780 packets. These packets stay in the inventory. In the signed controls, both pages that yielded figures were selected. A separate four-page check also found a missed standalone statement, Voltas page 21. The independent all-page review still checked that particular document. Other documents with similar text can be missed. Counts of nominated pages describe the work undertaken. Each accepted figure also needs a dated original and the same issuer, quarter, basis and units. [Signed page inventory](../data/fundamentals/nse/modern-optical-plan-summaries/9062be34a9411ff83cd431d5fb58dc2e8471b72b07cc11a97fe64d9ea07ba78d.json) · [Control-page comparison](../data/fundamentals/nse/modern-optical-plan-summaries/9062be34a9411ff83cd431d5fb58dc2e8471b72b07cc11a97fe64d9ea07ba78d-known-pages.json)

Mankind Pharma’s June 2023 consolidated filing illustrates a remaining data gap. The modern reader verified operating revenue and total net profit on page 8. The source also prints basic EPS of ₹12.15 and diluted EPS of ₹12.14. The current automated reader does not recognize the exact caption “Basic EPS (in INR)”. The expense row also lacks the readable adjacent comparative cell required by its column check. The independent review identified both omissions. The original and later structured values remain separately available for audit. The historical signal calculation uses only the fields the frozen method establishes. [Original-page numerical check](../data/fundamentals/review/first-broad-positive-mankind.json)

The latest coverage JSON records SHA-256 for the membership file, session certificate, both exchange filing ledgers, the canonical per-basis original records and the coverage tables. Every published receipt is retained by hash in `data/fundamentals/coverage-history/`. Each source and materialized product is retained as the corresponding hash-named gzip object under `objects/`. New editions update the current pointers after archiving their source and product bytes. Each filing keeps its source timestamp and revision classification. [Current coverage receipt](../data/fundamentals/coverage.json)


The archival and coverage worker can be rerun from the separate fundamentals worktree after the two source ledgers are materialized. The NSE loader also accepts the committed gzip vintage that matches the SHA-256 in the filing receipt.

```sh
uv run --frozen python -m thesistally.results_worker coverage
uv run --frozen python -m thesistally.fundamental_coverage_report
```

To resume the modern original-statement checks on the archived Mac worktree, the following commands use the same content-addressed page plan and previously completed receipts. The final worker also checks the full legacy plan, rebuilds the coverage ledger, and runs both preregistered horizons.

```sh
uv run --frozen python -m thesistally.fundamental_modern_build --plan 9062be34a9411ff83cd431d5fb58dc2e8471b72b07cc11a97fe64d9ea07ba78d --workers 2
uv run --frozen python -m thesistally.fundamental_finish --wait-for-modern-sources --workers 3
```

## Results by year

The official attachment acquisition pass finished. The signed plan counted 15,577 URLs (15,525 archived, 52 unavailable). [Source census](../data/fundamentals/nse/checkpoints/cb381118ee9ad0593180b7d98ac5e7d976487621ce56e7b9b1136ecdf802b1f4/0d9900329b56e04e63d1563fc15dcd57af2c3c818dae123157e7e02c284970c5/index.json)

Through 2026-11-20, 610 of 10,221 eligible security-quarters had four verified first-reported fields (5.97%). 0 of 10 walk-forward windows reached 80%. [Coverage receipt](../data/fundamentals/coverage-history/0e93eb357fb6e0905f52fd9a518a571f1738030a5b38ec4a97b0dca9ea7bb6ce.json)

| Year | Quarter-end cohort covered / eligible | Coverage | Filing-season covered / eligible | Coverage | Window status |
| --- | ---: | ---: | ---: | ---: | --- |
| 2014 | 0 / 800 | 0.0% | — | — | Development period |
| 2015 | 0 / 800 | 0.0% | — | — | Development period |
| 2016 | 0 / 803 | 0.0% | — | — | Development period |
| 2017 | 0 / 804 | 0.0% | 0 / 804 | 0.0% | Incomplete |
| 2018 | 25 / 804 | 3.1% | 17 / 804 | 2.1% | Incomplete |
| 2019 | 32 / 804 | 4.0% | 31 / 804 | 3.9% | Incomplete |
| 2020 | 64 / 801 | 8.0% | 60 / 802 | 7.5% | Incomplete |
| 2021 | 68 / 800 | 8.5% | 66 / 800 | 8.2% | Incomplete |
| 2022 | 69 / 800 | 8.6% | 69 / 800 | 8.6% | Incomplete |
| 2023 | 64 / 802 | 8.0% | 66 / 801 | 8.2% | Incomplete |
| 2024 | 61 / 802 | 7.6% | 62 / 803 | 7.7% | Incomplete |
| 2025 | 123 / 801 | 15.4% | 102 / 800 | 12.8% | Incomplete |
| 2026 | 104 / 600 | 17.3% | 137 / 801 | 17.1% | Incomplete |

The December 2025 official membership contained the demerger placeholder INE2KCE01013 (DUMMYHDLVR, later KWIL). The registered denominator includes it. Removing it would leave 10,220 historical quarters and 137 / 800 (17.12%) for 2026. The 2026 window classification would stay the same.

## Field coverage and gaps

| First-reported field | Eligible security-quarters |
| --- | ---: |
| Basic EPS | 925 |
| Net profit | 1,107 |
| Operating profit | 1,034 |
| Revenue | 1,313 |

Among eligible quarters, 315 had EPS and lacked a full set of four fields. EPS was the only attested field for 48. Across both accounting bases, warmup quarters and stocks outside the quarter-end universe, 1,445 canonical filings had verified EPS.

| Gap among incomplete quarters (groups can overlap) | Security-quarters |
| --- | ---: |
| No formal result or board notice could be attributed to this ISIN and quarter | 267 |
| A listed structured or original release attachment was unavailable | 595 |
| Original figures, quarter column, reporting period or issuer could not be certified | 9,265 |
| First-publication status or usable time could not be verified | 818 |
| An unavailable attachment could not be distinguished from a revised notice | 0 |

The following historical gap reasons were the most common. The per-quarter ledger links each observed quarter and gap to its attributed source where available. [Quarter-level evidence](../data/fundamentals/quarter-coverage.csv)

| Gap reason | Security-quarters |
| --- | ---: |
| first release facts unreconciled | 8,849 |
| Issuer ISIN disagrees with certificate | 1,201 |
| missing one or more quarterly facts | 865 |
| Current-quarter duration has no supporting contexts | 837 |
| Old HTML lacks a current-quarter ISIN for catalogue alias | 600 |
| subsequent structured vintage | 485 |
| missing original attachment | 415 |
| no verified filing | 267 |
| no independently corroborated original text fields | 176 |
| structured attachment unavailable | 112 |
| exchange labels revision | 110 |
| first release attachment unavailable | 78 |

## Coverage by filing format and stock

A stock-quarter can have multiple submission formats. The second count requires an original complete set from a filing recorded in that format. The final two columns count gaps on uncovered quarters and may overlap.

| Official source format | Eligible security-quarters with format | Complete in format | Unavailable attachment | Unresolved facts, period or identity |
| --- | ---: | ---: | ---: | ---: |
| board notice only | 1,781 | 0 | 107 | 1,733 |
| formal catalogue without structured attachment | 400 | 0 | 400 | 396 |
| integrated XBRL | 1,395 | 227 | 13 | 1,168 |
| legacy HTML | 3,152 | 0 | 223 | 3,102 |
| legacy XBRL | 5,138 | 383 | 120 | 4,733 |
| no attributed record | 267 | 0 | 0 | 0 |

Of 448 securities with quarter-end membership, 332 had no fully covered quarter. The largest per-security gap was 51 quarters. 42 shared it, including ADANIPORTS, AMBUJACEM, APOLLOHOSP, ASHOKLEY, ASIANPAINT. [Each stock by source format](../data/fundamentals/stock-format-coverage.csv) · [Each stock by year](../data/fundamentals/stock-year-coverage.csv)

## BSE cross-exchange results

The final check covered 15,546 original NSE packages, 234,732 pages of native text, 65,574 OCR page reads and 16,474 original PDF hyperlink checks. The official market bulletins contributed 434,860 announcements for the study symbols. The check was completed at 2026-11-23T06:55:56.896129+00:00 and identified 30 distinct direct BSE links. 0 original BSE dissemination timestamps were verified. [Per-link evidence and timestamps](../data/fundamentals/bse/rechecks/066ada0e9d571e19fa0e7149797a096b285aa619d43d00d179c1914ce8361463.json)

| Linked document assessment | Distinct BSE URLs |
| --- | ---: |
| advance notice of a board meeting | 9 |
| attachment unavailable | 15 |
| equity acquisition or financing notice | 2 |
| executive appointment or resignation notice | 1 |
| issuer operational press release | 3 |

52 planned NSE original URLs lacked a usable source. 2,974 source page reads had fewer than 50 native or optically recognized characters. Company signature dates, board meeting times, PDF download times and dates on later BSE or NSE documents were excluded as evidence of first exchange publication.
