"""Publish the four registered cases under the corrected opening-band convention."""

import argparse
import json
from pathlib import Path

from publish_stage3 import DOCS, ROOT, digest, link, n, read, table

REVISION = "stage3-entry-v2"
B_CASES = (("B10", "dip-rsi10"), ("B5", "dip-rsi5"))


def details(counts):
    return (
        ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in sorted(counts.items()))
        or "—"
    )


def perc(value):
    return n(100 * value) if value is not None else "—"


def observed_return(block, name, field):
    return n(block.get(name, {}).get(field))


def monthly_section(variants):
    body, findings = ["## A · Steady Momentum"], []
    source_checks = {
        (r["day"], r["isin"]): r["symbol"]
        for r in read(ROOT / "data/stage3/entry-price-gap-checks.json")["cases"]
    }
    for variant in ("A126", "A252"):
        e = variants[variant]
        q, eligible, observed = e["qualification"], e["eligible_period"], e["overall"]
        body.append("### " + variant)
        counted = q["complete_windows"]
        wins = q["winning_windows"]
        finding = (
            f"{variant} · {q['verdict']}. Won {wins}/{counted} eligible windows. "
            f"Eligible-period net CAGR {observed_return(eligible, 'net', 'cagr_pct')}% against "
            f"{observed_return(eligible, 'benchmark', 'cagr_pct')}% for the Nifty 200 TRI. "
            f"Average exposure across all observed sessions was {n(observed.get('mean_exposure_pct'))}%."
        )
        upper = q["optimistic_breadth"]
        if q["valuation_complete_windows"] < q["planned_windows"]:
            finding += (
                f" Even if every unpriced window won, it could win at most "
                f"{upper['wins_if_all_unpriced_windows_win']}/{upper['windows_if_all_unpriced_windows_count']} windows. "
                + (
                    "The breadth test remains open."
                    if upper["could_pass"]
                    else "That would still miss the two-thirds requirement."
                )
            )
        findings.append(finding)
        body.append(finding)
        body.append(
            table(
                [
                    "Cohort",
                    "Marked days",
                    "Average exposure %",
                    "CAGR %",
                    "TRI CAGR %",
                    "Drawdown %",
                    "TRI DD %",
                    "Invested days",
                    "Net return on invested days %",
                    "Same-day TRI return %",
                ],
                [
                    [
                        label,
                        s["sessions"],
                        n(s.get("mean_exposure_pct")),
                        observed_return(s, "net", "cagr_pct"),
                        observed_return(s, "benchmark", "cagr_pct"),
                        observed_return(s, "net", "max_drawdown_pct"),
                        observed_return(s, "benchmark", "max_drawdown_pct"),
                        s.get("while_invested", {}).get("sessions", 0),
                        observed_return(s, "while_invested", "net_return_pct"),
                        observed_return(s, "while_invested", "tri_return_pct"),
                    ]
                    for label, s in [
                        ("All observed", observed),
                        ("Qualification windows", eligible),
                    ]
                ],
            )
        )
        windows = sorted(e["windows"].items(), key=lambda pair: pair[1]["scheduled"])
        body.append(
            table(
                [
                    "Window / year",
                    "Actual end",
                    "Marked / scheduled days",
                    "Exposure %",
                    "Net %",
                    "TRI %",
                    "Invested days",
                    "Invested net %",
                    "Matched TRI %",
                    "In qualification",
                ],
                [
                    [
                        f"{key} / {s['scheduled'][0][:4]}",
                        s.get("last", "—"),
                        f"{s['sessions']} / {s['scheduled_sessions']}",
                        n(s.get("mean_exposure_pct")),
                        observed_return(s, "net", "return_pct"),
                        observed_return(s, "benchmark", "return_pct"),
                        s.get("while_invested", {}).get("sessions", 0),
                        observed_return(s, "while_invested", "net_return_pct"),
                        observed_return(s, "while_invested", "tri_return_pct"),
                        "Yes" if s["complete"] else "No",
                    ]
                    for key, s in windows
                ],
            )
        )
        body.append(
            table(
                [
                    "Window / year",
                    "Planned buys",
                    "Filled",
                    "Filled band unknown",
                    "Blocked",
                    "Data blocks",
                    "Data block %",
                    "Blocked by reason",
                    "Final pending buy instructions",
                ],
                [
                    [
                        f"{key} / {s['scheduled'][0][:4]}",
                        c["planned"],
                        c["filled"],
                        c["filled_band_unknown"],
                        c["blocked"],
                        c["data_blocked"],
                        n(c["data_blocked_pct"]),
                        details(c["blocked_by_reason"]),
                        c["last_close_pending_buy_instructions"],
                    ]
                    for key, s in windows
                    for c in [s["coverage"]]
                ],
            )
        )
        body.append(
            table(
                [
                    "Observed sessions",
                    "Days",
                    "Net %",
                    "TRI %",
                    "Exposure %",
                    "Annual vol %",
                    "Sharpe",
                    "Annual turnover %",
                    "Cost drag, CAGR points",
                ],
                [
                    [
                        label,
                        v["sessions"],
                        observed_return(v, "net", "return_pct"),
                        observed_return(v, "benchmark", "return_pct"),
                        n(v.get("mean_exposure_pct")),
                        observed_return(v, "net", "annual_volatility_pct"),
                        observed_return(v, "net", "sharpe"),
                        n(v.get("annualized_one_way_turnover_pct")),
                        n(v.get("cost_drag_cagr_pp")),
                    ]
                    for label, v in [
                        ("All", observed),
                        *[(f"Prior gate {g}", v) for g, v in e["by_regime"].items()],
                    ]
                ],
            )
        )
        loss = observed["loss_diagnosis"]
        body.append(
            f"The portfolio lost money in {loss['negative_months']} of {loss['observed_months']} observed months and trailed the TRI in {loss['underperforming_months']}."
        )
        for key, s in windows:
            if s["gap"]:
                gap = s["gap"]
                symbol = source_checks.get((gap["day"], gap["isin"]), gap["isin"])
                body.append(
                    f"{key} stopped on {gap.get('day')} for {symbol} ({gap['isin']}) because of the recorded gap “{gap['reason']}”. Its last verifiable mark was {s.get('last', 'unavailable')}."
                )
        body.append(
            f"Window breadth requires {q['winning_windows_required']} wins among the {counted} eligible windows. The higher-CAGR check {'passed' if q['cagr_pass'] else 'failed'} and the maximum-drawdown check {'passed' if q['drawdown_pass'] else 'failed'}."
        )
    return body, findings


def swing_section(variants):
    body, findings = ["## B · Dip in Steady Risers"], []
    for variant, key in B_CASES:
        for book in ("funded", "equal_risk"):
            e = variants[key][book]
            q = e["qualification"]
            gates = q["criteria"]
            o = e["overall"]
            name = f"{variant} · {book.replace('_', ' ')}"
            finding = (
                f"{name} · {q['verdict']}. {o['closed']} measured closes. "
                f"The {gates['closed_oos']['value']} counted mature closes averaged {n(gates['mean_r']['value'], 3)}R net "
                f"(90% interval {n(q['mean_net_r_ci90'][0], 3)} to {n(q['mean_net_r_ci90'][1], 3)}R). "
                f"{len(q['positive_counted_windows'])}/{q['breadth_denominator']} eligible windows contributed positive votes."
            )
            findings.append(finding)
            exclusions = [
                s["scheduled"][0][:4]
                for s in e["coverage_by_window"].values()
                if s["entry_coverage"]["coverage_excluded"]
            ]
            reason_counts = {}
            for sample in e["coverage_by_window"].values():
                for reason, count in sample["entry_coverage"]["data_reasons"].items():
                    reason_counts[reason] = reason_counts.get(reason, 0) + count
            funding_note = (
                f"Planned entries blocked by data ({details(reason_counts)}). "
                f"The data-coverage gate excludes {', '.join(sorted(exclusions)) or 'no years'}."
            )
            body.extend(["### " + name, finding, funding_note])
            vals = sorted(e["coverage_by_window"].items(), key=lambda pair: pair[1]["scheduled"])
            body.append(
                table(
                    [
                        "Window / signal year",
                        "Raw setups",
                        "Pre-order vetoes",
                        "Censored",
                        "Planned",
                        "Fills",
                        "Filled band unknown",
                        "Data blocks",
                        "Other blocks",
                        "Data block %",
                        "Coverage",
                    ],
                    [
                        [
                            f"{w} / {s['scheduled'][0][:4]}",
                            c["raw_signals"],
                            sum(c["pre_order_exclusions"].values()),
                            c["censored"],
                            c["planned_entries"],
                            c["filled"],
                            c["band_unknown_fills"],
                            c["blocked_data"],
                            c["blocked_other"],
                            perc(c["data_blocked_fraction"]),
                            "Excluded"
                            if c["coverage_excluded"]
                            else "Pass"
                            if c["planned_entries"]
                            else "No plans",
                        ]
                        for w, s in vals
                        for c in [s["entry_coverage"]]
                    ],
                )
            )
            body.append(
                table(
                    [
                        "Window / year",
                        "Data blocks by reason",
                        "Other planned-entry blocks by reason",
                        "Pre-order vetoes by reason",
                    ],
                    [
                        [
                            f"{w} / {s['scheduled'][0][:4]}",
                            details(c["data_reasons"]),
                            details(c["other_reasons"]),
                            details(c["pre_order_exclusions"]),
                        ]
                        for w, s in vals
                        for c in [s["entry_coverage"]]
                    ],
                )
            )
            rows = []
            for w, s in vals:
                year = s["scheduled"][0][:4]
                group = e["by_window"][w]
                annual = e["by_year"][year]
                for field in ("closed", "mean_gross_r", "mean_cost_r", "mean_net_r"):
                    if annual[field] != group[field]:
                        raise ValueError(f"Year and window disagree on {name} {year} {field}")
                rows.append(
                    [
                        f"{w} / {year}",
                        f"{s['scanned']} / {s['expected']}",
                        group["closed"],
                        group["mature_closed"],
                        n(group["mean_gross_r"], 3),
                        n(group["mean_cost_r"], 3),
                        n(group["mean_net_r"], 3),
                        n(group["profit_factor"]),
                        n(
                            group["account"].get(
                                "pnl_pct_of_reference", group["account"]["return_pct"]
                            )
                        ),
                        n(group["account"]["nifty200_buy_hold_return_pct"]),
                        "Yes" if w in q["counted_windows"] else "No",
                    ]
                )
            rows.append(
                [
                    "All observed",
                    "",
                    o["closed"],
                    o["mature_closed"],
                    n(o["mean_gross_r"], 3),
                    n(o["mean_cost_r"], 3),
                    n(o["mean_net_r"], 3),
                    n(o["profit_factor"]),
                    "",
                    "",
                    "",
                ]
            )
            body.append(
                table(
                    [
                        "Window / signal year",
                        "Marked / scheduled days",
                        "Closed trades",
                        "Mature closes",
                        "Gross mean R",
                        "Mean cost R",
                        "Net mean R",
                        "Net profit factor",
                        "Account return or equal-risk P&L / reference %",
                        "Matched TRI %",
                        "Counted cohort",
                    ],
                    rows,
                )
            )
            body.append(
                table(
                    ["Qualification", "Observed", "Required", "Passed"],
                    [
                        [
                            k,
                            details(v["value"])
                            if isinstance(v["value"], dict)
                            else n(v["value"], 3),
                            v["operator"] + " " + n(v["threshold"], 3),
                            "Yes" if v["passed"] else "No",
                        ]
                        for k, v in gates.items()
                    ],
                )
            )
            body.append(
                table(
                    ["Signal-date regime", "Closed trades", "Gross R", "Cost R", "Net R"],
                    [
                        [
                            gate,
                            group["closed"],
                            n(group["mean_gross_r"], 3),
                            n(group["mean_cost_r"], 3),
                            n(group["mean_net_r"], 3),
                        ]
                        for gate, group in e["by_regime"].items()
                    ],
                )
            )
            body.append(
                table(
                    ["Exit reason", "Closed", "Losers", "Gross R", "Net R"],
                    [
                        [
                            reason,
                            data["closed"],
                            data["losers"],
                            n(data["mean_gross_r"], 3),
                            n(data["mean_net_r"], 3),
                        ]
                        for reason, data in o["exit_reasons"].items()
                    ],
                )
            )
            body.append(
                table(
                    ["Distribution", "Count", "p10", "p25", "Median", "p75", "p90"],
                    [
                        [
                            label,
                            v["n"],
                            *[n(v.get(k)) for k in ("p10", "p25", "median", "p75", "p90")],
                        ]
                        for label, v in [
                            ("Favorable excursion (R)", o["mfe_r"]),
                            ("Adverse excursion (R)", o["mae_r"]),
                            (
                                "Sessions to losing stop trigger",
                                o["days_to_stop_losers"]["trigger_sessions"],
                            ),
                            (
                                "Sessions to losing stop sale",
                                o["days_to_stop_losers"]["exit_sessions"],
                            ),
                        ]
                    ],
                )
            )
            body.append(
                f"{o['losers_reached_1r']['reached']} of {o['losers_reached_1r']['losers']} losing trades first reached +1R. "
                f"Average fees cost {n(o['mean_fee_r'], 3)}R and adverse slippage cost {n(o['mean_slippage_r'], 3)}R per closed trade."
            )
            for w, s in vals:
                if s["valuation_gap"] or s["ledger_audit_discrepancies"]:
                    body.append(
                        f"{w} valuation gap {s['valuation_gap']}. Ledger discrepancies {s['ledger_audit_discrepancies']}."
                    )
    return body, findings


def publish(monthly, swing):
    a, b = read(monthly), read(swing)
    if not (a["proof"]["entry_band_convention"] == b["proof"]["entry_band_convention"] == REVISION):
        raise ValueError("Different entry conventions")
    benchmark = digest(ROOT / "data/stage3/benchmark.csv")
    if not (a["proof"]["benchmark_sha256"] == b["proof"]["benchmark_sha256"] == benchmark):
        raise ValueError("Different benchmark archives")
    prereg = digest(DOCS / "PREREGISTRATION.md")
    if not (a["proof"]["preregistration_sha256"] == b["proof"]["preregistration_sha256"] == prereg):
        raise ValueError("Different registrations")
    if a["proof"]["market_snapshot_sha256"] != b["proof"]["snapshot_sha256"]:
        raise ValueError("Different market snapshots")
    previous = read(ROOT / "data/backtests/stage3-study.json")
    previous_monthly = next(ROOT / key for key in previous["studies"] if "steady-momentum" in key)
    if digest(previous_monthly) != previous["studies"][str(previous_monthly.relative_to(ROOT))]:
        raise ValueError("The superseded monthly archive changed")
    old = read(previous_monthly)["variants"]
    primary = a["variants"]["A126"]
    early = [
        w for w in primary["windows"].values() if w["scheduled"][0][:4] in {"2017", "2018", "2019"}
    ]
    first_years = ", ".join(
        f"{w['scheduled'][0][:4]} {n(w['mean_exposure_pct'])}%"
        + ("" if w["valuation_complete"] else f" through {w['last']}")
        for w in sorted(early, key=lambda w: w["scheduled"])
    )
    correction = (
        f"The superseded A126 account averaged {n(old['A126']['overall']['mean_exposure_pct'])}% invested. "
        f"With the corrected buys, A126 averaged {n(primary['overall']['mean_exposure_pct'])}% over the sessions that could be valued. "
        f"Average exposure by year was {first_years}. "
        "The old and revised overall figures cover different numbers of marked sessions."
    )
    pa, fa = monthly_section(a["variants"])
    pb, fb = swing_section(b["variants"])
    body = [
        "# Stage 3 · Revised opening-band results",
        "## Decisions",
        correction,
        *fa,
        *fb,
        "B qualification requires a net expectancy above +0.25R. Missing surveillance classifications count against entry coverage. Each book has its own planned orders and coverage denominator.",
        "These four reruns supersede the earlier entry-blocked results. A126 and B10 are the primary definitions. A252 and B5 are the registered sensitivities. The test schedule starts in January 2017 and ends on 20 November 2026. Each window starts with ₹10 lakh. [Data convention and denominators](STAGE3_ENTRY_REVISION.md) · [Original strategy registration](PREREGISTRATION.md)",
        "## How to read the measurements",
        "A’s exposure is the average daily marked stock value divided by account equity. Its return while invested links net whole-account returns only on sessions carrying shares at the previous close or during that session. The TRI uses the same individual intervals, including opening or liquidation sessions. Invested returns in the tables are cumulative. A full-schedule return requires every scheduled mark.",
        "Coverage fails when more than 10% of planned entries with a scheduled opening opportunity were blocked by missing data. A valid unknown-band purchase counts as filled. B records raw setups and separate pre-order vetoes so that a declined or deferred order can be traced to its cause. Zero planned orders give an undefined percentage. A physically truncated book has a separate valuation gap.",
        "For B, each annual test window is also one calendar-year signal cohort. Net mean R equals gross mean R less actual modeled costs in original risk units. The annual/window tables include all closed outcomes. Qualification pools mature outcomes from complete, coverage-eligible windows with at least 20 mature closes. A terminal position still open or awaiting entry has no closed-trade return.",
        *pa,
        *pb,
        "## Evidence",
        "The machine results retain the full precision, period dates, coverage, decision criteria and source fingerprints. "
        + " · ".join(
            [
                link(monthly, "Monthly results"),
                link(swing, "Swing results"),
                link(ROOT / "data/stage3/benchmark.json", "Official TRI source"),
                link(ROOT / "data/stage3/entry-price-gap-checks.json", "Held closing-price checks"),
                link(ROOT / "data/stage3/entry-review.json", "Independent audit"),
            ]
        ),
        "From the repository root, reproduce the monthly replay with `uv run --frozen python -m thesistally.steady_replay` and the swing replay with `uv run --frozen python -m thesistally.stage3_dip`. The results include the snapshot and source hashes. Open Reports with **3** in the terminal to read the revised summary.",
        "The prior Stage 3 numbers are archived as superseded. [Earlier report](STAGE3_REPORT.md) · [Experiment register](EXPERIMENTS.md) · [Research and reviewer log](RESEARCH_LOG.md)",
    ]
    path = DOCS / "STAGE3_ENTRY_REPORT.md"
    path.write_text("\n\n".join(body) + "\n")
    output = ROOT / "data/backtests/stage3-study-entry-v2.json"
    payload = {
        "type": "stage3_study",
        "start": "2017-01-02",
        "end": "2026-11-20",
        "rules": "Stage 3 · Revised opening bands",
        "entry_band_convention": REVISION,
        "market_sha256": a["proof"]["market_snapshot_sha256"],
        "benchmark_sha256": benchmark,
        "preregistration_sha256": prereg,
        "supersedes": "stage3-study",
        "studies": {str(p.relative_to(ROOT)): digest(p) for p in (monthly, swing, path)},
        "terminal_summary": "Stage 3 · Revised opening bands\n\n"
        + "\n\n".join([correction, *fa, *fb])
        + "\n\nFull yearly returns and blocked orders: docs/STAGE3_ENTRY_REPORT.md",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(path)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("monthly", type=Path)
    parser.add_argument("swing", type=Path)
    args = parser.parse_args()
    publish(args.monthly.resolve(), args.swing.resolve())
