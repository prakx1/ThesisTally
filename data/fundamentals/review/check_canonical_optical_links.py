"""Link published first-period facts to their admitted physical XBRL corroboration."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from functools import lru_cache

from check_modern_physical_pages import archive, original_record
from reconcile import F, sha

N = F / "nse"


def run(require_both=False):
    coverage_raw = (F / "coverage.json").read_bytes()
    coverage = json.loads(coverage_raw)
    numerical = json.loads((N / "filings-receipt.json").read_bytes())
    ledger = coverage["filing_sources"]["nse"]["sha256"]
    assert numerical["sha256"] == ledger
    frozen = numerical["frozen_ledger_inputs_sha256"]
    inputs = archive(N / "ledger-inputs" / (frozen + ".json.gz"), frozen)
    scope = json.loads((N / "terminal-xbrl-scope.json").read_bytes())
    refs = {}
    editions = {}
    for rel in inputs.get("reviewed_xbrl_optical_editions", []):
        path = N / rel
        marker = json.loads(path.read_bytes())
        which = path.parent.name
        if marker["plan"] != scope["cohorts"][which] or marker["evaluator"] != scope["evaluator"]:
            continue
        ed = marker["edition_sha256"]
        assert path.stem == ed and which not in editions
        study = archive(N / "xbrl-optical-editions" / (ed + ".json.gz"), ed)
        assert (study["plan"], study["cohort"], study["evaluator"]) == (
            marker["plan"],
            which,
            marker["evaluator"],
        )
        editions[which] = ed
        for row in study["records"]:
            h = row["receipt_sha256"]
            if h in refs:
                assert refs[h] == row
            refs[h] = row
    if require_both:
        assert set(editions) == set(scope["cohorts"])

    @lru_cache(maxsize=512)
    def raw_receipt(digest):
        return original_record(refs[digest])

    accepted = Counter()
    secondary = Counter()
    observations = {}
    cadence = Counter()
    total = 0
    raw = (F / "first-releases.jsonl").read_bytes()
    assert sha(raw) == coverage["products"]["first-releases.jsonl"]
    for line in raw.splitlines():
        row = json.loads(line)
        root = row.get("notice_proof") or {}
        todo = [(True, root)]
        seen_accepted = False
        while todo:
            primary, proof = todo.pop()
            for key in (
                "additional_reviewed_xbrl_optics",
                "additional_native_cells",
                "additional_original_optics",
            ):
                if proof.get(key):
                    todo.append((False, proof[key]))
            for field, w in proof.get("field_page_evidence", {}).items():
                if "receipt_sha256" not in w or not {"boxes_sha256", "field", "value"} <= w.keys():
                    continue
                h = w["receipt_sha256"]
                raw_proof = raw_receipt(h)
                i = raw_proof["identity"]
                assert (i["isin"], i["basis"], i["period_end"], i["release_at"]) == (
                    row["isin"],
                    row["basis"],
                    row["period_end"],
                    row["announced_at"],
                )
                assert (
                    i["original_sha256"] == root["notice_sha256"]
                    and i["original_url"] == root["notice_url"]
                )
                assert field == w["field"]
                sample = {k: v for k, v in w.items() if k != "receipt_sha256"}
                assert sample in raw_proof["observations"]
                value = Decimal(w["value"])
                assert (
                    value
                    == Decimal(str(raw_proof["values"][field]))
                    == Decimal(str(i["structured_values"][field]))
                )
                assert sample.get("admissible", True) and not raw_proof["unreadable_pages"]
                if primary:
                    assert row[field] is not None and value == Decimal(str(row[field]))
                    accepted[field] += 1
                    seen_accepted = True
                    observations[
                        "/".join((row["isin"], row["period_end"], row["basis"], field))
                    ] = h
                else:
                    secondary[field] += 1
            if primary:
                assert all(
                    row[field] is None for field in proof.get("original_field_conflicts", {})
                )
        if seen_accepted:
            total += 1
            cadence[row["period_end"][:4]] += 1
    return {
        "coverage_sha256": sha(coverage_raw),
        "source_ledger": ledger,
        "frozen_inputs": frozen,
        "cohort_editions": editions,
        "basis_quarters_with_direct_optical_fields": total,
        "basis_quarters_by_reporting_year": dict(cadence),
        "direct_optical_values_by_field": dict(accepted),
        "nested_supporting_witnesses_by_field_including_duplicates": dict(secondary),
        "canonical_field_to_receipt_sha256": observations,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-both", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.require_both), indent=2, sort_keys=True))
