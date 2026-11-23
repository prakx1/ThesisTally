"""Publish the capital-gains-only comparison from a continuous, certified monthly run."""

import argparse
from pathlib import Path

from publish_stage3 import DOCS, ROOT, digest, link, n, read, table

from thesistally.equity_tax import DEFAULT_CONFIG
from thesistally.market import staging
from thesistally.pilot_data import write_json
from thesistally.steady_tax import compare


def publish(continuous, market, benchmark, config, output, report):
    receipt = read(continuous)
    ledger = continuous.parent / "tax-continuous.sqlite"
    result = compare(ledger, market, benchmark, config)
    if (
        receipt["journal_sha256"] != result["journal_sha256"]
        or digest(ledger) != result["journal_sha256"]
        or read(benchmark.with_suffix(".json"))["sha256"] != result["benchmark_sha256"]
    ):
        raise ValueError("Continuous account or TRI checksum disagrees with its receipt")
    if read(market.parent / "preflight.json")["snapshot_sha256"] != receipt["snapshot"]:
        raise ValueError("Tax source market does not match the account snapshot")
    result.update(
        tax_source_files={
            str(path.relative_to(ROOT)): digest(path)
            for path in (
                Path(__file__).resolve(),
                ROOT / "src/thesistally/equity_tax.py",
                ROOT / "src/thesistally/steady_tax.py",
            )
        },
        continuous_receipt_sha256=digest(continuous),
        market_sha256=receipt["snapshot"],
        scheduled_last=receipt["scheduled_last"],
        valuation_gaps=receipt["valuation_gaps"],
    )
    rows = []
    body = [
        "# Monthly momentum after capital-gains tax",
        "This comparison starts each strategy with ₹10 lakh at the 30 December 2016 close. The strategy holds the same shares and makes the same trades as a continuous paper account through its last verifiable session. Each benchmark holds units tracking the official Nifty 200 gross total-return index from that starting close and sells once at the strategy’s endpoint. The original walk-forward qualification uses fresh annual accounts.",
    ]
    for variant, data in result["variants"].items():
        run = data["run"]
        if (
            data["end"] != receipt["last_by_book"][run]
            or data["start_prior_close"] != receipt["anchor"]
        ):
            raise ValueError(f"Tax dates do not match certified {run}")
        for label, x in [
            ("strategy • actual sales", data["strategy"]),
            ("strategy • illustrative terminal sale", data["hypothetical_terminal_liquidation"]),
            ("TRI • single terminal sale", data["tri_proxy"]),
        ]:
            if x:
                rows.append(
                    [
                        variant,
                        label,
                        data["end"],
                        n(x["before_tax"], 0),
                        n(x["tax"], 0),
                        n(x["after_tax"], 0),
                        n(x["before_tax_cagr_pct"]),
                        n(x["after_tax_cagr_pct"]),
                        n(x["investor_xirr_pct"]),
                    ]
                )
    body.append(
        table(
            [
                "Book",
                "Treatment",
                "Last close",
                "Pre-tax value ₹",
                "Cumulative CG tax ₹",
                "After-tax value ₹",
                "Pre-tax CAGR %",
                "Accrued-tax CAGR %",
                "Investor XIRR %",
            ],
            rows,
        )
    )
    body.append(
        "The actual-sales row deducts annual capital-gains liabilities and values open holdings at their closing prices. The illustrative sale closes every available unit at the same final close less five basis points and the existing sale costs, recalculates FIFO tax for that financial year and credits no refund for unused losses. It supplies a terminal-sale comparison with the TRI. Restricted outstanding entitlements prevent an illustrative sale."
    )
    body.append(
        "Investor XIRR accounts for when taxes leave the investor’s wallet. It uses a ₹10 lakh outflow at entry, external capital-gains payments each 31 March, and final proceeds less that partial year’s assessment. The portfolio keeps its simulated share count. Accrued-tax CAGR deducts cumulative rupees from the closing mark and therefore omits the timing of those payments. XIRR uses actual days divided by 365. The established CAGR convention uses 365.2425. For the benchmark, this day-count difference is the only distinction between the rates."
    )
    for variant, data in result["variants"].items():
        gap = receipt["valuation_gaps"].get(data["run"])
        body.append("## " + variant)
        msg = (
            f"The book has {data['sessions']:,} marked sessions through {data['end']}. "
            f"Terminal stocks were worth ₹{data['terminal_stock_value']:,.0f}. "
            f"Actual fills incurred ₹{data['fees']:,.0f} in fees and ₹{data['slippage']:,.0f} in slippage, "
            f"for total transaction costs of ₹{data['fees'] + data['slippage']:,.0f}."
        )
        if gap:
            msg += f" Valuation stopped on {gap['day']} for {gap['isin']} ({gap['reason']}). No subsequent return or tax is assigned to this account."
        body.append(msg)
        body.append(
            table(
                [
                    "Financial year",
                    "Sessions",
                    "One-way turnover %",
                    "Fees ₹",
                    "Slippage ₹",
                    "Realised ST gain ₹",
                    "Realised LT gain ₹",
                    "ST tax ₹",
                    "LT tax ₹",
                    "Cess ₹",
                    "Total tax ₹",
                    "Strategy value after accrued tax ₹",
                    "TRI tax ₹",
                    "TRI value after accrued tax ₹",
                ],
                [
                    [
                        f"{year}–{(year + 1) % 100:02}",
                        d["sessions"],
                        n(d["one_way_turnover_pct"]),
                        *[
                            n(d[k], 0)
                            for k in (
                                "fees",
                                "slippage",
                                "realised_short_gain",
                                "realised_long_gain",
                                "short_tax",
                                "long_tax",
                                "cess",
                                "tax",
                                "after_accrued_tax",
                                "benchmark_tax",
                                "benchmark_after_accrued_tax",
                            )
                        ],
                    ]
                    for year, d in data["per_fy"].items()
                ],
            )
        )
        body.append(
            f"Dividend stripping disallowed ₹{data['loss_disallowed_dividend']:,.0f} of otherwise deductible losses. Bonus stripping transferred ₹{data['loss_deferred_bonus']:,.0f} of cost to remaining free shares. Fractional proceeds were ₹{data['corporate_fraction_cash']:,.2f}."
        )
    src = result["rates"]["sources"]
    body.extend(
        [
            "## Tax and cost assumptions",
            "The taxpayer is a resident individual investing in STT-paid, delivery-based listed equity. The model assumes other income uses the basic exemption and personal rebates, the portfolio is below the surcharge threshold and tax returns are filed on time. Dividend income taxes, interest on advance-tax instalments and personal deductions are outside this capital-gains comparison. A separate cash ledger deducts each FY’s tax from wealth at year-end, with the last partial FY accrued through the endpoint. That tax ledger earns zero interest and does not resize later orders. The pre-tax trade ledger and capital allocated to orders stay as simulated.",
            "The financial year runs April–March. Each short-term or long-term tax column gives tax before education cess. Listed equity held for more than 12 months is long-term. Before April 2018 qualifying long-term gains were exempt. From April 2018 the LTCG rate was 10% over ₹1 lakh. STCG was 15% through 22 July 2024. Transfers from 23 July 2024 use 20% short term and 12.5% long term. The ₹1.25 lakh aggregate long-term allowance applies to all FY 2024–25 and each subsequent year shown. The model uses 3% cess through FY 2017–18 and 4% thereafter. The 2025 Act retains these listed-equity rates under sections 196 and 198 for tax year 2026–27. "
            + f"[2018 transition]({src['grandfather_and_2018_rates']}) · [Cess change]({src['education_cess']}) · [Finance (No. 2) Act 2024]({src['finance_act_2024']}) · [Income-tax Act 2025 as amended in 2026]({src['finance_act_2026']})",
            "Realised gains use demat FIFO, actual slipped prices and deductible transfer/acquisition costs. Paid STT stays in portfolio costs and is excluded from tax deductions. Splits carry unit-adjusted original cost and holding dates. Bonus units start at zero cost on the published allotment date, subject to bonus-stripping adjustments. The ledger checks whole-unit holdings against every daily journal state. For fractional proceeds the comparison treats the custodian’s market sale as a listed-equity disposal and applies the documented simulation’s proceeds date. "
            + f"[Listed-equity tax and FIFO]({src['holding_period_and_fifo']}) · [Transfer expenses]({src['capital_gains_deductions']})",
            "The January 2018 grandfathered basis for a pre-February-2018 long-term share is the greater of actual cost and the lower of the 31 January traded high and gross sale value. If there was no trade that day, use the last prior traded high. The TRI is modeled as one accumulating equity-oriented fund unit series; its January 31 reference NAV is the published daily TRI. The modeled redemption pays the 0.001% equity-fund sale STT, which reduces proceeds and cannot reduce taxable gains. The proxy assumes zero tracking error, fund fees and other investor dealing charges. "
            + f"[Section 55 acquisition basis]({src['grandfather_fmv']}) · [Fund redemption STT]({src['fund_redemption_stt']})",
            "Portfolio-only losses offset gains annually. Long-term losses offset taxable long-term gains. Short-term losses may offset either class. Unused eligible losses expire after eight following financial years. The mixed-rate FY uses available setoffs and the single long-term allowance against higher-rate eligible gains first. The published JSON gives carryforwards, exemption used, grandfathered prices and source hashes. "
            + f"[Loss carryforward]({src['carryforward']}) · [Exempt dividends]({src['dividends']}) · [Dividend and bonus stripping]({src['stripping']}) · [Extension to listed shares]({src['share_bonus_extension']})",
            "One-way turnover sums half of each session’s purchased and sold notional divided by its preceding equity. The fiscal-year table reports observed turnover, including partial first and last years. Slippage is the adverse difference from the opening quote, already reflected in cash, and is shown separately from explicit fees. Totals and tax apply to the original unrounded amounts. Tables display rounded rupees.",
            "## Reproduction",
            "The script reads and hashes the certified continuous journal, tax table and TRI, with a read-only SQLite connection. To recompute with another tax table, pass its path with `--config`. "
            + " · ".join(
                [
                    link(config, "Configurable historical rates"),
                    link(output, "Machine report"),
                    link(continuous, "Continuous account receipt"),
                    link(benchmark.with_suffix(".json"), "Official TRI source and checksum"),
                ]
            ),
        ]
    )
    with staging(report.parent) as tmp:
        (tmp / "report.md").write_text("\n\n".join(body) + "\n")
        write_json(output, result)
        (tmp / "report.md").replace(report)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("continuous", type=Path, help="Certified continuous.json receipt")
    parser.add_argument("market", type=Path, help="Derived stage3/market folder")
    parser.add_argument("--benchmark", type=Path, default=ROOT / "data/stage3/benchmark.csv")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=ROOT / "data/backtests/steady-tax-v1.json")
    parser.add_argument("--report", type=Path, default=DOCS / "A_MOMENTUM_TAX.md")
    args = parser.parse_args()
    publish(
        args.continuous.resolve(),
        args.market.resolve(),
        args.benchmark.resolve(),
        args.config.resolve(),
        args.output.resolve(),
        args.report.resolve(),
    )
