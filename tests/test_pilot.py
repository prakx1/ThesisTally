import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import pytest

from thesistally import evaluation, market, pilot
from thesistally.signals import Candidate


def test_pilot_resumes_continuous_account_and_reconciles_official_fills(tmp_path, monkeypatch):
    root = tmp_path / "pilot" / "market"
    root.mkdir(parents=True)
    receipt = root.parent / "preflight.json"
    days = [date(2024, 1, i) for i in range(1, 6)]
    receipt.write_text(
        json.dumps(
            {
                "quarantine": [
                    {
                        "day": str(d),
                        "isin": "IN1",
                        "symbol": "A",
                        "reason": "unexplained_close_move",
                    }
                    for d in (days[1], days[3])
                ]
            }
        )
    )
    sentinel = tmp_path / "journal.sqlite"
    sentinel.write_bytes(b"external journal")
    monkeypatch.setattr(
        pilot,
        "preflight_inputs",
        lambda *args: (
            days,
            {"snapshot_sha256": "fixture", "supplementary_inputs_sha256": "fixture"},
        ),
    )
    monkeypatch.setattr(pilot.provenance, "snapshot", lambda _: {"code_hash": "fixed"})
    candidate = Candidate(
        "A", "IN1", "Banks", "trend_pullback", 100, 90, 120, {"atr": 4.0, "rs_percentile": 0.8}
    )
    fail = {days[2]}

    def prepare(args):
        _, d, _cfg = args
        if d in fail:
            fail.remove(d)
            raise ValueError("fixture interruption")
        op, hi, lo, cl = {
            days[0]: (99, 101, 98, 100),
            days[1]: (99, 101, 97, 100),
            days[2]: (98, 121, 88, 115),
            days[3]: (86, 88, 85, 87),
            days[4]: (91, 102, 87, 98),
        }[d]
        f = pd.DataFrame(
            [
                {
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": 1000,
                    "fast": 98,
                    "trail": 95,
                }
            ],
            index=[d],
        )
        f.attrs["exchange"] = {
            "ceiling": 200,
            "floor": 86 if d == days[3] else 50,
            "dividend": 2.5 if d == days[2] else 0,
        }
        frames = {
            "NIFTY 50": pd.DataFrame(index=[d - timedelta(days=1), d]),
            "NIFTY 200": f,
            "A": f,
        }
        out = root / "sessions" / str(d) / "bhavcopy.csv"
        out.parent.mkdir(parents=True)
        out.write_text(f"ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\nIN1,EQ,{op},{hi},{lo},{cl}\n")
        return {"enabled": True}, [candidate] if d == days[0] else [], frames, {"IN1": "A"}

    monkeypatch.setattr(evaluation, "prepared_session", prepare)
    with pytest.raises(ValueError, match="fixture interruption"):
        pilot.pilot(root, receipt, revision="r1")
    report_path, complete = pilot.pilot(root, receipt, revision="r1")
    assert complete
    report = json.loads(report_path.read_text())
    assert report["books"]["portfolio"]["all"]["closed"] == 1
    assert report["audit"]["costs_and_cash"]["portfolio"]["entitlements_inr"] > 0
    assert report["runtime_sessions_measured"] == report["scanned"] == len(days)
    assert report["equity"]["ending_equity"] == pytest.approx(
        1e6 + report["books"]["portfolio"]["all"]["net_inr"]
    )
    assert report["audit"]["checks"]["buy_at_open"] == 1
    assert report["audit"]["checks"]["sell_within_observed_range"] == 1
    (collision,) = report["audit"]["stop_first_collisions"]
    assert collision["collision"] == str(days[2]) and collision["exit_day"] == str(days[4])
    assert collision["deferred_days"] == [str(days[3])] and collision["book"] == "portfolio"
    opening = report["audit"]["orders_on_close_quarantine"]
    assert [r["side"] for r in opening] == ["buy", "sell"]
    assert [[e["kind"] for e in r["events"]] for r in opening] == [
        ["fill", "active"],
        ["exit_deferred"],
    ]
    db_path = report_path.parent / "journal.sqlite"

    def ledger():
        with closing(sqlite3.connect(db_path)) as db:
            return {
                t: list(db.execute(f"SELECT * FROM {t} ORDER BY rowid"))
                for t in ("runs", "calls", "fills", "events", "scans", "account_marks")
            }

    before = ledger()
    assert pilot.pilot(root, receipt, revision="r1") == (report_path, True)
    assert ledger() == before and sentinel.read_bytes() == b"external journal"
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE fills SET fees=0 WHERE side='buy'")
    assert pilot.pilot(root, receipt, revision="r1") == (report_path, False)
    bad = json.loads(report_path.read_text())["audit"]["examples"]
    assert {row["check"] for row in bad} == {
        "fee_schedule_and_DP",
        "closed_net_cash",
        "closed_net_risk",
        "funded_equity_and_cash",
    }


def test_pilot_will_not_open_forward_workspace(tmp_path):
    with pytest.raises(market.MarketError, match="pilot/market"):
        pilot.pilot(tmp_path, tmp_path / "preflight.json")
    assert not list(tmp_path.iterdir())


def test_preflight_fingerprint_changed_archive_stops_before_opening_journal(tmp_path):
    from thesistally.pilot_data import snapshot_hash

    root = tmp_path / "pilot" / "market"
    day = date(2024, 1, 1)
    manifest = root / "sessions" / str(day) / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"sha256":{}}')
    context = root / "context" / str(day) / "context.sha256"
    context.parent.mkdir(parents=True)
    context.write_text("a" * 64)
    receipt = root.parent / "preflight.json"
    receipt.write_text(
        json.dumps(
            {
                "complete": True,
                "start": str(day),
                "end": str(day),
                "sessions": [str(day)],
                "snapshot_sha256": snapshot_hash(root, [day]),
            }
        )
    )
    assert pilot.preflight_inputs(root, receipt)[0] == [day]
    manifest.write_text('{"changed":true}')
    with pytest.raises(market.MarketError, match="differ from"):
        pilot.pilot(root, receipt, revision="r1")
    assert not (root.parent / "runs").exists()


def test_pilot_binds_issuer_credit_evidence_before_replay(tmp_path, monkeypatch):
    import hashlib

    from thesistally import pilot_data

    day = "2024-01-01"
    root = tmp_path / "pilot" / "market"
    evidence = root / "sources" / "compulsory-actions" / "PREDECESSOR"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(pilot_data, "snapshot_hash", lambda *args: "fixture")
    receipt = root.parent / "preflight.json"
    receipt.write_text(
        json.dumps(
            {
                "complete": True,
                "sessions": [day],
                "start": day,
                "end": day,
                "snapshot_sha256": "fixture",
            }
        )
    )
    proof = pilot.preflight_inputs(root, receipt)[1]
    (evidence / "credit.pdf").write_bytes(b"dated credit document")
    doc = {
        "url": "https://example.test/credit",
        "sha256": hashlib.sha256(b"dated credit document").hexdigest(),
    }
    (evidence / "sources.json").write_text(json.dumps({"credit.pdf": doc}))
    changed = pilot.preflight_inputs(root, receipt)[1]
    assert changed["supplementary_inputs_sha256"] != proof["supplementary_inputs_sha256"]
    (evidence / "credit.pdf").write_bytes(b"changed")
    with pytest.raises(market.MarketError, match="Changed compulsory-action evidence"):
        pilot.preflight_inputs(root, receipt)
