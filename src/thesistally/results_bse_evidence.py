"""Seal and replay the supplemental BSE count using portable source objects."""

import base64
import gzip
import io
import json
import re
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

from . import results_bse_census as census
from .discovery_registry import digest, encoded, write_once


def add(tar, name, body):
    entry = tarfile.TarInfo(name)
    entry.size = len(body)
    entry.mode = 0o444
    tar.addfile(entry, io.BytesIO(body))


def control_receipts(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        database.execute("BEGIN")
        lines = []
        for obs in database.execute("SELECT * FROM observations ORDER BY id"):
            raw = census.raw_by_hash(database, obs["sha"]) if obs["sha"] else None
            row = dict(obs) | {
                "body_base64": base64.b64encode(raw).decode("ascii") if raw is not None else None
            }
            lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
        return ("\n".join(lines) + "\n").encode()


def pack(root, db):
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as g,
        tarfile.open(fileobj=g, mode="w|") as tar,
    ):
        for path in sorted(root.glob("issuer-leads*.json")):
            add(tar, path.name, path.read_bytes())
        receipts = b"".join(
            (json.dumps(dict(r), sort_keys=True, separators=(",", ":")) + "\n").encode()
            for r in db.execute("SELECT * FROM observations ORDER BY id")
        )
        add(tar, "observations.jsonl", receipts)
        controls = root / "inline" / "controls" / "source-archive.sqlite3"
        if controls.is_file():
            add(tar, "controls.jsonl", control_receipts(controls))
        for sha, body in db.execute("SELECT sha,body FROM objects ORDER BY sha"):
            raw = gzip.decompress(body)
            if digest(raw) != sha:
                raise ValueError("Portable BSE source does not match recorded SHA-256")
            add(tar, "objects/" + sha, raw)
    return buffer.getvalue()


def restore(root, raw):
    with census.connection(root) as db, tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                raise ValueError("Unexpected compressed BSE evidence member")
            body = tar.extractfile(member).read()
            if re.fullmatch(r"issuer-leads[\w-]*\.json", member.name):
                write_once(root / member.name, body)
            elif member.name == "controls.jsonl":
                for line in body.splitlines():
                    row = json.loads(line)
                    if (
                        row["sha"]
                        and digest(base64.b64decode(row["body_base64"], validate=True))
                        != row["sha"]
                    ):
                        raise ValueError("Corrupt XBRL retrieval control")
                write_once(root / "controls.jsonl", body)
            elif member.name == "observations.jsonl":
                for line in body.splitlines():
                    values = json.loads(line)
                    db.execute(
                        "INSERT INTO observations(id,url,observed,status,kind,sha,headers,error) VALUES(:id,:url,:observed,:status,:kind,:sha,:headers,:error)",
                        values,
                    )
            elif (
                re.fullmatch(r"objects/[a-f0-9]{64}", member.name)
                and digest(body) == member.name[8:]
            ):
                db.execute(
                    "INSERT INTO objects VALUES(?,?)",
                    (digest(body), gzip.compress(body, compresslevel=1, mtime=0)),
                )
            else:
                raise ValueError("Unrecognized or corrupt BSE source member")
        missing = db.execute(
            "SELECT COUNT(*) FROM observations WHERE sha IS NOT NULL AND sha NOT IN (SELECT sha FROM objects)"
        ).fetchone()[0]
        if missing:
            raise ValueError("A portable BSE receipt is missing its object")
        db.commit()


def seal():
    root = census.ROOT
    result = census.report(root=root)
    schedule = result["request_coverage"]
    if any(
        schedule[k]
        for k in (
            "incomplete_catalogue_requests",
            "unresolved_gap_urls_with_fewer_than_three_attempts",
            "unresolved_priority_urls_with_fewer_than_three_attempts",
        )
    ):
        raise RuntimeError("The bounded public source census is still in progress")
    report_sha = digest(encoded(result))
    with census.connection(root) as db:
        db.execute("BEGIN")
        if db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] != sum(
            result["observations"].values()
        ):
            raise RuntimeError("A source receipt arrived after the count snapshot")
        raw = pack(root, db)
    folder = root / "reports" / report_sha
    write_once(folder / "sources.tar.gz", raw)
    write_once(
        root / "sealed.json",
        encoded(
            {
                "coverage_sha256": report_sha,
                "source_bundle_sha256": digest(raw),
                "source_bundle_bytes": len(raw),
            }
        ),
    )
    print(f"Sealed {report_sha} with source bundle {digest(raw)}")


def verify():
    root = census.ROOT
    manifest = json.loads((root / "sealed.json").read_bytes())
    sha = manifest["coverage_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("Invalid coverage hash")
    folder = root / "reports" / sha
    raw = (folder / "sources.tar.gz").read_bytes()
    if digest(raw) != manifest["source_bundle_sha256"]:
        raise ValueError("Portable BSE evidence bundle hash mismatch")
    with tempfile.TemporaryDirectory(dir=root) as temp:
        target = Path(temp)
        restore(target, raw)
        actual = census.report(root=target)
        if digest(encoded(actual)) != sha or digest((folder / "coverage.json").read_bytes()) != sha:
            raise ValueError("Restored public BSE count differs from the sealed result")
        for name in ("windows.csv", "stock-quarters.csv", "previously-covered.csv"):
            if (folder / name).read_bytes() != (target / "reports" / sha / name).read_bytes():
                raise ValueError(f"Restored BSE count differs for {name}")
    print(f"Verified sources, numerical output and all three CSVs for {sha}")
