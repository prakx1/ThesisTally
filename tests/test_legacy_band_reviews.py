from datetime import date

from thesistally import legacy_band_reviews as bands


def test_review_requires_exact_session_and_numeric_eq_isin(monkeypatch):
    board = {
        "Rev": [
            {"A": "Sr.No.", "B": "Symbol", "C": "Series", "E": "Band", "F": "ISIN"},
            {"A": "1", "B": "ABC", "C": "EQ", "E": "5", "F": "INE216A01022"},
            {"A": "2", "B": "DEF", "C": "BE", "E": "10", "F": "INE216A01022"},
            {"A": "3", "B": "GHI", "C": "EQ", "E": "10", "F": "INE216A01023"},
        ]
    }
    monkeypatch.setattr(bands, "workbooks", lambda _: [board])
    assert bands.read_review(b"") == [("ABC", "INE216A01022", 5)]
    assert bands.effective_date("Changes in price bands from December 06, 2019") == date(
        2019, 12, 6
    )
    assert bands.effective_date("Changes in price bands") is None


def test_published_changes_not_applied_before_they_are_known_and_conflicts_stay_unknown():
    prior = date(2019, 12, 5)
    effective = date(2019, 12, 6)
    identities = {"ABC": "INE216A01022", "DEF": "INE467B01029"}
    circular = {"url": "nse/circular", "sha256": "123"}
    daily = {
        "url": "nse/daily",
        "sha256": "456",
        "effective": "2019-12-06",
        "last_modified": "Thu, 05 Dec 2019 14:10:04 GMT",
    }
    records = [("ABC", "INE216A01022", 10), ("DEF", "INE467B01029", 10)]
    labels, conflicts = bands.resolve(
        effective,
        prior,
        identities,
        [(prior, records, circular)],
        ([("ABC", 5), ("DEF", 10)], daily),
    )
    assert (
        conflicts == ["ABC"]
        and labels["DEF"]["percent"] == 10
        and len(labels["DEF"]["sources"]) == 2
    )
    assert bands.resolve(effective, prior, identities, [(effective, records, circular)], None) == (
        {},
        [],
    )
    assert bands.resolve(
        effective, prior, identities, [(prior, [("ABC", "INE467B01029", 5)], circular)], None
    ) == ({}, [])


def test_daily_after_column_only_and_preserve_non_numeric_conflict():
    raw = b"Sr. No,Symbol,Series,Security Name,From,To\n1,A,EQ,Company,20,5\n2,B,BE,Company,20,10\n3,C,EQ,Company,2,No Band\nnull\n"
    assert bands.daily_labels(raw) == [("A", 5), ("C", None)]
    assert bands.daily_labels(raw.replace(b"\n", b"\r")) == [("A", 5), ("C", None)]


def test_effective_dated_price_changes_require_matching_prior_evening_origin_version():
    # NSE/SURV/47235 published 4 February 2021 for the next open. All 291
    # changes appear in eq_band_changes_05022021.csv. The earlier file has
    # three unrelated changes. Its label cannot be carried to the 5th.
    import pytest

    prior = date(2021, 2, 4)
    assert bands.known_by_prior_close("Thu, 04 Feb 2021 13:30:33 GMT", prior)
    assert not bands.known_by_prior_close("Fri, 05 Feb 2021 01:00:00 GMT", prior)
    assert not bands.known_by_prior_close("Thu, 17 Jan 2019 12:00:00 GMT", date(2014, 1, 1))
    wrong = {
        "url": "nse/file",
        "sha256": "1",
        "effective": "2021-02-04",
        "last_modified": "Thu, 04 Feb 2021 13:30:33 GMT",
    }
    with pytest.raises(ValueError, match="applicable-date"):
        bands.resolve(date(2021, 2, 5), prior, {"A": "isin"}, [], ([("A", 5)], wrong))
