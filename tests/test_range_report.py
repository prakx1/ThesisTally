import pandas as pd
import pytest

from thesistally.range_report import (
    TIME_CLOSE,
    account_summary,
    bounce_counts,
    compare,
    pooled,
    selection_counts,
)


def position(due=None, exit_reason=None, mature=True, buy="2020-01-02", status="closed"):
    return {
        "buy_day": buy,
        "mature": mature,
        "first_exit_due": due,
        "exit_reason": exit_reason,
        "status": status,
    }


def test_persistence_observability_and_first_trigger_do_not_depend_on_profit_or_sale():
    result = bounce_counts(
        [
            position("Range target close"),
            position("Range target close", status="active"),
            position("Closing stop"),
            position(exit_reason=TIME_CLOSE),
            position("Range target close", mature=False),
            position(status="unmeasurable"),
            position(status="active"),
            position(exit_reason="Compulsory delisting settlement"),
            position("Range target close", buy=None),
        ]
    )
    assert result == {
        "target": 2,
        "stop_or_time": 2,
        "horizon_censored": 1,
        "path_censored": 2,
        "early_compulsory_exit": 1,
    }
    other = bounce_counts(
        [position("Closing stop"), position("Range target close"), position(exit_reason=TIME_CLOSE)]
    )
    pair = compare(result, other)
    assert pair["range"]["rate"] == 0.5
    assert pair["difference_pp"] == pytest.approx(100 * (0.5 - 1 / 3))
    assert compare(bounce_counts([]), other)["difference_pp"] is None
    grouped = pooled([pair, compare(bounce_counts([position("Range target close")]), other)])
    assert grouped["range"]["rate"] == 3 / 5
    assert grouped["other"]["rate"] == 2 / 6
    with pytest.raises(ValueError, match="trigger"):
        bounce_counts([position("Time stop without 1R")])


def test_classification_and_daily_unobservable_stay_separate_by_exact_window_dates():
    days = ["2019-12-30", "2019-12-31"]
    daily = pd.DataFrame(
        {"day": days, "members": [200, 201], "daily_unobservable": [3, 1], "raw_setups": [1, 2]}
    )
    signals = pd.DataFrame(
        {
            "day": [*days, days[1], "2020-01-01"],
            "cohort": ["range", "unknown", "ineligible", "other"],
            "reason": ["", "no prior low", "liquidity", ""],
        }
    )
    s = selection_counts(signals, daily, days)
    assert s["by_cohort"] == {"range": 1, "other": 0, "unknown": 1, "ineligible": 1}
    assert s["member_sessions"] == 401 and s["daily_unobservable"] == 4
    assert s["unclassified_reasons"] == {"no prior low": 1}
    with pytest.raises(ValueError, match="raw signals"):
        selection_counts(signals.iloc[1:], daily, days)


def test_matched_benchmark_uses_prior_close_and_last_observed_mark():
    marks = [
        {"day": "2020-01-01", "equity": 90, "invested": 45},
        {"day": "2020-01-02", "equity": 105, "invested": 0},
    ]
    data = account_summary(
        marks,
        100,
        {"2019-12-31": 120, "2020-01-01": 130, "2020-01-02": 144},
        "2019-12-31",
        {"scanned": 2},
    )
    assert data == pytest.approx(
        {"net_return_pct": 5, "tri_pct": 20, "max_drawdown_pct": 10, "exposure_pct": 25}
    )


def test_modified_setup_fields_or_coverage_cannot_publish(tmp_path):
    import json

    from thesistally.range_report import sha_file, verify_selection

    products = {}
    for name in ("signals.parquet", "coverage.parquet", "monthly.parquet"):
        path = tmp_path / name
        path.write_bytes(b"registered")
        products[name] = {"sha256": sha_file(path)}
    manifest = tmp_path / "manifest.json"
    receipt = {"products": products, "snapshot_sha256": "certified"}
    manifest.write_text(json.dumps(receipt))
    proof = {
        "signals_sha256": products["signals.parquet"]["sha256"],
        "signal_manifest_sha256": sha_file(manifest),
        "market": {"snapshot_sha256": "certified"},
    }
    assert verify_selection(tmp_path, proof) == receipt
    (tmp_path / "coverage.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="coverage.parquet"):
        verify_selection(tmp_path, proof)
