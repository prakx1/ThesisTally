import json

import httpx
import pytest

from thesistally import results_bse_issuer_leads as identity

ISIN = "INE001A01036"
NEW_CLASS = "INE001A01028"
OTHER = "INE009A01021"


def test_assigns_search_leads_to_ambiguous_and_retired_classes_only_for_discovery():
    quarters = [
        {"isin": ISIN, "symbol": "OLD"},
        {"isin": ISIN, "symbol": "NEW"},
        {"isin": NEW_CLASS, "symbol": "PRIOR"},
        {"isin": OTHER, "symbol": "UNLISTED"},
    ]
    source = {"url": "https://www.bseindia.com/", "sha256": "a" * 64}
    rows = [
        {"SCRIP_CD": "500001", "ISIN_NUMBER": ISIN, "scrip_id": "NEW"},
        {"SCRIP_CD": "500002", "ISIN_NUMBER": "INE001A02025", "scrip_id": "ALT"},
        {"SCRIP_CD": "500003", "ISIN_NUMBER": "INE999B01000", "scrip_id": "OLD"},
    ]
    out = identity.build(quarters, [(source, rows)])
    by_key = {(r["isin"], r["scrip_code"]): r["match"] for r in out["leads"]}
    assert by_key == {
        (ISIN, "500001"): "exact_current_master_isin",
        (ISIN, "500002"): "issuer_prefix_search_only",
        (ISIN, "500003"): "symbol_search_only",
        (NEW_CLASS, "500001"): "issuer_prefix_search_only",
        (NEW_CLASS, "500002"): "issuer_prefix_search_only",
    }
    assert out["unmatched"] == [{"isin": OTHER, "symbols": ["UNLISTED"]}]
    assert all(r["symbols"] == ["NEW", "OLD"] for r in out["leads"] if r["isin"] == ISIN)


def test_frozen_masters_are_reusable_and_corruption_fails_closed(tmp_path):
    requested = []

    def reply(req):
        status = req.url.params["status"]
        requested.append(status)
        return httpx.Response(
            200, json=[{"SCRIP_CD": "500001", "Segment": "Equity", "Status": status}]
        )

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        one = list(identity.official_masters(http, tmp_path))
        assert list(identity.official_masters(http, tmp_path)) == one
        assert requested == list(identity.STATUSES)
        payload = next(tmp_path.glob("*.raw"))
        payload.write_bytes(json.dumps([]).encode())
        with pytest.raises(ValueError, match="frozen receipt"):
            list(identity.official_masters(http, tmp_path))
