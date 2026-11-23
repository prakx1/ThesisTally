"""Preregistration and append-only evidence for discovery experiment families."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .discovery_guard import REPO

REGISTRATIONS = Path("docs/discovery-families")
SAFE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
LABEL = "EXPLORATORY — hypothesis generation only, not evidence"


def identifier(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError(f"Invalid research identifier {value!r}")
    return value


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def write_once(path, raw):
    """Publish complete bytes atomically. A conflicting earlier result stays recoverable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Directory locking avoids transient hard links during concurrent isolation checks.
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(parent, fcntl.LOCK_EX)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError(f"Immutable discovery evidence differs at {path}")
            return
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            stage = Path(tmp.name)
            try:
                tmp.write(raw)
                tmp.flush()
                os.fsync(tmp.fileno())
                stage.replace(path)
            finally:
                stage.unlink(missing_ok=True)
    finally:
        os.close(parent)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Unavailable discovery git provenance: " + result.stderr.decode().strip())
    return result.stdout


def families(repo=REPO):
    result = {}
    ids = set()
    for file in sorted((repo / REGISTRATIONS).glob("*.json")):
        name = file.relative_to(repo).as_posix()
        # First-add commit contains both the machine definition and its dated human registration.
        commit = (
            git(repo, "log", "--diff-filter=A", "-1", "--format=%H", "--", name).decode().strip()
        )
        raw = file.read_bytes()
        if not commit or git(repo, "show", f"{commit}:{name}") != raw:
            raise ValueError(f"Commit the original family definition before validation: {name}")
        family = json.loads(raw)
        key = identifier(family["family"])
        lock = digest(raw)
        prereg = git(repo, "show", f"{commit}:docs/PREREGISTRATION.md")
        cards = family["hypotheses"]
        if (
            key != file.stem
            or key in result
            or lock.encode() not in prereg
            or not 1 <= len(cards) <= 10
        ):
            raise ValueError(f"Invalid or unregistered family {name}")
        for card in cards:
            cid = identifier(card["id"])
            if cid in ids or cid.encode() not in prereg:
                raise ValueError(f"Duplicate or undocumented hypothesis {cid}")
            ids.add(cid)
        result[key] = {**family, "registration_commit": commit, "registration_sha256": lock}
    return result


def proposed(root, family):
    key = family["family"]
    for card in family["hypotheses"]:
        record = {
            "family": key,
            "id": card["id"],
            "status": "proposed",
            "evidence": (REGISTRATIONS / f"{key}.json").as_posix(),
            "hypothesis": card,
            "registration_commit": family["registration_commit"],
            "registration_sha256": family["registration_sha256"],
        }
        write_once(root / "register" / card["id"] / "01-proposed.json", encoded(record))


def register(root, registrations):
    for family in registrations.values():
        proposed(root, family)
    # The archive remains visible even if a future checkout omits old strategy plugins.
    rows = []
    for path in sorted((root / "register").glob("*/01-proposed.json")):
        events = [json.loads(p.read_bytes()) for p in sorted(path.parent.glob("*.json"))]
        if any(e["id"] != path.parent.name for e in events):
            raise ValueError("Hypothesis history does not match its identifier")
        rows.append({**events[-1], "events": events})
    return rows
