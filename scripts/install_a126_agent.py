"""Install the tagged, public-data A126 paper runtime for macOS login sessions."""

import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.thesistally.a126-paper"


def install_code(repo, runtime, tag):
    # A user LaunchAgent cannot access Documents on default macOS privacy settings.
    # This checkout contains the tagged public code; it is independent of that folder.
    if not (runtime / ".git").is_dir():
        runtime.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(runtime)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(runtime),
            "fetch",
            "--quiet",
            "--depth=1",
            str(repo),
            f"refs/tags/{tag}:refs/tags/{tag}",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(runtime), "checkout", "--quiet", "--detach", tag], check=True)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv for the local frozen environment.")
    subprocess.run([uv, "sync", "--frozen", "--offline", "--project", str(runtime)], check=True)


def install_data(repo, destination, cfg):
    source = repo / "data"
    anchor = cfg["anchor_session"]
    target = destination / "stage3/market/sessions" / anchor
    registered_sources = [Path("stage3/market/sources/stage3-events.json")]
    packages = repo / "src/thesistally/revisions"
    for name in (
        "stage3-holder-actions.json",
        "stage3-entry-holder-actions.json",
        "close-holder-actions.json",
    ):
        for item in json.loads((packages / name).read_bytes())["evidence"]:
            registered_sources.append(Path("stage3/evidence/holder-events") / item["file"])
    anchor_folder = source / "stage3/market/sessions" / anchor
    registered_sources.extend(p.relative_to(source) for p in anchor_folder.iterdir() if p.is_file())
    for path in (source / "sources/reference").glob("actions-*.json"):
        registered_sources.append(path.relative_to(source))
    for name in sorted(set(registered_sources)):
        output = destination / name
        output.parent.mkdir(exist_ok=True, parents=True)
        if not output.exists():
            shutil.copyfile(source / name, output)
        elif output.read_bytes() != (source / name).read_bytes():
            raise SystemExit(f"The installed immutable source differs: {name}")
    if not target.is_dir():
        raise SystemExit("Missing frozen starting archive.")


def main():
    if sys.platform != "darwin":
        raise SystemExit("Schedule 'radar forward-update' with this host's job runner.")
    repo = Path(__file__).resolve().parents[1]
    cfg = json.loads((repo / "src/thesistally/revisions/a126-forward.json").read_bytes())
    tag = cfg["tag"]
    support = Path.home() / "Library/Application Support/ThesisTally"
    runtime = support / "releases" / tag
    data = support / "paper-data"
    install_code(repo, runtime, tag)
    install_data(repo, data, cfg)
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], text=True
    ).strip()
    guard = support / "run_verified_a126.sh"
    body = (repo / "scripts" / guard.name).read_bytes()
    if not guard.exists() or guard.read_bytes() != body:
        tmp = guard.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.chmod(0o700)
        tmp.replace(guard)
    agents = Path.home() / "Library/LaunchAgents"
    logs = Path.home() / "Library/Logs/ThesisTally"
    agents.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    service = f"gui/{os.getuid()}"
    job = f"{service}/{LABEL}"
    plist = agents / (LABEL + ".plist")
    body = plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": ["/bin/sh", str(guard), str(runtime), tag, commit],
            "WorkingDirectory": str(runtime),
            "EnvironmentVariables": {"RADAR_DATA": str(data)},
            "StartInterval": 600,
            "RunAtLoad": True,
            "ProcessType": "Background",
            "StandardOutPath": str(logs / "a126-forward.log"),
            "StandardErrorPath": str(logs / "a126-forward.err"),
        }
    )
    current = (
        subprocess.run(["launchctl", "print", job], capture_output=True, check=False).returncode
        == 0
    )
    if not plist.exists() or plist.read_bytes() != body:
        if current:
            subprocess.run(["launchctl", "bootout", job], check=True)
            current = False
        tmp = plist.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.chmod(0o600)
        tmp.replace(plist)
    if not current:
        subprocess.run(["launchctl", "bootstrap", service, str(plist)], check=True)
    pointer = repo / "data/forward/installation.json"
    pointer.parent.mkdir(exist_ok=True)
    tmp = pointer.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tag": tag, "data_root": str(data)}, indent=2) + "\n")
    tmp.replace(pointer)
    print(
        f"Loaded {LABEL}. launchd runs the tagged paper-only collector at login and every ten minutes."
    )


if __name__ == "__main__":
    main()
