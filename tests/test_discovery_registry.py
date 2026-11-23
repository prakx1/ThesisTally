import hashlib
import json
import subprocess

import pandas as pd
import pytest

from thesistally import discovery_plugins as adapters
from thesistally import discovery_registry as registry


def family():
    return {
        "family": "discovery-test",
        "hypotheses": [
            {"id": "example-1", "strategy": "sample", "description": "precommitted filter"}
        ],
    }


def run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_family_requires_original_committed_document_and_keeps_old_register(tmp_path):
    repo = tmp_path
    run_git(repo, "init", "-q")
    folder = repo / registry.REGISTRATIONS
    folder.mkdir(parents=True)
    p = folder / "discovery-test.json"
    p.write_text(json.dumps(family()))
    with pytest.raises(ValueError):
        registry.families(repo)
    (repo / "docs/PREREGISTRATION.md").write_text(
        "example-1 " + hashlib.sha256(p.read_bytes()).hexdigest()
    )
    run_git(repo, "add", "docs")
    run_git(
        repo,
        "-c",
        "user.name=Research Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "preregister",
    )
    frozen = registry.families(repo)
    out = tmp_path / "data/discovery"
    first = registry.register(out, frozen)
    assert first[0]["status"] == "proposed" and first == registry.register(out, {})
    record = out / "register/example-1/01-proposed.json"
    with pytest.raises(ValueError, match="Immutable"):
        registry.write_once(record, b"replacement")
    p.write_text(p.read_text() + " ")
    with pytest.raises(ValueError, match="original"):
        registry.families(repo)


def test_external_plugin_round_trip_and_tamper_detection(tmp_path, monkeypatch):
    class Plugin:
        def load(self, archive):
            assert archive == tmp_path
            return pd.DataFrame({"day": ["2021-01-04"], "net_r": [0.5]}), {"w1": {}}

    class Example:
        name = "my_strategy"

        def load(self):
            return Plugin

    monkeypatch.setattr(adapters.metadata, "entry_points", lambda **kwargs: [Example()])
    out = tmp_path / "data/discovery"
    frame, receipt = adapters.sample(out, tmp_path, "my_strategy")
    assert tuple(frame.net_r) == (0.5,) and receipt["windows"] == {"w1": {}}
    (out / "samples/my_strategy/trades.parquet").write_bytes(b"invalid")
    with pytest.raises(ValueError, match="receipt"):
        adapters.sample(out, tmp_path, "my_strategy")
