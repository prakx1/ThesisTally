# Strategy D2 historical XBRL coverage

## Final decision across NSE and BSE

**D2 is parked. Zero of ten annual windows have verified first-XBRL coverage of 80%. Five were required to run D40 and D20.** Even if every unresolved ordering question for quarters with usable figures in the recovered archive were resolved favorably, only 2025 and 2026 could pass. This upper count describes the recovered data. The exchange archives may contain filings that the study could not retrieve. [Sealed counts](../data/fundamentals/d2-bse/reports/4258c037271a8c8be21ae40c80086d8434a1dab47893580420b82d7b30e024b6/coverage.json) · [Independent audit](../data/fundamentals/review/d2-bse-final-1382fc21f57b.json) · [Decision receipt](../data/fundamentals/d2-bse/decision.json)

| Test year | Eligible quarters | Confirmed first XBRL | Verified coverage | Upper count from available figures | Needed for 80% |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2017 | 804 | 0 | 0.0% | 0 | 644 |
| 2018 | 804 | 105 | 13.1% | 309 | 644 |
| 2019 | 804 | 235 | 29.2% | 460 | 644 |
| 2020 | 802 | 463 | 57.7% | 569 | 642 |
| 2021 | 800 | 492 | 61.5% | 561 | 640 |
| 2022 | 800 | 539 | 67.4% | 601 | 640 |
| 2023 | 801 | 390 | 48.7% | 559 | 641 |
| 2024 | 803 | 557 | 69.4% | 600 | 643 |
| 2025 | 800 | 312 | 39.0% | 712 | 640 |
| 2026 through 20 Nov | 801 | 10 | 1.2% | 772 | 641 |

In 2025, the NSE-only count was 711. Checking the first publication on either exchange verified 312 of 800 eligible quarters. In 2026 through 20 November, the count went from 772 on NSE alone to 10 verified across both exchanges. Many BSE catalogue entries claim an earlier timestamp, while the linked original could not be verified at that time. For example, BSE's integrated catalogue dated Reliance's March 2025 consolidated filing to 25 April, but its linked rendering was associated with a June submission. Using the June document at the April clock would introduce hindsight. [Annual counts](../data/fundamentals/d2-bse/reports/4258c037271a8c8be21ae40c80086d8434a1dab47893580420b82d7b30e024b6/windows.csv) · [Evidence for previously covered quarters](../data/fundamentals/d2-bse/reports/4258c037271a8c8be21ae40c80086d8434a1dab47893580420b82d7b30e024b6/previously-covered.csv)

The BSE search added two previously uncovered quarters, HDFC Bank in June 2020 and Astral in December 2024. The latter belongs to the 2025 filing window. It also confirmed 31 quarters already counted from NSE under an earlier BSE edition. All 2,224 integrated attachment candidates in the two decision-relevant years received three observations apiece. The same client recovered the expected bytes in only 3 of 46 requests for known-good documents. All 23 of its issuer-catalogue control requests succeeded. [Checks on previously missing quarters](../data/fundamentals/d2-bse/reports/4258c037271a8c8be21ae40c80086d8434a1dab47893580420b82d7b30e024b6/stock-quarters.csv) · [Attachment availability controls](../data/fundamentals/d2-bse/inline/reports/ffa2194f83aa41963030e783fea85ed5735b57df42586b87dad61fca36eb73f5/file-availability.json) · [Portable source archive](../data/fundamentals/d2-bse/reports/4258c037271a8c8be21ae40c80086d8434a1dab47893580420b82d7b30e024b6/sources.tar.gz)

Neither a historical D2 performance test nor AI-assisted extraction was run. A separate user service began collecting new public results on 23 November 2026. [Forward service and live filing](D2_FORWARD_COLLECTION.md)

## Earlier NSE-only source edition

**Two of ten windows reach 80% under the signed XML identity and field checks.** The count covers official quarterly results through the last certified session, 20 November 2026. The same two years clear 80% if any timely, complete parsed XML for that issuer-quarter is admitted, including amendments. [Coverage receipt](../data/fundamentals/d2-xbrl/coverage.json)

| Test window | Eligible | Listed XML by cutoff | Complete first XML | Coverage | Needed | Complete any XML edition |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2017 | 804 | 0 | 0 | 0.0% | 644 | 0 |
| 2018 | 804 | 456 | 309 | 38.4% | 644 | 323 |
| 2019 | 804 | 667 | 460 | 57.2% | 644 | 488 |
| 2020 | 802 | 763 | 568 | 70.8% | 642 | 568 |
| 2021 | 800 | 771 | 561 | 70.1% | 640 | 561 |
| 2022 | 800 | 710 | 601 | 75.1% | 640 | 602 |
| 2023 | 801 | 770 | 559 | 69.8% | 641 | 559 |
| 2024 | 803 | 780 | 600 | 74.7% | 643 | 600 |
| 2025 | 800 | 793 | 711 | 88.9% | 640 | 720 |
| 2026 | 801 | 797 | 772 | 96.4% | 641 | 779 |

“Listed XML” counts a security-quarter when the source catalogue listed a result by its test-year cutoff. The independent counts of available bytes and complete parsed editions distinguish missing files and unusable facts. In total 5,141 of the 10,221 study stock-quarters, including the development period, had a complete first XML by their own filing-season end. The four-field study counts each ISIN and quarter once. [Window counts](../data/fundamentals/d2-xbrl/windows.csv) · [Every security-quarter, selected accession, source hash, usable session and gap](../data/fundamentals/d2-xbrl/stock-quarters.csv)

### How the NSE-only source edition was counted

The numerical counter uses the first exchange-listed XML accession for each ISIN, quarter and accounting basis. Each accepted document must supply all four finite values for the current three-month period under the archived parser. Revenue, operating profit, net profit and basic EPS retain the Strategy D field and unit conventions. The parser checks current XBRL period, security and reporting basis, and stores the validated XML’s SHA-256 with its fields. The comparison reads these structured-document fields even when an earlier PDF was incomplete. The frozen BSE fallback contributed no independent structured filings. [Registered Strategy D data definitions](PREREGISTRATION.md#d--post-earnings-momentum) · [Signed historical input ledger](../data/fundamentals/nse/vintages/cb381118ee9ad0593180b7d98ac5e7d976487621ce56e7b9b1136ecdf802b1f4.jsonl.gz)

Each XML uses its own official catalogue clock. For the legacy NSE catalogue this is the timestamp of exchange dissemination, with the full broadcast timestamp as fallback. For integrated filings the signed catalogue uses the latest valid creation, broadcast or revision timestamp supplied by the exchange. A date without a time does not establish a release. A filing after 15:30 IST, on a weekend or on a holiday first becomes usable on the next actual NSE session. Synthetic ledger rows that describe earlier PDF editions do not contribute an XML accession or move its clock. [NSE catalogue field mapping](../src/thesistally/fundamental_nse.py) · [Trading-calendar convention](../src/thesistally/fundamental_coverage.py)

Explicit revisions are ineligible as first results. The earliest unavailable, incomplete or conflicting XML cannot be filled with a later XML. Complete consolidated data takes precedence, with a complete standalone first filing as fallback. Fields cannot be joined across reporting bases. The last table column is a ceiling within the signed parsed-XML dataset and the same time cutoffs. New evidence resolving an issuer identity or changing an XML parser could change that ceiling. Data gaps include 1,183 test-window stock-quarters where a first XML had an unresolved ISIN mismatch. For some these sources lack an explicit XML ISIN while the catalogue names a different share-class identifier. The previously signed XML parser requires original ISIN confirmation in that case. [Issuer and field checks](../src/thesistally/fundamental_facts.py)

The denominator uses the original point-in-time membership and the previous December plus the current March, June and September quarters. Each window closes on its final certified NSE session. The integer threshold is at least four-fifths of the eligible count. The isolated worktree can reproduce the count with `.venv/bin/python -B -m thesistally.results_xbrl_coverage`. Its normal entry point runs offline and confines writes to the dedicated D2 coverage directory. [Coverage calculation](../src/thesistally/results_xbrl_coverage.py)

## Comparing first publications across the two exchanges

A stock-quarter contributes at most once. For each accounting basis the reconciliation compares the timestamp of the first original NSE XBRL with the BSE historical results and integrated financial filing catalogues. A BSE source has to identify the original edition, the issuer's dated share class, the reporting basis and a three-month period containing all four required fields. A later submission cannot establish what the market knew when an earlier filing appeared. Multiple links to one submission do not add observations. [Historical BSE census](../src/thesistally/results_bse_census.py) · [Quarter and unit verification](../src/thesistally/results_bse_facts.py)

Two examples illustrate how this affects the count. HDFC Bank's June 2020 consolidated XBRL supplied the four quarterly figures, including basic EPS of ₹12.60. Hindustan Unilever's March 2019 original supplied annual figures with placeholder values in the quarterly column. Only the HDFC Bank filing contributes a quarter. Where BSE names an earlier original whose contents or publication time cannot be verified, the count records that uncertainty. A later NSE filing can establish a figure by its own publication time while leaving the earlier release unresolved. [First-edition checks](../src/thesistally/results_bse_priority.py) · [Inline XBRL and catalogue reconciliation](../src/thesistally/results_bse_inline.py)

Repeated requests for the same previously verified BSE originals returned both 404 responses and the matching document bytes, while the issuer catalogue remained available. The source receipts distinguish these retrieval failures from filings that supplied the wrong period, lacked a field or were amendments. Coverage measures what the study could verify from the exchanges.
