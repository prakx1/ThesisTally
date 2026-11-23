"""Host-enforced write boundary for historical research workers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAC_PROFILE = """
(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write-data (literal "/dev/null"))
(allow file-write* (subpath (param "DISCOVERY")))
"""


def output_root(repo=REPO, *, workstream="discovery"):
    repo = Path(repo).resolve(strict=True)
    if workstream not in {"discovery", "fundamentals"}:
        raise ValueError("Unknown isolated research directory")
    directory = repo / "data" / workstream
    # Never follow a data or output symlink during initial directory creation.
    for folder in (repo / "data", directory):
        if folder.is_symlink():
            raise ValueError(f"Discovery directory is a symlink: {folder}")
        folder.mkdir(exist_ok=True)
    # An existing hard link would allow writes to the same inode outside this directory.
    for base, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            path = Path(base, name)
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError(f"Discovery outputs must be independent files: {path}")
    return directory


def confined(
    command, directory, *, cwd=REPO, env=None, check=True, stdin=None, public_collector=None
):
    directory = Path(directory).resolve(strict=True)
    if public_collector is not None:
        from .fundamental_sources import NETWORK, worker_command

        if (
            public_collector not in NETWORK
            or list(command) != worker_command(public_collector)
            or directory != Path(cwd).resolve() / "data/fundamentals"
            or not (Path(cwd) / ".git").is_file()
        ):
            raise ValueError(
                "Public networking requires a registered fundamentals collector in a linked worktree"
            )
    if sys.platform == "darwin":
        profile = "/usr/bin/sandbox-exec"
        policy = (
            MAC_PROFILE if public_collector is None else MAC_PROFILE.replace("(deny network*)", "")
        )
        args = [profile, "-D", f"DISCOVERY={directory}", "-p", policy, *command]
    elif sys.platform.startswith("linux") and (bwrap := shutil.which("bwrap")):
        args = [
            bwrap,
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(directory),
            str(directory),
            *(["--unshare-net"] if public_collector is None else []),
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--",
            *command,
        ]
    else:
        raise RuntimeError("Discovery requires macOS sandbox-exec or Linux bubblewrap.")
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=not isinstance(stdin, bytes),
        input=stdin,
        check=check,
    )


def launch(operation, arguments):
    if not (REPO / ".git").is_file():
        raise ValueError("Run discovery from its dedicated linked git worktree.")
    destination = output_root()
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(REPO / "src"),
        TMPDIR=str(destination),
        TMP=str(destination),
        TEMP=str(destination),
    )
    cmd = [
        sys.executable,
        "-B",
        "-c",
        "from thesistally.discovery_worker import main; main()",
        operation,
        *arguments,
    ]
    result = confined(cmd, destination, env=env, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Discovery failed")
    return result.stdout.rstrip()
