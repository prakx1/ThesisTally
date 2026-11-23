"""Inputs to the registered Range Bouncers study, isolated from all paper accounts."""

import argparse
import fcntl
import json
from pathlib import Path

import httpx

from . import (
    close_evidence,
    convention_market,
    dip_bonus,
    dip_holder_market,
    market,
    steady_actions,
)
from .pilot_data import write_json

SNAPSHOT = "9033de9d28bdc23dcbe7207e63f029502bb83be4230c37dd30bb6a8027c8dea5"


def certified_view(data):
    evidence = data / "stage3/close-verification/evidence-held-pair.json"
    digest = market.sha(evidence.read_bytes())
    root = evidence.parent / "views" / digest[:20]
    receipt = json.loads((root / "stage3/preflight.json").read_bytes())
    if (
        receipt["snapshot_sha256"] != SNAPSHOT
        or receipt.get("independent_close_input_sha256") != digest
        or not receipt["complete"]
    ):
        raise market.MarketError("Range Bouncers requires the registered seven-release archive.")
    return root, receipt, evidence


def supplemental_bonuses(data, *, restore):
    """Verify the actual exchange documents before admitting supplemental holder terms."""
    items, evidence = [], []
    folder = data / "range-bouncers/evidence"
    for path in sorted(Path(__file__).with_name("revisions").glob("range-holder-*.json")):
        register = json.loads(path.read_bytes())
        sources = {r["url"] for r in register["evidence"]}
        for row in register["actions"]:
            if (
                row["type"] != "bonus"
                or {row["bonus"]["source_url"], row["bonus"]["listing_source"]} - sources
            ):
                raise market.MarketError("Range holder terms have missing source evidence")
            items.append(row)
        for record in register["evidence"]:
            destination = folder / record["file"]
            if not destination.exists() and restore:
                response = httpx.get(record["url"], timeout=45, follow_redirects=True)
                response.raise_for_status()
                payload = steady_actions.evidence_bytes(record, response.content)
                if market.sha(payload) != record["sha256"]:
                    raise market.MarketError(
                        f"Downloaded range holder evidence changed: {record['file']}"
                    )
                folder.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            if (
                not destination.is_file()
                or market.sha(destination.read_bytes()) != record["sha256"]
            ):
                raise market.MarketError(
                    f"Missing or changed range holder evidence: {record['file']}"
                )
            evidence.append(record)
    return items, evidence


def holder_inputs(root):
    base = dip_holder_market.split_receipt(root, revised=True)
    extra = steady_actions.verified(root, steady_actions.CLOSE_HOLDERS.read_bytes())
    for row in extra["actions"]:
        if row["type"] == "stock_split" and "bonus" not in row:
            base["actions"].append(row)
        elif row["type"] == "bonus":
            base["bonuses"].append(row)
        else:
            raise market.MarketError("Unsupported supplemental holder terms")
    base["evidence"].extend(extra["evidence"])
    dip_bonus.parse(json.dumps(base["bonuses"]).encode())
    # A combined split/bonus cannot be represented as a simple same-ISIN bonus.
    # Make the exception visible to the replay before any affected position is valued.
    composite = [
        r
        for r in steady_actions.holder_register(root, revised=True)["actions"]
        if r["type"] == "stock_split" and r.get("bonus")
    ]
    return base, composite


def prepare_market(data, *, certify=False):
    data = Path(data).resolve()
    root, parent, evidence = certified_view(data)
    directory = data / "range-bouncers/inputs"
    directory.mkdir(exist_ok=True, parents=True)
    with (directory / ".lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        if certify:
            close_evidence.certify(data, evidence)
            convention_market.certify(root)
        holders, composite = holder_inputs(root)
        additions, documents = supplemental_bonuses(data, restore=certify)
        holders["bonuses"].extend(additions)
        holders["evidence"].extend(documents)
        dip_bonus.parse(json.dumps(holders["bonuses"]).encode())
        receipt = {
            "snapshot_sha256": parent["snapshot_sha256"],
            "before_replay_preflight_sha256": market.sha(
                (root / "stage3/preflight.json").read_bytes()
            ),
            "session_manifests": parent["session_manifests"],
            "holder_sha256": market.sha(json.dumps(holders, sort_keys=True).encode()),
            "composite_holder_actions": composite,
            "verified_closes": parent["independent_close_count"],
        }
        directory = directory / receipt["holder_sha256"][:20]
        directory.mkdir(exist_ok=True)
        path = directory / "certified.json"
        if certify:
            write_json(path, receipt)
        elif not path.is_file() or json.loads(path.read_bytes()) != receipt:
            raise market.MarketError("Run python -m thesistally.range_market before replay.")
        view = dip_holder_market.holder_market(root, directory, holders)
        return view, receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    path, receipt = prepare_market(args.data, certify=True)
    print(path, receipt["snapshot_sha256"], len(receipt["session_manifests"]))
