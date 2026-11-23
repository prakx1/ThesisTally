import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import baseline_replay as baseline
from thesistally import evaluation, market


def test_paired_windows_use_one_cross_section_and_resume_each_book(tmp_path, monkeypatch):
    root = tmp_path / "baseline" / "market"
    root.mkdir(parents=True)
    days = [DAY + timedelta(days=i) for i in range(4)]
    cfgs = baseline.fixed_books()
    first = pick()
    first.features["atr"] = 5
    interruptions = {days[2]}
    prepared = []

    def prepare(args):
        _, day, cfg = args
        assert cfg["version"] == "r2"
        if day in interruptions:
            interruptions.remove(day)
            raise RuntimeError("interruption")
        prepared.append(day)
        close = 86 if day == days[2] else 99
        q = prices(open_=98, high=101, low=min(95, close), close=close)
        f = pd.DataFrame([vars(q)], index=[day])
        f.attrs["exchange"] = MARKET
        path = root / "sessions" / str(day) / "bhavcopy.csv"
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(
            f"ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\nA,EQ,{q.open},{q.high},{q.low},{q.close}\n"
        )
        regime = {**REGIME, "detector_funnel": {"high_momentum": {"detected": 0}}}
        return (
            regime,
            [first] if day == days[0] else [],
            {**calendar(day), "A": f, "NIFTY 200": f},
            {"A": "A"},
        )

    monkeypatch.setattr(evaluation, "prepared_session", prepare)
    directory = root.parent / "runs" / "fixture"
    directory.mkdir(parents=True)
    args = (root, directory, "walk-1", days, ["2014-01-01", "2016-12-30"], cfgs, [])
    with pytest.raises(RuntimeError, match="interruption"):
        baseline.run_window(*args)
    manifest = baseline.run_window(*args)
    assert prepared == days
    assert all(b["complete"] for b in manifest["books"].values())
    assert len(manifest["books"]) == 4
    path = directory / manifest["journal"]
    digest = market.sha(path.read_bytes())
    assert baseline.run_window(*args) == manifest and market.sha(path.read_bytes()) == digest
    with closing(sqlite3.connect(path)) as db:
        for label, cfg in cfgs:
            row = db.execute(
                "SELECT target,planned_target,buy,size,regime FROM calls WHERE run=?", (label,)
            ).fetchone()
            target, planned, buy, qty, regime = row
            assert planned == 120
            assert target == (120 if label.startswith("r1") else buy + 2 * (buy - 90))
            assert json.loads(regime)["detector_funnel"]["high_momentum"]["detected"] == 0
            if label.endswith("-equal-risk"):
                assert qty == int(5000 / (buy - 90))
        assert not list(db.execute("SELECT * FROM holdout_uses"))
    second = baseline.run_window(
        root, directory, "walk-2", days[2:], ["2015-01-01", "2017-01-01"], cfgs, []
    )
    with closing(sqlite3.connect(directory / second["journal"])) as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
        assert {r[0] for r in db.execute("SELECT equity FROM account_marks")} == {1_000_000}


def test_failed_preflight_cannot_start_baseline(tmp_path):
    root = tmp_path / "baseline" / "market"
    root.mkdir(parents=True)
    receipt = root.parent / "preflight.json"
    receipt.write_text('{"complete":false}')
    with pytest.raises(market.MarketError, match="unresolved blockers"):
        baseline.replay(root, receipt)
    assert not (root.parent / "runs").exists()


def test_price_only_receipt_cannot_unlock_the_full_history(tmp_path):
    root = tmp_path / "baseline" / "market"
    root.mkdir(parents=True)
    receipt = root.parent / "preflight.json"
    receipt.write_text('{"complete":true,"snapshot_sha256":"old-price-only-input"}')
    with pytest.raises(market.MarketError, match="earlier_controls certification"):
        baseline.replay(root, receipt)
    assert not (root.parent / "runs").exists()
