import gzip
import json

import pytest

from thesistally.discovery_registry import digest, encoded
from thesistally.fundamental_facts import FIELDS
from thesistally.fundamental_legacy_batch import _paths
from thesistally.fundamental_modern_build import verify_cases
from thesistally.fundamental_review_optics import evaluator, identity
from thesistally.fundamental_xbrl_processing import audit_cohort, expected_jobs
from thesistally.fundamental_xbrl_subset import selection


def write(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path


def archive(path, data):
    raw = encoded(data)
    sha = digest(raw)
    return sha, write(path / (sha + ".json.gz"), gzip.compress(raw))


def optical_case(root, monkeypatch, *, nominated=True):
    version = "source-selector" if nominated else None
    pdf = digest(b"original-pdf")
    url = "https://archives.nseindia.com/corporate/board.pdf"
    objects = root / "rendered-page-objects"
    values = {**dict.fromkeys(FIELDS), "eps": 10}
    filing = {
        "id": "original",
        "isin": "INE000",
        "basis": "consolidated",
        "period_end": "2025-06-30",
        "source_url": "original.xml",
        "tags": {"eps": "Basic"},
        **values,
    }
    case = {
        "filing": filing,
        "structured_sha256": digest(b"xml"),
        "release_at": "2025-09-09T14:51:00+05:30",
    }
    pkg = {
        "sha256": pdf,
        "source_url": url,
        "pdfs": [{"pdf_sha256": pdf, "pages": ["cover", "statement", "notes", ""]}],
    }
    src = write(root / "notice-text" / "sample.json.gz", gzip.compress(encoded(pkg)))
    job = {
        "url": url,
        "original_sha256": pdf,
        "package_path": str(src.relative_to(root)),
        "package_sha256": digest(src.read_bytes()),
        "cases": [case],
    }
    if nominated:
        job["selected_pages"] = {pdf: [2, 3]}
    scope = selection({pdf: 3}, job.get("selected_pages"), version)
    tools = {
        "source_sha256": digest(b"reader"),
        "poppler": "poppler",
        "macos_build": "build",
        "platform": "test",
    }
    pages = []
    observations = []

    def observe(filing, png, words):
        if filing["basis"] == "consolidated" and words == ["basic EPS 10"] and png == b"statement":
            return [
                {"field": "eps", "value": "10", "method": "scoped_cell", "evidence": {"column": 2}}
            ]
        return []

    monkeypatch.setattr("thesistally.fundamental_xbrl_receipts.page_observations", observe)
    for i, t in enumerate(["cover", "statement", "notes"], 1):
        if nominated and i == 1:
            continue
        png = t.encode()
        words = ["basic EPS 10"] if i == 2 else []
        bsha = digest(encoded(words))
        psha = digest(png)
        old, _, inputs = _paths(root, {**tools, "pdf_sha256": pdf, "page": i})
        write(old, encoded({"inputs": inputs, "png": psha, "word_boxes": bsha}))
        write(objects / bsha[:2] / (bsha + ".json.gz"), gzip.compress(encoded(words)))
        page = {
            "pdf_sha256": pdf,
            "page": i,
            "png_sha256": psha,
            "boxes_sha256": bsha,
            "raster_source": {"method": "original-poppler-png"},
        }
        pages.append(page)
        if i == 2:
            write(objects / psha[:2] / (psha + ".png"), png)
            observations.extend({**o, **page} for o in observe(filing, png, words))
    info = identity(filing, case["structured_sha256"], url, pdf, case["release_at"])
    body = {
        "identity": info,
        "selection": scope,
        "package_sha256": job["package_sha256"],
        "package_file": job["package_path"],
        "omitted_pages": int(nominated),
        "tools": tools,
        "pages": pages,
        "unreadable_pages": [],
        "observations": observations,
        "values": values,
        "conflicts": {},
        "nonmatching_fields": {},
    }
    return job, body, version


def signed(root, body):
    sha, p = archive(root / "original-xbrl-optics", body)
    return {
        **{
            k: body[k]
            for k in (
                "selection",
                "omitted_pages",
                "unreadable_pages",
                "values",
                "conflicts",
                "nonmatching_fields",
            )
        },
        "key": digest(encoded({"identity": body["identity"], "selection": body["selection"]})),
        "base_key": digest(encoded(body["identity"])),
        "file": str(p.relative_to(root)),
        "receipt_sha256": sha,
        "id": body["identity"]["id"],
        "physical_pages": len(body["pages"]),
        "supporting_page_counts": {"eps": 1} if body["values"]["eps"] is not None else {},
    }


@pytest.mark.parametrize("nominated", [False, True])
def test_publication_and_resume_bind_original_quarter_and_physical_reads(
    tmp_path, monkeypatch, nominated
):
    job, b, version = optical_case(tmp_path, monkeypatch, nominated=nominated)
    row = signed(tmp_path, b)
    assert verify_cases(tmp_path, job, [row], version)["observed_pages"] == 3 - int(nominated)
    with pytest.raises(ValueError, match="missing, extra or duplicate"):
        verify_cases(tmp_path, job, [row, row], version)
    for key, val in (
        ("release_at", "2025-09-10T12:00:00+05:30"),
        ("original_url", "second"),
        ("original_sha256", "other"),
        ("id", "other"),
        ("isin", "old id"),
        ("basis", "standalone"),
        ("period_end", "2024-06-30"),
        ("structured_url", "later.xml"),
        ("structured_sha256", "other"),
        ("structured_values", {}),
        ("tags", {}),
        ("evaluator", "another"),
    ):
        bad = signed(tmp_path, {**b, "identity": {**b["identity"], key: val}})
        with pytest.raises(ValueError, match="different|another|missing, extra"):
            verify_cases(tmp_path, job, [bad], version)
    for altered in (
        [*b["pages"][:-1]],
        b["pages"] * 2,
        [],
        [{**b["pages"][0], "page": 4}, *b["pages"][1:]],
    ):
        with pytest.raises(ValueError, match="physical pages|page read"):
            verify_cases(tmp_path, job, [signed(tmp_path, {**b, "pages": altered})], version)
    for row_key, val in (
        ("physical_pages", 900),
        ("omitted_pages", 6),
        ("supporting_page_counts", {"eps": 2}),
    ):
        with pytest.raises(ValueError, match="count"):
            verify_cases(tmp_path, job, [{**row, row_key: val}], version)


def test_unsupported_cells_fail_even_in_internally_consistent_signed_receipt(tmp_path, monkeypatch):
    job, b, version = optical_case(tmp_path, monkeypatch)
    first = b["observations"][0]
    erased = {**b, "observations": [], "values": dict.fromkeys(FIELDS)}
    with pytest.raises(ValueError, match="differ from the original"):
        verify_cases(tmp_path, job, [signed(tmp_path, erased)], version)
    for observations, message in (
        ([], "differ from the original"),
        ([dict(first, page=1)], "absent or failed"),
        ([dict(first, value="45")], "differ from the original"),
    ):
        with pytest.raises(ValueError, match=message):
            verify_cases(
                tmp_path, job, [signed(tmp_path, {**b, "observations": observations})], version
            )
    bad = {**b, "observations": [{**first, "evidence": {"column": 5}}]}
    with pytest.raises(ValueError, match="differ from the original"):
        verify_cases(tmp_path, job, [signed(tmp_path, bad)], version)
    bad = {
        **b,
        "pages": [{**b["pages"][0], "boxes_sha256": digest(b"other")}, *b["pages"][1:]],
        "observations": [],
        "values": dict.fromkeys(FIELDS),
    }
    with pytest.raises(ValueError, match="different original source page"):
        verify_cases(tmp_path, job, [signed(tmp_path, bad)], version)


def test_gaps_must_cover_exact_failed_page_and_cannot_establish_fields(tmp_path, monkeypatch):
    job, b, version = optical_case(tmp_path, monkeypatch)
    pdf, page = b["pages"][0]["pdf_sha256"], b["pages"][0]["page"]
    gap = {"pdf_sha256": pdf, "page": page, "returncode": 1}
    with pytest.raises(ValueError, match="absent or failed"):
        verify_cases(
            tmp_path,
            job,
            [signed(tmp_path, {**b, "pages": b["pages"][1:], "unreadable_pages": [gap]})],
            version,
        )
    legitimate = {
        **b,
        "pages": b["pages"][1:],
        "unreadable_pages": [gap],
        "observations": [],
        "values": dict.fromkeys(FIELDS),
    }
    assert verify_cases(tmp_path, job, [signed(tmp_path, legitimate)], version)["failed_pages"] == 1
    for gaps in (
        [gap, gap],
        [{**gap, "returncode": 0}],
        [{"reason": "some_original_archive_members_are_unreadable"}],
    ):
        with pytest.raises(ValueError, match="failure|partition"):
            verify_cases(
                tmp_path, job, [signed(tmp_path, {**legitimate, "unreadable_pages": gaps})], version
            )


@pytest.mark.parametrize("nominated", [False, True])
def test_terminal_marker_reconciles_full_or_selected_cohort_with_same_validator(
    tmp_path, monkeypatch, nominated
):
    job, body, version = optical_case(tmp_path, monkeypatch, nominated=nominated)
    name = "modern-statement-pages" if nominated else "independent-earlier-release"
    jobs = (
        [job]
        if nominated
        else [[job[k] for k in ("url", "package_path", "package_sha256", "cases")]]
    )
    plan = {"source_edition": "ledger", "selection_version": version, "jobs": jobs}
    key, _ = archive(tmp_path / "xbrl-optical-plans", plan)

    def run(body):
        rows = [signed(tmp_path, body)]
        study = {
            "records": rows,
            "tools": body["tools"],
            "selection_version": version,
            "source_edition": "ledger",
            "plan": key,
            "evaluator": evaluator(),
            "cohort": name,
            "counts": {
                "packages_accounted_for": 1,
                "nominated_pages": 2,
                "filings_with_reads": 1,
                "packages_without_nominated_pages": 0,
                "fields_confirmed": 1,
            },
        }
        ed, _ = archive(tmp_path / "xbrl-optical-editions", study)
        marker = write(
            tmp_path / (ed + ".json"),
            encoded({"plan": key, "evaluator": evaluator(), "edition_sha256": ed, "cases": 1}),
        )
        return audit_cohort(tmp_path, name, key, evaluator(), "ledger", [marker])

    assert run(body)["field_observations"] == {"eps": 1}
    with pytest.raises(ValueError, match="partition"):
        run({**body, "pages": []})
    with pytest.raises(ValueError, match="duplicate original"):
        expected_jobs(name, dict(plan, jobs=jobs * 2))


def test_unreadable_zip_attachment_documents_a_package_gap_separately(tmp_path, monkeypatch):
    import io
    import zipfile

    job, body, version = optical_case(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("results.pdf", b"%PDF-existing statement")
        z.writestr("unreadable.xls", b"data")
    sha = digest(buf.getvalue())
    write(tmp_path / "notice-objects" / sha[:2] / (sha + ".gz"), gzip.compress(buf.getvalue()))
    source = tmp_path / job["package_path"]
    pkg = json.loads(gzip.decompress(source.read_bytes()))
    pkg["sha256"] = sha
    source.write_bytes(gzip.compress(encoded(pkg)))
    job.update(original_sha256=sha, package_sha256=digest(source.read_bytes()))
    body.update(
        package_sha256=job["package_sha256"],
        values=dict.fromkeys(FIELDS),
        unreadable_pages=[{"reason": "some_original_archive_members_are_unreadable"}],
    )
    body["identity"]["original_sha256"] = sha
    assert verify_cases(tmp_path, job, [signed(tmp_path, body)], version) == {
        "observed_pages": 2,
        "failed_pages": 0,
        "packages_with_unreadable_members": 1,
        "omitted_pages": 1,
        "original_packages": 1,
    }
    with pytest.raises(ValueError, match="partition"):
        verify_cases(tmp_path, job, [signed(tmp_path, dict(body, unreadable_pages=[]))], version)


def test_zero_page_nominations_are_reported_and_reconciled(tmp_path, monkeypatch):
    job, body, version = optical_case(tmp_path, monkeypatch)
    job["selected_pages"] = {job["original_sha256"]: []}
    name = "modern-statement-pages"
    plan = {"source_edition": "ledger", "selection_version": version, "jobs": [job]}
    key, _ = archive(tmp_path / "xbrl-optical-plans", plan)
    study = {
        "cohort": name,
        "plan": key,
        "evaluator": evaluator(),
        "source_edition": "ledger",
        "selection_version": version,
        "tools": body["tools"],
        "records": [],
        "no_nominated_pages": {digest(encoded(job)): ["original"]},
        "counts": {"packages_accounted_for": 1, "packages_without_nominated_pages": 1},
    }
    edition, _ = archive(tmp_path / "xbrl-optical-editions", study)
    marker = write(
        tmp_path / (edition + ".json"),
        encoded({"plan": key, "evaluator": evaluator(), "edition_sha256": edition, "cases": 0}),
    )
    stats = audit_cohort(tmp_path, name, key, evaluator(), "ledger", [marker])
    assert stats["filings_without_nominated_pages"] == 1
    assert stats["verified_physical_reads"]["omitted_pages"] == 3
