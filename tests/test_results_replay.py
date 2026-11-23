import json
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import pytest
from test_research import MARKET, REGIME, prices

from thesistally import equity_tax
from thesistally import results_replay as replay
from thesistally.journal import journal
from thesistally.results_replay_tax import add_action


def test_rejected_year_cannot_open_a_ledger_or_publish_zero_returns(tmp_path):
    start = date(2021, 1, 1)
    args = (
        tmp_path / "missing_archive",
        tmp_path / "missing_signals",
        tmp_path,
        "walk-5",
        [start],
        ["2018-01-01", "2020-12-31"],
        {"coverage_eligible": False},
    )
    out, records = replay.run_year(args)
    assert not records and out["status"] == "incomplete_filing_coverage"
    assert all(
        b["trades"] is b["strategy_return_pct"] is b["after_tax"] is None
        for b in out["books"].values()
    )
    assert not list(tmp_path.iterdir())


def test_eligible_year_runs_funded_and_equal_risk_and_reconciles_tax(tmp_path, monkeypatch):
    # 43 consecutive synthetic market sessions, a closing stop and then a gapped opening sale.
    start = date(2024, 8, 2)
    days = [start + timedelta(days=i) for i in range(43)]
    prior = days[0] - timedelta(days=1)
    market = tmp_path / replay.VIEW / "market"
    market.mkdir(parents=True)
    (market / "corporate_actions.json").write_text("[]")
    tape = {}
    series = {}
    for i, d in enumerate(days):
        f = market / "sessions" / str(d)
        f.mkdir(parents=True)
        (f / "manifest.json").write_text("{}")
        tape[str(d)] = replay.digest(f / "manifest.json")
        o, h, lo, c = (110, 110, 105, 108) if i == 3 else (100, 101, 91, 93 if i == 2 else 100)
        series[d] = (o, h, lo, c)
        (f / "bhavcopy.csv").write_text(
            "ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\nI1,EQ," + f"{o},{h},{lo},{c}\n"
        )
    (market.parent / "certified.json").write_text(json.dumps({"session_manifests": tape}))
    stage = tmp_path / "stage3"
    stage.mkdir()
    (stage / "benchmark.csv").write_text(
        "day,close\n" + "".join(f"{d},{100 + i}\n" for i, d in enumerate([prior, *days]))
    )
    monkeypatch.setattr(replay.range_cash, "load_events", lambda *args: {})

    def session(root, day, previous, rows, items, cfg, cash):
        o, h, low, c = series[day]
        frame = pd.DataFrame([vars(prices(o, h, low, c))], index=[day])
        controls = {**MARKET, "surveillance": False}
        frame.attrs["exchange"] = controls
        frames = {
            "S": frame,
            "I1": frame,
            "NIFTY 200": frame,
            "NIFTY 50": pd.DataFrame(index=[previous, day]),
        }
        return REGIME, frames, {"I1": "S"}, {"S": controls}

    monkeypatch.setattr(replay.range_replay, "session", session)
    path = tmp_path / "signals.parquet"
    pd.DataFrame(
        [
            {
                "day": str(days[0]),
                "usable_session": str(prior),
                "symbol": "S",
                "isin": "I1",
                "sector": "Banks",
                "close": 100.0,
                "atr14": 2.0,
                "sue": 2.0,
                "reaction": 0.01,
                "filing_id": "nse:1",
            }
        ]
    ).to_parquet(path, index=False)
    outdir = tmp_path / "runs"
    outdir.mkdir()
    args = (
        tmp_path,
        path,
        outdir,
        "walk-8",
        days,
        ["2021-08-01", str(prior)],
        {"coverage_eligible": True},
    )
    out, records = replay.run_year(args)
    for name, book in out["books"].items():
        assert book["complete"], book["audit"]["discrepancies"]
        assert book["trades"] == 1 and book["entry_coverage"]["filled"] == 1
        assert book["mean_gross_r"] > book["mean_net_r"] > 0
        assert book["benchmark_return_pct"] == pytest.approx(43)
        taxes = book["after_tax"]
        with closing(journal(outdir / "walk-8" / "journal.sqlite")) as db:
            gain = db.execute(
                """SELECT sum((CASE WHEN f.side='sell' THEN 1 ELSE -1 END)*f.price*f.qty-f.fees
                    + f.price*f.qty*.001) FROM fills f JOIN calls c ON c.id=f.call_id WHERE c.run=?""",
                (name,),
            ).fetchone()[0]
        assert taxes["per_fy"][2024]["realised_short_gain"] == pytest.approx(gain)
        assert taxes["strategy"]["tax"] == pytest.approx(gain * 0.20 * 1.04)
        assert taxes["tri"]["tax"] == pytest.approx(430000 * 0.20 * 1.04)
        assert records[name][0]["exit_reason"] == "Closing stop"
    assert replay.run_year(args)[0]["journal_sha256"] == out["journal_sha256"]


def test_converted_units_keep_basis_and_fraction_is_realized_only_on_payout():
    book = equity_tax.TaxBook(equity_tax.load_config())
    book.add("A", equity_tax.Lot(3, date(2024, 1, 1), 100))
    fractions = {}
    common = {
        "predecessor_isin": "A",
        "successor_isin": "B",
        "fraction": 0.5,
        "cash_date": "2024-03-01",
    }
    add_action(
        book,
        {**common, "stage": "converted", "ratio": [1, 2], "successor_qty": 1},
        date(2024, 2, 1),
        fractions,
        set(),
        {},
    )
    add_action(
        book,
        {**common, "stage": "credited", "fractional_cash": 0},
        date(2024, 2, 1),
        fractions,
        set(),
        {},
    )
    assert not book.gains and len(fractions["A"]) == 1
    add_action(
        book,
        {**common, "stage": "fraction_paid", "fractional_cash": 125},
        date(2024, 3, 1),
        fractions,
        set(),
        {},
    )
    assert not fractions and book.gains == [(date(2024, 3, 1), "short", 25.0)]
