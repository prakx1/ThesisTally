import gzip
import json

import pytest

from thesistally.discovery_registry import digest, encoded
from thesistally.fundamental_modern_build import read_plan


def test_only_the_pinned_modern_plan_can_be_resumed(tmp_path):
    plans = tmp_path / "xbrl-optical-plans"
    plans.mkdir()
    body = encoded({"source_edition": "ledger", "selection_version": "selector", "jobs": []})
    key = digest(body)
    file = plans / (key + ".json.gz")
    file.write_bytes(gzip.compress(body))
    assert read_plan(tmp_path, key) == json.loads(body)
    file.write_bytes(gzip.compress(body + b" "))
    with pytest.raises(ValueError, match="fingerprint changed"):
        read_plan(tmp_path, key)
    with pytest.raises(ValueError, match="Invalid quarterly"):
        read_plan(tmp_path, "../outside")
