from thesistally.fundamental_notice_alias import selected


def test_alias_notice_never_substitutes_a_more_convenient_isin():
    r = {
        "symbol": "NEW",
        "sm_isin": "INE111A01018",
        "an_dt": "02-Feb-2020 11:00:00",
        "exchdisstime": "02-Feb-2020 11:02:20",
        "desc": "Outcome of Board Meeting",
        "attchmntFile": "https://archives.nseindia.com/corporate/board.pdf",
    }
    receipt = {
        "url": "https://www.nseindia.in/api/corporate-announcements?index=equities",
        "sha256": "a" * 64,
    }
    n = selected(r, {"INE111A01026"}, receipt)
    assert n["isin_scope"] == "other_reported_isin" and n["isin"] == "INE111A01018"
    assert (
        n["announced_at"] == "2020-02-02T11:02:20+05:30"
        and n["catalogue_sha256"] == receipt["sha256"]
    )
    assert selected(r, {r["sm_isin"]}, receipt)["isin_scope"] == "exact_plan_isin"
    assert not selected(
        dict(r, desc="Investor Meet", attchmntText="Conference tomorrow"), {r["sm_isin"]}, receipt
    )
