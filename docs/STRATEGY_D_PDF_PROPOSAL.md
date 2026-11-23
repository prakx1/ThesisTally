# Proposal for extracting first-reported results from PDFs with Codex

**Decision requested later.** Consider this pass only if the revised XBRL source rule leaves fewer than five walk-forward windows with at least 80% coverage. The signed Strategy D study had zero eligible windows under its original source rule. Its release contains no audited window counts under the revised rule. The estimates below use existing filing and page inventories. They contain no new extraction results. [Signed coverage](FUNDAMENTALS_COVERAGE.md) · [Independent review](RESULTS_D_REVIEW.md)

## Potential coverage gain

I used a narrow definition of PDF-only. Start with the 10,221 point-in-time eligible ISIN-quarters in the signed coverage CSV. Remove already complete quarters and any quarter with a listed structured XML or HTML result for that ISIN on either reporting basis. Of the remaining 545 quarters, 276 have a representative original-notice link with a PDF in the local archive. This list can omit alternate notice links. A listed but unusable XML or HTML file is outside this estimate. Having a PDF does not establish that it reports all four figures on one basis before the relevant cutoff. [Quarter inventory](../data/fundamentals/quarter-coverage.csv) · [Versioned filing ledger](../data/fundamentals/nse/vintages/cb381118ee9ad0593180b7d98ac5e7d976487621ce56e7b9b1136ecdf802b1f4.jsonl.gz)

| Assumed share of the 276 quarters newly completed | Additional complete stock-quarters | Gain across 10,221 |
| ---: | ---: | ---: |
| 25% | 69 | 0.68 percentage points |
| 50% | 138 | 1.35 percentage points |
| 75% | 207 | 2.03 percentage points |
| 100% | 276 | 2.70 percentage points |

These are planning scenarios. Recovery can be zero. Operating profit must be explicitly printed under the proposed rule. A filing that supplies only three original figures can still leave a coverage gap.

| Filing season | Candidate stock-quarters | Maximum increase in that window |
| --- | ---: | ---: |
| 2018 | 130 | 16.2 percentage points |
| 2019 | 93 | 11.6 percentage points |
| 2020 | 5 | 0.6 percentage points |
| 2021 | 5 | 0.6 percentage points |
| 2022 | 25 | 3.1 percentage points |
| 2023 | 3 | 0.4 percentage points |
| 2017 and 2024–2026 | 0 | 0 |
| Development period | 15 | Outside the ten test windows |

Before an extraction approval, join this list to the revised XBRL result by ISIN and quarter. Remove newly covered cases and those without an eligible original publication time. For each year show the revised count, the number still needed to reach 80%, and the maximum count with every remaining PDF recovered. That comparison shows whether reaching five windows is even possible with this scope. [Annual denominators](RESULTS_D_REPORT.md)

## What Codex would read

Use the installed terminal CLI with the existing ChatGPT sign-in. A local program would render the original PDF pages, attach images to a read-only non-interactive Codex job and require a fixed JSON schema. The workspace would contain only the assigned public filing images and page identifiers. Keep the source URL, notice hash, member PDF hash, 1-based physical page number and original exchange release time in the local ledger. Record model, prompt version and returned evidence with each extraction. [Codex automation](https://developers.openai.com/codex/noninteractive/) · [Authentication](https://developers.openai.com/codex/auth/)

For revenue, operating profit, net profit and basic EPS, return the exact printed number, quoted row label, quoted column heading, period, units and reporting basis. Use an explicit unknown for an unreadable or ambiguous item. Derived operating profit, an annual or year-to-date number, diluted EPS, and an inferred release time cannot fill a missing quarterly field. When evidence spans pages, retain the page and quote for each supporting header. Derive usable session from the archived exchange timestamp and existing market calendar. Preserve subsequent restatements as separate editions.

## Independent accuracy checks

- **XBRL agreement.** In a separate control group, sample 100 filings with an original PDF statement page and comparable XBRL. Select them reproducibly across years, layouts and accounting bases. Codex sees the page without the XBRL answers. Match by ISIN, quarter, basis, concept and units. Compare with the XBRL for the same reporting vintage at the PDF’s printed precision. Report matched, mismatched, missing and revision-incomparable counts by field, plus agreement among comparable emitted values and extraction coverage across all expected cells. Controls contribute no PDF-only coverage gain.
- **Human error rate.** Freeze the output list and record a random seed. Draw 200 emitted PDF-only field extractions without replacement, or review all if fewer exist. A human reviewer checks the original page and column for value, sign, units, period, basis and citation. Record one error for a field with any incorrect component. Report errors / reviewed, a 95% interval and the error types. Check the four expected rows on those pages for omissions and report those separately. Accuracy and agreement rates can be reported only after extraction and review. A second model reading the pages would not satisfy the human check.

## Workload and cash budget

The 276 notices contain 285 PDF occurrences and 5,001 inventoried pages. At up to four pages per invocation, keeping each batch within one member PDF, the primary pass requires 1,364 Codex invocations. Budget another 100 one-page invocations for the XBRL controls, or **1,464 initial invocations** before any retries or extra context pages. A smaller post-XBRL list reduces this estimate. CLI usage varies with images, prompt and model. The installed CLI is signed in through ChatGPT. Proposed additional model spend is **₹0**, conditional on using included allowance only, with paid credits, overages and API-key fallback disabled. Pause or wait for allowance renewal if needed. The remaining allowance and completion time for this workload are unknown. [Codex pricing and limits](https://developers.openai.com/codex/pricing/)

Allow **about 7–13 hours of human review** for 200 field extractions at an assumed 2–4 minutes each, plus time to resolve disagreements. At an illustrative ₹500/hour, the 200 checks would cost about ₹3,300–₹6,700 if paid externally. No reviewer has been booked. Approval would be specific to the extraction scope and budget. The proposed outputs would stay separate from the sealed study and A126 forward paper trading.
