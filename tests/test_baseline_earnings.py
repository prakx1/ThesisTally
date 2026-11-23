from datetime import date, datetime

from thesistally import baseline_earnings


def test_tata_motor_dvr_and_ordinary_shares_share_board_meetings():
    day = date(2017, 2, 9)
    members = {"TATAMOTORS": "INE155A01022", "TATAMTRDVR": "IN9155A01020"}
    notices = {date(2017, 2, 14): {(datetime(2017, 1, 25), "IN9155A01012", "TATAMTRDVR")}}
    found, uncertain = baseline_earnings.match_notices(day, members, notices, lambda d: members)
    assert not uncertain
    assert all(found[s][0] == 5 for s in members)


def test_results_join_is_by_issuer_at_signal_time_even_when_symbol_changes():
    day = date(2020, 1, 2)
    meeting = date(2020, 1, 6)
    before = datetime(2020, 1, 2, 15, 44)
    after = datetime(2020, 1, 2, 15, 46)
    names = {"OLDNAME": "INE123A01012", "REUSED": "INE999A01013", "NEWISSUE": "INE654A01023"}
    published = {
        meeting: {
            (before, "INE123A01012", "LATERNAME"),
            (before, "INE999X01015", "REUSED"),
            (before, "INE654A01014", "OLDISSUE"),
        }
    }
    announced, unknown = baseline_earnings.match_notices(day, names, published, lambda d: names)
    assert not unknown
    assert announced["OLDNAME"][:2] == (4, 0)
    assert announced["NEWISSUE"][:2] == (4, 1)
    assert "REUSED" not in announced
    published[meeting].add((after, "INE999A01013", "REUSED"))
    assert baseline_earnings.match_notices(day, names, published, lambda d: names) == (
        announced,
        unknown,
    )


def test_notice_without_isin_needs_same_dated_security_at_announcement_and_scan():
    day = date(2020, 1, 2)
    meeting = date(2020, 1, 6)
    announced = datetime(2019, 12, 20, 12, 0)
    names = {"X": "INE123A01012"}
    rows = {meeting: {(announced, "-", "X")}}
    found, unknown = baseline_earnings.match_notices(
        day, names, rows, lambda d: {"X": "INE888A01013"}
    )
    assert found == {} and unknown == {"X"}
    found, unknown = baseline_earnings.match_notices(day, names, rows, lambda d: names)
    assert found["X"][:2] == (4, 2) and not unknown
