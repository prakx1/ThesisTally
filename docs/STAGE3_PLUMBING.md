# Stage 3 execution and data conventions

All ten funded r2 accounts reached their scheduled end. They committed 2,451 of 2,451 test sessions. The table is a check of account continuity under the new conventions. The three retired detectors and their original results remain archived.

| Test year | Sessions committed | Funded entries | Closed trades | Opening sales with unknown floor |
| --- | ---: | ---: | ---: | ---: |
| 2017 | 248 | 0 | 0 | 0 |
| 2018 | 246 | 0 | 0 | 0 |
| 2019 | 245 | 0 | 0 | 0 |
| 2020 | 252 | 21 | 21 | 13 |
| 2021 | 248 | 31 | 31 | 25 |
| 2022 | 248 | 10 | 10 | 7 |
| 2023 | 246 | 36 | 33 | 18 |
| 2024 | 249 | 118 | 115 | 4 |
| 2025 | 249 | 102 | 98 | 0 |
| 2026 through 20 November | 220 | 50 | 43 | 0 |

Each account started with ₹10 lakh. The rule revision is r2 and the separately recorded execution convention is `stage3-v1`. The first three windows had no funded entries. [Dated window receipts](../data/stage3/r2-convention-replay/e722f88c7cc3e270/result.json)

## Quarantine and the 2024 account

The release ledger contains 12 genuine large moves. Each receipt identifies the ISIN, raw OHLC, prior close, dated NSE final, price-band sources, and their checksums. For a dynamic band breach, we verify that the open was inside the starting rupee band, the prior official list classified the stock as dynamically banded, and the same day's official end-of-day security master used the final close as its reference. The daily files establish these facts. They do not publish a tape of individual intraday band-expansion times. Release information first applies to the session close. Additional checks reject wrong prior closes, malformed candles, unsupported band classifications and discrepant band references. Releasing one price observation preserves independent corporate or price quarantines.

On 4 June 2024 ADANIPORTS opened at ₹1,558 and closed at ₹1,248.95 against ₹1,583.95 the prior session. Its initial published limits were ₹1,425.55 and ₹1,742.30. The dated end-of-day security master published ₹1,124.05 and ₹1,373.80 around the genuine close. The revised r2 portfolio followed different earlier fills and did not hold ADANIPORTS that day. A held-position regression exercises the released close, and the rerun directly verifies that the 2024 account reached 31 December. [NSE final](https://nsearchives.nseindia.com/content/historical/EQUITIES/2024/JUN/cm04JUN2024bhav.csv.zip) · [3 June band classification](https://nsearchives.nseindia.in/content/equities/sec_list_03062024.csv) · [4 June end-of-day master](https://nsearchives.nseindia.com/content/cm/NSE_CM_security_04062024.csv.gz)

The revised ledger retains 204 large moves without sufficient corroborating band evidence, including 24 in the pre-2014 warm-up. An old percentage label alone cannot recover an actual flexed rupee range. It also retains 49 unresolved action observations and three benchmark sessions with close-only data. The parent Stage 2 archive supplies the original raw observations and gaps. The derived view restores indicator history only for identified releases and priced rights. It independently rechecks the 1,764 modified session archives against NSE raw daily prices and binds all 3,190 daily manifests to the snapshot. [Release and rights register](../data/stage3/market/sources/stage3-events.json) · [Remaining quarantines](../data/stage3/market/sources/adjusted/quarantine.json) · [Data receipt](../data/stage3/preflight.json)

## Rights and the 2021 account

A non-subscribing holder keeps its original shares and receives zero cash for the rights. The holder incurs the economic dilution in the actual ex-rights quote. For historical indicators and active price instructions, derive the theoretical ex-rights price from the prior close, the entitlement ratio and the subscription price. Above-market offers use a factor of one. A held call retains its original paid cost and initial R denominator. A `corporate_action` event records the ISIN, date, source, terms, prior levels, zero cash and zero subscription. Unknown or unsupported simultaneous actions retain their own disposition.

The register contains 39 rights issues. Four earlier adjustments required a price-history correction, in SPARC, CANBK, TATASTEEL and IDEA. INDHOTEL's 11 November 2021 rights offered one share per nine at ₹150. Its ₹215.30 previous close implies ₹208.77 ex-rights and a factor of 0.96967023. A regression carries the shares through that event. The actual r2 rerun exited the holding months earlier. Its time exit was due on 22 July, and the 23 July open was ₹146 with an unknown lower band. The account sold 552 shares at ₹145.927 after five basis points of slippage, charged ₹98.90 in modeled fees and journaled the unknown price band. Known lower locks and absent trading volume still defer sales. All 67 such flagged sales are counted above. [2021 NSE notices](https://www.nseindia.in/api/corporates-corporateActions?index=equities&from_date=01-01-2021&to_date=31-12-2021) · [2021 account event receipt](../data/stage3/r2-convention-replay/e722f88c7cc3e270/result.json)

## Reproduction

From a materialized and certified Stage 2 archive, run the following in the repository. The output journals live under `data/stage3/r2-convention-replay/<snapshot-prefix>/walk-*/`.

```sh
uv run --frozen python -m thesistally.convention_market
uv run --frozen python -m thesistally.convention_replay
uv run --frozen pytest -q tests/test_stage3_conventions.py tests/test_quarantine_timing.py
```

Ordinary forward scans dated 21 November 2026 onward apply the same execution conventions when advancing previously issued calls. Forward rights records use dated NSE notices and actual prior raw closes. The scanner saves dated release evidence, retries unavailable final band sources and flags unresolved observations. Corporate events and flagged sales identify the convention applied. Existing call journals retain their historical revision and signals.
