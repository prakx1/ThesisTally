"""Independently reconcile each original XBRL optical receipt to its real scheduled pages."""

import argparse
import gzip
import json
from collections import Counter, defaultdict
from decimal import Decimal

from check_optical_financials import original_rgb_digest
from check_progress_bound import canon, progress_receipt
from reconcile import METRICS, F, sha

N = F / "nse"


def archive(path, digest):
    raw = gzip.decompress(path.read_bytes())
    assert sha(raw) == digest, path
    return json.loads(raw)


def original_record(row):
    path = (N / row["file"]).resolve(strict=True)
    assert path.is_relative_to((N / "original-xbrl-optics").resolve())
    return archive(path, row["receipt_sha256"])


def source(package, expected, url):
    raw = (N / package).read_bytes()
    assert sha(raw) == expected
    body = json.loads(gzip.decompress(raw))
    assert body["source_url"] == url
    member = {}
    for d in body["pdfs"]:
        texts = d["pages"]
        h = d["pdf_sha256"]
        count = len(texts) - int(bool(texts) and not texts[-1].strip())
        assert h not in member or member[h] == count
        member[h] = count
    return body["sha256"], member


def source_fields(record, original, all_pages, expected, scope, evaluator, case, url, package):
    raw = original_record(record)
    ident = raw["identity"]
    filing = case["filing"]
    assert all(ident[k] == filing[k] for k in ("id", "isin", "basis", "period_end"))
    assert ident["evaluator"] == evaluator and ident["release_at"] == case["release_at"]
    assert (ident["original_url"], ident["original_sha256"]) == (url, original)
    assert (ident["structured_sha256"], ident["structured_url"]) == (
        case["structured_sha256"],
        filing["source_url"],
    )
    assert ident["structured_values"] == {k: filing[k] for k in METRICS}
    assert raw["package_sha256"] == package and scope == raw["selection"] == record["selection"]
    assert record["base_key"] == sha(canon(ident))
    assert record["key"] == sha(canon({"identity": ident, "selection": scope}))
    pages = {(s["pdf_sha256"], s["page"]): s for s in raw["pages"]}
    assert len(pages) == len(raw["pages"]) == record["physical_pages"]
    missing = [(g["pdf_sha256"], g["page"]) for g in raw["unreadable_pages"] if "page" in g]
    assert len(missing) == len(set(missing)) and set(pages).isdisjoint(missing)
    assert set(pages) | set(missing) == expected
    for g in raw["unreadable_pages"]:
        if "page" not in g:
            assert g == {"reason": "some_original_archive_members_are_unreadable"}
    assert record["unreadable_pages"] == raw["unreadable_pages"]
    assert record["omitted_pages"] == raw["omitted_pages"] == len(all_pages - expected)

    proofs = defaultdict(lambda: defaultdict(set))
    for observation in raw["observations"]:
        primary = (observation["pdf_sha256"], observation["page"])
        page = pages[primary]
        assert all(
            observation[k] == page[k] for k in ("png_sha256", "boxes_sha256", "raster_source")
        )
        if observation.get("admissible", True):
            proofs[observation["field"]][Decimal(observation["value"])].add(primary)
    supported, conflict, mismatch = {}, {}, {}
    values = dict.fromkeys(METRICS)
    for field, candidates in proofs.items():
        assert field in METRICS
        if len(candidates) > 1:
            conflict[field] = set(candidates)
            continue
        amount, page_refs = next(iter(candidates.items()))
        reference = filing[field]
        if reference is None or amount != Decimal(str(reference)):
            mismatch[field] = amount
            continue
        if not raw["unreadable_pages"]:
            values[field] = float(amount)
            supported[field] = len(page_refs)
    assert values == record["values"] == raw["values"]
    assert supported == record["supporting_page_counts"]
    assert raw["conflicts"] == record["conflicts"]
    assert {k: set(map(Decimal, v)) for k, v in raw["conflicts"].items()} == conflict
    assert raw["nonmatching_fields"] == record["nonmatching_fields"]
    assert {k: Decimal(v) for k, v in raw["nonmatching_fields"].items()} == mismatch
    observed_pages = {(r["pdf_sha256"], r["page"]) for r in raw["observations"]}
    return pages, len(missing), supported, observed_pages


def census(name, live, snapshot=None):
    reference = json.loads((N / "terminal-xbrl-scope.json").read_bytes())
    plan_id = reference["cohorts"][name]
    evaluator = reference["evaluator"]
    plan = archive(N / "xbrl-optical-plans" / (plan_id + ".json.gz"), plan_id)
    assert plan["source_edition"] == reference["source_edition"]

    def belongs(path):
        raw = json.loads(path.read_bytes())
        return raw["plan"] == plan_id and raw["evaluator"] == evaluator

    by_case = {}
    markers = {}
    if live:
        assert name == "modern-statement-pages"
        (run,) = [p for p in (N / "modern-optical-attempts").glob("*/run.json") if belongs(p)]
        assert sha(run.read_bytes()) == run.parent.name
        marks, snapshot = progress_receipt(run.parent, snapshot)
        assert marks["run_sha256"] == run.parent.name
        for key, digest in marks["markers_sha256"].items():
            raw = (run.parent / key[:2] / (key + ".json")).read_bytes()
            assert sha(raw) == digest
            markers[key] = json.loads(raw)
        edition = None
    else:
        assert snapshot is None
        (edition,) = [
            p.stem for p in (N / "xbrl-optical-completions" / name).glob("*.json") if belongs(p)
        ]
        study = archive(N / "xbrl-optical-editions" / (edition + ".json.gz"), edition)
        assert (study["plan"], study["evaluator"], study["cohort"]) == (plan_id, evaluator, name)
        for r in study["records"]:
            identity = original_record(r)["identity"]
            key = r["id"], identity["original_url"], identity["release_at"]
            assert key not in by_case
            by_case[key] = r
    counts = Counter()
    receipts = {}
    words = set()
    fieldless = {}
    for task in plan["jobs"]:
        key = sha(canon(task))
        if live and key not in markers:
            counts["pending_packages"] += 1
            continue
        if name == "modern-statement-pages":
            url, package, package_sha, cases = (
                task[k] for k in ("url", "package_path", "package_sha256", "cases")
            )
            selected = task["selected_pages"]
            scope = {
                "mode": "nominated",
                "pages": selected,
                "selection_version": plan["selection_version"],
                "nomination_sha256": sha(canon(selected)),
            }
        else:
            url, package, package_sha, cases = task
            scope = {"mode": "all_pages"}
        original, source_members = source(package, package_sha, url)
        every = {(h, i) for h, n in source_members.items() for i in range(1, n + 1)}
        if name == "modern-statement-pages":
            assert task["original_sha256"] == original and set(selected) == set(source_members)
            assert all(ids == sorted(set(ids)) for ids in selected.values())
            expected = {(h, i) for h, ids in selected.items() for i in ids}
            assert expected <= every
        else:
            expected = every
        if live:
            marker = markers.pop(key)
            assert marker["job"] == key and marker["nominated_pages"] == len(expected)
            live_records = {r["id"]: r for r in marker["records"]}
            assert len(live_records) == len(marker["records"])
        counts["checked_packages"] += 1
        if not expected:
            counts["packages_without_selected_pages"] += 1
            if live:
                assert not live_records
            else:
                assert study["no_nominated_pages"][key] == [c["filing"]["id"] for c in cases]
            continue
        for case in cases:
            fid = case["filing"]["id"]
            if live:
                r = live_records.pop(fid)
            else:
                r = by_case.pop((fid, url, case["release_at"]))
            pages, bad, fields, observed = source_fields(
                r, original, every, expected, scope, evaluator, case, url, package_sha
            )
            counts["checked_accessions"] += 1
            counts["page_accession_reads"] += len(pages)
            for (document, number), page in pages.items():
                box = page["boxes_sha256"]
                if box not in words:
                    path = N / "rendered-page-objects" / box[:2] / (box + ".json.gz")
                    data = gzip.decompress(path.read_bytes())
                    assert sha(data) == box and isinstance(json.loads(data), list)
                    words.add(box)
                rgb = page["raster_source"].get("rgb_sha256")
                if (document, number) not in observed and rgb:
                    fieldless[original, document, number] = rgb
            counts["unreadable_page_accession_reads"] += bad
            counts["accessions_with_verified_values"] += bool(fields)
            for f in fields:
                counts["verified_" + f] += 1
            receipts[r["key"]] = r["receipt_sha256"]
        if live:
            assert not live_records
    assert not markers and not by_case
    sampled = []
    for key in sorted(fieldless, key=lambda pair: sha(canon(pair))):
        original, document, number = key
        try:
            rendered = original_rgb_digest(original, document, number, 2400)
        except AssertionError as failure:
            if failure.args != ((original, document),):
                raise
            counts["non_pdf_sources_outside_raw_pdf_sample"] += 1
            continue
        assert rendered == fieldless[key], key
        sampled.append(
            {
                "source_sha256": original,
                "pdf_sha256": document,
                "page": number,
                "rgb_sha256": rendered,
            }
        )
        if len(sampled) == 12:
            break
    return {
        "cohort": name,
        "source_edition": reference["source_edition"],
        "plan": plan_id,
        "evaluator": evaluator,
        "edition": edition,
        "live_marker_snapshot": snapshot,
        "receipt_set_sha256": sha(canon(receipts)),
        "census": dict(counts),
        "unique_word_box_objects_rehashed": len(words),
        "sampling_candidates_with_no_financial_cell_observation_at_that_accession": len(fieldless),
        "fieldless_page_raw_pdf_pixel_checks": sampled,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cohort", choices=["independent-earlier-release", "modern-statement-pages"])
    p.add_argument("--live", action="store_true")
    p.add_argument("--snapshot")
    a = p.parse_args()
    print(json.dumps(census(a.cohort, a.live, a.snapshot), indent=2, sort_keys=True))
