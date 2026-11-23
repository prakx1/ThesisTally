from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import pytest
from test_dip import setup
from test_research import DAY, MARKET, REGIME, calendar, prices

from thesistally import dip_coverage, stage3_dip
from thesistally.baseline_qualification import validation_config
from thesistally.journal import advance, journal, process


@pytest.mark.parametrize("n, excluded", [(9, False), (8, True)])
def test_revised_coverage_distinguishes_actual_open_data_and_other_known_vetoes(
    tmp_path, n, excluded
):
    cfg = dict(stage3_dip.registered_books(revised=True))["dip-rsi10-equal-risk"]
    base = setup(cfg)[0]

    def candidate(s, **change):
        return replace(base, isin=s, symbol=s, features={**base.features, **change})

    cs = [candidate(str(i)) for i in range(n)] + [
        candidate("U", surveillance="unknown"),
        candidate("E", surveillance="unknown", earnings_in_days=2),
    ]
    with closing(journal(tmp_path / "ledger")) as db:
        process(db, "test", DAY, REGIME, cs, calendar(DAY), cfg, unconstrained=True)
        next_day = DAY + timedelta(days=1)
        with db:
            for c in db.execute("SELECT * FROM calls WHERE run='test'").fetchall():
                advance(
                    db,
                    c,
                    str(next_day),
                    prices(100, 102, 99, 100),
                    cfg,
                    {**MARKET, "ceiling": None},
                )
        with db:
            dip_coverage.capture(db, "test", str(next_day), [candidate("last")], cfg, REGIME)
        result = dip_coverage.entry_coverage(db, "test", str(next_day), "unconstrained")
    assert result["planned_entries"] == n + 1
    assert result["raw_signals"] == n + 3
    assert result["censored"] == 1
    assert result["filled"] == result["band_unknown_fills"] == n
    assert result["data_reasons"] == {"surveillance_unknown": 1}
    assert result["pre_order_exclusions"] == {"upcoming_results": 1}
    assert result["coverage_excluded"] is excluded


def test_coverage_removes_rejected_windows_from_all_inference_but_preserves_physical_gaps():
    def sample(eligible, complete=True):
        return {"complete": complete, "entry_coverage": {"coverage_excluded": not eligible}}

    rows = [
        {
            "mature": True,
            "status": "closed",
            "net_r": net,
            "net_inr": net,
            "day": str(i),
            "window": w,
        }
        for w, net in (("good", 0.5), ("bad", -100.0))
        for i in range(120)
    ]
    coverage = {"good": sample(True), "sparse": sample(True), "bad": sample(False)}
    r = stage3_dip.covered_qualification(rows, coverage, validation_config())
    assert r["coverage_excluded_windows"] == ["bad"]
    assert r["criteria"]["mean_r"]["value"] == 0.5 and r["mean_net_r_ci90"] == [0.5, 0.5]
    assert r["criteria"]["closed_oos"]["value"] == 120
    assert r["criteria"]["positive_window_fraction"]["value"] == 0.5
    assert r["criteria"]["completed_replays"]["value"] == 3
    assert not r["qualified"] and r["scheduled_windows"] == 3
    coverage.update(bad=sample(False, False))
    r = stage3_dip.covered_qualification(rows, coverage, validation_config())
    assert not r["criteria"]["completed_replays"]["passed"]
    assert r["incomplete_windows"] == ["bad"]


def test_funded_data_veto_uses_capacity_at_its_rank_without_reserving_cash(tmp_path):
    cfg = dict(stage3_dip.registered_books(revised=True))["dip-rsi10-portfolio"]
    base = setup(cfg)[0]
    candidates = [
        replace(
            base,
            isin=isin,
            symbol=isin,
            features={
                **base.features,
                "steady_score": score,
                "steady_rank": int(6 - score),
                "surveillance": "unknown" if isin in ("UNKNOWN_FIRST", "UNKNOWN_LAST") else False,
            },
        )
        for isin, score in [("UNKNOWN_FIRST", 5.0), ("A", 4.0), ("B", 3.0), ("UNKNOWN_LAST", 1.0)]
    ]
    with closing(journal(tmp_path / "ledger")) as db:
        process(db, "funded", DAY, REGIME, candidates, calendar(DAY), cfg)
        funding = dict(db.execute("SELECT isin,allocation FROM calls"))
        assert funding == {
            "UNKNOWN_FIRST": "surveillance_unknown",
            "A": "portfolio",
            "B": "portfolio",
            "UNKNOWN_LAST": "surveillance_unknown",
        }
        assert dict(db.execute("SELECT isin,reason FROM dip_funding_veto")) == {
            "UNKNOWN_LAST": "position_limit"
        }
        with db:
            for c in db.execute("SELECT * FROM calls").fetchall():
                advance(db, c, str(DAY + timedelta(days=1)), prices(100, 101, 99, 100), cfg, MARKET)
        sample = dip_coverage.entry_coverage(
            db, "funded", str(DAY + timedelta(days=1)), "portfolio"
        )
    assert sample["planned_entries"] == 3 and sample["blocked_data"] == 1
    assert sample["pre_order_exclusions"] == {"position_limit": 1}
