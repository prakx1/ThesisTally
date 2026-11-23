import json
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest

from thesistally import results_bse_inline as inline

PREFIX = "https://www.bseindia.com/XBRLFILES/"
TOKEN = "Integrated_Finance_Ind_As_532540_810202622559"
NATIVE = PREFIX + "IFIndasUploadDocument/" + TOKEN + ".xml"
RENDER = PREFIX + "IFIndasDuplicateUploadDocument/" + TOKEN + "_IFIndAs.html"
TARGET = {
    "scrip_code": "532540",
    "period_end": "2026-09-30",
    "basis": "consolidated",
    "published_at": "2026-10-08T22:05:59.380000+05:30",
    "archive_url": NATIVE,
}
VALID = {
    "Quarter_Name": "Consolidated-Sep-Qtr-2026",
    "filing_date_time": "2026-10-08T22:05:59.38",
    "revised_date_time": None,
    "status": "New",
    "revised_reson": None,
    "xbrlurl": RENDER,
    "XbrlFile": RENDER,
    "seourl": "/stock-share-price/tata-consultancy-services-ltd/tcs/532540/",
}


def test_dual_catalogue_match_requires_same_clock_scrip_accession_scope_and_original():
    assert inline.match_original([VALID], TARGET) == (
        {
            "inline_url": RENDER,
            "publisher_row": VALID,
            "linked_edition_time": TARGET["published_at"],
        },
        "matched",
    )
    later = {
        **VALID,
        "status": "Revised",
        "filing_date_time": None,
        "revised_date_time": "2026-10-09T15:22:00.01",
        "XbrlFile": RENDER.replace("22559", "later"),
        "xbrlurl": RENDER.replace("22559", "later"),
    }
    assert inline.match_original([VALID, later], TARGET)[1] == "matched"


@pytest.mark.parametrize(
    "change",
    [
        {"filing_date_time": "2026-10-08T22:05:59.381"},
        {"Quarter_Name": "Standalone-Sep-Qtr-2026"},
        {"Quarter_Name": "Consolidated-Sep-Hly-2026"},
        {"Quarter_Name": "Consolidated-Jun-Qtr-2026"},
        {"XbrlFile": RENDER.replace("22559", "22558"), "xbrlurl": RENDER.replace("22559", "22558")},
        {
            "XbrlFile": RENDER.replace("532540", "500180"),
            "xbrlurl": RENDER.replace("532540", "500180"),
        },
        {"seourl": "/different/500180/"},
        {"XbrlFile": None, "xbrlurl": None},
        {"XbrlFile": RENDER.replace("22559", "22558")},
        {"filing_date_time": None},
        {"status": "Revised", "revised_date_time": "2026-10-08T22:05:59.38"},
    ],
)
def test_an_approximate_match_never_certifies_an_earlier_original(change):
    assert inline.match_original([{**VALID, **change}], TARGET)[0] is None


def test_earlier_unknown_or_reused_edition_excludes_the_nominal_first_document():
    earlier = {**VALID, "filing_date_time": "2026-10-08T20:00:00"}
    assert inline.match_original([earlier, VALID], TARGET)[0] is None
    rev = {**VALID, "status": "Revised", "revised_date_time": "2026-10-10T12:12:12"}
    assert (
        inline.match_original([VALID, rev], TARGET)[1] == "integrated_filename_reused_by_revision"
    )
    assert inline.match_original([VALID, {**VALID, "filing_date_time": None}], TARGET)[0] is None


def test_inline_original_attachment_receipts_portable_idempotent_and_source_verifiable(tmp_path):
    db_in = tmp_path / "in"
    case_url = inline.CATALOGUE + TARGET["scrip_code"]
    asof = datetime.now(UTC).isoformat()
    with closing(inline.census.connection(db_in)) as a:
        raw = json.dumps({"Table": [VALID]}).encode()
        inline.census.save(a, (case_url, asof, 200, "inline_catalogue", raw, {}, None))
        inline.census.save(a, (RENDER, asof, 404, "http_error", b"not found", {}, None))
        a.commit()
    with closing(inline.census.connection(tmp_path / "out")) as out:
        for _ in range(2):
            inline.import_receipts(out, db_in)
        assert out.execute("select count(*) from observations").fetchone()[0] == 2
        sha, rows = inline.catalogue_map(out)[case_url]
        assert sha == inline.digest(raw)
        assert inline.match_original(rows, TARGET)[0]["inline_url"] == RENDER


def test_catalogue_response_requires_actual_json_table():
    url = inline.CATALOGUE + "532540"
    for payload, expected in [
        (b'{"Table":[]}', "inline_catalogue"),
        (b"[]", "unverified_catalogue"),
        (b"<html/>", "unverified_catalogue"),
    ]:
        with httpx.Client(
            transport=httpx.MockTransport(lambda req, raw=payload: httpx.Response(200, content=raw))
        ) as client:
            assert inline._get(client, url)[3] == expected


def test_independent_catalogue_does_not_require_legacy_clock_and_keeps_old_calls_and_revisions():
    earlier = {**VALID, "filing_date_time": "2026-10-07T18:05:00"}
    half = {**earlier, "Quarter_Name": "Consolidated-Sep-Hly-2026"}
    new_href = RENDER.replace("22559", "110202622010")
    revision = {
        **VALID,
        "status": "Revised",
        "filing_date_time": None,
        "revised_date_time": "2026-11-01T22:00:01",
        "XbrlFile": new_href,
        "xbrlurl": new_href,
    }
    rows = inline.integrated_editions([half, earlier, revision], "532540")
    result = rows["2026-09-30", "consolidated"]
    assert len(result) == 2 and result[0]["filing_scope"] == "Quarter" and result[0]["new"]
    assert result[0]["at"] == "2026-10-07T18:05:00+05:30"
    assert result[1]["at"] == "2026-11-01T22:00:01+05:30" and result[1]["has_revision_marker"]


@pytest.mark.parametrize(
    "label,scope",
    [
        ("Consolidated-Mar-Yearly-2025", "Year"),
        ("Consolidated-Dec-NineMths-2024", "Nine Months"),
        ("Consolidated-Sep-Hly-2024", "Half Year"),
    ],
)
def test_other_durations_bind_own_clock_only_and_retain_unknown_first(label, scope):
    named = {**VALID, "Quarter_Name": label, "filing_date_time": None}
    selected = next(iter(inline.integrated_editions([named], "532540").values()))[0]
    assert selected["at"] is None and selected["status"] == "New" and not selected["new"]
    assert selected["filing_scope"] == scope and selected["venue"] == "BSE Integrated"


def test_global_original_worker_only_visits_integrated_hrefs_from_approved_windows(
    monkeypatch, tmp_path
):
    queue = tmp_path / "global-targets.jsonl"
    monkeypatch.setattr(inline, "GLOBAL_TARGETS", queue)
    monkeypatch.setattr(inline, "ROOT", tmp_path / "archive")
    requests = []
    monkeypatch.setattr(
        inline,
        "collect",
        lambda db, urls, kind, attempt, **kwargs: requests.append((urls, kind, attempt)),
    )

    def write(year):
        rows = [
            {"venue": "BSE Integrated", "url": RENDER, "season": year},
            {"venue": "BSE Result Archive", "url": NATIVE, "season": "2025"},
        ]
        queue.write_text("\n".join(json.dumps(row) for row in rows))

    write("2026")
    inline.global_originals(2)
    assert requests == [({RENDER}, "xbrl", 1), ({RENDER}, "xbrl", 2)]
    write("2024")
    with pytest.raises(ValueError):
        inline.global_originals(1)


def test_current_client_probes_matching_original_hash_separately_from_coverage(
    tmp_path, monkeypatch
):
    one, two = inline.FILE_CONTROLS
    raw = b'<xbrl xmlns="http://www.xbrl.org/2003/instance"><context id="real"/></xbrl>'
    monkeypatch.setattr(inline, "FILE_CONTROLS", {one: inline.digest(raw), two: "different"})

    def get(req):
        return httpx.Response(
            200, content=b'{"Table":[]}' if str(req.url).startswith(inline.CATALOGUE) else raw
        )

    with (
        closing(inline.census.connection(tmp_path / "controls")) as controls,
        httpx.Client(transport=httpx.MockTransport(get)) as http,
    ):
        inline.check_controls(controls, http)
        observed = {r[0]: r[1] for r in controls.execute("select url,kind from observations")}
        assert observed == {
            one: "known_original_bytes",
            two: "different_xbrl_bytes",
            inline.CATALOGUE + "532540": "inline_catalogue",
        }
        with closing(inline.census.connection(tmp_path / "evidence")) as evidence:
            for _ in range(2):
                inline.include_target_control_bytes(evidence, controls, {one})
            rows = evidence.execute("SELECT url,kind,sha FROM observations").fetchall()
            assert [tuple(r) for r in rows] == [(one, "xbrl", inline.digest(raw))]
