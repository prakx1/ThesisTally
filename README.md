# ThesisTally

ThesisTally tests trading ideas on the Nifty 200 stocks that belonged to the index at the time. Its local terminal tracks paper trades after costs. **Paper trading only. No broker orders. No investment advice.**

[![ThesisTally’s Prices tab. The Nifty indices sit above a searchable stock table showing price, daily change, open, high and low. Keyboard shortcuts run along the bottom.](docs/figures/terminal-prices.png)](docs/figures/terminal-prices.png)

*Prices at the NSE close on 22 November 2024. Click the screenshot to open it at full size.*

[Quick start](#quick-start) · [Architecture](#architecture) · [Full research diary](docs/RESEARCH_SUMMARY.md)

## What the experiments found

### Monthly momentum beat the index in five of ten windows

A126 buys stocks that rose steadily over 126 sessions. Across the linked annual tests, it grew **19.00% a year after costs**, versus **14.28%** for the Nifty 200 including dividends. Its worst peak-to-trough fall was **25.64%**, versus **38.03%**. It needed seven wins to qualify and remains a paper candidate. [Annual results](docs/A126_CANDIDATE_REPORT.md)

![A126’s annual lead or shortfall against the index from 2017 to 20 November 2026. Its largest leads came in 2023 and 2024.](docs/figures/a126-window-returns.png)

### After tax, A126 came close to holding the index

A separate test followed one investor through the tax years. Selling all holdings at the end gave annualised after-tax returns of **13.61% for A126**, **13.34% for the index** and **11.50% for A252**, the 252-session variant. [Tax assumptions and results](docs/A_MOMENTUM_TAX.md)

![Before- and after-tax returns for A126, A252 and the index in the continuous-account study.](docs/figures/momentum-after-tax.png)

### Daily swing strategies

These results give each trade the same risk budget. **1R** is the amount initially risked.

| Idea | What we found |
| --- | --- |
| Pullbacks and volatility breakouts | After costs, 2,847 counted pullbacks averaged **−0.075R** and 314 counted breakouts averaged **−0.133R**. Both also lost before costs across all closes. [Baseline](docs/BASELINE_REPORT.md) |
| Buying near a 52-week high | The rule detected 41 setups across training and testing and closed **zero trades**. It was retired alongside pullbacks and breakouts. [Signal funnel](docs/BASELINE_REPORT.md) · [Decision](docs/PREREGISTRATION.md#experiment-budget-and-retirement) |
| Buying dips in rising stocks | The two variants averaged **+0.042R** and **+0.072R** after costs. Both fell short of the **+0.25R** hurdle. [Dip study](docs/STAGE3_ENTRY_REPORT.md) |
| Buying near the bottom of a range | Both exits were tested on the same **48 mature trades**. No eligible year reached the 20 trades required to count. [Range study](docs/RANGE_BOUNCERS_REPORT.md) |
| Filtering past signals | **None of seven** filters passed on later years. The best beat its unfiltered strategy in three of six windows. Four wins were required. [Discovery results](docs/DISCOVERY.md) |

### Earnings signals need better filing history

**Zero of ten** years met the 80% filing-coverage requirement. Five were needed to test earnings surprises. The strategy’s return is unknown. New filings are being collected with their publication times. [Coverage audit](docs/STRATEGY_D2_XBRL_COVERAGE.md) · [Forward collector](docs/D2_FORWARD_COLLECTION.md)

![Verified first-filing coverage by year. The highest was 69.4% in 2024, below the 80% threshold.](docs/figures/d2-filing-coverage.png)

## Architecture

The daily scanner and historical replay share execution rules. The frozen monthly portfolio keeps a separate ledger. [Architecture guide](docs/current-design.md) · [Data gaps](docs/DATA_GAPS.md)

![System architecture showing the dated data archive, shared swing engine, frozen monthly paper account and results-filing collector.](docs/figures/thesistally-architecture.svg)

### From an exchange record to a paper trade

```mermaid
flowchart TD
    A["NSE daily price files, dated membership and company notices"] --> B["Check prices, corporate actions and when each fact became public"]
    B --> C["At the close: apply the recorded rule to eligible stocks"]
    C --> D["At the planned open: simulate the fill or record why it was missed"]
    D --> E["Journal cash, holdings, costs and exits"]
    E --> F["Compare with the benchmark on the same dates"]
    F --> G["Freeze the candidate and observe new sessions on paper"]
```

## Quick start

Clone the repository, then run these commands with Python 3.11 or newer and uv installed. [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

```sh
uv sync --frozen
uv run --frozen radar
```

Press **3** for Reports, select **Monthly momentum**, then press **Enter**. Saved reports open without credentials. For live quotes, choose **Log in to Kite (F6)**, then **Check status (F5)** to verify your session. [Login and daily operation](docs/operations.md#kite-login-and-status)

![The Reports tab with the monthly momentum study selected.](docs/figures/terminal-reports.png)

| Installation | Opened report |
| --- | --- |
| [![Terminal installation and command help.](docs/figures/install-terminal.png)](docs/figures/install-terminal.png) | [![The monthly momentum report open in the terminal.](docs/figures/terminal-report-detail.png)](docs/figures/terminal-report-detail.png) |

The public checkout includes the terminal and saved studies. Full historical replays and the frozen account require the original research history and certified data archive.

Keep live calls private. Register new strategy rules before testing. [Experiment register](docs/EXPERIMENTS.md) · [Strategy plugin guide](docs/DISCOVERY.md#adding-a-strategy-plugin) · [MIT license](LICENSE)
