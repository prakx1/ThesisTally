"""Install the standalone public results collector for the current macOS login."""

import hashlib
import importlib.metadata
import os
import plistlib
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

LABEL = "com.thesistally.results-filings"
SUPPORT = Path.home() / "Library/Application Support/ThesisTally-results"
MODULES = (
    "__init__",
    "results_forward",
    "results_forward_nse",
    "results_forward_bse",
    "results_forward_roster",
    "results_forward_watch",
    "fundamental_nse",
    "fundamental_notices",
    "fundamental_notice_proof",
    "fundamental_period",
)


def replace(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(content)
    staging = Path(tmp.name)
    staging.chmod(0o600)
    staging.replace(path)


def install():
    if sys.platform != "darwin":
        raise SystemExit("This installer requires macOS launchd")
    repo = Path(__file__).resolve().parents[1]
    files = {
        name + ".py": subprocess.check_output(
            ["git", "-C", str(repo), "show", "HEAD:src/thesistally/" + name + ".py"]
        )
        for name in MODULES
    }
    # The installer runs inside the research venv. Reuse its exact, public HTTP
    # dependency versions in a separate runtime that can run outside Documents.
    deps = ("httpx", "httpcore", "certifi", "idna", "anyio", "sniffio", "h11", "typing_extensions")
    pins = [f"{name}=={importlib.metadata.version(name)}" for name in deps]
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + content)
    digest.update(("\n".join(pins) + sys.version).encode())
    revision = digest.hexdigest()[:16]
    release = SUPPORT / "releases" / revision
    package = release / "app/thesistally"
    package.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = package / name
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Existing installed collector differs from its release")
        if not path.exists():
            replace(path, content)
    env = release / "venv"
    py = env / "bin/python"
    if not py.exists():
        venv.EnvBuilder(symlinks=True, with_pip=False).create(env)
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--python", str(py), "--no-deps", *pins], check=True
    )
    logs = SUPPORT / "logs"
    logs.mkdir(exist_ok=True)
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(exist_ok=True)
    plist = agents / (LABEL + ".plist")
    body = plistlib.dumps(
        {
            "Label": LABEL,
            "RunAtLoad": True,
            "StartInterval": 1800,
            "WorkingDirectory": str(release / "app"),
            "ProgramArguments": [str(py), "-B", "-m", "thesistally.results_forward"],
            "EnvironmentVariables": {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            "StandardOutPath": str(logs / "collection.log"),
            "StandardErrorPath": str(logs / "collection.err"),
            "ProcessType": "Background",
        }
    )
    # Prove the installed release can import its sources without access to the research checkout.
    subprocess.run(
        [str(py), "-B", "-c", "from thesistally.results_forward import main"],
        cwd=release / "app",
        env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
        check=True,
    )
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    loaded = (
        subprocess.run(["launchctl", "print", target], check=False, capture_output=True).returncode
        == 0
    )
    if not plist.exists() or plist.read_bytes() != body:
        if loaded:
            subprocess.run(["launchctl", "bootout", target], check=True)
            loaded = False
        replace(plist, body)
    subprocess.run(["launchctl", "enable", target], check=True)
    if not loaded:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print(f"{target} · 30-minute interval · release {revision}")
    print(SUPPORT / "data/fundamentals/forward/filings.sqlite3")


if __name__ == "__main__":
    install()
