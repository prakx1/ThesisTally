"""Keep a retrievable source commit for local studies, including uncommitted checkouts."""

import fcntl
import hashlib
import subprocess
from pathlib import Path


def snapshot(root):
    package = Path(__file__).resolve().parent
    checkout = package.parent.parent
    folder = (root / "provenance" / "source").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    tracked = {
        Path("src/thesistally") / p.relative_to(package): p
        for p in package.rglob("*")
        if p.is_file() and p.suffix in {".py", ".yaml", ".json"}
    }
    for name in ("pyproject.toml", "uv.lock"):
        if (checkout / name).is_file():
            tracked[Path(name)] = checkout / name

    def git(*args, check=True):
        return subprocess.run(
            [
                "git",
                "-C",
                str(folder),
                "-c",
                "user.name=ThesisTally",
                "-c",
                "user.email=research@localhost",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            check=check,
            capture_output=True,
            text=True,
        )

    digest = hashlib.sha256()
    with (folder.parent / ".source.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (folder / ".git").exists():
            git("init", "--quiet")
        for name in git("ls-files").stdout.splitlines():
            if Path(name) not in tracked:
                (folder / name).unlink(missing_ok=True)
        for name, original in sorted(tracked.items()):
            raw = original.read_bytes()
            digest.update(str(name).encode() + b"\0" + raw)
            path = folder / name
            if path.exists() and path.read_bytes() == raw:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        git(
            "add",
            "-A",
            "--",
            "src",
            *[k for k in ("pyproject.toml", "uv.lock") if (folder / k).exists()],
        )
        if git("diff", "--cached", "--quiet", check=False).returncode:
            git("commit", "--quiet", "-m", "Archive research source " + digest.hexdigest()[:12])
        source = git("rev-parse", "HEAD").stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "code_hash": digest.hexdigest(),
        "code_commit": source,
        "checkout_commit": head.stdout.strip() if head.returncode == 0 else None,
    }


def journal_proof(root, db, run, include_ai=False):
    """Fingerprint exactly the local evidence used for a newly generated journal report."""
    import json

    from .journal import saved_rules

    digest = hashlib.sha256()
    for table in [
        "calls",
        "events",
        "fills",
        "entitlements",
        "corporate_cash",
        "corporate_receivables",
    ] + (["ai_verdicts", "research_reports"] if include_ai else []):
        sql = (
            "SELECT * FROM calls WHERE run=? ORDER BY id"
            if table == "calls"
            else f"SELECT t.* FROM {table} t JOIN calls c ON t.call_id=c.id WHERE c.run=? ORDER BY t.rowid"
        )
        for row in db.execute(sql, (run,)):
            digest.update(json.dumps([table, dict(row)], sort_keys=True).encode())
    if include_ai:
        # Monthly spending in the audit also includes on-demand symbol research.
        for row in db.execute("SELECT * FROM ai_calls ORDER BY id"):
            digest.update(json.dumps(["ai_calls", dict(row)], sort_keys=True).encode())
    revisions = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT revision FROM calls WHERE run=? ORDER BY revision", (run,)
        )
    ]
    configs = {revision: saved_rules(db, revision)["config_hash"] for revision in revisions}
    return {
        "rule_revision": revisions,
        "config_hash": configs,
        "snapshot_hash": digest.hexdigest(),
        **snapshot(root),
        "model_version": [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT model_version FROM calls WHERE run=? ORDER BY 1", (run,)
            )
        ],
        "prompt_version": [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT v.prompt_version FROM ai_verdicts v JOIN calls c ON v.call_id=c.id WHERE c.run=? ORDER BY 1",
                (run,),
            )
        ]
        if include_ai
        else [],
    }
