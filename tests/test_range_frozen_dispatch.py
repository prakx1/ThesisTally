from pathlib import Path

import pytest

from thesistally import frozen_dispatch as dispatch
from thesistally import frozen_forward


def test_research_engine_drift_reads_only_the_original_frozen_tag(tmp_path, monkeypatch):
    if not frozen_forward.tagged({"tag": "a126-forward-2026-11-21"}):
        pytest.skip("The frozen git tag must be fetched for this integration test")
    monkeypatch.setattr(frozen_forward, "engine_sha256", lambda: "research-engine")
    result = dispatch.invoke(tmp_path)
    assert result["revision"] == "a126-forward-2026-11-21"
    assert result["freeze_tag_verified"] and result["sessions"] == 0
    assert not (tmp_path / "forward").exists()
    monkeypatch.setattr(dispatch, "FROZEN_COMMIT", "wrong")
    with pytest.raises(dispatch.market.MarketError, match="differs"):
        dispatch.invoke(Path(tmp_path))
