import runpy
from datetime import date
from pathlib import Path

import pytest
from test_baseline_metrics import replay  # noqa: F401 -- shared four-book journal fixture

from thesistally.baseline_metrics import analyze
from thesistally.evaluation import walk_windows
from thesistally.signals import rules

publish = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/publish_baseline.py"))


def test_interrupted_walk_cannot_shrink_the_qualification_denominator():
    dates = [date(y, 1, 2) for y in range(2014, 2024)]
    receipt = {"sessions": list(map(str, dates)), "complete": True, "snapshot_sha256": "archive"}
    schedule = walk_windows(dates, 3, 1, None)
    manifest = {
        "windows": [
            {"name": n, "sessions": list(map(str, ds)), "training": tr} for n, ds, tr in schedule
        ],
        "snapshot_sha256": "archive",
        "configs": {
            r: {
                "rule_revision": rules(revision=r)["revision"],
                "config_hash": rules(revision=r)["config_hash"],
            }
            for r in ("r1", "r2")
        },
    }
    publish["validate_schedule"](manifest, receipt)
    manifest["windows"].pop()
    with pytest.raises(ValueError, match="scheduled test window"):
        publish["validate_schedule"](manifest, receipt)


def test_report_renders_both_books_and_missing_cohorts(replay):  # noqa: F811
    report = analyze(replay)
    report.update(
        start="2014-01-01",
        end="2026-11-20",
        sessions=21,
        data_coverage={"membership": {"traded_isins": 2, "interval_count": 2}},
        windows=[{"name": "walk-1", "test": ["2026-10-01", "2026-10-21"]}],
        training_momentum={
            "sessions": 0,
            "totals": {"detector_funnel": {}, "signal_dispositions": {}, "stop_bounds": {}},
        },
        momentum={
            r: {
                "detector": {},
                "allocation": {},
                "signal_stop_exclusion_combinations": {},
                "dispositions": {"funded": {}, "equal_risk": {}},
                "entry_or_censor_reasons": {"funded": {}, "equal_risk": {}},
            }
            for r in ("r1", "r2")
        },
    )
    report["provenance"].update(snapshot_sha256="test", code_commit="test", checkout_commit="test")
    report["data_coverage"]["by_year"] = {}
    report["window_signal_dispositions"] = {
        "walk-1": {r: {"allocation_reasons": {}, "allocated_open_misses": {}} for r in ("r2", "r1")}
    }
    doc = publish["render"](report, "example.json")
    for title in (
        "Verdicts",
        "Exit reasons",
        "While positions were exposed",
        "r1 by exit-day gate",
        "r2 by signal year",
        "Qualification and sensitivity",
    ):
        assert title in doc
    assert "No closed trades" in doc and "not enough data" in doc
    assert "Equal risk" in publish["terminal_summary"](report)


def test_opening_miss_diagnosis_uses_dated_isin(tmp_path, monkeypatch):
    import sqlite3

    from thesistally import exchange

    day = "2018-01-02"
    folder = tmp_path / "sessions" / day
    folder.mkdir(parents=True)
    (folder / "members.csv").write_text("Symbol,ISIN Code\nRENAMED,I1\nKNOWN,I2\n")
    monkeypatch.setattr(
        exchange,
        "read_context",
        lambda *a, **k: {"RENAMED": {"ceiling": None}, "KNOWN": {"ceiling": 80}},
    )
    ledger = tmp_path / "journal.sqlite"
    with sqlite3.connect(ledger) as db:
        db.executescript(
            "CREATE TABLE calls(id,run,allocation,isin);CREATE TABLE events(call_id,day,kind,detail);"
        )
        for n in range(1, 4):
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?)", (n, "r2-portfolio", "portfolio", f"I{n}")
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?)",
                (n, day, "missed", "Opening liquidity or upper price band"),
            )
    result = publish["window_attrition"]([("window", ledger)], tmp_path)
    assert result["window"]["r2"]["band_misses"] == {
        "unknown_upper_band": 1,
        "published_band_or_liquidity": 1,
        "off_index_at_open": 1,
    }


def test_deferred_stop_distinguishes_missing_floor_and_circuit(tmp_path, monkeypatch):
    import sqlite3

    from thesistally import exchange

    day = "2019-01-02"
    folder = tmp_path / "sessions" / day
    folder.mkdir(parents=True)
    (folder / "members.csv").write_text("Symbol,ISIN Code\nUNKNOWN,I1\nCIRCUIT,I2\n")
    (folder / "bhavcopy.csv").write_text(
        "TckrSymb,SctySrs,ISIN,OpnPric,TtlTradgVol\nUNKNOWN,EQ,I1,40,100\nCIRCUIT,EQ,I2,80,50\n"
    )
    monkeypatch.setattr(
        exchange,
        "read_context",
        lambda *a, **k: {"UNKNOWN": {"floor": None}, "CIRCUIT": {"floor": 80}},
    )
    ledger = tmp_path / "journal.sqlite"
    with sqlite3.connect(ledger) as db:
        db.executescript(
            "CREATE TABLE calls(id,run,allocation,isin,strategy,status);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY,call_id,day,kind,detail);"
        )
        for n in (1, 2):
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?)",
                (n, "r2-portfolio", "portfolio", f"I{n}", "trend_pullback", "closed"),
            )
            db.execute(
                "INSERT INTO events(call_id,day,kind,detail) VALUES(?,?,?,?)",
                (n, day, "exit_deferred", "Opening liquidity or lower price band"),
            )
    result = publish["exit_delays"]([("w", ledger)], tmp_path)["r2"]["funded"]["trend_pullback"]
    assert result["events"] == {"unknown_lower_band": 1, "at_lower_circuit": 1}
    assert (
        result["affected_trades"],
        result["median_deferred_sessions"],
        result["max_deferred_sessions"],
    ) == (2, 1, 1)
