"""Publish qualification using only the registered walk-forward accounts."""

import argparse
import json
from pathlib import Path

from publish_stage3 import ROOT, digest, link, n, read, table


def publish(result_path, evidence_path):
    docdir = ROOT / "docs"
    result, evidence = read(result_path), read(evidence_path)
    if result["proof"]["preregistration_sha256"] != digest(docdir / "PREREGISTRATION.md"):
        raise ValueError("Different preregistration")
    if result["proof"].get("close_evidence_sha256") != digest(evidence_path):
        raise ValueError("Price verification receipt differs from the run")
    lines = ["# A126 candidate review", "## Closing prices"]
    rows = [
        [
            c["day"],
            c["symbol"],
            n(c["prior_close"]),
            n(c["close"]),
            "Verified" if c["verified"] else "Unverified",
        ]
        for c in evidence["cases"]
    ]
    lines.extend(
        [
            table(["Session", "Stock", "Previous close ₹", "NSE close ₹", "Decision"], rows),
            "The evidence file records each quote supplier, exchange identity, corporate-action search and adjustment used to compare share units. A release takes effect at the close of the affected session. Every later ranking uses the restored price history available at that date. "
            + link(evidence_path, "Official records and discrepancies"),
            "The five originally disputed closes matched NSE cash and Groww. Kite matched three of the five close pairs exactly. The reopened 2020 path also held ICICIPRULI on 23 and 25 March. On both sessions the NSE cash final, Kite and Upstox agreed. [Case-by-case prices and disagreements](HELD_CLOSE_VERIFICATION.md)",
            "## Registered results",
            "Each of the ten walk-forward windows starts at ₹10 lakh. Net returns include fees and adverse slippage. The Nifty 200 gross total-return index uses the same observed dates. To qualify, a setting must beat the index in two-thirds of eligible windows, have higher linked CAGR and keep maximum drawdown within 1.2 times the benchmark. A physically incomplete history cannot qualify.",
        ]
    )
    headline = []
    for variant in ("A126", "A252"):
        v = result["variants"][variant]
        q, em = v["qualification"], v["eligible_period"]
        all_obs = v["overall"]
        wins, count = q["winning_windows"], q["complete_windows"]
        summary = (
            f"{variant} {q['verdict']}. {q['valuation_complete_windows']}/10 windows reached their end. "
            f"It won {wins}/{count} eligible windows (required {q['winning_windows_required']}). "
            f"Net CAGR {n(em['net']['cagr_pct'])}% versus {n(em['benchmark']['cagr_pct'])}% for the TRI. "
            f"Maximum drawdown {n(em['net']['max_drawdown_pct'])}% versus {n(em['benchmark']['max_drawdown_pct'])}%. "
            f"Average exposure {n(em['mean_exposure_pct'])}% on eligible dates."
        )
        headline.append(summary)
        lines.extend(
            [
                "### " + variant,
                summary,
                table(
                    [
                        "Window",
                        "Last mark",
                        "Marked / scheduled sessions",
                        "Eligible",
                        "Net return %",
                        "TRI %",
                        "Max DD %",
                        "TRI DD %",
                        "Avg exposure %",
                        "One-way turnover %¹",
                        "Fees ₹",
                        "Slippage ₹",
                    ],
                    [
                        [
                            w["scheduled"][0][:4],
                            w.get("last", "—"),
                            f"{w['sessions']} / {w['scheduled_sessions']}",
                            "Yes" if w["complete"] else "No",
                            n(w["net"]["return_pct"]),
                            n(w["benchmark"]["return_pct"]),
                            n(w["net"]["max_drawdown_pct"]),
                            n(w["benchmark"]["max_drawdown_pct"]),
                            n(w["mean_exposure_pct"]),
                            n(w["one_way_turnover_pct"]),
                            n(w["fee_inr"], 0),
                            n(w["slippage_inr"], 0),
                        ]
                        for w in sorted(v["windows"].values(), key=lambda w: w["scheduled"])
                    ],
                ),
                (
                    f"Across observed annual-account sessions, average exposure was {n(all_obs['mean_exposure_pct'])}%. "
                    f"Modeled fees totaled ₹{all_obs['fee_inr']:,.0f} and adverse slippage totaled ₹{all_obs['slippage_inr']:,.0f}, for a combined ₹{all_obs['fee_inr'] + all_obs['slippage_inr']:,.0f}."
                ),
            ]
        )
        for w in v["windows"].values():
            if w["gap"]:
                g = w["gap"]
                lines.append(
                    f"The {w['scheduled'][0][:4]} account stopped at {g['day']} for {g['isin']}. The ledger records “{g['reason']}”."
                )
    lines.extend(
        [
            "¹ One-way turnover is half the sum of gross buy and sell notionals divided by preceding equity on each session, added over the observed window. The 2026 row ends on 20 November. Fees include modeled transaction charges and taxes on execution. The slippage column estimates adverse execution against the opening quote.",
            "## Tax and forward paper observation",
            "The after-tax calculation uses continuous lots and the actual sale dates across Indian financial years. The benchmark defers its capital-gains calculation until its single terminal sale. [After-tax comparison](A_MOMENTUM_TAX.md)",
            "The candidate is the 126-session primary registration. The 252-session sensitivity remains a historical comparison. The frozen A126 paper account starts after the anchor close of 20 November 2026 and counts only subsequent completed sessions. Run `radar report --since-freeze` to see the prospective account and its date-matched TRI comparator.",
            "Both dip variants are parked as **small gross edge, below hurdle after costs**. Their historical trades are in the experiment register. [Experiment register](EXPERIMENTS.md)",
            "## Reproduction and evidence",
            "Run `python -m thesistally.close_study --task build`, then `--task annual` and `--task continuous` in the project's uv environment. The continuous ledger supplies the tax comparison. Qualification uses only the ten fresh annual accounts. "
            + link(result_path, "Full-precision monthly results"),
        ]
    )
    file = docdir / "A126_CANDIDATE_REPORT.md"
    file.write_text("\n\n".join(lines) + "\n")
    payload = {
        "type": "stage3_study",
        "start": "2017-01-02",
        "end": "2026-11-20",
        "rules": "Monthly momentum · independent closing-price review",
        "market_sha256": result["proof"]["market_snapshot_sha256"],
        "preregistration_sha256": result["proof"]["preregistration_sha256"],
        "studies": {
            str(p.relative_to(ROOT)): digest(p) for p in [file, result_path, evidence_path]
        },
        "terminal_summary": "\n\n".join(
            [
                "A126 candidate review",
                *headline,
                "Both dip variants are parked as small gross edge, below hurdle after costs. Find the annual comparisons in docs/A126_CANDIDATE_REPORT.md. Run radar report --since-freeze for the prospective A126 account.",
            ]
        ),
    }
    target = ROOT / "data/backtests/steady-close-study.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(file)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("result", type=Path)
    ap.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "data/stage3/close-verification/evidence-held-pair.json",
    )
    args = ap.parse_args()
    publish(args.result.resolve(), args.evidence.resolve())
