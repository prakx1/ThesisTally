import json
from pathlib import Path

import pytest

from thesistally.discovery_trades import build, checked, signal_levels, signal_rank


def test_closing_stop_features_never_read_fill_price_or_later_conversion():
    # A B fill moved the stop, and the share basis subsequently halved.
    saved = {"entry": 50.0, "stop": 47.0, "features": '{"atr14": 1.0, "planned_stop": 94}'}
    original = {
        "original_features": {"atr14": 2.0, "planned_stop": 94.0},
        "original_entry": 100.0,
        "original_stop": 94.0,
    }
    f, entry, stop = signal_levels(saved, original, "b10")
    assert (f["atr14"], entry, stop) == (2.0, 100.0, 94.0)
    signal = {"features": json.dumps({"planned_stop": 94.0}), "entry": 100.0, "stop": 96.0}
    assert signal_levels(signal, None, "b5")[2] == 94.0
    # Rights events contain the prior feature JSON and levels.
    assert signal_levels(saved, signal, "pullback")[1:] == (100.0, 96.0)


def test_rank_quintiles_follow_strategy_specific_signal_score():
    assert signal_rank("pullback", {"rs_percentile": 1}) == "Q1"
    assert signal_rank("pullback", {"rs_percentile": 0.2}) == "Q5"
    assert signal_rank("pullback", {}) == "unknown"
    assert [signal_rank("b5", {"steady_rank": n}) for n in (1, 8, 9, 40)] == [
        "Q1",
        "Q1",
        "Q2",
        "Q5",
    ]
    with pytest.raises(ValueError):
        signal_rank("b10", {"steady_rank": 41})


def test_changed_source_rejected(tmp_path):
    fake = tmp_path / "replay.json"
    fake.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        checked(fake, "invalid")


def test_audited_historical_sample_and_observation_clocks():
    archive = Path(__file__).resolve().parents[2] / "ThesisTally" / "data"
    if not (archive / "baseline/runs/e923bed5c9d9448d7340/walk-10/journal.sqlite").exists():
        pytest.skip("the optional archived equal-risk journals are unavailable")
    frame, metadata = build(archive)
    assert frame.groupby("strategy").counted.sum().to_dict() == {
        "pullback": 2847,
        "b10": 1175,
        "b5": 515,
    }
    assert not frame.trade_id.duplicated().any()
    assert (frame.closed_day >= frame.buy_day).all()
    assert (frame.day < frame.buy_day).all()
    assert frame.full_horizon_day.notna().equals(frame.mature)
    for key in metadata:
        assert len(metadata[key]) == 10
        assert all(
            w["sessions"][0] == w["start"] and w["sessions"][-1] == w["end"]
            for w in metadata[key].values()
        )
    assert (frame.loc[frame.counted, "coverage_eligible"]).all()
    assert metadata["pullback"]["walk-1"]["planned_entries"] is None
    assert metadata["b10"]["walk-1"]["planned_entries"] == 629
    assert metadata["b10"]["walk-1"]["blocked_data"] == 34


def test_archive_plan_denominators_include_independently_censored_data_blocks():
    from thesistally.discovery_trades import build_plans

    archive = Path(__file__).resolve().parents[2] / "ThesisTally" / "data"
    if not (archive / "stage3/runs/dip-e3687b17ddfbbdf17642/walk-10/journal.sqlite").is_file():
        pytest.skip("The optional archived B journals are unavailable")
    plans = build_plans(archive)
    assert not plans.trade_id.duplicated().any()
    assert plans.groupby("strategy").size().to_dict() == {"b10": 3413, "b5": 1546}
    assert plans.groupby("strategy").blocked_data.sum().to_dict() == {"b10": 570, "b5": 248}
    assert plans.loc[plans.blocked_data, "signal_close"].gt(0).all()
    assert (
        plans.loc[plans.blocked_data, "signal_rank_quintile"]
        .isin([f"Q{n}" for n in range(1, 6)])
        .all()
    )
