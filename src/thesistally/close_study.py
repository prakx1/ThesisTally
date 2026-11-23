"""Materialize corroborated closing prices in an isolated, fingerprinted research archive."""

import argparse
import fcntl
import json
from contextlib import closing
from datetime import date
from pathlib import Path

from . import convention_market, market, steady_actions, steady_replay
from .evaluation import walk_windows
from .pilot_data import checked_link, write_json
from .steady_momentum import open_journal


def accepted_closes(path, ledger):
    cases = json.loads(path.read_bytes())["cases"]
    jumps = {
        (r["day"], r["isin"]): r for r in ledger if r["reason"] == "unexplained_move_over_20pct"
    }
    accepted, seen = {}, set()
    for case in cases:
        key = (case["day"], case["isin"])
        if key in seen or key not in jumps or not isinstance(case.get("verified"), bool):
            raise market.MarketError(f"Invalid independent close decision {key}")
        seen.add(key)
        if any(case[field] != jumps[key][field] for field in ("close", "prior_close", "symbol")):
            raise market.MarketError(f"Closing evidence and quarantined quote disagree {key}")
        if case["verified"]:
            accepted[key] = case
    return accepted


def build(data, evidence):
    from .close_evidence import certify

    certify(data, evidence)
    parent = data / "stage3"
    digest = market.sha(evidence.read_bytes())
    root = parent / "close-verification/views" / digest[:20]
    root.mkdir(parents=True, exist_ok=True)
    for name in ("sources", "universes", "baseline"):
        checked_link(data / name, root / name)
    support = root / "stage3/evidence"
    support.mkdir(parents=True, exist_ok=True)
    checked_link(parent / "evidence/holder-events", support / "holder-events")
    for file in (parent / "evidence").glob("sec_list_*.csv"):
        checked_link(file, support / file.name)
    ready = root / "stage3/preflight.json"
    with (root / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        archived = json.loads(ready.read_bytes()) if ready.exists() else None
        if archived and archived.get("independent_close_input_sha256") != digest:
            raise market.MarketError("Different corroboration receipt in this archive")
        # Git stores the small audit receipt. A fresh clone still needs the actual
        # session files, and an interrupted local materialization can be resumed.
        if not archived or any(
            not (root / "stage3/market/sessions" / d / "manifest.json").is_file()
            for d in archived["session_manifests"]
        ):
            convention_market.prepare(root, close_evidence=evidence)
            if archived and archived != json.loads(ready.read_bytes()):
                raise market.MarketError("Rebuilt close study differs from the published archive")
    if json.loads(ready.read_bytes()).get("independent_close_input_sha256") != digest:
        raise market.MarketError("Derived archive does not match the requested decisions")
    return root


def continuous(root, proof, out):
    dates = list(map(date.fromisoformat, proof["session_manifests"]))
    tests = walk_windows(dates, 3, 1, None)
    days = [d for _, group, _ in tests for d in group]
    prior = dates[dates.index(days[0]) - 1]
    archive = root / "stage3/market"
    path = out / "tax-continuous.sqlite"
    out.mkdir(parents=True, exist_ok=True)
    definition = {
        "purpose": "tax-report-only",
        "snapshot": proof["snapshot_sha256"],
        "holder_sha256": market.sha(steady_actions.CLOSE_HOLDERS.read_bytes()),
        "code_sha256": steady_replay.source_hash(),
    }
    with closing(open_journal(path)) as db:
        gaps = steady_replay.step(archive, db, "tax-continuous", days, prior, definition)
        ends = {k: v for k, v in db.execute("SELECT run,max(day) FROM sessions GROUP BY run")}
    if (
        steady_replay.source_hash() != definition["code_sha256"]
        or convention_market.certify(root)["snapshot_sha256"] != definition["snapshot"]
    ):
        raise market.MarketError("Inputs changed during the continuous after-tax study")
    write_json(
        out / "continuous.json",
        {
            "anchor": str(prior),
            "scheduled_last": str(days[-1]),
            "last_by_book": ends,
            "valuation_gaps": gaps,
            "journal_sha256": market.sha(path.read_bytes()),
            **definition,
        },
    )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--task", choices=["build", "annual", "continuous"], required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    base = data / "stage3/close-verification"
    evidence = args.evidence or base / "evidence-held-pair.json"
    root = build(data, evidence)
    if args.task == "build":
        print(root, flush=True)
        return
    out = base / "runs" / root.name
    if args.task == "annual":
        print(steady_replay.replay(root / "stage3/market", data / "stage3/benchmark.csv", out))
    else:
        proof = convention_market.certify(root)
        print(
            continuous(
                root,
                proof,
                out
                / "continuous"
                / market.sha(
                    (
                        steady_replay.source_hash()
                        + market.sha(steady_actions.CLOSE_HOLDERS.read_bytes())
                    ).encode()
                )[:20],
            )
        )


if __name__ == "__main__":
    main()
