# Stage 3 opening-band data revision

Registered before the revised replay, 21 November 2026. The owner superseded the missing-upper-band assumption in the Stage 3 results at 6b127ce. The four experiment IDs, rankings, gates, costs, exits and holding periods are those in the [strategy registration](PREREGISTRATION.md). This is data revision `stage3-entry-v2`.

An otherwise executable buy uses the official positive opening quote, tradable security and normal adverse slippage. A verified upper lock prevents a buy. If an opening upper limit is unavailable or the actual opening quote exceeds the reported limit, use the opening quote and record `band_unknown` on the executed buy. No intraday high, low or closing price establishes whether the opening order could execute. A known nontrading security or zero-volume session still cannot supply a fill.

## Window coverage

Count distinct planned purchases with an opening opportunity in the window. A counts new purchases and top-ups, excluding sales, gate-off instructions and unchanged weights. B counts submitted buys in each book and otherwise-eligible setups blocked by missing mandatory eligibility data. For those setups, check independently known vetoes before assigning a data block. Show signals, actual fills, data rejections, execution/rule/capital rejections and pending orders separately. Do not count an unknown-band fill as a data rejection. Do not charge the last close's plan against coverage when its next open is beyond the window.

Coverage fails exactly when ten times the number blocked by data exceeds the number planned. Exactly 10% passes. Zero plans produce an undefined fraction and a visible zero-opportunity window. Keep under-sampled periods visible. Exclude windows failing coverage from qualification means, CIs, profit factors, linked A and benchmark returns and the denominator for the two-thirds breadth criterion. Disclose the schedule and the reduced denominator. An unresolved valuation interrupts the affected account and must be reported separately; a missing stock price cannot be replaced with a flat or zero return.

## Measurements

Report each window and calendar year under both settings. A's invested-session return compounds the whole-account net returns on dates with an opening holding or intraday stock exposure. Link the official Nifty 200 TRI over the identical individual close-to-close intervals, including each window's preceding close for the first interval. Also report calendar-period performance and average exposure across all observed days. B's year and window cohorts report measured closes, gross mean R, fee/slippage cost R and net mean R. Qualification remains based on the registered mature-trade cohort. Retain gross turnover, realized fills and data gaps in the machine evidence. A return after a corporate action requires the same dated holder evidence as the earlier study.

The earlier study is superseded for decisions due to entry blocking; its records, prices, journals and original source commits remain available. This rerun consumes zero additional strategy experiments. [Earlier study](STAGE3_REPORT.md) · [Experiment register](EXPERIMENTS.md)
