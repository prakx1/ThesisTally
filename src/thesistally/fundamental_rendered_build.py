"""Archive independent optical witnesses for conflicting original quarterly basic EPS cells."""

import gzip
import json
import os
import platform
import subprocess
import sys
from decimal import Decimal

from .discovery_guard import REPO, confined, output_root
from .discovery_registry import digest, encoded, write_once
from .fundamental_media import archive_documents
from .fundamental_rendered import METHOD, identity, receipt_path, scalar, selector

SOURCE = REPO / "research/results_rendered_page.swift"


def candidates(root):
    receipt = json.loads((root / "filings-receipt.json").read_bytes())
    if "field_reviews" in receipt:
        review = receipt["field_reviews"]
        raw = gzip.decompress((root / review["file"]).read_bytes())
        if digest(raw) != review["sha256"]:
            raise ValueError("Changed field-quarantine edition")
        for r in json.loads(raw):
            eps = r["rejected"].get("eps", {})
            if eps.get("structured_value") is not None:
                yield r["id"], eps["structured_value"], r["notice_url"], r["notice_sha256"], r
    else:
        raw = gzip.decompress((root / "vintages" / (receipt["sha256"] + ".jsonl.gz")).read_bytes())
        if digest(raw) != receipt["sha256"]:
            raise ValueError("Changed provisional filing edition")
        for line in raw.splitlines():
            r = json.loads(line)
            proof = r.get("notice_proof") or {}
            later = r["structured_values"]["eps"]
            if (
                r["first_reported"]
                and proof.get("method") == "issuer_heading_dated_first_pdf_column"
                and later is not None
                and r["eps"] is not None
                and r["eps"] != later
            ):
                yield r["id"], later, proof["notice_url"], proof["source_sha256"], proof


def original_pdf(root, sha, proof):
    blob = gzip.decompress((root / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(blob) != sha:
        raise ValueError("Changed original announcement")
    pdf = next(
        doc
        for info, doc in archive_documents(blob, root=root)
        if info["pdf_sha256"] == proof["pdf_sha256"]
    )
    if digest(pdf) != proof["pdf_sha256"]:
        raise ValueError("Changed original PDF member")
    return pdf


def render_png(root, pdf, page, size):
    return confined(
        [
            "pdftoppm",
            "-q",
            "-f",
            page,
            "-l",
            page,
            "-singlefile",
            "-scale-to",
            size,
            "-png",
            "-",
        ],
        root.parent,
        stdin=pdf,
    ).stdout


def page_image(root, program, pdf, page, size):
    png = render_png(root, pdf, page, size)
    boxes_raw = encoded(json.loads(confined([str(program)], root.parent, stdin=png).stdout))
    return png, boxes_raw


def witness(root, program, row, url, sha, proof, tools):
    key_fields = identity(row, {"source_url": url}, {"sha256": sha}, proof, "eps")
    key = digest(encoded(key_fields))
    path = receipt_path(root, key)
    if path.is_file():
        return "verified"
    attempt = root / "rendered-page-attempts" / digest(encoded(tools)) / (key + ".json")
    if attempt.is_file():
        return json.loads(attempt.read_bytes())["status"]
    pdf = original_pdf(root, sha, proof)
    page = str(proof["page"])
    status = "unresolved_on_original_pixels"
    readings = []
    # The second size is consulted only if the first transcription is unreadable.
    # Disagreement about an otherwise legible number remains an open discrepancy.
    for size in ("3200", "2400"):
        png, boxes_raw = page_image(root, program, pdf, page, size)
        boxes = json.loads(boxes_raw)
        observation = selector(row, boxes)
        value = scalar(observation["cell"]["text"]) if observation else None
        capsule = {
            "image_long_edge": int(size),
            "rendered_page_sha256": digest(png),
            "boxes_sha256": digest(boxes_raw),
            "observation": observation,
        }
        readings.append(capsule)
        if value is None:
            continue
        if value != Decimal(str(row["eps"])):
            status = "readable_original_differs_from_structured"
            break
        objects = root / "rendered-page-objects"
        for name, data, suffix in (
            ("rendered_page_sha256", png, ".png"),
            ("boxes_sha256", gzip.compress(boxes_raw, mtime=0), ".json.gz"),
        ):
            ref = capsule[name]
            write_once(objects / ref[:2] / (ref + suffix), data)
        write_once(
            path, encoded({"identity": key_fields, "method": METHOD, "tools": tools, **capsule})
        )
        status = "verified"
        break
    write_once(
        attempt,
        encoded({"identity": key_fields, "tools": tools, "status": status, "readings": readings}),
    )
    return status


def environment():

    if sys.platform != "darwin" or not (REPO / ".git").is_file():
        raise ValueError("Use the isolated results worktree on macOS")
    data = output_root(workstream="fundamentals")
    root = data / "nse"
    build = data / "ocr-build"
    build.mkdir(exist_ok=True)
    options = {
        "source_sha256": digest(SOURCE.read_bytes()),
        "selector_sha256": digest((REPO / "src/thesistally/fundamental_rendered.py").read_bytes()),
        "macos_build": subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip(),
        "poppler": subprocess.run(
            ["pdftoppm", "-v"], capture_output=True, text=True, check=True
        ).stderr.splitlines()[0],
        "platform": platform.machine(),
    }
    binary = build / ("page-verifier-" + options["source_sha256"][:16])
    if not binary.exists():
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-O",
                "-module-cache-path",
                str(build / "cache"),
                str(SOURCE),
                "-o",
                str(binary),
            ],
            check=True,
            env={**os.environ, "TMPDIR": str(build)},
        )
    return root, binary, options


def main():
    from collections import Counter

    root, binary, options = environment()
    from .fundamental_nse import load_plans

    plans = {r["id"]: r for r in load_plans(root)}
    counts = Counter()
    for filing_id, eps, url, sha, proof in candidates(root):
        if filing_id in plans:
            counts[
                witness(root, binary, {**plans[filing_id], "eps": eps}, url, sha, proof, options)
            ] += 1
    print(json.dumps(counts, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
