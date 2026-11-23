import pytest

from thesistally.baseline_qualification import (
    cash_profit_factor,
    clustered_mean_ci,
    qualification,
    validation_config,
)


def trades(window, n, outcome, *, mature=True, status="closed", year=2020):
    return [
        {
            "day": f"{year}-01-{1 + i % 20:02}",
            "net_r": outcome,
            "net_inr": outcome * 1000,
            "window": window,
            "mature": mature,
            "status": status,
        }
        for i in range(n)
    ]


def test_strict_thresholds_maturity_and_window_breadth():
    cfg = validation_config()
    rows = (
        trades("A", 50, 0.26) + trades("B", 50, 0.26, year=2021) + trades("C", 19, -20, year=2022)
    )
    rows += trades("C", 1, -20, mature=False) + trades("C", 5, 90, status="active")
    q = qualification(rows, {"A": True, "B": True, "C": True}, cfg)
    assert q["qualified"] and q["verdict"] == "edge"
    assert q["mature_closed_all"] == 119 and q["mature_closed_in_counted_windows"] == 100
    assert q["criteria"]["positive_window_fraction"]["value"] == pytest.approx(2 / 3)
    q = qualification(rows, {"A": True, "B": True, "C": True, "D": True}, cfg)
    assert not q["qualified"]  # empty or sparse windows never count as wins
    q = qualification(trades("A", 100, 0.25), {"A": True}, cfg)
    assert not q["criteria"]["mean_r"]["passed"]
    assert cash_profit_factor([{"net_inr": 140}, {"net_inr": -100}]) == 1.4


def test_incomplete_windows_cannot_qualify_or_establish_a_negative_verdict():
    cfg = validation_config()
    rows = trades("A", 100, -1) + trades("B", 100, 0.18)
    q = qualification(rows, {"A": True, "B": False}, cfg)
    assert q["incomplete_windows"] == ["B"] and q["counted_windows"] == ["A"]
    assert not q["qualified"] and q["verdict"] == "not enough data"
    assert qualification(trades("A", 100, -1), {"A": True}, cfg)["verdict"] == "no edge"
    empty = qualification([], {"A": True}, cfg)
    assert empty["criteria"]["mean_r"]["value"] is None and not empty["qualified"]


def test_date_cluster_sampling_preserves_within_date_dependence():
    cfg = validation_config()
    rows = [{"day": "2024-01-01", "net_r": 1}] * 90 + [{"day": "2024-01-02", "net_r": -1}]
    assert clustered_mean_ci(rows, cfg) == [-1, 1]
    assert clustered_mean_ci(rows[:90], cfg) == [None, None]


def test_regular_r2_walk_forward_uses_the_same_mature_cohorts_and_date_clusters(
    tmp_path, monkeypatch
):
    import pandas as pd

    from thesistally import evaluation
    from thesistally.signals import rules

    parts = []
    for year in range(2020, 2023):
        ds = list(pd.bdate_range(f"{year}-01-01", periods=25).date)
        parts.append((f"walk-{year - 2019}", ds, None))
    counts = {"walk-1": 50, "walk-2": 50, "walk-3": 19}
    monkeypatch.setattr(evaluation, "provenance", lambda *args: {"snapshot_hash": "fixture"})

    def play(db, root, days, books):
        for run, cfg in books:
            period = run.split("walk-")[-1].split("-")[0]
            group = "walk-" + period
            with db:
                db.execute("INSERT INTO runs VALUES(?,?,1000000,1000000)", (run, cfg["revision"]))
                db.executemany(
                    "INSERT INTO scans VALUES(?,?,?,0)",
                    [(run, str(d), '{"enabled":true}') for d in days],
                )
                rows = []
                for i in range(counts[group] + 1):
                    # An additional late, early-closing winner cannot enter qualification.
                    day = days[-2] if i == counts[group] else days[i % 3]
                    r = 10 if day == days[-2] else (-1 if group == "walk-3" else 0.5)
                    rows.append((run, str(day), str(i), str(i), cfg["revision"], r * 100, r))
                db.executemany(
                    """INSERT INTO calls(run,day,symbol,isin,sector,strategy,revision,regime,features,
                    entry,stop,target,size,allocation,status,net,outcome_r)
                    VALUES(?,?,?,?,'banks','trend_pullback',?,'{"enabled":true}','{}',100,95,110,10,
                    'portfolio','closed',?,?)""",
                    rows,
                )
        return {}

    monkeypatch.setattr(evaluation, "replay_window", play)
    cfg = rules(revision="r2")
    days = [d for _, part, _ in parts for d in part]
    result = evaluation.evaluate(tmp_path, parts, days, cfg, {"mode": "walk_forward"}, False)
    q = result["qualification"]["trend_pullback"]
    assert q["qualified"] and q["verdict"] == "edge"
    assert q["mature_closed_all"] == 119 and q["aggregate"]["closed"] == 100
    assert q["criteria"]["positive_window_fraction"]["value"] == pytest.approx(2 / 3)
    assert q["criteria"]["mean_r"]["value"] == 0.5 and q["mean_net_r_ci90"] == [0.5, 0.5]
    assert "positive_weak_windows" not in q["criteria"]
    assert result["qualification_config"]["bootstrap_unit"] == "signal_session"
    assert result["blackout_comparison"]["test_windows"][0]["calendar_days"]["closed"] == 51
