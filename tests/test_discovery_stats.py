import json

import numpy as np
import pandas as pd
import pytest

from thesistally.discovery_stats import (
    LABEL,
    benjamini_hochberg,
    block_contributions,
    explore,
    select_window,
    validate_card,
    validate_family,
)


def records(w, year, *, both=True):
    days = [d.date().isoformat() for d in pd.bdate_range(f"{year}-01-04", periods=80)]
    rows = []
    for i, day in enumerate(days[:-20]):
        for value in ("low", "high") if both else ("high",):
            value_r = (0.8 + 0.2 * np.cos(i / 8)) if value == "low" else -0.6 + 0.1 * np.sin(i / 10)
            rows.append(
                {
                    "window": w,
                    "year": year,
                    "day": day,
                    "closed_day": days[i + 3],
                    "full_horizon_day": days[i + 20],
                    "status": "closed",
                    "counted": True,
                    "mature": True,
                    "stock": "X" if value == "low" else "Y",
                    "sector": "bank",
                    "volatility_tercile": value,
                    "gate": "on",
                    "net_r": value_r,
                    "net_inr": 5000 * value_r,
                }
            )
    return rows, {
        "start": days[0],
        "end": days[-1],
        "sessions": days,
        "complete": True,
        "coverage_eligible": True,
    }


def card(identifier="VOL-LOW", fields=None):
    return {
        "id": identifier,
        "strategy": "pullback",
        "description": "Low volatility",
        "filters": fields or [{"field": "volatility_tercile", "values": ["low"]}],
    }


def fixture():
    windows, rows = {}, []
    for i, year in enumerate(range(2019, 2027)):
        key = f"walk-{i + 1}"
        annual, windows[key] = records(key, year)
        rows += annual
    return pd.DataFrame(rows), windows


def test_discovery_explores_only_counted_cells_and_retains_zero_buckets():
    rows, _ = fixture()
    rows.loc[rows.year.eq(2019), "counted"] = False
    res = explore(rows, years=[2019, 2020], dimensions=["year", "gate", "volatility_tercile"])
    cells = {(r["dimension"], r["value"]): r for r in res["slices"]}
    assert res["trades"] == 120 and res["slices_examined"] == len(cells) == 8
    assert cells["year", "2019"]["trades"] == cells["gate", "off"]["trades"] == 0
    assert cells["volatility_tercile", "middle"]["ci90"] == [None, None]
    assert cells["volatility_tercile", "low"]["trades"] == 60
    assert all(c["label"] == LABEL for c in cells.values())
    assert cells["year", "2020"]["ci90"][0] < cells["year", "2020"]["ci90"][1]
    assert json.loads(json.dumps(res, allow_nan=False))["slices_examined"] == 8


def test_prior_selection_excludes_future_unsettled_and_immature_results():
    rows, _ = fixture()
    closed = rows.iloc[[0]].copy()
    closed["stock"] = "A"
    closed["net_r"] = 1
    variations = []
    for name, field in [("B", "closed_day"), ("C", "full_horizon_day"), ("D", "day")]:
        v = closed.copy()
        v["stock"], v["net_r"], v[field] = name, 900, "2021-01-04"
        variations.append(v)
    younger = closed.copy()
    younger["day"], younger["closed_day"], younger["full_horizon_day"] = (
        "2017-01-04",
        "2017-01-05",
        "2017-02-01",
    )
    younger["stock"], younger["net_r"] = "E", 998
    history = pd.concat([closed, younger, *variations], ignore_index=True)
    probes = pd.DataFrame({"stock": list("ABCDE"), "net_r": [99, -99, 0, 3, 4]})
    selected, receipt = select_window(
        history,
        probes,
        "2021-01-04",
        {"selector": {"field": "stock", "min_trades": 1, "lookback_years": 3, "top_fraction": 0.2}},
    )
    assert selected.tolist() == [True, False, False, False, False]
    assert receipt["selected_labels"] == ["A"] and receipt["historical_trades"] == 1
    assert receipt["eligible_history_last_horizon"] < receipt["as_of_exclusive"]


def test_bh_family_denominator_includes_null_or_untestable_card():
    assert np.allclose(benjamini_hochberg([0.5, 0.01, 0.03, 1]), [2 / 3, 0.04, 0.06, 1])
    assert np.allclose(benjamini_hochberg([0.03, 0.03]), [0.03, 0.03])
    with pytest.raises(ValueError):
        benjamini_hochberg([np.nan])


def test_paired_effect_uses_entire_date_matched_book_with_shared_signed_blocks():
    frame, windows = fixture()
    zero = card("NEVER", [{"field": "gate", "values": ["off"]}])
    result = validate_family({"pullback": frame}, {"pullback": windows}, [card(), zero])
    row, unseen = result["hypotheses"]
    assert row["passed"] and row["windows_won"] == row["scheduled_windows"] == 6
    assert row["windows_required"] == 4 and row["paired_difference_r"] > 0.6
    assert row["windows"][0]["filtered"]["trades"] == 60
    assert row["windows"][0]["unfiltered"]["trades"] == 120
    assert row["p_one_sided"] < row["p_bh"] < 0.05
    assert unseen["p_one_sided"] == unseen["p_bh"] == 1 and not unseen["passed"]
    assert unseen["paired_difference_r"] is None
    assert 0 <= result["global_null"]["expected_bh_discoveries"] <= 0.1
    assert json.loads(json.dumps(result, allow_nan=False))["passed_ids"] == ["VOL-LOW"]
    subset = frame[frame.window.eq("walk-1")]
    identical = block_contributions(subset, subset.gate.eq("on"), windows["walk-1"]["sessions"])
    assert all(abs(value) < 1e-12 for value in identical.values())


def test_coverage_and_sparse_windows_cannot_win_or_leave_denominator():
    rows, windows = fixture()
    windows["walk-3"]["coverage_eligible"] = False
    windows["walk-4"]["complete"] = False
    keep = ~(rows.window.isin(["walk-5", "walk-6", "walk-7"]) & rows.volatility_tercile.eq("low"))
    rows = rows[keep]
    result = validate_family({"pullback": rows}, {"pullback": windows}, [card()])
    detail = result["hypotheses"][0]
    assert detail["windows_won"] == 1 and detail["windows_required"] == 4
    assert detail["comparable_windows"] == 1 and detail["p_one_sided"] == 1
    assert detail["qualification"]["incomplete_windows"] == ["walk-4"]
    assert detail["qualification"]["coverage_excluded_windows"] == ["walk-3"]
    assert not detail["passed"] and result["global_null"]["expected_bh_discoveries"] == 0


@pytest.mark.parametrize(
    "c",
    [
        card("BAD", [{"field": "net_r", "values": ["positive"]}]),
        card("BAD", [{"field": "year", "values": ["2025"]}]),
        card("BAD", [{"field": "gate", "values": ["maybe"]}]),
        card("BAD", [{"field": "stock", "values": ["unknown"]}]),
        {
            **card(),
            "selector": {"field": "stock", "top_fraction": 0, "min_trades": 2, "lookback_years": 3},
        },
    ],
)
def test_no_unknown_outcome_or_ex_post_filter(c):
    with pytest.raises(ValueError):
        validate_card(c)


def test_subgroup_data_coverage_accounts_for_possible_blocked_unknowns_and_exact_cutoff():
    from thesistally.discovery_stats import coverage_for_selection

    fields = {"signal_time_predicates": [{"field": "volatility_tercile", "values": ["low"]}]}
    plans = pd.DataFrame(
        {
            "volatility_tercile": ["low"] * 9 + ["unknown", "unknown", "high"],
            "blocked_data": [False] * 9 + [True, False, True],
        }
    )
    meta = {"planned_entries": 12, "blocked_data": 2}
    calc = coverage_for_selection(plans, fields, meta)
    assert calc["coverage_eligible"] and calc["worst_case_fraction"] == 0.1
    assert calc["selected_planned"] == 9 and calc["uncertain_membership"] == 2
    plans.loc[10, "blocked_data"] = True
    with pytest.raises(ValueError, match="census"):
        coverage_for_selection(plans, fields, meta)
    meta["blocked_data"] = 3
    assert not coverage_for_selection(plans, fields, meta)["coverage_eligible"]
    fields.update(selection_field="volatility_tercile", selected_labels=[])
    none = coverage_for_selection(plans, fields, meta)
    assert none["worst_case_planned"] == 0 and none["uncertain_membership"] == 0
    with pytest.raises(ValueError, match="requires"):
        coverage_for_selection(None, fields, meta)
