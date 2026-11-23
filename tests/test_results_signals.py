from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from thesistally.results_signals import announcements, price_setup, timestamp, usable


def filings():
    periods = pd.date_range("2013-03-31", periods=16, freq="QE")
    calendar = list(pd.bdate_range("2012-01-02", "2018-12-31").date)
    rows = []
    for i, end in enumerate(periods):
        reported = end.date() + timedelta(days=20)
        stamp = timestamp(str(reported) + "T16:10:00+05:30")
        rows.append(
            {
                "isin": "issuer",
                "basis": "standalone",
                "period_end": str(end.date()),
                "announced_at": stamp.isoformat(),
                "usable_session": str(usable(stamp, calendar)),
                "first_reported": True,
                "eps": i * i / 10 + (20 if i == 12 else 0),
            }
        )
    return rows, calendar


def test_exchange_clock_at_close_holidays_and_after_close():
    sessions = [date(2024, 1, 25), date(2024, 1, 29), date(2024, 1, 30)]
    assert usable(timestamp("2024-01-25T10:00:00+00:00"), sessions) == sessions[0]
    assert usable(timestamp("2024-01-25T15:30:01+05:30"), sessions) == sessions[1]
    assert usable(timestamp("2024-01-26T10:00:00+05:30"), sessions) == sessions[1]
    with pytest.raises(ValueError):
        timestamp("2024-01-26T10:00:00")


def test_eight_prior_yoy_changes_and_future_releases_cannot_change_the_score():
    rows, cal = filings()
    members = lambda day: {"issuer"}
    past, counts = announcements(rows[:13], cal, members)
    assert counts["unavailable_sue"] == 12 and len(past) == 1
    eps = np.array([r["eps"] for r in rows[:13]])
    delta = eps[4:] - eps[:-4]
    assert past[0]["sue"] == pytest.approx(delta[-1] / np.std(delta[:-1], ddof=1))
    revised = {**rows[0], "first_reported": False, "eps": 1000}
    later_consol = {
        **rows[12],
        "basis": "consolidated",
        "announced_at": rows[13]["announced_at"],
        "usable_session": rows[13]["usable_session"],
    }
    future, _ = announcements([*rows, revised, later_consol], cal, members)
    assert [r for r in future if r["period_end"] == past[0]["period_end"]] == past


def test_missing_period_known_late_and_mixed_basis_do_not_supply_a_score():
    rows, cal = filings()
    for changed in (
        [*rows[1:13]],
        [{**rows[0], "basis": "consolidated"}, *rows[1:13]],
        [
            {
                **rows[0],
                "announced_at": rows[13]["announced_at"],
                "usable_session": rows[13]["usable_session"],
            },
            *rows[1:13],
        ],
        [{**r, "eps": f"{i / 10:.1f}"} for i, r in enumerate(rows[:13])],
    ):
        assert announcements(changed, cal, lambda d: {"issuer"})[0] == []


def test_three_session_return_uses_four_closes_and_decision_date_inputs_only():
    dates = pd.bdate_range("2018-02-01", periods=26).date
    price = np.arange(len(dates)) + 100.0
    frame = pd.DataFrame(
        {"close": price, "high": price + 1, "low": price - 1, "volume": 1.0e7}, index=dates
    )
    index = pd.Series(400.0, index=dates)
    days = dates[-6:-2]
    data, reason = price_setup(frame, index, days)
    assert reason is None and data["close"] == frame.loc[days[-1], "close"]
    assert data["reaction"] == pytest.approx(
        frame.loc[days[-1], "close"] / frame.loc[days[0], "close"] - 1
    )
    frame.loc[dates[-1], "close"] = 1.0e9
    assert price_setup(frame, index, days)[0] == data
    frame.loc[days[1], "close"] = np.nan
    assert price_setup(frame, index, days)[1] == "reaction_missing"


def test_changed_first_print_and_unlogged_exchange_changes_are_rejected(tmp_path):
    from thesistally.discovery_registry import digest, encoded
    from thesistally.results_signals import verify_filing_products

    products = {}
    sources = {}
    for name in (
        "first-releases.jsonl",
        "quarter-coverage.csv",
        "stock-coverage.csv",
        "stock-year-coverage.csv",
    ):
        (tmp_path / name).write_text(name)
        products[name] = digest(name.encode())
    for exchange in ("nse", "bse"):
        (tmp_path / exchange).mkdir()
        (tmp_path / exchange / "filings.jsonl").write_text(exchange)
        sources[exchange] = {"sha256": digest(exchange.encode())}
    (tmp_path / "nse/filings-receipt.json").write_bytes(encoded(sources["nse"]))
    coverage = encoded({"products": products, "filing_sources": sources})
    assert verify_filing_products(tmp_path, coverage) == b"first-releases.jsonl"
    (tmp_path / "bse/filings.jsonl").write_bytes(b"revision")
    with pytest.raises(ValueError, match="digest|SHA-256|hash"):
        verify_filing_products(tmp_path, coverage)
    (tmp_path / "first-releases.jsonl").write_bytes(b"later comparative")
    with pytest.raises(ValueError, match="coverage product changed"):
        verify_filing_products(tmp_path, coverage)


def test_percentile_uses_same_day_batch_prior_90_days_and_membership():
    original, cal = filings()
    rows = []
    for idx in range(6):
        for row in original[:13]:
            record = dict(row, isin=f"I{idx}")
            if row == original[12]:
                record["eps"] += idx
                if idx == 5:
                    record["eps"] += 1000  # excluded historical nonmember
            rows.append(record)
    ranked, _ = announcements(rows, cal, lambda day: {f"I{i}" for i in range(5)})
    assert [(r["isin"], r["peers"]) for r in ranked] == [("I4", 5)]
    # A later outlier is visible in its own 90-day rank and cannot revise the prior announcement.
    later = original[13].copy()
    later["announced_at"] = "2017-03-01T13:00:00+05:30"
    later["usable_session"] = "2017-03-01"
    later["isin"] = "I0"
    new, _ = announcements([*rows, later], cal, lambda d: {f"I{i}" for i in range(5)})
    assert new[0] == ranked[0] and new[-1]["peers"] == 1


def test_consolidated_available_in_initial_batch_wins_on_its_own_history():
    first, cal = filings()
    extra = [{**r, "basis": "consolidated", "eps": 5 + r["eps"] / 2} for r in first[:13]]
    decisions, _ = announcements([*first[:13], *extra], cal, lambda day: {"issuer"})
    assert len(decisions) == 1 and decisions[0]["basis"] == "consolidated"


def test_same_session_standalone_eps_fallback_precedes_scoring():
    first, cal = filings()
    group = [{**r, "basis": "consolidated"} for r in first[:13]]
    group[-1]["eps"] = None
    result, _ = announcements([*first[:13], *group], cal, lambda day: {"issuer"})
    assert result[0]["basis"] == "standalone"
    group[-1]["eps"] = 42
    # The basis choice cannot be optimized using score availability or sign.
    assert announcements([*first[:13], group[-1]], cal, lambda day: {"issuer"})[0] == []


def test_signal_history_diagnostic_counts_consecutive_asof_quarters_and_zero_variance():
    rows, calendar = filings()
    flat = [{**r, "eps": 1} for r in rows[:13]]
    _, counts = announcements(flat, calendar, lambda _: {"issuer"})
    assert counts["consecutive_original_eps_13"] == counts["zero_prior_change_variance"] == 1
    assert sum(counts[f"consecutive_original_eps_{n}"] for n in range(14)) == 13
    flat[5]["eps"] = None
    _, counts = announcements(flat, calendar, lambda _: {"issuer"})
    assert counts["consecutive_original_eps_0"] == 1
    assert counts["consecutive_original_eps_7"] == 1
    assert counts["consecutive_original_eps_13"] == counts["zero_prior_change_variance"] == 0


def test_profit_only_bulletin_can_precede_first_eps_only_over_certified_absence():
    rows, cal = filings()
    release = rows[12]
    early = {
        **release,
        "basis": "consolidated",
        "eps": None,
        "announced_at": "2016-04-19T13:00:00+05:30",
        "usable_session": "2016-04-19",
    }
    in_index = lambda _: {"issuer"}
    expected, _ = announcements(rows[:13], cal, in_index)
    assert expected and early["usable_session"] < release["usable_session"]
    assert not announcements([*rows[:13], early], cal, in_index)[0]
    early.update(
        eps_absence_from=early["announced_at"],
        eps_absence_before="2016-04-23T13:00:00+05:30",
        eps_absence_evidence=[{"source": "official filing"}],
    )
    assert announcements([*rows[:13], early], cal, in_index)[0] == expected
    early["eps_absence_before"] = release["announced_at"]
    assert not announcements([*rows[:13], early], cal, in_index)[0]


def test_unattributable_earlier_other_basis_blocks_reaction_but_preserves_eps_history():
    from thesistally.fundamental_coverage import reconcile

    rows, cal = filings()
    rows = [
        dict(
            r,
            id=str(i),
            source_url="https://www.nseindia.com/filing",
            sha256="a" * 64,
            revenue=1,
            net_profit=1,
            operating_profit=1,
        )
        for i, r in enumerate(rows)
    ]
    dates = [d.isoformat() for d in cal]
    early = {
        **rows[12],
        "id": "earlier",
        "basis": "consolidated",
        "announced_at": "2016-04-19T13:00:00+05:30",
        "first_reported": False,
        "eps": None,
        "gap": "first_release_attachment_unavailable",
    }
    baseline, _ = reconcile(rows, dates)
    ranked, initial = announcements(baseline, cal, lambda _: {"issuer"})
    originals, _ = reconcile([*rows, early], dates)
    after, counts = announcements(originals, cal, lambda _: {"issuer"})
    assert [r for r in ranked if r["period_end"] == rows[12]["period_end"]]
    assert not [r for r in after if r["period_end"] == rows[12]["period_end"]]
    assert counts["unverified_first_eps_session"] == 1
    assert counts["sue_scored"] == initial["sue_scored"] - 1
    assert next(r for r in originals if r["id"] == "12")["eps"] == rows[12]["eps"]
