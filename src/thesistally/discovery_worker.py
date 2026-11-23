"""Offline discovery command implementations, invoked in the isolated child process."""

import csv
import io
import json
import math
import os
import sys
from pathlib import Path

import pandas as pd

from . import discovery_plugins as adapters
from . import discovery_stats as stats
from .discovery_guard import REPO
from .discovery_registry import (
    LABEL,
    digest,
    encoded,
    families,
    git,
    identifier,
    proposed,
    register,
    write_once,
)

DEVELOPMENT = (2017, 2018, 2019, 2020)
ROOT = REPO / "data/discovery"


def archive_root():
    if path := os.environ.get("THESISTALLY_DISCOVERY_ARCHIVE"):
        return Path(path).resolve(strict=True)
    common = git(REPO, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common.decode().strip()).parent / "data"


def suggestions(strategy, report):
    """Choose two distinct well-populated predictors, plus a historical stock selector if feasible."""
    cells = report["slices"]
    if not report["trades"]:
        return []
    overall = (
        sum(
            r["mean_net_r"] * r["trades"] for r in cells if r["dimension"] == "year" and r["trades"]
        )
        / report["trades"]
    )
    minimum = max(20, math.ceil(report["trades"] / 10))
    pool = [
        r
        for r in cells
        if r["dimension"] not in ("year", "stock", "gate")
        and r["value"].casefold() != "unknown"
        and minimum <= r["trades"] < report["trades"]
        and r["signal_dates"] >= 8
        and r["mean_net_r"] > overall
    ]
    pool.sort(key=lambda r: (-r["mean_net_r"], r["dimension"], r["value"]))
    cards, used = [], set()
    for row in pool:
        field, value = row["dimension"], row["value"]
        if field in used:
            continue
        used.add(field)
        cards.append(
            {
                "strategy": strategy,
                "description": f"{strategy} entries in {field} = {value}",
                "filters": [{"field": field, "values": [value]}],
            }
        )
        if len(cards) == 2:
            break
    names = [
        r
        for r in cells
        if r["dimension"] == "stock" and r["trades"] >= 5 and r["value"] != "unknown"
    ]
    if len(names) >= 10:
        cards.append(
            {
                "strategy": strategy,
                "description": f"{strategy} entries in the top fifth of stocks by prior three-year mean net R, minimum five observed trades",
                "selector": {
                    "field": "stock",
                    "min_trades": 5,
                    "top_fraction": 0.2,
                    "lookback_years": 3,
                },
            }
        )
    for card in cards:
        card["id"] = strategy + "-" + digest(encoded(card))[:8]
    return cards


def discover(strategy, development_only, registrations):
    frame, receipt = adapters.sample(ROOT, archive_root(), strategy)
    registered = [
        card
        for family in registrations.values()
        for card in family["hypotheses"]
        if card["strategy"] == strategy
    ]
    dev = stats.explore(frame, years=DEVELOPMENT)
    cards = suggestions(strategy, dev)
    for card in cards:
        write_once(
            ROOT / "proposals" / (card["id"] + ".json"),
            encoded(
                {
                    "label": LABEL,
                    "hypothesis": card,
                    "strategy": strategy,
                    "status": "proposed",
                    "sample_sha256": receipt["sha256"],
                    "development_years": DEVELOPMENT,
                }
            ),
        )
    part = "development" if development_only or not registered else "full"
    report = dev if part == "development" else stats.explore(frame)
    payload = {
        **report,
        "strategy": strategy,
        "period": part,
        "sample_sha256": receipt["sha256"],
        "hypothesis_cards": registered or cards,
        "card_source": "2017–2020 development sample only",
    }
    directory = ROOT / "exploration" / strategy / part
    write_once(directory / "slices.json", encoded(payload))
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "label",
            "dimension",
            "value",
            "trades",
            "signal_dates",
            "mean_net_r",
            "ci90_lower",
            "ci90_upper",
        ],
    )
    writer.writeheader()
    for row in report["slices"]:
        values = dict(row)
        values["ci90_lower"], values["ci90_upper"] = values.pop("ci90")
        writer.writerow(values)
    write_once(directory / "slices.csv", buf.getvalue().encode())
    # An ISIN may have more than one symbol in a period. Display every dated alias.
    if {"stock", "symbol"} <= set(frame):
        population = frame.loc[frame.counted & frame.mature]
        if part == "development":
            population = population[population.year.isin(DEVELOPMENT)]
        aliases = population.groupby(["stock", "symbol"], as_index=False).agg(
            first_signal=("day", "min"), last_signal=("day", "max")
        )
        aliases.insert(0, "label", LABEL)
        write_once(directory / "stock-symbols.csv", aliases.to_csv(index=False).encode())
    return payload, directory


def method_hashes():
    from . import baseline_qualification

    return {
        "stats": digest(Path(stats.__file__).read_bytes()),
        "qualification": digest(Path(baseline_qualification.__file__).read_bytes()),
        "qualification_config": digest(baseline_qualification.CONFIG_PATH.read_bytes()),
    }


def validate(cid, registrations):
    identifier(cid)
    matched = [f for f in registrations.values() if any(c["id"] == cid for c in f["hypotheses"])]
    if len(matched) != 1:
        raise ValueError(f"{cid} has no committed family preregistration")
    family = matched[0]
    proposed(ROOT, family)
    if method_hashes() != family["method_sha256"]:
        raise ValueError("Validation method differs from the preregistered method")
    cards = family["hypotheses"]
    frames, windows, plans, sample_hashes = {}, {}, {}, {}
    for strategy in sorted({card["strategy"] for card in cards}):
        frame, receipt = adapters.sample(ROOT, archive_root(), strategy)
        frames[strategy], windows[strategy] = frame, receipt["windows"]
        sample_hashes[strategy] = receipt["sha256"]
        sample_hashes[strategy + "-receipt"] = digest(
            (ROOT / "samples" / strategy / "receipt.json").read_bytes()
        )
        if p := receipt.get("plans_sha256"):
            path = ROOT / "samples" / strategy / "plans.parquet"
            binary = path.read_bytes()
            if p != digest(binary):
                raise ValueError("Plan data differs from the registered sample receipt")
            plans[strategy] = pd.read_parquet(io.BytesIO(binary))
            sample_hashes[strategy + "-plans"] = p
    if sample_hashes != family["sample_sha256"]:
        raise ValueError("Family registered for a different discovery sample")
    directory = ROOT / "families" / family["family"]
    output = directory / "validation.json"
    result = stats.validate_family(
        frames,
        windows,
        cards,
        first_year=family["first_validation_year"],
        alpha=family["alpha"],
        plans_by_strategy=plans,
    )
    result.update(
        family=family["family"],
        sample_sha256=sample_hashes,
        registration_commit=family["registration_commit"],
        registration_sha256=family["registration_sha256"],
    )
    write_once(output, encoded(result))
    rows = []
    for hypothesis in result["hypotheses"]:
        for window in hypothesis["windows"]:
            rows.append(
                {
                    "hypothesis": hypothesis["id"],
                    "start": window["start"],
                    "end": window["end"],
                    "filtered_trades": window["filtered"]["trades"],
                    "filtered_mean_net_r": window["filtered"]["mean_net_r"],
                    "unfiltered_trades": window["unfiltered"]["trades"],
                    "unfiltered_mean_net_r": window["unfiltered"]["mean_net_r"],
                    "difference_r": window["difference_r"],
                    "coverage_eligible": window["coverage_eligible"],
                    "meets_comparison_sample": window["meets_comparison_sample"],
                    "win": window["won"],
                }
            )
    write_once(
        directory / "window-comparisons.csv", pd.DataFrame(rows).to_csv(index=False).encode()
    )
    for row in result["hypotheses"]:
        event = {
            "id": row["id"],
            "family": family["family"],
            "status": "passed" if row["passed"] else "failed",
            "evidence": str(output.relative_to(REPO)),
            "evidence_sha256": digest(encoded(result)),
            "registration_commit": family["registration_commit"],
            "registration_sha256": family["registration_sha256"],
        }
        write_once(ROOT / "register" / row["id"] / "02-validation.json", encoded(event))
    return next(r for r in result["hypotheses"] if r["id"] == cid), result


def main():
    import argparse
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["discover", "register", "validate"])
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--development", action="store_true")
    opts = parser.parse_args()
    with (ROOT / ".worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registrations = families()
        if opts.command == "discover":
            data, directory = discover(identifier(opts.identifier), opts.development, registrations)
            if opts.json:
                print(encoded(data).decode())
            else:
                print(LABEL)
                print(
                    f"{opts.identifier} · {data['period']} · {data['trades']} counted trades · {data['slices_examined']} slices"
                )
                print(directory / "slices.csv")
                for c in data["hypothesis_cards"]:
                    print(c["id"], c["description"])
        elif opts.command == "register":
            rows = register(ROOT, registrations)
            known = {r["id"] for r in rows}
            for path in sorted((ROOT / "proposals").glob("*.json")):
                row = json.loads(path.read_bytes())
                if row["hypothesis"]["id"] not in known:
                    rows.append(
                        {
                            "id": row["hypothesis"]["id"],
                            "status": "proposed",
                            "hypothesis": row["hypothesis"],
                            "evidence": str(path.relative_to(REPO)),
                            "label": LABEL,
                        }
                    )
            if opts.json:
                print(encoded(rows).decode())
            else:
                for row in rows:
                    print(row["id"], row["status"], row["evidence"])
                if not rows:
                    print("No hypotheses recorded.")
        elif any(
            f.get("kind") == "preregistered_strategy"
            and any(c["id"] == opts.identifier for c in f["hypotheses"])
            for f in registrations.values()
        ):
            from .results_registry import recorded

            row = recorded(identifier(opts.identifier))
            if opts.json:
                print(encoded(row).decode())
            else:
                print(f"{row['id']} · {row['evaluation']} · {row['evidence']}")
                for book, decision in row["books"].items():
                    print(f"{book}: {decision['verdict']}")
        else:
            selected, result = validate(opts.identifier, registrations)
            if opts.json:
                print(encoded({"requested": selected, "family": result}).decode())
            else:
                print(
                    f"{opts.identifier} · {'passed' if selected['passed'] else 'failed'} · {selected['windows_won']}/{len(selected['windows'])} windows won (requires {selected['windows_required']}) · BH-adjusted p {selected['p_bh']:.4f}"
                )
                print(
                    f"{result['family_size']} hypotheses corrected together · {len(result['passed_ids'])} passed"
                )
                print(ROOT / "families" / result["family"] / "validation.json")


if __name__ == "__main__":
    from .discovery_guard import launch

    print(launch(sys.argv[1], sys.argv[2:]))
