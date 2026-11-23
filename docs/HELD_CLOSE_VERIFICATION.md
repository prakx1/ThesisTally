# Independent checks of the five held NSE closing prices

The five closing quarantines from the preceding Stage 3 report can be released under the owner’s new two-source convention. For **each** instrument, the NSE cash bhavcopy and the independently published Groww NSE cash series give identical opens, highs, lows and closes on both the prior session and the flagged session, in the historical share units. The corporate-action register has no ex-date event for any of the five moves. Each decision takes effect at the close of the flagged session. [Full case evidence, raw observations, source URLs and checksums](../data/stage3/close-verification/evidence.json)

| Stock and session | NSE prior → close, ₹ | Groww quoted prior → close | Kite quoted prior → close | NSE Clearing underlying prior → close, ₹ |
| --- | ---: | ---: | ---: | ---: |
| PCJEWELLER · 2 Feb 2018 | 484.30 → 364.25 | 48.430 → 36.425 | 48.43 → 36.42 | 484.30 → 364.25 |
| BAJFINANCE · 23 Mar 2020 | 2,951.90 → 2,266.15 | 295.190 → 226.615 | 295.00 → 226.50 | 2,952.25 → 2,266.10 |
| MANAPPURAM · 25 Mar 2020 | 84.30 → 101.90 | 84.30 → 101.90 | 84.30 → 101.90 | 84.30 → 101.90 |
| IDEA · 29 Dec 2023 | 13.25 → 16.00 | 13.25 → 16.00 | 13.25 → 16.00 | 13.25 → 16.00 |
| POLYCAB · 11 Jan 2024 | 4,911.85 → 3,878.45 | 4,911.85 → 3,878.45 | 4,911.85 → 3,878.45 | 4,911.90 → 3,878.40 |

PCJEWELLER’s December 2024 subdivision issued ten shares per original share. BAJFINANCE’s June 2025 subdivision and bonus together gave ten shares per original share. The NSE explicitly published Bajaj’s combined adjustment factor. Multiplication by the documented factors restores the Groww observations to their earlier units exactly. The two Kite comparisons in the table differ on the original price scale. All three unadjusted cases agree directly with Kite. Each Kite comparison uses the original archived provider response stored as daily candles. A fresh read during this check returned a session or entitlement error. [PC Jeweller clearing circular](https://archives.nseindia.com/content/circulars/CMPT65546.pdf) · [Bajaj adjustment circular](https://archives.nseindia.com/content/circulars/FAOP68547.pdf) · [Bajaj ISIN notice](https://archives.nseindia.com/content/circulars/CML68472.pdf) · [Kite historical candle format](https://kite.trade/docs/connect/v3/historical/)

The clearing cash-volatility file independently confirms the PCJEWELLER, MANAPPURAM and IDEA pairs. For BAJFINANCE, the same-day derivatives risk file reports ₹2,266.10. The original NSE cash-market price of ₹2,266.15 is corroborated exactly by Groww. POLYCAB also differs by ₹0.05 in the clearing file, while the NSE final, Groww and Kite all agree to the paisa. Only exact pairs from independent publications count as corroboration. [BAJFINANCE clearing report](https://archives.nseindia.com/archives/nsccl/volt/FOVOLT_23032020.csv) · [Official exchange field definitions](https://archives.nseindia.com/content/press/EOD_CM.pdf)

The review examined NSE filings over the three calendar days on either side of each move. The exchange queried PCJEWELLER’s price decline on the day. POLYCAB received a news-verification query and supplied a clarification. IDEA disclosed a trading-window closure, followed by an exchange news query. BAJFINANCE later clarified a false call invitation. MANAPPURAM’s nearby filings concerned a committee meeting, trading window and credit rating. None identified a split, bonus, distribution or other ex-date adjustment explaining the move. The receipt links every reviewed announcement and the complete annual NSE action-query results. Later filings and unit changes support the retrospective data check and do not enter the signal calculations. [Dated NSE sources by stock](../data/stage3/close-verification/evidence.json)

With the historical market archive materialized, the following read-only command verifies the original files, recomputes the independent price comparisons and checks the stored decisions.

```sh
uv run --frozen python -m thesistally.close_evidence
```

## Two further jumps encountered in the 2020 account

The reopened A252 account held 136 ICICIPRULI shares during two more large moves. On 23 March the stock closed at ₹238.80 after ₹304.20. On 25 March it closed at ₹320.45 after ₹254.45. The NSE cash finals, Kite historical archive and independently published Upstox cash candles agree on each pair of closes without a share-unit conversion. Groww returned zero candles on both queries. NSE Clearing’s risk prices differed by ₹0.10 on each flagged day (₹238.90 and ₹320.35). NSE’s action register showed no ex-date event and the scoped company-announcements queries returned no filings. Both observations qualify for close-time release. [Seven-case supplementary receipt](../data/stage3/close-verification/evidence-held-pair.json) · [Upstox historical candle fields](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)

The first receipt preserves the actual March 20 account state. To establish the second holding, a copy of that journal was advanced through the March 23 and 24 sessions using the first release and the same daily account engine. Neither day was a scheduled rebalance, and the position remained at 136 shares with no pending order. The two dated account states are included with their hashes. The original five-case receipt remains byte-identical. [March 20 holding](../data/stage3/close-verification/followup-held-1.json) · [March 24 holding](../data/stage3/close-verification/followup-held-2.json)

```sh
uv run --frozen python -m thesistally.close_evidence --evidence data/stage3/close-verification/evidence-held-pair.json
```
