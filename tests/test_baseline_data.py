from datetime import date
from fractions import Fraction

import pandas as pd

from thesistally import baseline_data, historical_sessions


def test_cached_benchmark_alias_uses_raw_ohlc_and_preserves_next_session(tmp_path, monkeypatch):
    from thesistally import constituents

    early, late = date(2013, 2, 7), date(2013, 2, 8)
    index = pd.DataFrame(
        {
            "Open Index Value": [5936.45],
            "High Index Value": [5978.5],
            "Low Index Value": [5927.6],
            "Closing Index Value": [5938.8],
        },
        index=["NIFTY 50"],
    )
    monkeypatch.setattr(constituents, "load_daily", lambda root, day: (None, index))
    tape = pd.DataFrame(
        {
            "day": [early, late, late],
            "symbol": ["NIFTY 200", "NIFTY 200", "NIFTY 50"],
            "close": [3035, 3020, 5903.5],
        }
    )
    rebuilt, repairs = baseline_data.restore_benchmark_warmup(tmp_path, tape)
    assert repairs == [early]
    matched = rebuilt[rebuilt.symbol.eq("NIFTY 50")].set_index("day")
    assert matched.at[early, "adjusted_close"] == 5938.8
    assert matched.at[late, "prior_close"] == 5938.8
    assert matched.at[late, "close"] == 5903.5
    checked, repaired_again = baseline_data.restore_benchmark_warmup(tmp_path, rebuilt)
    assert repaired_again == []
    pd.testing.assert_frame_equal(checked, rebuilt)


def test_archived_nse_csv_must_match_replay_input(tmp_path):
    import json
    import zipfile

    import pytest

    from thesistally import history, market

    day = date(2014, 1, 1)
    archive = tmp_path / "sources" / "nse-daily" / str(day)
    session = tmp_path / "sessions" / str(day)
    archive.mkdir(parents=True)
    session.mkdir(parents=True)
    original = b"TIMESTAMP,SYMBOL,ISIN,CLOSE\n01-JAN-2014,HOME,IND123,15.25\n"
    with zipfile.ZipFile(archive / "bhavcopy.zip", "w") as zipped:
        zipped.writestr("quotes.csv", original)
    (archive / "indices.csv").write_bytes(b"official indices")
    files = {
        p.name: {"url": "https://example.test/" + p.name, "sha256": market.sha(p.read_bytes())}
        for p in archive.iterdir()
    }
    (archive / "sources.json").write_text(json.dumps(files))
    bhavcopy = history.normalize_bhav(original, day).to_csv(index=False).encode()
    manifest = {
        "source_files": files,
        "sha256": {
            "indices.csv": files["indices.csv"]["sha256"],
            "bhavcopy.csv": market.sha(bhavcopy),
        },
    }
    (session / "manifest.json").write_text(json.dumps(manifest))
    assert baseline_data.audit_primary_archives(tmp_path, [str(day)])["existing_archives"] == 1
    manifest["sha256"]["bhavcopy.csv"] = market.sha(b"different prices")
    (session / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(market.MarketError, match="differs from the replay input"):
        baseline_data.audit_primary_archives(tmp_path, [str(day)])


def test_publisher_may_append_unrelated_indices_but_must_preserve_every_observation():
    import pytest

    from thesistally import market

    initial = b"Index Name,Close\nNifty 200,1.50\n"
    assert baseline_data.compare_index_editions(initial, initial + b"Other,0.89\n") == ["Other"]
    with pytest.raises(market.MarketError, match="Changed or missing"):
        baseline_data.compare_index_editions(initial, b"Index Name,Close\nNifty 200,1.55\n")


def test_split_ratio_uses_abbreviated_terms_and_combined_bonus():
    assert baseline_data.split_terms([" Fv Splt Frm Rs 10 To Re 1 "]) == 10
    assert baseline_data.split_terms(["Bonus 1:2/Face Value Split From Rs 2 To Re 1"]) == 3
    assert baseline_data.split_terms(["Face Value Split Rs.5/- To Rs.2/-"]) == Fraction(5, 2)


def test_late_print_preserves_raw_ex_date_and_starts_new_identifier_only_on_observation():
    days = list(map(date.fromisoformat, ["2015-01-07", "2015-01-08", "2015-01-09"]))
    rows = pd.DataFrame(
        {
            "day": days,
            "symbol": ["X"] * 3,
            "isin": ["OLD", "OLD", "NEW"],
            "chain": ["OLD", "OLD", "NEW"],
            "price_factor": [1.0, 0.5, 1.0],
            "share_factor": [1.0, 2.0, 1.0],
            "close": [200.0, 101.0, 102.0],
        }
    )
    for name in historical_sessions.FIELDS:
        if name != "close":
            rows[name] = rows.close
        factor = rows.share_factor if name == "volume" else rows.price_factor
        rows["adjusted_" + name] = rows[name] / factor
    actions = {(days[1], "X"): [{"subject": "Face Value Split From Rs 2 To Re 1"}]}
    early = rows.iloc[:2].copy()
    assert baseline_data.index_identifier_history(early, early, actions, days[:2]) == []
    evidence = baseline_data.index_identifier_history(rows, rows, actions, days)
    pd.testing.assert_frame_equal(early, rows.iloc[:2])
    assert evidence[0]["mode"] == "joined_on_new_print"
    assert rows["isin"].tolist() == ["OLD", "OLD", "NEW"]
    assert rows.chain.tolist() == ["OLD"] * 3
    assert (rows.adjusted_close * rows.price_factor.iloc[-1]).tolist() == [100, 101, 102]


def test_holder_scheme_without_split_evidence_keeps_separate_histories():
    days = [date(2020, 3, 13), date(2020, 3, 16)]
    frame = pd.DataFrame(
        {
            "day": days,
            "symbol": ["X", "X"],
            "isin": ["OLD", "NEW"],
            "chain": ["OLD", "NEW"],
            "price_factor": [1.0, 1.0],
            "share_factor": [1.0, 1.0],
        }
    )
    before = frame.copy()
    evidence = baseline_data.index_identifier_history(frame, frame, {}, days)
    assert evidence[0]["mode"] == "separate_chains"
    pd.testing.assert_frame_equal(frame, before)


def test_announced_repair_is_effective_before_new_id_prints(tmp_path, monkeypatch):
    import json

    from thesistally import market

    folder = tmp_path / "study"
    view = folder / "market"
    pdf = folder / "evidence" / "splits" / "dated.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"archived test circular")
    package = tmp_path / "test-package"
    (package / "revisions").mkdir(parents=True)
    term = {
        "symbol": "X",
        "old_isin": "OLD",
        "new_isin": "NEW",
        "effective": "2019-01-15",
        "old_shares": 1,
        "new_shares": 5,
        "subject": "Face Value Split From Rs 10 To Rs 2",
        "sources": [
            {
                "issued": "2019-01-14",
                "url": "https://example.test/dated.pdf",
                "sha256": market.sha(pdf.read_bytes()),
            }
        ],
    }
    (package / "revisions" / "historical-split-evidence.json").write_text(json.dumps([term]))
    monkeypatch.setattr(baseline_data, "__file__", str(package / "baseline_data.py"))
    days = list(map(date.fromisoformat, ["2019-01-14", "2019-01-15", "2019-01-16"]))
    raw = pd.DataFrame(
        {
            "day": days,
            "symbol": "X",
            "isin": ["OLD", "OLD", "NEW"],
            "chain": ["OLD", "OLD", "NEW"],
            "price_factor": 1.0,
            "share_factor": 1.0,
            "unexplained_jump": [False, True, False],
        }
    )
    for f in historical_sessions.FIELDS:
        raw[f] = [500.0, 101.0, 103.0]
        raw["adjusted_" + f] = raw[f]
    snapshots = []
    for n in [2, 3]:
        tape = raw.iloc[:n].copy()
        ledger = [{"day": "2019-01-15", "isin": "OLD", "reason": "unexplained_move_over_20pct"}]
        actions = {}
        assert baseline_data.restore_dated_splits(tape, view, ledger, actions) == [term]
        baseline_data.index_identifier_history(tape, tape, actions, days[:n])
        assert ledger[0]["reason"] == "unresolved_action"
        assert not tape.unexplained_jump.any()
        snapshots.append(tape)
    pd.testing.assert_frame_equal(snapshots[0], snapshots[1].iloc[:2])
    assert snapshots[1].chain.tolist() == ["OLD"] * 3
    assert (snapshots[1].adjusted_close * 0.2).tolist() == [100.0, 101.0, 103.0]


def test_stage1_republication_preserves_new_full_view(tmp_path):
    import json

    from thesistally import market, pilot_data

    root = tmp_path / "data"
    pilot = root / "pilot" / "market"
    full = root / "baseline" / "market"
    for p in [root, pilot, full]:
        (p / "universes").mkdir(parents=True)
    header = "symbol,isin,from_date,to_date,source_url\n"
    canonical = root / "universes/nifty200_membership.csv"
    canonical.write_text(header + "X,I,2014-01-01,2024-01-03,original\n")
    (pilot / "universes" / canonical.name).write_text(header + "X,I,2024-01-01,2024-01-03,pilot\n")
    args = (root, pilot, date(2024, 1, 1), date(2024, 1, 3))
    p1 = pilot_data.publish_canonical(*args)
    (root / "pilot/preflight.json").write_text(json.dumps({"canonical_membership": p1}))
    (full / "universes" / canonical.name).write_text(header + "X,I,2014-01-01,2024-01-03,full\n")
    proof = baseline_data.publish_membership(root, full)
    (root / "baseline/preflight.json").write_text(
        json.dumps({"complete": True, "canonical_membership": proof})
    )
    assert pilot_data.publish_canonical(*args) == p1
    assert market.sha(canonical.read_bytes()) == proof["published_sha256"]
    assert baseline_data.publish_membership(root, full) == proof


def test_missing_controls_cannot_downgrade_a_certified_session_to_unknown(tmp_path):
    import json

    import pytest

    from thesistally import market, pilot_data

    day = date(2014, 1, 1)
    base = tmp_path / "baseline"
    view = base / "market"
    for part, name in [("sessions", "manifest.json"), ("context", "context.sha256")]:
        p = view / part / str(day) / name
        p.parent.mkdir(parents=True)
        p.write_text("{}")
    (view / "sources").mkdir(parents=True)
    cover = base / "earlier-controls" / "coverage.json"
    cover.parent.mkdir()
    cover.write_text(
        json.dumps(
            {
                "complete": True,
                "from": str(day),
                "to": str(day),
                "sessions": 1,
                "days": [{"day": str(day), "sources_sha256": "expected"}],
            }
        )
    )
    legacy = base / "legacy-surveillance/coverage.json"
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps(
            {"complete": True, "from": "2013-01-01", "to": "2013-12-31", "sessions": 0, "days": []}
        )
    )
    core = {"sessions": [str(day)], "snapshot_sha256": pilot_data.snapshot_hash(view, [day])}
    with pytest.raises(market.MarketError, match="differs from its receipt"):
        baseline_data.finalize_controls(tmp_path, core)


def test_legacy_annexures_verify_isin_and_only_fill_missing_surveillance(tmp_path):
    import json

    import pytest

    from thesistally import market, pilot_data

    day = date(2019, 6, 12)
    study = tmp_path / "baseline"
    view = study / "market"
    source = study / "legacy-surveillance" / str(day)
    source.mkdir(parents=True)
    raw = source.parent / "raw" / "dated.zip"
    raw.parent.mkdir()
    raw.write_bytes(b"official-archive")
    context = view / "context" / str(day) / "context.json"
    context.parent.mkdir(parents=True)
    pilot_data.write_json(
        context, {"symbols": {"X": {"surveillance": "unknown", "floor": 90, "ceiling": 110}}}
    )
    session = view / "sessions" / str(day)
    session.mkdir(parents=True)
    pilot_data.write_json(session / "manifest.json", {"prior_day": "2019-06-11"})
    padding = "".join(f"P{i},INE{i + 100:09d},S,EQ\n" for i in range(199))
    (session / "members.csv").write_text(
        "Symbol,ISIN Code,Industry,Series\nX,INE000000001,S,EQ\n" + padding
    )
    for isin in ["INE000000001", "INE000000002"]:
        pilot_data.write_json(source / "symbols.json", {"X": {"isin": isin, "surveillance": True}})
        pilot_data.write_json(
            source / "sources.json",
            {
                "prior": "2019-06-11",
                "symbols_hash": market.sha((source / "symbols.json").read_bytes()),
                "files": {
                    "../raw/dated.zip": {
                        "url": "https://example.test/dated.zip",
                        "sha256": market.sha(raw.read_bytes()),
                    }
                },
            },
        )
        record = {"sources_sha256": market.sha((source / "sources.json").read_bytes())}
        if isin == "INE000000001":
            overlay = baseline_data.legacy_control_view(study, view, day, record, {})
            assert json.loads((overlay / "symbols.json").read_bytes()) == {
                "X": {"surveillance": True}
            }
        else:
            with pytest.raises(market.MarketError, match="does not match membership"):
                baseline_data.legacy_control_view(study, view, day, record, {})
