"""Enumerate original PDF and image folios inside official issuer result attachments."""

from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .fundamental_nse import ROOT, digest

MAX_MEMBER = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ENTRIES = 1024


def media(raw):
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        return "zip"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        return "tiff"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw.startswith(b"BM"):
        return "bmp"
    return None


def gif_folio(raw, work, tif):
    # Retain all displayed frames in source order. Never flatten an animated or
    # multipage issuer release to a single picture.
    width = int.from_bytes(raw[6:8], "little")
    height = int.from_bytes(raw[8:10], "little")
    if not 0 < width * height <= 120_000_000:
        raise ValueError("Original GIF statement exceeds the bounded raster decoder")
    info = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-f",
            "gif",
            "-count_frames",
            "-show_entries",
            "stream=width,height,nb_read_frames",
            "-of",
            "json",
            "-i",
            "pipe:0",
        ],
        input=raw,
        capture_output=True,
        check=False,
        timeout=30,
    )
    streams = json.loads(info.stdout).get("streams", []) if not info.returncode else []
    if len(streams) != 1:
        raise ValueError("Cannot verify the original GIF statement dimensions")
    try:
        width, height, count = [int(streams[0][k]) for k in ("width", "height", "nb_read_frames")]
    except (KeyError, ValueError) as e:
        raise ValueError("Original GIF statement has no verified frame count") from e
    if min(width, height, count) < 1 or count > 120 or width * height * count > 120_000_000:
        raise ValueError("Original GIF statement exceeds the bounded raster decoder")
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "gif",
            "-ignore_loop",
            "1",
            "-i",
            "pipe:0",
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
            "-c:v",
            "tiff",
            "-threads",
            "1",
            "-compression_algo",
            "deflate",
            str(work / "frame-%05d.tif"),
        ],
        input=raw,
        capture_output=True,
        check=False,
        timeout=60,
    )
    pages = sorted(work.glob("frame-*.tif"))
    if decoded.returncode or len(pages) != count:
        raise ValueError("GIF statement decoder did not reproduce every original frame")
    if count == 1:
        pages[0].rename(tif)
    else:
        joined = subprocess.run(
            ["tiffcp", *(str(p) for p in pages), str(tif)],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if joined.returncode:
            raise ValueError("Cannot assemble the statement’s original GIF frames")


def _image_pdf(raw, kind, root):
    # libtiff preserves every TIFF directory (page). Use an explicit title/date
    # and replace only its fixed-width pair of generated PDF document identifiers.
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        work = Path(temporary)
        tif = work / "page.tif"
        if kind == "tiff":
            tif.write_bytes(raw)
        elif kind == "gif":
            gif_folio(raw, work, tif)
        else:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    "image2pipe",
                    "-i",
                    "pipe:0",
                    "-frames:v",
                    "1",
                    "-map_metadata",
                    "-1",
                    "-c:v",
                    "tiff",
                    "-threads",
                    "1",
                    "-compression_algo",
                    "deflate",
                    str(tif),
                ],
                input=raw,
                capture_output=True,
                check=False,
                timeout=60,
            )
            if result.returncode:
                raise ValueError("Cannot decode original issuer image")
        path = work / "page.pdf"
        rendered = subprocess.run(
            [
                "tiff2pdf",
                "-e",
                "19700101000000",
                "-c",
                "ThesisTally",
                "-a",
                "issuer",
                "-t",
                "exchange filing",
                "-s",
                "financial results",
                "-k",
                "quarterly results",
                "-o",
                str(path),
                str(tif),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if rendered.returncode:
            raise ValueError("Cannot render original TIFF folio")
        pdf = path.read_bytes()
    ident = digest(raw)[:32].upper().encode()
    pat = rb"(/ID\s*\[\s*<)([0-9a-fA-F]{32})(>\s*<)([0-9a-fA-F]{32})(>\s*\])"
    pdf = re.sub(pat, lambda m: m[1] + ident + m[3] + ident + m[5], pdf)
    if not pdf.startswith(b"%PDF"):
        raise ValueError("Invalid image folio rendering")
    return pdf


def archive_documents(raw, *, root=ROOT):
    """Yield (member provenance, PDF bytes). Never extract a ZIP path to disk."""
    state = {"bytes": 0, "entries": 0}

    def walk(blob, label, depth):
        typ = media(blob)
        if typ == "zip":
            if depth >= 2:
                raise ValueError("Nested result archive exceeds two layers")
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for f in archive.infolist():
                    if f.is_dir():
                        continue
                    state["entries"] += 1
                    state["bytes"] += f.file_size
                    if (
                        state["entries"] > MAX_ENTRIES
                        or state["bytes"] > MAX_TOTAL
                        or f.file_size > MAX_MEMBER
                    ):
                        raise ValueError("Issuer archive exceeds the documented bounded decoder")
                    if f.flag_bits & 1:
                        raise ValueError("Encrypted issuer attachment")
                    try:
                        contents = archive.read(f)
                    except (RuntimeError, NotImplementedError) as e:
                        raise ValueError("Unsupported issuer archive compression") from e
                    yield from walk(
                        contents, (label + "!" if label else "") + f.filename, depth + 1
                    )
            return
        if typ not in {"pdf", "jpeg", "tiff", "bmp", "gif"}:
            return
        pdf = blob if typ == "pdf" else _image_pdf(blob, typ, root)
        yield (
            {
                "source_member": label,
                "source_member_sha256": digest(blob),
                "source_media": typ,
                "pdf_sha256": digest(pdf),
                "rendering": "original_pdf" if typ == "pdf" else "canonical_tiff2pdf_v1",
            },
            pdf,
        )

    yield from walk(raw, None, 0)


def all_statement_members_readable(raw, *, depth=0):
    """An omitted attachment cannot establish the absence of the opposite accounting basis."""
    typ = media(raw)
    if typ != "zip":
        return typ in {"pdf", "jpeg", "tiff", "bmp", "gif"}
    if depth >= 2:
        return False
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(f.file_size for f in entries) > MAX_TOTAL:
            return False
        for f in entries:
            if (
                f.is_dir()
                or Path(f.filename).name.lower() in {"thumbs.db", ".ds_store", "desktop.ini"}
                or f.filename.startswith("__MACOSX/")
            ):
                continue
            if f.flag_bits & 1 or f.file_size > MAX_MEMBER:
                return False
            with archive.open(f) as stream:
                head = stream.read(8)
                if media(head) == "zip":
                    if not all_statement_members_readable(head + stream.read(), depth=depth + 1):
                        return False
                elif media(head) not in {"pdf", "jpeg", "tiff", "bmp", "gif"}:
                    return False
    return True
