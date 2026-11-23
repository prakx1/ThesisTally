import json

import httpx
import pytest

from thesistally import results_forward_roster as roster


def fixtures(count=200):
    csv_rows = ["ISIN Code,Symbol,Series"]
    listings = []
    for n in range(count):
        isin = f"INE{n:09d}"
        csv_rows.append(f"{isin},SYMBOL{n},EQ")
        listings.append(
            {
                "ISIN_NUMBER": isin,
                "SCRIP_CD": str(500000 + n),
                "Status": "Active",
                "Segment": "Equity",
            }
        )
    return "\n".join(csv_rows).encode(), listings


def get(raw, listings):
    def csv_page(request):
        return httpx.Response(200, content=raw)

    def master(request):
        assert request.headers["Origin"] == "https://www.bseindia.com"
        return httpx.Response(200, content=json.dumps(listings).encode())

    with (
        httpx.Client(transport=httpx.MockTransport(csv_page)) as n,
        httpx.Client(transport=httpx.MockTransport(master)) as b,
    ):
        return roster.fetch(n, b)


def test_seed_preserves_unknown_and_ambiguous_members_and_both_source_bytes():
    raw, listings = fixtures(201)
    listings[0]["ISIN_NUMBER"] = "INE999999999"
    listings += [
        {**listings[1], "SCRIP_CD": "600000"},
        {**listings[2], "ISIN_NUMBER": "INE555555555"},
    ]
    pages, matched = get(raw, listings)
    assert pages[0].raw == raw and json.loads(pages[1].raw) == listings
    assert len(matched) == 201
    assert [row["scrip"] for row in matched[:4]] == [None, None, None, "500003"]


def test_roster_failures_remain_retryable():
    raw, listings = fixtures(200)
    with pytest.raises(ValueError, match="Incomplete BSE"):
        get(raw, listings[:199])
    with pytest.raises(ValueError, match="Official archive responses"):
        get(fixtures(199)[0], listings)
    with pytest.raises(ValueError, match="Official archive responses"):
        get(raw + b"\nINE000000000,OTHER,EQ", listings)
    with pytest.raises(ValueError, match="Malformed BSE"):
        get(raw, [*listings[:-1], {}])
