from collections import defaultdict

from thesistally import results_bse_census as c
from thesistally import results_bse_inline as inline
from thesistally import results_bse_priority as priority

CODE = "500325"
ARCHIVE_URL = "https://www.bseindia.com/XBRLFILES/IFIndasUploadDocument/Integrated_Finance_Ind_As_500325_1262025135955.xml"
INLINE_URL = "https://www.bseindia.com/XBRLFILES/IFIndasDuplicateUploadDocument/Integrated_Finance_Ind_As_500325_1262025135955_IFIndAs.html"
DATES = ["2025-04-25", "2025-04-28", "2025-06-12", "2025-06-13"]
CASE = {
    "season": "2025",
    "isin": "INE002A01018",
    "symbol": "RELIANCE",
    "period_end": "2025-03-31",
    "window_end": "2025-06-13",
}
KEY = ("2025-03-31", "consolidated")


def inputs():
    source = {
        "scripcode": CODE,
        "Quarter": "Consolidated-Mar-25;JQ25;100;c",
        "Fld_QuarterName": "Quarter",
        "Status": "New",
        "Filing_Date_Time": "2025-06-12T14:00:00",
        "Revised_Date_Time": None,
        "Revision_Reason": "",
        "conso_xbrl_link": ARCHIVE_URL,
    }
    legacy = {CODE: ("a", c.entries({"Table": [source]}, CODE))}
    other = {
        "Quarter_Name": "Consolidated-Mar-Qtr-2025",
        "status": "New",
        "filing_date_time": "2025-04-25T21:17:06.097",
        "xbrlurl": INLINE_URL,
    }
    public = {inline.CATALOGUE + CODE: ("b", [other])}
    nse = {("INE002A01018", *KEY): (c.stamp("2025-04-25T21:57:16"), True, {}, {"approved": True})}
    route = defaultdict(set, {"INE002A01018": {CODE}})
    return legacy, public, nse, route


def test_third_clock_displaces_signed_nse_and_requires_original_objects(tmp_path):
    legacy, new, nse, routing = inputs()
    merged, codes = priority.merge_catalogues(legacy, new)
    no_rivals, contenders = priority.classify(CASE, merged, routing, nse, DATES, codes)
    assert not no_rivals
    choice = contenders["consolidated"][0]
    assert choice["url"] == INLINE_URL and choice["usable"] == "2025-04-28"
    assert choice["requires_dated_bytes"]
    with c.connection(tmp_path) as db:
        raw = b"x"
        c.save(db, (INLINE_URL, "retrieved", 200, "xbrl", raw, {}, None))
        assert (
            c.original_bytes(db, INLINE_URL, {c.digest(raw)}, choice["at"], require_dated=True)[1]
            == "undated_bytes_for_conflicting_publication_clocks"
        )
        c.save(
            db,
            (
                INLINE_URL,
                "retrieved2",
                200,
                "xbrl",
                raw,
                {"last-modified": "Thu, 12 Jun 2025 08:29:55 GMT"},
                None,
            ),
        )
        assert (
            c.original_bytes(db, INLINE_URL, {c.digest(raw)}, choice["at"], require_dated=True)[1]
            == "object_modified_after_filing"
        )


def test_known_empty_integrated_catalogue_can_clear_nse_but_unobserved_cannot():
    legacy, _, nse, routing = inputs()
    known, codes = priority.merge_catalogues(legacy, {inline.CATALOGUE + CODE: ("b", [])})
    assert priority.classify(CASE, known, routing, nse, DATES, codes)[0] == ["consolidated"]
    unknown, codes = priority.merge_catalogues(legacy, {})
    assert not priority.classify(CASE, unknown, routing, nse, DATES, codes)[0]
