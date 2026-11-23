# Strategy Discovery Engine

**None of the seven preregistered filters passed.** In the six rolling validation windows from January 2021 through 20 November 2026, the best window count was three. A pass required four, independent strategy qualification, and a statistically supported improvement after correcting for the seven comparisons. [Preregistration](PREREGISTRATION.md#discovery-family-1--rolling-equal-risk-filters) · [Annual comparisons](../data/discovery/families/discovery-v1/window-comparisons.csv)

## What survived validation

The pullback filters improved the unfiltered mean in some years, but both lost money per trade after costs across their counted trades. The B5 bottom-rank filter earned +0.111R per trade across its counted windows, with 71 trades versus the required 100. The B10 stock picker had too few recent, settled signals per stock to supply a single comparable window.

| Strategy and registered filter | Counted filtered trades | Filtered mean net R | Annual windows won / 6 | Adequately sampled comparisons | Paired difference in R | BH-adjusted p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pullback, planned stop below 5% | 2,050 | −0.112 | 2 | 5 | +0.036 | 0.969 |
| Pullback, market breadth above 60% | 2,377 | −0.078 | 3 | 5 | +0.035 | 0.969 |
| B10, planned stop from 5% to below 7% | 189 | −0.038 | 1 | 3 | −0.089 | 1.000 |
| B10, weakest quintile of the top 40 | 156 | +0.048 | 2 | 3 | −0.016 | 1.000 |
| B10, strongest fifth of stocks by trailing three-year trade performance | 0 | — | 0 | 0 | — | 1.000 |
| B5, weakest quintile of the top 40 | 71 | +0.111 | 1 | 3 | +0.016 | 0.969 |
| B5, planned stop from 5% to below 7% | 47 | +0.042 | 1 | 1 | +0.184 | 1.000 |

A counted trade is a mature close in a complete, coverage-eligible window with at least 20 selected trades. The paired difference gives each adequate annual comparison equal weight. It measures the change in mean after-cost R relative to the full unfiltered book over the same annual dates. The B5 stop result reflects one adequately sampled year. Inference required at least two. Its p-value of 1 keeps it in the registered family. The validation JSON retains all 42 window results, both trade counts in each comparison, every qualification criterion and annual selection receipts. [Validation evidence](../data/discovery/families/discovery-v1/validation.json)

### How often would chance alone produce a discovery?

Using 10,000 joint simulations in which the filters have zero relative edge, an average of **0.25** hypotheses per simulated family reached nominal 5% significance. After BH correction, the average was **0.0417** and **3.35%** of simulated families had any corrected discovery. Five hypotheses had enough data for inference; two received p = 1. The 0.0417 figure is an upper bound for the average number that could also pass the absolute performance and annual-win hurdles in this model. These estimates are conditional on the observed samples, chosen filters, and block dependence model.

## EXPLORATORY — hypothesis generation only, not evidence

| Equal-risk book | All historical counted trades | Full-history slices | Populated slices | Development trades | Development slices |
| --- | ---: | ---: | ---: | ---: | ---: |
| Trend pullback r2 | 2,847 | 330 | 320 | 31 | 72 |
| Dip in Steady Risers, RSI(2) < 10 | 1,175 | 334 | 320 | 394 | 184 |
| Dip in Steady Risers, RSI(2) < 5 | 515 | 271 | 256 | 174 | 141 |
| **Total** | **4,537** | **935** | **896** | **599** | **397** |

A slice is one strategy, dimension and value within a period. The 397 development cells informed card selection. The 935 full-history cells were released after registration, giving 1,332 period-specific cell examinations in total. The trade rows overlap across slices and the B thresholds share signals. Each published cell includes its trade count, distinct signal dates, mean net R and 90% signal-date-clustered interval when estimable. A small interval on a selected historical winner cannot account for the other cells examined. [Pullback slices](../data/discovery/exploration/pullback/full/slices.csv) · [B10 slices](../data/discovery/exploration/b10/full/slices.csv) · [B5 slices](../data/discovery/exploration/b5/full/slices.csv)

The early development samples are concentrated in a single year per book. All 31 counted pullbacks came from 2020, with zero counted in each of 2017–2019. All 394 B10 and 174 B5 development trades came from 2017. The reviewer reproduced every slice and all 42 annual validation comparisons. [Independent review](../data/discovery/review/discovery-v1.md)

The stock key is the signal-date ISIN, with a dated ticker lookup alongside each CSV. Sector uses the industry recorded in that session’s membership file. Size uses the eligible AMFI six-month average full market capitalization in crores. Volatility uses 20 closing log returns, annualized with 252 sessions. Liquidity uses 20 sessions of median daily close times volume. The three measures are separately ranked across that day’s point-in-time Nifty 200, with ties kept together. A tercile requires at least 90% of the cross-section to be measured.

Regime uses the original entry gate, signal-date VIX and breadth. VIX boundaries are 15 and 22. Breadth boundaries are 40% and 60%. Year is the signal year. Q1 is strongest. Pullbacks use the signal’s archived cross-sectional relative-strength percentile; B uses five groups of eight in its original top-40 ranking. Stop distance is the percentage between the signal close and the original planned stop. Stop bands are below 5%, 5% to below 7%, and at least 7%.

**Coverage matters.** Every counted trade occurred with the original gate on. The gate-off cells therefore have zero trades and undefined returns. Unknown sector affects 451 counted pullbacks, 111 B10 trades and 50 B5 trades. Unknown size affects 49, 400 and 176 respectively. The first usable AMFI observation is 6 January 2018. The availability rule allows for the publication deadline and dated later revisions. The archived version of the June 2024 sheet could not establish timely availability for that period. The prior released sheet may be used for up to 370 days. Stock-level rank and size calculations include historical members that later left the index. [Size evidence and source dates](../data/discovery/sources/amfi/manifest.json)

## How the test works

1. **Reconcile historical inputs.** Read the original equal-risk journals in SQLite immutable, read-only mode. Check the pinned report and ledger hashes, original revisions, counts, and mean net R. Draw descriptors from signal-date membership and prices. Independent trades can overlap, so omitting one has no effect on another’s sizing or fill. Filtering this book keeps the original next-open execution, stops, costs, slippage and corporate-action cash flows. It does not simulate a pooled funded account.
2. **Generate and lock cards.** Only 2017–2020 outcomes informed the seven suggestions. The algorithm chose up to two sufficiently populated historical slices from different dimensions, and could add a prior-performance stock selector. The registration commit binds the hypothesis list, both B plan censuses, trade samples, calendars, coverage, qualification settings and inference code. Full-history exploratory results are already exposed for this family; further ideas drawn from them need fresh validation data. Published aggregate history for the original strategies was known before this work.
3. **Make a decision at each window start.** Fixed cards commit to an entry predicate before the window begins and evaluate it on each signal’s available data. The stock-selector card fixes an actual ISIN list before January trading, based on trades in the trailing three years with both final cash settlement and a completed holding horizon before the cutoff. The list stays fixed for the window. At least five prior counted trades are required per stock, with deterministic ties and rounding. Dates, selected names and sample sizes are in the receipt.
4. **Compare fairly.** Use the same scheduled annual dates for the selected and original equal-risk books. Strategy B uses its complete planned-entry census for signal exclusions, including data-blocked plans. A window must satisfy both the original and filtered coverage rules. If a blocked plan’s membership is uncertain, test the conservative upper bound before counting the window. Missing data blocking more than 10% of planned entries excludes a window. The older pullback archive predates that planned-entry census. Its published baseline completion and maturity rules apply. Ineligible and sparse years still contribute zero wins to the four-of-six requirement.
5. **Test the whole family together.** Apply the versioned r2 criteria to closed, mature calls. Counted windows need 20 trades. Qualification also needs 100 counted trades overall, mean net R above +0.25, positive lower bound of the 90% signal-date bootstrap interval, net rupee profit factor above 1.4, and positive means in two thirds of eligible windows. Test the relative effect using equal-weighted annual differences. Apply the same simulated sign to the paired contributions in each nonoverlapping 20-session exchange block across all cards. Use one-sided p-values and BH at 5% across every registered card. Correlated strategies, overlapping holding periods, few adequate windows and very sparse stock histories limit precision. The simulations quantify a specified null model. [Qualification config](../src/thesistally/validation/stage-2-v1.json) · [BH method](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x)

## Terminal commands

Run these commands inside the dedicated `ThesisTally-discovery` worktree. The first command lists the registered cards and their evidence paths.

```sh
uv run --frozen radar register
uv run --frozen radar discover pullback
uv run --frozen radar discover b10
uv run --frozen radar discover b5 --development
uv run --frozen radar validate pullback-b71cffc7
```

Add `--json` for machine-readable results. Validation of any one ID recomputes the complete registered family before returning its corrected result. Every hypothesis retains its proposal and subsequent decision as separate records. Repeating a command checks existing evidence. Conflicting bytes fail without replacing an earlier result.

Analyses run offline in a child process. The host allows writes under the linked worktree’s `data/discovery/`. On macOS the child uses `sandbox-exec`. On Linux it requires usable `bubblewrap`. The independent source directory defaults to the main checkout’s historical `data/`; `THESISTALLY_DISCOVERY_ARCHIVE` can point to another copy. The committed discovery samples can be read without regenerating the original ledgers. The A126 paper service runs from its own frozen release.

## Adding a strategy plugin

Expose a class through the Python entry-point group `thesistally.discovery_strategies`. Its name becomes the argument to `radar discover`. The class implements `load(archive)` and returns a pandas DataFrame and a mapping of annual window IDs to metadata. It can implement `plans(archive)` to return the planned-entry census used for coverage gating. This lets a strategy such as Range Bouncers supply its own archived equal-risk ledger without editing the core.

```toml
[project.entry-points."thesistally.discovery_strategies"]
my_strategy = "my_package.discovery:MyStrategy"
```

Each trade needs a stable identifier, ISIN in `stock`, `window`, ISO signal `day`, `closed_day` for final settlement, `full_horizon_day`, `year`, `status`, `mature`, `counted`, `net_r`, `net_inr` and the named exploratory dimensions. Include `symbol` for symbol lookups and source hashes in `frame.attrs["provenance"]`. Each window lists `start`, `end`, the ordered exchange `sessions`, `complete` and `coverage_eligible`. An adapter with a coverage census also supplies `planned_entries`, `blocked_data` and dated plan rows with the same selection fields and a boolean `blocked_data`. The built-in B adapter shows how to recover missing plan descriptors from the parallel signal journal. [Adapter contract](../src/thesistally/discovery_plugins.py) · [Historical adapter](../src/thesistally/discovery_trades.py)

`discover` records suggestions made on the development period. Before validating a new family, add its cards to `docs/PREREGISTRATION.md` and add the machine definition under `docs/discovery-families/` in the same commit. Include the original definition's SHA-256 in the text. The existing family file shows the schema and required fingerprints. Use unique identifiers and preregister the full family against an unexposed period. Existing evidence and decisions remain in the register. [Family definition](discovery-families/discovery-v1.json)
