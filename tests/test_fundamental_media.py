import io
import shutil
import struct
import zipfile

import pytest

from thesistally import fundamental_media
from thesistally.fundamental_media import archive_documents
from thesistally.fundamental_media_recovery import supplemental_needed
from thesistally.fundamental_nse import digest


def archive(name, content):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, content)
    return buf.getvalue()


def image_tiff():
    # Unsigned monochrome 2x2 image with a single, uncompressed native TIFF strip.
    entries = (
        (256, 4, 2),
        (257, 4, 2),
        (258, 3, 8),
        (259, 3, 1),
        (262, 3, 1),
        (273, 4, 122),
        (277, 3, 1),
        (278, 4, 2),
        (279, 4, 4),
    )
    raw = struct.pack("<2sHIH", b"II", 42, 8, len(entries))
    for tag, t, val in entries:
        raw += struct.pack("<HHII", tag, t, 1, val)
    return raw + struct.pack("<I", 0) + bytes([255, 127, 63, 0])


@pytest.mark.skipif(not shutil.which("tiff2pdf"), reason="libtiff renderer unavailable")
def test_images_preserve_original_hash_and_reproducible_derivative(tmp_path):
    orig = image_tiff()
    raw = archive("board attachment", orig)
    assert supplemental_needed(raw)
    first = list(archive_documents(raw, root=tmp_path))
    assert first == list(archive_documents(raw, root=tmp_path))
    ((info, pdf),) = first
    assert info["source_member_sha256"] == digest(orig)
    assert info["pdf_sha256"] == digest(pdf)
    assert info["source_media"] == "tiff"
    assert pdf.startswith(b"%PDF")


def test_zip_member_identity_and_nested_extensionless_pdf(tmp_path):
    pdf = b"%PDF-1.4\n"
    raw = archive("original.zip", archive("../unknown", pdf))
    assert supplemental_needed(raw)
    ((info, copy),) = archive_documents(raw, root=tmp_path)
    assert info["source_member"] == "original.zip!../unknown"
    assert info["source_media"] == "pdf" and info["source_member_sha256"] == digest(pdf)
    assert copy == pdf
    assert not list(tmp_path.iterdir())
    assert not supplemental_needed(archive("board.pdf", pdf))


def test_zip_limits_apply_before_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr(fundamental_media, "MAX_MEMBER", 1)
    with pytest.raises(ValueError, match="bounded decoder"):
        list(archive_documents(archive("excess.pdf", b"%PDF-1.4"), root=tmp_path))


def test_source_manifest_accounts_for_recovered_earlier_failures(tmp_path):
    import json

    from thesistally.fundamental_manifest import NOTICE_BATCHES, notice_run_state

    urls = [
        "https://archives.nseindia.com/a.pdf",
        "https://archives.nseindia.com/b.pdf",
        "https://archives.nseindia.com/c.pdf",
    ]
    for plan, gaps in NOTICE_BATCHES.values():
        (tmp_path / plan).write_text(json.dumps(urls if plan.startswith("notice-request") else []))
        (tmp_path / gaps).write_text("[]")
    (tmp_path / "notice-request-failures").mkdir()
    for url in urls[:2]:
        (tmp_path / "notice-request-failures" / (digest(url.encode()) + ".json")).write_text(
            json.dumps([url, "No PDF in historical extraction"])
        )
    packets = [{"url": urls[0], "retrieved_url": urls[0], "sha256": "a" * 64}]
    rows, state = notice_run_state(tmp_path, packets)
    assert state["by_status"] == {"archived": 1, "unavailable": 1, "pending": 1}
    assert not state["complete"] and rows[0]["earlier_failure"]
    packets.append({"url": urls[2], "retrieved_url": urls[2], "sha256": "b" * 64})
    _, state = notice_run_state(tmp_path, packets)
    assert state["complete"]


def test_verify_every_source_page_in_a_continuation(tmp_path, monkeypatch):
    import gzip
    from types import SimpleNamespace

    from thesistally import fundamental_nse_ledger as ledger

    source = b"%PDF-1.4\ntest"
    sha = digest(source)
    directory = tmp_path / "notice-objects" / sha[:2]
    directory.mkdir(parents=True)
    (directory / (sha + ".gz")).write_bytes(gzip.compress(source))
    package = {"sha256": sha, "pdfs": [{"pdf_sha256": sha, "pages": ["first", "second"]}]}
    proof = {"pdf_sha256": sha, "page": 1, "pages_used": [1, 2]}
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    original = {"1": "first", "2": "second"}

    def render(cmd, **kwargs):
        return SimpleNamespace(stdout=original[cmd[cmd.index("-f") + 1]].encode() + b"\f")

    monkeypatch.setattr(ledger.subprocess, "run", render)
    out = ledger.verify_notice(package, proof)
    assert out["native_page_text_sha256"] == {"1": digest(b"first"), "2": digest(b"second")}
    original["2"] = "changed"
    with pytest.raises(ValueError, match="no longer matches"):
        ledger.verify_notice(package, proof)


def test_reverse_queue_never_reaches_active_forward_range(tmp_path, monkeypatch):
    from thesistally import fundamental_notice_tail as tail

    plan = [str(i) for i in range(12)]
    already = {"0", "1", "10"}
    monkeypatch.setattr(tail, "terminal", lambda root, url: url in already)
    monkeypatch.setattr(tail, "SEPARATION", 4)
    monkeypatch.setattr(tail, "FORWARD_IN_FLIGHT", 0)
    monkeypatch.setattr(tail, "MAX_IN_FLIGHT", 3)
    f, r, items = tail.next_batch(tmp_path, plan, 0, 11)
    assert (f, r, items) == (2, 7, ["11", "9", "8"])
    assert tail.next_batch(tmp_path, plan, f, r) == (2, 6, ["7"])


def test_dvr_and_ordinary_are_distinct_in_the_dated_bhavcopy():
    from datetime import date

    from thesistally.fundamental_identity import exchange_equity

    raw = b"SYMBOL,SERIES,TIMESTAMP,ISIN\nTATAMOTORS,EQ,01-AUG-2024,INE155A01022\nTATAMTRDVR,EQ,01-AUG-2024,IN9155A01020\n"
    proof = exchange_equity(archive("cm.csv", raw), date(2024, 8, 1))
    assert len(proof["155A"]) == 2


def test_published_checkpoint_requires_same_content_address_for_ledger_and_census(
    tmp_path, monkeypatch
):
    import json

    from thesistally import fundamental_checkpoint

    (tmp_path / "source-manifests").mkdir()
    (tmp_path / "source-manifests/index.json").write_text(json.dumps({"filings_sha256": "a" * 64}))
    (tmp_path / "filings-receipt.json").write_text(json.dumps({"sha256": "b" * 64}))
    monkeypatch.setattr(fundamental_checkpoint, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="matching source census"):
        fundamental_checkpoint.publish()

    (tmp_path / "withdrawals").mkdir()
    (tmp_path / "withdrawals" / ("b" * 64 + ".json")).write_text("{}")
    with pytest.raises(ValueError, match="withdrawn"):
        fundamental_checkpoint.publish()


def test_network_retry_keeps_first_failure_and_honors_all_source_evidence(tmp_path, monkeypatch):
    import json

    from thesistally import fundamental_notice_proof as notices
    from thesistally import fundamental_retry as retries

    url = "https://nsearchives.nseindia.in/corporate/test.pdf"
    key = digest(url.encode())
    original = [url, "[Errno 8] nodename nor servname provided"]
    p = tmp_path / "notice-request-failures" / (key + ".json")
    p.parent.mkdir()
    p.write_text(json.dumps(original))
    monkeypatch.setattr(retries, "ROOT", tmp_path)
    monkeypatch.setattr(notices, "ROOT", tmp_path)
    assert retries.transient(original[1]) and not retries.transient("404 for official resource")
    monkeypatch.setattr(
        retries, "archive_notice", lambda client, url, retry: [url, "404 for official resource"]
    )
    retries.retry_one(None, {"sha256": "b" * 64}, ("notice", url, original[1]))
    assert json.loads(p.read_bytes()) == original
    h = retries.history(tmp_path, "notice", url)
    assert len(h) == 1 and h[0]["status"] == "unavailable" and h[0]["health"]["sha256"] == "b" * 64
    retries.retry_one(None, {}, ("notice", url, original[1]))
    assert len(retries.history(tmp_path, "notice", url)) == 1


def test_mirror_audit_replaces_a_last_origin_404_with_the_complete_official_observation(
    tmp_path, monkeypatch
):
    from thesistally import fundamental_mirror_audit as audit
    from thesistally.fundamental_manifest import failure_state

    monkeypatch.setattr(audit, "ROOT", tmp_path)
    url = "https://nsearchives.nseindia.in/archives/financial_results/old.html"
    first = "Client error '404 Not Found' for last mirror"
    assert audit.needs_check(tmp_path, "structured", url, first)
    incomplete = "Official archive responses: archives.nseindia.com=connection ConnectError; nsearchives.nseindia.in=HTTP 404"
    absent = "Official archive responses: archives.nseindia.com=HTTP 404; nsearchives.nseindia.com=HTTP 404; nsearchives.nseindia.in=HTTP 404"
    responses = iter([incomplete, absent])
    monkeypatch.setattr(audit, "archive_structured", lambda client, url: [url, next(responses)])
    audit.check(None, {"sha256": "b" * 64}, ("structured", url, first))
    assert audit.needs_check(tmp_path, "structured", url, first)
    assert (
        failure_state(tmp_path, "structured", url, first)["status"]
        == "awaiting_healthy_network_retry"
    )
    audit.check(None, {"sha256": "c" * 64}, ("structured", url, first))
    assert not audit.needs_check(tmp_path, "structured", url, first)
    state = failure_state(tmp_path, "structured", url, first)
    assert state["status"] == "unavailable" and state["terminal_official_error"] == absent
    assert len(state["mirror_audit"]) == 2 and state["error"] == first


@pytest.mark.skipif(
    not all(shutil.which(x) for x in ("ffprobe", "ffmpeg", "tiffcp", "tiff2pdf", "pdfinfo")),
    reason="native folio tools unavailable",
)
def test_original_gif_folio_retains_every_frame(tmp_path):
    import re
    import subprocess

    header = bytes.fromhex("47494638396101000100800000000000ffffff")
    frame = bytes.fromhex("21f904000a0000002c0000000001000100000202440100")
    image = header + frame * 2 + b";"
    packet = archive("issuer results.gif", image)
    assert supplemental_needed(packet)
    ((info, pdf),) = archive_documents(packet, root=tmp_path)
    assert info["source_media"] == "gif" and info["source_member_sha256"] == digest(image)
    meta = subprocess.run(["pdfinfo", "-"], input=pdf, capture_output=True, check=True).stdout
    assert re.search(rb"Pages:\s*2\b", meta)
    assert next(archive_documents(packet, root=tmp_path))[0]["pdf_sha256"] == digest(pdf)


def test_complete_acquisition_distinguishes_official_mirror_exhaustion_from_missing_files(tmp_path):
    import json

    from thesistally.fundamental_manifest import NOTICE_BATCHES, notice_run_state
    from thesistally.fundamental_mirror_audit import MAX_ATTEMPTS
    from thesistally.fundamental_retry import transient

    url = "https://nsearchives.nseindia.in/corporate/NETWORK18.pdf"
    error = f"Client error '404 Not Found' for url '{url}'"
    assert not transient(error)
    for name, gaps in NOTICE_BATCHES.values():
        (tmp_path / name).write_text(json.dumps([url]))
        (tmp_path / gaps).write_text("[]")
    key = digest(url.encode())
    f = tmp_path / "notice-request-failures" / (key + ".json")
    f.parent.mkdir()
    f.write_text(json.dumps([url, error]))
    _, open_state = notice_run_state(tmp_path, [])
    assert not open_state["complete"] and open_state["by_status"] == {
        "awaiting_official_mirror_audit": 1
    }
    audits = tmp_path / "mirror-audits/notice" / key
    audits.mkdir(parents=True)
    for n in range(MAX_ATTEMPTS):
        (audits / f"{n:02}.json").write_text(
            json.dumps(
                {
                    "status": "transient",
                    "response": "connection timeout",
                    "health": {"sha256": "a" * 64},
                }
            )
        )
    _, finished = notice_run_state(tmp_path, [])
    assert finished["complete"] and not finished["all_planned_documents_archived"]
    assert finished["bounded_transport_exhaustions"] == 1


def test_download_handover_freezes_network_only_and_requires_finished_attempts(
    tmp_path, monkeypatch
):
    import gzip
    import json
    from pathlib import Path

    from thesistally import fundamental_download_readiness as ready
    from thesistally.fundamental_nse import pack, write_json

    monkeypatch.setattr(ready, "ROOT", tmp_path)
    index = {"notice_acquisition": {"complete": False}}
    src = tmp_path / "source-manifests"
    write_json(src / "index.json", index)
    with pytest.raises(ValueError, match="still pending"):
        ready.publish()
    assert not (tmp_path / "source-downloads-terminal.json").exists()
    copies = {}
    folio = {
        "candidate_url_keys_sha256": digest(b"\n"),
        "readable_source_packets": 0,
        "media_decoder_sha256": digest(Path(fundamental_media.__file__).read_bytes()),
    }
    for name in (
        "notice-folio-pass",
        "transfer-retry-summary",
        "mirror-audit-summary",
        "identity-bhavcopies",
        "notice-case-document-gaps",
        "notice-extra-category-manifest",
        "notice-extra-category-rows",
        "notice-formal-review-manifest",
        "notice-formal-review-rows",
        "notice-categories-document-gaps",
        "notice-fiscal-document-gaps",
        "notice-formal-document-gaps",
        "notice-document-gaps",
        "dated-alias-attachment-gaps",
    ):
        p = tmp_path / (name + ".json")
        write_json(p, folio if name == "notice-folio-pass" else {})
        raw = p.read_bytes()
        sha = digest(raw)
        file = sha + ".json.gz"
        pack(src / file, raw)
        copies[p.name] = {"file": file, "sha256": sha}
    h = digest(b"")
    blob = {"file": h + ".jsonl.gz", "sha256": h, "records": 0}
    pack(src / blob["file"], b"")
    index.update(
        notice_acquisition={"complete": True, "by_status": {}, "planned_urls": 0},
        structured_acquisition_complete=True,
        structured_acquisition={},
        first_notice=blob,
        structured=blob,
        structured_requests=blob,
        raw_notice_receipts=blob,
        inventories=copies,
        accepted_pages={"file": "deliberately-unmaterialized-return-data"},
        filings_sha256="not-a-fundamental-result",
    )
    write_json(src / "index.json", index)
    write_json(tmp_path / "attachment-plan.json", [])
    monkeypatch.setattr(
        ready, "plan_documents", lambda rows, plans: ["https://archives.nseindia.com/first.pdf"]
    )
    with pytest.raises(ValueError, match="lack a planned disposition"):
        ready.publish()
    assert not (tmp_path / "source-downloads-terminal.json").exists()
    monkeypatch.setattr(ready, "plan_documents", lambda rows, plans: [])
    ready.publish()
    marker = json.loads((tmp_path / "source-downloads-terminal.json").read_bytes())
    census = gzip.decompress((tmp_path / marker["source_census_file"]).read_bytes())
    assert marker["network_only"] and digest(census) == marker["source_census_sha256"]
    assert "filings_sha256" not in json.loads(census) and "accepted_pages" not in json.loads(census)
    ready.publish()


def test_unreadable_original_zip_enclosure_blocks_a_proof_of_absence():
    from thesistally.fundamental_media import all_statement_members_readable

    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("statement.pdf", b"%PDF-1.4")
        z.writestr("Other basis.mdi", b"EP*\0opaque")
    assert not all_statement_members_readable(b.getvalue())
    assert all_statement_members_readable(archive("bare-name", b"%PDF-1.4"))
    assert not all_statement_members_readable(
        archive("other.zip", archive("financial statement.txt", b"consolidated figures"))
    )
