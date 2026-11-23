"""Publish the registered study from its immutable results and corrected r2 coverage."""

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def n(value, digits=2):
    return (
        "—" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    )


def table(headers, rows):
    return "\n".join(
        "| " + " | ".join(str(v).replace("|", "/") for v in row) + " |"
        for row in (headers, ["---"] * len(headers), *rows)
    )


def link(path, title):
    return f"[{title}]({os.path.relpath(path, DOCS)})"


def orders(values):
    return ", ".join(f"{k} {v:,}" for k, v in sorted(values.items())) or "none"


def monthly_section(a):
    body = [
        "## A · Steady momentum",
        "The portfolio is marked daily. Annual windows compound by linking returns across fresh accounts. The comparator includes reinvested dividends. The cost estimate restores fees and adverse slippage on the same executed quantities. Turnover is one-way annualized. Any incomplete window contributes observations only through its last verifiable mark. Full-schedule interpretation requires every window to finish.",
    ]

    def arow(label, group):
        if not group["sessions"]:
            return [label, 0] + ["—"] * 9
        net, index = group["net"], group["benchmark"]
        return [
            label,
            group["sessions"],
            n(net["return_pct"]),
            n(index["return_pct"]),
            n(net["cagr_pct"]),
            n(index["cagr_pct"]),
            n(net["max_drawdown_pct"]),
            n(index["max_drawdown_pct"]),
            n(net["annual_volatility_pct"]),
            n(net["sharpe"]),
            n(group["mean_exposure_pct"]),
        ]

    headers = [
        "Period",
        "Sessions",
        "Net %",
        "TRI %",
        "Net CAGR %",
        "TRI CAGR %",
        "Net DD %",
        "TRI DD %",
        "Net vol %",
        "Net Sharpe",
        "Exposure %",
    ]
    for variant, entry in a["variants"].items():
        block, q = entry["overall"], entry["qualification"]
        body += [
            "### " + variant,
            table(
                headers,
                [arow("All observed", block)]
                + [
                    arow(
                        f"{k}, {w['scheduled'][0][:4]}"
                        + ("" if w["complete"] else " · interrupted"),
                        w,
                    )
                    for k, w in sorted(
                        entry["windows"].items(), key=lambda item: item[1]["scheduled"]
                    )
                ]
                + [arow("Prior gate " + k, v) for k, v in entry["by_regime"].items()],
            ),
        ]
        loss, sales = block["loss_diagnosis"], entry["realized_sales_excluding_income"]
        cash_years = [
            w["scheduled"][0][:4]
            for w in entry["windows"].values()
            if w["complete"] and w.get("mean_exposure_pct") == 0
        ]
        if cash_years:
            body.append(
                "The account stayed entirely in cash in "
                + ", ".join(sorted(cash_years))
                + ". The register below counts missed entries where the historical opening band could not be verified."
            )
        if q["complete_windows"] < q["planned_windows"]:
            upper = q["winning_windows"] + q["planned_windows"] - q["complete_windows"]
            body.append(
                f"Even if every incomplete window beat the benchmark, this result could have at most "
                f"{upper} winning windows against the requirement of {q['winning_windows_required']}."
            )
        body += [
            f"The benchmark had {n(block['benchmark']['annual_volatility_pct'])}% annual volatility and a {n(block['benchmark']['sharpe'])} Sharpe. Annual one-way turnover was {n(block['annualized_one_way_turnover_pct'])}%, fees were ₹{block['fee_inr']:,.0f}, and adverse slippage was ₹{block['slippage_inr']:,.0f}. Costs reduced CAGR by {n(block['cost_drag_cagr_pp'])} percentage points. The strategy lost money in {loss['negative_months']}/{loss['observed_months']} observed months and trailed the index in {loss['underperforming_months']}. Its peak-to-trough loss ran from {block['net']['drawdown_peak']} to {block['net']['drawdown_trough']}. {sales['losing']} of {sales['sales']} sales realized a loss on shares before distributions.",
            f"Qualification requires seven wins, higher CAGR and drawdown ≤1.2× the index. The respective checks were {q['winning_windows']}/10, {'pass' if q['cagr_pass'] else 'fail'} and {'pass' if q['drawdown_pass'] else 'fail'}.",
            "Fills and exceptions. "
            + orders(entry["fills"])
            + ". "
            + orders(entry["order_exceptions"])
            + ".",
            "Corporate events. " + orders(entry["company_events"]) + ".",
        ]
        for k, w in entry["windows"].items():
            if w["gap"]:
                body.append(
                    f"{k} closed its last verified session on {w.get('last', '—')}. Evidence gap: {w['gap']}."
                )
    body.append(
        "Each annual row above is both a test window and a calendar-year breakdown. Gate subsets link the days following each corresponding signal-date gate and annualize on 252 observed sessions. Their returns cover different sequences of dates."
    )
    return body


def swing_section(cohort):
    body = [
        "## B · Dip in steady risers",
        "Each trade risks the distance from its slipped purchase to its initial three-ATR stop. Gross R restores fees and adverse slippage while keeping the actual orders. Trade summaries include every measured close. Qualification considers only completed holding windows in complete test windows with at least 20 mature closes. The equal-risk book assigns ₹5,000 to each eligible risk independently. Its marked profit or loss can be expressed relative to the initial ₹10 lakh reference.",
    ]
    body += [
        table(
            [
                "Run",
                "Book",
                "Plans in book",
                "Bought",
                "Closed",
                "Mature",
                "Win %",
                "Gross R",
                "Cost R",
                "Net R",
                "PF",
                "Mean sessions",
            ],
            [
                [
                    v,
                    l,
                    o["plans"],
                    o["bought"],
                    o["closed"],
                    o["mature_closed"],
                    n(100 * o["win_rate"] if o["win_rate"] is not None else None),
                    n(o["mean_gross_r"]),
                    n(o["mean_cost_r"]),
                    n(o["mean_net_r"]),
                    n(o["profit_factor"]),
                    n(o["mean_holding_sessions"]),
                ]
                for v, l, e in cohort
                for o in [e["overall"]]
            ],
        )
    ]
    for variant, label, e in cohort:
        o, qual = e["overall"], e["qualification"]
        coverage = e["coverage_by_window"].values()
        events = {
            k: sum(w["events"][k]["events"] for w in coverage)
            for k in ("exit_deferred", "exit_unknown_price_band")
        }
        marks = e["marked_account_summary"]
        exposure = marks.get("mean_exposure_pct", marks.get("mean_gross_exposure_pct_of_reference"))
        dd = marks.get(
            "worst_window_max_drawdown_pct",
            marks.get("worst_window_max_peak_to_trough_loss_pct_of_reference"),
        )
        body += [
            f"### {variant} · {label}",
            (
                f"Average exposure was {n(exposure)}%. The largest within-window peak-to-trough loss was {n(dd)}%. "
                f"The journals contain {events['exit_deferred']} deferred-sale events and {events['exit_unknown_price_band']} slipped opening sales with unknown lower bands. "
                "Equal-risk exposure and peak-to-trough loss use the fixed capital reference."
            ),
            table(
                [
                    "Window / signal year",
                    "Closed",
                    "Mature",
                    "Net R",
                    "Marked %¹",
                    "Nifty TRI %",
                    "Sessions scanned / scheduled",
                ],
                [
                    [
                        f"{k}, {w['account']['start'][:4]}",
                        w["closed"],
                        w["mature_closed"],
                        n(w["mean_net_r"]),
                        n(w["account"].get("pnl_pct_of_reference", w["account"]["return_pct"])),
                        n(w["account"]["nifty200_buy_hold_return_pct"]),
                        f"{e['coverage_by_window'][k]['scanned']}/{e['coverage_by_window'][k]['expected']}",
                    ]
                    for k, w in sorted(
                        e["by_window"].items(), key=lambda item: item[1]["account"]["start"] or ""
                    )
                ],
            ),
            "¹ Funded shows window return after costs. Equal risk shows cumulative position P&L divided by the fixed ₹10 lakh reference. Benchmark returns start at the preceding session’s closing index.",
            table(
                [
                    "Gate",
                    "Signal-cohort closes",
                    "Signal-cohort net R",
                    "Exit-cohort closes",
                    "Exit-cohort net R",
                    "Marked sessions after gate",
                    "Marked P&L ₹",
                ],
                [
                    [
                        gate,
                        row["closed"],
                        n(row["mean_net_r"]),
                        ex["closed"],
                        n(ex["mean_net_r"]),
                        mark["sessions"],
                        n(mark["marked_net_pnl_inr"], 0),
                    ]
                    for gate, row in e["by_regime"].items()
                    for ex in [e["by_exit_regime"][gate]]
                    for mark in [e["marked_account_summary"]["regime_pnl"][gate]]
                ],
            ),
            table(
                ["Exit reason", "Closed", "Losses", "Gross R", "Net R"],
                [
                    [k, r["closed"], r["losers"], n(r["mean_gross_r"]), n(r["mean_net_r"])]
                    for k, r in o["exit_reasons"].items()
                ],
            ),
            table(
                ["Excursion or timing", "Count", "p10", "p25", "Median", "p75", "p90"],
                [
                    [key, row["n"], *[n(row[p]) for p in ("p10", "p25", "median", "p75", "p90")]]
                    for key, row in [
                        ("Favorable excursion, R", o["mfe_r"]),
                        ("Adverse excursion, R", o["mae_r"]),
                        (
                            "Sessions to losing stop trigger",
                            o["days_to_stop_losers"]["trigger_sessions"],
                        ),
                        ("Sessions to losing stop sale", o["days_to_stop_losers"]["exit_sessions"]),
                    ]
                ],
            ),
            f"{o['losers_reached_1r']['reached']} of {o['losers_reached_1r']['losers']} losing trades first reached +1R while the position was open. Across all measured closes the average cost was {n(o['mean_fee_r'])}R in fees and {n(o['mean_slippage_r'])}R in adverse slippage.",
            "Entry and endpoint dispositions. "
            + orders(o["statuses"])
            + ". "
            + orders(e["status_reasons"]["missed"])
            + ".",
            table(
                ["Qualification criterion", "Measured value", "Requirement", "Result"],
                [
                    [
                        key,
                        (", ".join(f"{k}={v}" for k, v in val["value"].items()) or "none")
                        if isinstance(val["value"], dict)
                        else n(val["value"], 3),
                        f"{val['operator']} {n(val['threshold'], 3)}",
                        "Pass" if val["passed"] else "Fail",
                    ]
                    for key, val in qual["criteria"].items()
                ],
            ),
        ]
        for k, w in e["coverage_by_window"].items():
            if w["valuation_gap"] or w["ledger_audit_discrepancies"]:
                body.append(
                    f"Coverage issue in {k}. {w['valuation_gap']}. Ledger audit discrepancies {w['ledger_audit_discrepancies']}."
                )
    return body


def report(steady, dip, corrected):
    a, b, fix = map(read, (steady, dip, corrected))
    if len(fix["windows"]) != 10 or not fix["complete"]:
        raise ValueError("The ten corrected r2 controls must finish before publication.")
    receipt = ROOT / "data/stage3/preflight.json"
    cert = read(receipt)
    if not (
        cert["snapshot_sha256"]
        == a["proof"]["market_snapshot_sha256"]
        == b["proof"]["snapshot_sha256"]
        == fix["snapshot_sha256"]
    ):
        raise ValueError("The studies and r2 control use different market snapshots.")
    if not (
        a["proof"]["preregistration_sha256"]
        == b["proof"]["preregistration_sha256"]
        == digest(DOCS / "PREREGISTRATION.md")
    ):
        raise ValueError("Study inputs differ from the committed registration.")
    if not (
        a["proof"]["benchmark_sha256"]
        == b["proof"]["benchmark_sha256"]
        == digest(ROOT / "data/stage3/benchmark.csv")
    ):
        raise ValueError("The official total-return index changed between studies.")
    cohort = [
        (key, label, b["variants"][revision][book])
        for key, revision in [("B10", "dip-rsi10"), ("B5", "dip-rsi5")]
        for label, book in [("Funded", "funded"), ("Equal risk", "equal_risk")]
    ]
    body = ["# Stage 3 walk-forward study", "## Decisions"]
    headlines = []
    for variant, entry in a["variants"].items():
        qual, block = entry["qualification"], entry["overall"]
        headlines.append(
            f"**{variant} · {qual['verdict']}.** Beat the Nifty 200 total-return index in {qual['winning_windows']}/10 scheduled windows. {qual['complete_windows']}/10 windows reached the scheduled end. The observed net CAGR was {n(block['net']['cagr_pct'])}% versus {n(block['benchmark']['cagr_pct'])}% for the index over the same observed sessions."
        )
    for variant, label, entry in cohort:
        qual, o = entry["qualification"], entry["overall"]
        c = qual["criteria"]
        headlines.append(
            f"**{variant} {label.lower()} · {qual['verdict']}.** {o['closed']} measured closes, {c['closed_oos']['value']} mature closes in counted windows. Their net mean was {n(c['mean_r']['value'])}R, with a 90% date-clustered interval of [{n(qual['mean_net_r_ci90'][0])}, {n(qual['mean_net_r_ci90'][1])}]R."
        )
    body += headlines + [
        "The primary definitions are A126 and B10. A252 and B5 are the two registered sensitivities. Every window started with ₹10 lakh. January 2017 through 20 November 2026 supplied the ten scheduled tests, with three preceding years of history for each start. The latest year ends at the final available session. [Registered mechanics and thresholds](PREREGISTRATION.md)",
    ]
    primary = b["variants"]["dip-rsi10"]["equal_risk"]
    qualification = primary["qualification"]
    counts = qualification["mature_closed_by_window"]
    by_date = sorted(
        primary["coverage_by_window"], key=lambda k: primary["coverage_by_window"][k]["scheduled"]
    )
    counted = [
        primary["coverage_by_window"][k]["scheduled"][0][:4]
        for k in by_date
        if k in qualification["counted_windows"]
    ]
    body += [
        (
            f"For the primary dip rule, the equal-risk book supplied enough mature closes to count "
            f"{len(counted)}/{len(counts)} windows ({', '.join(counted) or 'none'}). Its profit factor was "
            f"{n(qualification['criteria']['profit_factor']['value'])}, against a requirement above 1.4. "
            "The number of trades and the number of independent market periods answer different questions."
        ),
    ]
    monthly = a["variants"]["A126"]["overall"]
    trades = primary["overall"]
    sensitivity = b["variants"]["dip-rsi5"]["funded"]["qualification"]
    body.extend(
        [
            (
                f"The monthly primary held an average of {n(monthly['mean_exposure_pct'])}% in shares. "
                "Both monthly portfolios spent 2017–2019 entirely in cash as their planned purchases lacked verified opening bands. "
                "Both beat the index in 2024."
            ),
            (
                f"Across all {trades['closed']} measured equal-risk B10 closes, gross expectancy was "
                f"{trades['mean_gross_r']:+.3f}R and the average cost was {trades['mean_cost_r']:.3f}R, leaving "
                f"{trades['mean_net_r']:+.3f}R net. The tighter B5 funded setting had a positive confidence interval, "
                f"but its counted mean of {sensitivity['criteria']['mean_r']['value']:+.3f}R fell below the +0.25R hurdle. "
                f"It had a positive counted mean in {len(sensitivity['positive_counted_windows'])}/10 windows against seven required."
            ),
            "## Corrected execution and data",
        ]
    )
    body.append(
        "All 2,451 scheduled r2 test sessions were marked. The corrected accounts registered 67 slipped opening sales with unknown lower bands. "
        "INDHOTEL sold 552 shares at ₹145.927 on 23 July 2021. The 4 June 2024 ADANIPORTS close was verified at ₹1,248.95. "
        "The revised account carried no ADANIPORTS position that day, so a held-position regression separately exercised that mark. [Execution audit and exchange sources](STAGE3_PLUMBING.md)"
    )
    body += [
        f"The market receipt covers {cert['sessions']:,} sessions and {cert['releases']} verified price-move releases. It records {cert['rights_issues']} rights issues. A holder declines subscription, keeps the existing share count and receives zero entitlement cash. An unknown opening lower band permits a slipped sale with an explicit journal flag. Positive volume and observed locked circuits still control execution. "
        + link(receipt, "Market receipt")
        + " · "
        + link(corrected, "Corrected r2 accounts and case events")
    ]
    body += [
        table(
            ["r2 funded window", "Scheduled end", "Sessions scanned / expected", "Reached end"],
            [
                [
                    v["window"],
                    v["last"],
                    f"{v['scanned']} / {v['expected']}",
                    "Yes" if v["complete"] else "No",
                ]
                for v in fix["windows"]
            ],
        )
    ]
    body += monthly_section(a)
    body += swing_section(cohort)
    body += [
        "## Reproduction and data limits",
        "To open this study, run `uv run radar`, select **Reports** with **3**, and open **Stage 3**. The experiment register links the four decisions below. The earlier strategies are retired from new calls. All three historical detectors and their Stage 2 reports are preserved.",
        "Entry permissions depend on dated exchange data. Historical opening ranges were recoverable for 147,270 of 639,334 member observations in the parent archive. Missing purchase bands particularly constrain the early years. Zero exposure in a rising market reduces portfolio returns and provides little direct evidence about the stocks ranked then. A rejected or inconclusive study tests the registered rule under these data and execution constraints. [Historical data gaps](DATA_GAPS.md)",
        "Every eligible B purchase had a measurable path through closure or the end of its window. Six excluded shadow records across the two variants encountered unresolved corporate actions. They represent two CANBK setups rejected by the results blackout and one VEDL setup rejected by the regime gate. Neither book included those signals in its decision cohort. [Research and review log](RESEARCH_LOG.md)",
        "Source files contain full-precision results, the scored periods and the execution receipts. "
        + " · ".join(
            link(p, t)
            for p, t in (
                (steady, "Monthly study JSON"),
                (dip, "Swing study JSON"),
                (corrected, "r2 coverage"),
                (ROOT / "data/stage3/benchmark.json", "Official TRI sources"),
            )
        ),
        f"Registered definitions at `ad4dec1`. Market SHA-256 `{cert['snapshot_sha256']}`. Study IDs `{a['identity']}` and `{b['identity']}`. [Experiment register](EXPERIMENTS.md) · [Research and review log](RESEARCH_LOG.md)",
    ]
    body += [
        "To reproduce the numbers using the certified local market, use the source commits named in the monthly and swing receipts. Restore the recorded holder evidence, then run the corresponding study.",
        "```sh\nuv run --frozen python -m thesistally.steady_actions\nuv run --frozen python -m thesistally.steady_replay\nuv run --frozen python -m thesistally.stage3_dip\n```",
    ]
    path = DOCS / "STAGE3_REPORT.md"
    path.write_text("\n\n".join(body) + "\n")
    results = {
        "type": "stage3_study",
        "start": fix["windows"][0]["first"],
        "end": fix["windows"][-1]["last"],
        "rules": "Stage 3 · Steady momentum and Dip in steady risers",
        "market_sha256": cert["snapshot_sha256"],
        "studies": {str(p.relative_to(ROOT)): digest(p) for p in (steady, dip, corrected, path)},
        "terminal_summary": "Stage 3 · Walk-forward results\n\n"
        + "\n\n".join(line.replace("**", "") for line in headlines)
        + "\n\nDetails in docs/STAGE3_REPORT.md",
    }
    (ROOT / "data/backtests/stage3-study.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("steady", "dip", "corrected"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    report(args.steady.resolve(), args.dip.resolve(), args.corrected.resolve())
