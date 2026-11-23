import gzip
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from thesistally.discovery_registry import digest, encoded, write_once
from thesistally.fundamental_bse_recheck import (
    package_scan,
    references,
    signed_rows,
)

BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/results.pdf"


def pdf_with_link():
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> /Annots [4 0 R] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [5 5 10 10] /A << /S /URI /URI ("
        + BSE.encode()
        + b") >> >>",
    ]
    pdf = b"%PDF-1.5\n"
    pos = []
    for i, k in enumerate(objects, 1):
        pos.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + k + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for v in pos:
        pdf += f"{v:010d} 00000 n \n".encode()
    return (
        pdf
        + f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )


def setup_pack(root):
    pdf = pdf_with_link()
    pdfsha = digest(pdf)
    q = io.BytesIO()
    with zipfile.ZipFile(q, "w") as z:
        z.writestr("results.pdf", pdf)
    raw = q.getvalue()
    sha = digest(raw)
    url = "https://nsearchives.nseindia.in/financial.zip"
    native = "notice-text/" + digest(url.encode()) + ".json.gz"
    oc = f"notice-ocr/vision-r3-layout-v1/{pdfsha[:2]}/{pdfsha}.json.gz"
    sources = {
        "notice-objects/" + sha[:2] + "/" + sha + ".gz": raw,
        native: encoded(
            {"source_url": url, "sha256": sha, "pdfs": [{"pdf_sha256": pdfsha, "pages": ["", ""]}]}
        ),
        oc: encoded(
            {
                "method": "vision-r3-layout-v1",
                "pdf_sha256": pdfsha,
                "pages": {"1": "signed company account " + BSE},
            }
        ),
    }
    rec = encoded(
        {
            "url": url,
            "sha256": sha,
            "text_sha256": digest(sources[native]),
            "pdfs": [{"pdf_sha256": pdfsha, "pages": 1}],
        }
    )
    rec = (json.dumps(json.loads(rec)) + "\n").encode()
    meta = {"sha256": digest(rec), "file": digest(rec) + ".jsonl.gz", "records": 1}
    sources["source-manifests/" + meta["file"]] = rec
    for name, data in sources.items():
        f = root / name
        f.parent.mkdir(exist_ok=True, parents=True)
        f.write_bytes(gzip.compress(data, mtime=0))
    return {"native": [native], "ocr": [oc]}, meta


def test_references_require_a_real_exchange_host_and_direct_attachment():
    assert references("Transfer side note www.bseindia.com and this original " + BSE)[0] == [BSE]
    assert (
        references(
            "https://example.invalid/bseindia.com/file.pdf https://bseindia.com.invalid/attach/file.pdf https://evilbseindia.com/r.pdf"
        )[0]
        == []
    )
    assert references('Open <a href="www.bseindia.com/xml-data/corpfiling/AttachHis/results.pdf">')[
        0
    ] == [BSE]


@pytest.mark.skipif(not shutil.which("pdfinfo"), reason="PDF annotation reader not installed")
def test_read_embedded_urls_and_optical_text_from_each_exact_archived_member(tmp_path):
    root = tmp_path / "nse"
    frozen, meta = setup_pack(root)
    tool = digest(Path(shutil.which("pdfinfo")).read_bytes())
    study = package_scan(root, frozen, meta, tool)
    assert study["counts"]["original_packages"] == 1
    assert {(v["source_type"], v["bse_url"], v["page"]) for v in study["links"]} == {
        ("optical", BSE, 1),
        ("PDF annotation", BSE, 1),
    }
    assert study == package_scan(root, frozen, meta, tool)
    with pytest.raises(ValueError, match="complete original package list"):
        package_scan(root, dict(frozen, native=[]), meta, tool)
    src = root / frozen["native"][0]
    src.write_bytes(gzip.compress(b"{}"))
    with pytest.raises((ValueError, KeyError)):
        package_scan(root, frozen, meta, tool)


def test_changed_signed_catalogue_is_rejected(tmp_path):
    root = tmp_path / "nse"
    _, meta = setup_pack(root)
    assert len(signed_rows(root, meta)) == 1
    with pytest.raises(ValueError, match="row count"):
        signed_rows(root, dict(meta, records=2))
    f = root / "source-manifests" / meta["file"]
    f.write_bytes(gzip.compress(b'{"different":true}\n'))
    with pytest.raises(ValueError, match="SHA-256"):
        signed_rows(root, meta)


def test_new_or_pending_bse_references_cannot_silently_pass_the_terminal_gate(tmp_path):
    from thesistally.fundamental_bse_links import summary

    with pytest.raises(ValueError, match="New direct BSE sources"):
        summary(tmp_path, [BSE], {})
    pending = json.loads(next((tmp_path / "bse/pending").glob("*.json")).read_bytes())
    assert pending == {"missing_official_sources": [BSE], "require_dissemination_review": []}


def test_denied_attachment_is_archived_and_never_used_as_an_earnings_clock(tmp_path, monkeypatch):
    import httpx

    from thesistally.fundamental_bse_links import capture, summary

    root = tmp_path / "nse"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(406, content=b"not acceptable")
        )
    ) as client:
        result = capture(root, BSE, client)
        assert capture(root, BSE, client) == result
    assert result["status"] == 406
    monkeypatch.setenv("PATH", "")
    report = summary(tmp_path, [BSE], {})
    assert report["pdftotext_sha256"] is None
    link = report["per_link"][BSE]
    assert link["document_purpose"] == "attachment unavailable"
    assert link["verified_bse_disseminated_at"] is None


def test_archived_pdf_needs_pdftotext_before_summary_can_be_published(tmp_path, monkeypatch):
    from thesistally.fundamental_bse_links import summary

    bse = tmp_path / "bse"
    pdf = pdf_with_link()
    sha = digest(pdf)
    write_once(bse / "source-objects" / (sha + ".gz"), gzip.compress(pdf, mtime=0))
    write_once(
        bse / "linked-sources" / (digest(BSE.encode()) + ".json"),
        encoded({"url": BSE, "status": 200, "sha256": sha}),
    )
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError, match="pdftotext"):
        summary(tmp_path, [BSE], {})


def test_existing_link_with_explicit_clock_cue_needs_a_proven_original(tmp_path):
    from thesistally.fundamental_bse_links import summary

    bse = tmp_path / "bse"
    body = b"Pretend BSE entity page"
    sha = digest(body)
    k = digest(BSE.encode())
    (bse / "source-objects").mkdir(parents=True)
    (bse / "source-objects" / (sha + ".gz")).write_bytes(gzip.compress(body, mtime=0))
    (bse / "linked-sources").mkdir()
    (bse / "linked-sources" / (k + ".json")).write_bytes(
        encoded(
            {
                "url": BSE,
                "status": 200,
                "sha256": sha,
                "official_clock_cues": [{"page": 1, "exact_line": "Dissemination Time"}],
            }
        )
    )
    with pytest.raises(ValueError, match="clock review"):
        summary(tmp_path, [BSE], {})
    (bse / "linked-clock-reviews").mkdir()
    (bse / "linked-clock-reviews" / (k + ".json")).write_bytes(
        encoded(
            {
                "url": BSE,
                "source_sha256": sha,
                "verified_public_dissemination_at": None,
                "reason": "The line refers to another document.",
            }
        )
    )
    assert summary(tmp_path, [BSE], {})["per_link"][BSE]["verified_bse_disseminated_at"] is None
    with pytest.raises(ValueError, match="canonical source ledger differ"):
        summary(tmp_path, [BSE], {BSE: "2025-09-30T15:29:00+05:30"})


@pytest.mark.skipif(
    not shutil.which("pdfinfo") or not shutil.which("pdftotext"), reason="Poppler is required"
)
def test_completed_cross_exchange_receipt_is_stable_and_a_new_clock_requires_republication(
    tmp_path, monkeypatch
):
    import httpx

    from thesistally import fundamental_bse_recheck as bse
    from thesistally.fundamental_bse_links import capture

    root = tmp_path / "nse"
    frozen, first = setup_pack(root)
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(406, content=b"no attachment"))
    ) as cli:
        capture(root, BSE, cli)
    stored = encoded(frozen)
    freeze = digest(stored)
    (root / "ledger-inputs").mkdir()
    (root / "ledger-inputs" / (freeze + ".json.gz")).write_bytes(gzip.compress(stored))
    ledger = "a" * 64
    (root / "filings-receipt.json").write_bytes(
        encoded({"sha256": ledger, "frozen_ledger_inputs_sha256": freeze})
    )
    req = b'{"status":"archived"}\n'
    reqsha = digest(req)
    (root / "source-manifests" / (reqsha + ".jsonl.gz")).write_bytes(gzip.compress(req))
    acquired = {"complete": True, "by_status": {"archived": 1}, "planned_urls": 1}
    cs = encoded(
        {
            "first_notice": first,
            "filings_sha256": ledger,
            "notice_acquisition": acquired,
            "notice_requests": {"sha256": reqsha, "file": reqsha + ".jsonl.gz", "records": 1},
        }
    )
    census = digest(cs)
    folder = root / "checkpoints" / ledger / census
    folder.mkdir(parents=True)
    (folder / "source-index.json.gz").write_bytes(gzip.compress(cs))
    index = encoded(
        {"filings_sha256": ledger, "source_census_sha256": census, "source_acquisition": acquired}
    )
    (folder / "index.json").write_bytes(index)
    src = {
        "sha256": ledger,
        "checkpoint_index_sha256": digest(index),
        "source_census_sha256": census,
        "source_acquisition": acquired,
    }
    meta = {
        "as_of": "2026-11-20",
        "membership_sha256": "b" * 64,
        "filing_sources": {"nse": src, "bse": {"sha256": digest(b"")}},
    }
    (tmp_path / "coverage.json").write_bytes(encoded(meta))
    monkeypatch.setattr(
        bse,
        "market_scan",
        lambda *a: {
            "sources_sha256": "c" * 64,
            "counts": {"eligible_symbol_announcements": 1},
            "links": [],
        },
    )
    monkeypatch.setattr(
        bse,
        "previous_evidence",
        lambda data: {
            "receipts": {"filings.jsonl": digest(b"")},
            "verified_original_bse_clocks": 0,
            "clock_by_url": {},
        },
    )
    a = bse.recheck(tmp_path, frozen, src)
    assert bse.recheck(tmp_path, frozen, src) == a
    assert a["candidate_direct_urls"] == 1 and a["unresolved_bse_clocks"] == 1
    assert a["packages"] == 1 and a["unresolved_nse_sources"] == 0
    raw = (tmp_path / a["receipt"]).read_bytes()
    assert digest(raw) == a["receipt_sha256"]
    assert json.loads(raw)["linked_bse_sources"]["pdftotext_sha256"] == digest(
        Path(shutil.which("pdftotext")).read_bytes()
    )
    meta["filing_sources"]["bse"]["sha256"] = digest(b"new original")
    (tmp_path / "coverage.json").write_bytes(encoded(meta))
    with pytest.raises(ValueError, match="rebuild canonical coverage"):
        bse.recheck(tmp_path, frozen, src)
