import gzip
import json

import pytest

from thesistally import fundamental_notice_language as language


def test_identifies_actual_earnings_and_excludes_future_events_or_other_issuers():
    def event(text, category="Press Release"):
        return {
            "desc": category,
            "attchmntText": text,
            "attchmntFile": "https://archives.nseindia.com/file.pdf",
        }

    assert (
        language.classify(event("Strides Arcolab reports Q3 FY 2014-15 results"))[
            "release_language"
        ]
        == "issuer_earnings_release"
    )
    assert language.classify(
        event("Informs about the Outcome of Board Meeting held yesterday", "Results")
    )
    for text in (
        "Wipro to announce results for the second quarter ended September 30",
        "Company published notice of board meeting to consider Q1 results",
        "Company's wholly-owned subsidiary reports Q3 FY2016 results",
        "Q2FY17 Results - Concall scheduled tomorrow",
    ):
        assert not language.classify(event(text))
    assert not language.classify(event("Wipro announces quarterly revenue guidance only"))


def test_old_exchange_bodies_bind_basis_period_issuer_hash_and_current_net_profit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(language, "ROOT", tmp_path)
    sub = tmp_path / "notice-language-sources"
    sub.mkdir()
    msg = {
        "desc": "Results Update, Quarter ended, 31-DEC-2014 (Standalone)",
        "attchmntText": "Net Sales of Rs. 150 lacs for quarter ending on 31-DEC-2014. Net Profit / (Loss) of Rs. (12.50) lacs for the quarter ending on 31-DEC-2014 against Rs. 318 lacs for the quarter ending on 31-DEC-2013.",
        "symbol": "ISSUER",
        "sm_isin": "INE555A01011",
        "an_dt": "01-Feb-2015 15:27:00",
        "attchmntFile": "-",
    }
    blob = json.dumps([msg]).encode()
    dig = language.sha(blob)
    (sub / (dig + ".json.gz")).write_bytes(gzip.compress(blob))
    notice = dict(
        **language.classify(msg),
        catalogue_sha256=dig,
        accession_sha256=language.accession(msg),
        isin=msg["sm_isin"],
        symbol="ISSUER",
        text=msg["attchmntText"],
        announced_at="2015-02-01T15:27:00+05:30",
        catalogue_url="https://www.nseindia.in/api/corporate-announcements",
    )
    filing = {
        "symbol": "ISSUER",
        "isin": msg["sm_isin"],
        "period_end": "2014-12-31",
        "basis": "standalone",
    }
    actual = language.inline_fact_proof(notice, filing)
    assert actual["values"] == {
        "net_profit": -1250000.0,
        "eps": None,
        "revenue": None,
        "operating_profit": None,
    }
    assert actual["accession_sha256"] == notice["accession_sha256"]
    assert language.inline_fact_proof(notice, dict(filing, basis="consolidated")) is None
    assert language.inline_fact_proof(dict(notice, declared_duration="year"), filing) is None
    with pytest.raises(ValueError, match="identity, time, or body changed"):
        language.inline_fact_proof(dict(notice, text=notice["text"].replace("318", "314")), filing)
    language.original_bodies.cache_clear()


def test_complete_summary_body_proves_eps_and_operating_profit_absent_without_converting_net_sales():
    body = (
        "Issuer Limited has informed the Exchange regarding the standalone Results for the quarter ended on 31-DEC-2014 as follows: "
        "Net Sales of Rs. 150 lacs for quarter ending on 31-DEC-2014 against Rs. 48 lacs for the quarter ending on 31-DEC-2013. "
        "Net Profit / (Loss) of Rs. 12 lacs for the quarter ending on 31-DEC-2014."
    )
    original = {"attchmntText": body, "attchmntFile": "-", "sm_name": "Issuer Limited"}
    filing = {"period_end": "2014-12-31", "basis": "standalone"}
    assert language.exhaustive_body(original, filing)
    for change in [
        {"attchmntText": body + " Basic EPS was Rs 4."},
        {"attchmntText": body + ' <a href="result.pdf">results</a>'},
        {"attchmntFile": "https://archives.nseindia.com/first.pdf"},
        {"sm_name": "Other Issuer Limited"},
        {"hasXbrl": True},
    ]:
        assert not language.exhaustive_body({**original, **change}, filing)
