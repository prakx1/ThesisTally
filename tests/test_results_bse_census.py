import pytest

from thesistally import results_bse_census as c


def row(url, status="New", at="2020-07-20T08:49:45", period="Jun-20"):
    return {
        "scripcode": 500180,
        "Quarter": f"Consolidated-{period};JQ20;10;c",
        "Fld_QuarterName": "Quarter",
        "Status": status,
        "conso_xbrl_link": url,
        "stand_xbrl_link": None,
        "Filing_Date_Time": at if status == "New" else None,
        "Revised_Date_Time": None if status == "New" else at,
        "Revision_Reason": "",
    }


U = "/XBRLFILES/FourOneUploadDocument/Banking_500180_197202020320.xml"
V = "/XBRLFILES/FourOneUploadDocument/Banking_500180_second.xml"
DAYS = ["2020-07-17", "2020-07-20", "2020-07-21", "2020-07-22"]


def original(rows):
    by_period = c.entries({"Table": rows}, "500180")
    return c.first_editions(by_period[("2020-06-30", "consolidated")], "2020-07-22", DAYS)


def test_basis_clock_original_and_revised_pointer():
    docs, why = original([row(U), row(V, "Revised", "2020-07-20T19:00:00")])
    assert not why and docs[0][0]["url"] == c.WEB + U and docs[0][1] == "2020-07-20"
    assert (
        original([row(U), row(U, "Revised", "2020-07-21T19:00:00")])[1]
        == "original_filename_reused_by_revision"
    )
    assert (
        original([row(U, "Revised"), row(V, "New", "2020-07-20T19:00:00")])[1]
        == "first_edition_is_revised_or_original_unverified"
    )
    assert original([row(U, at=None), row(V)])[1] == "undated_named_original"
    assert original([row(U, at="2020-07-20T15:30:01")])[0][0][1] == "2020-07-21"
    with pytest.raises(ValueError, match="Issuer disagreement"):
        c.entries({"Table": [row(U)]}, "000000")
    assert not original(
        [row("https://elsewhere.example/unsafe.xml"), row("/XBRLFILES/FourOneUploadDocument/")]
    )[0]


def test_cross_exchange_first_clock_and_simultaneous_facts():
    doc = original([row(U)])[0][0][0]
    pending = {"revenue": {42.0}}
    assert c.chronology(doc, (c.stamp("2020-07-20T08:48:00"), False, {})) == (
        "earlier_nse_xbrl",
        {},
    )
    assert c.chronology(doc, (c.stamp("2020-07-20T09:00:00"), True, pending)) == ("", {})
    assert c.chronology(doc, (c.stamp("2020-07-20T08:49:45"), True, pending)) == ("", pending)
    assert (
        c.chronology(doc, (c.stamp("2020-07-20T08:49:45"), False, pending))[0]
        == "simultaneous_nse_original_unverified"
    )


def test_receipt_errors_remain_retryable_known_xml_and_content_conflicts(tmp_path):
    raw = b'<xbrl xmlns="http://www.xbrl.org/2003/instance"/>'
    url = c.WEB + U
    with c.connection(tmp_path) as db:
        c.save(db, (url, "now", 404, c.classify(url, 404, b"404"), b"404", {}, None))
        assert not c.inventory(db)[1][url]
        c.save(
            db,
            (
                url,
                "later",
                200,
                c.classify(url, 200, raw),
                raw,
                {"last-modified": "Sun, 19 Jul 2020 14:33:20 GMT"},
                None,
            ),
        )
        attempts, known = c.inventory(db)
        assert attempts[url] == 2
        assert c.original_bytes(db, url, known[url], "2020-07-20T08:49:45+05:30")[0][1] == raw
        c.save(
            db,
            (
                url,
                "later",
                200,
                "xbrl",
                raw,
                {"last-modified": "Fri, 24 Jul 2020 03:33:20 GMT"},
                None,
            ),
        )
        assert (
            c.original_bytes(db, url, known[url], "2020-07-20T08:49:45+05:30")[1]
            == "object_modified_after_filing"
        )
        c.save(db, (url, "later", 200, "xbrl", raw + b" ", {}, None))
        assert (
            c.original_bytes(db, url, c.inventory(db)[1][url], "2020-07-20T08:49:45+05:30")[1]
            == "conflicting_object_versions"
        )


def test_portable_source_round_trip_includes_failed_and_verified_requests(tmp_path):
    from thesistally import results_bse_evidence as evidence

    root = tmp_path / "original"
    restored = tmp_path / "restored"
    url = c.WEB + U
    with c.connection(root) as db:
        (root / "issuer-leads.json").write_text('{"leads":[]}\n')
        c.save(db, (url, "one", 404, "http_error", b"<title>404</title>", {}, None))
        c.save(db, (url, "two", 200, "xbrl", b"<xbrl/>", {"etag": "2"}, None))
        bundle = evidence.pack(root, db)
        assert bundle == evidence.pack(root, db)
    evidence.restore(restored, bundle)
    assert (root / "issuer-leads.json").read_bytes() == (
        restored / "issuer-leads.json"
    ).read_bytes()
    with c.connection(restored) as copy:
        assert c.raw_by_hash(copy, c.digest(b"<xbrl/>")) == b"<xbrl/>"
        assert c.inventory(copy)[0][url] == 2
