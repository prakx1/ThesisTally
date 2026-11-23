"""Cross-check legacy classifications on four overlapping NSE modern-format dates."""

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from thesistally.legacy_evidence import effective_dates, gsm_table
from thesistally.legacy_snapshot import asm_on_day, dated_market_ids, members, pdf_text, stage_pdf
from thesistally.legacy_surveillance import (
    API,
    ARCHIVE,
    FOLDER,
    GSM,
    LT,
    ST,
    Cache,
    component,
    sha,
    write_json,
)


def check():
    cache = Cache(FOLDER)
    tests = {}
    for day, nxt in [
        (date(2024, 1, 23), date(2024, 1, 24)),
        (date(2024, 1, 24), date(2024, 1, 25)),
        (date(2024, 1, 25), date(2024, 1, 29)),
        (date(2024, 1, 29), date(2024, 1, 30)),
    ]:
        key = day.strftime("%d-%m-%Y")
        url = API + "?" + urlencode({"fromDate": key, "toDate": key})
        rows = json.loads(cache.get("overlap/" + str(day) + ".json", url))["data"]
        records = [r for r in rows if r["fileDept"] == "SURV" and component(r) in (LT, ST)]
        for r in records:
            cache.get("raw/" + r["circFilename"], ARCHIVE + r["circFilename"])
        gsmname = f"sec_list_{day:%d%m%Y}.csv"
        gsm = cache.get(
            "raw/" + gsmname, "https://nsearchives.nseindia.in/content/equities/" + gsmname
        )
        ses = Path("data/sessions") / str(day)
        ids = dated_market_ids(ses)
        universe = members(ses)
        memberids = set(universe.values())
        f = {GSM: gsm_table(gsm, ids, memberids)}
        act = {}
        for c in (LT, ST):
            positive, complete, _, _issue = asm_on_day(FOLDER, c, records, nxt)
            f[c] = {i: i in positive for i in memberids if complete or i in positive}
        data = {}
        path = Path("data/baseline/earlier-controls") / str(day)
        reg = (path / "surveillance.csv").read_bytes()
        regmeta = json.loads((path / "sources.json").read_bytes())["files"]["surveillance.csv"]
        assert sha(reg) == regmeta["sha256"]
        for r in csv.DictReader(io.StringIO(reg.decode("utf-8-sig"))):
            if r["Status"] != "A":
                continue
            i = ids.get((r["Symbol"], r["Series"]))
            if i not in memberids:
                continue
            vals = [v for k, v in r.items() if k == "GSM" or "Term ASM)" in k]
            if len(vals) != 3:
                raise ValueError("Unexpected flags")
            data[i] = {
                GSM: r["GSM"] != "100",
                LT: r["Long_Term_Additional_Surveillance_Measure (Long Term ASM)"] != "100",
                ST: r["Short_Term_Additional_Surveillance_Measure (Short Term ASM)"] != "100",
            }
        for c in (LT, ST, GSM):
            mismatches = [i for i in data if i in f[c] and f[c][i] != data[i][c]]
            act[c] = {
                "compared": len(f[c].keys() & data.keys()),
                "flagged": sum(f[c][i] for i in f[c].keys() & data.keys()),
                "mismatches": mismatches,
            }
        print(day, act, flush=True)
        if any(c["mismatches"] for c in act.values()):
            raise ValueError(f"Legacy surveillance classification differs from REG_IND on {day}")
        tests[str(day)] = {
            "next_open": str(nxt),
            "sources": [
                json.loads((FOLDER / "raw" / (r["circFilename"] + ".source.json")).read_bytes())
                for r in records
            ],
            "daily_gsm": json.loads((FOLDER / "raw" / (gsmname + ".source.json")).read_bytes()),
            "reg_ind": regmeta,
            "checks": act,
        }

    # The circular explicitly takes effect on 2 January. The stage was already
    # present in the 1 January EOD file and absent at the prior close.
    vintage = {}
    for stamp in ("31122019", "01012020"):
        n = f"sec_list_{stamp}.csv"
        raw = cache.get("raw/" + n, "https://nsearchives.nseindia.in/content/equities/" + n)
        rows = [
            r
            for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
            if r["Symbol"] == "BALKRISHNA"
        ]
        assert len(rows) == 1
        vintage[stamp] = {
            "remark": rows[0]["Remarks"],
            "ref": json.loads((FOLDER / "raw" / (n + ".source.json")).read_bytes()),
        }
    assert vintage["31122019"]["remark"] == "-" and vintage["01012020"]["remark"] == "GSM - Stage I"
    letter = "SURV43080.pdf"
    raw = cache.get("raw/" + letter, ARCHIVE + letter)
    assert stage_pdf(raw) == {"INE875R01011"} and effective_dates(pdf_text(raw)) == {
        date(2020, 1, 2)
    }
    for k in ("31122019", "01012020"):
        d = datetime.strptime(k, "%d%m%Y").date()
        assert any(
            s == "BALKRISHNA" and i == "INE875R01011"
            for (s, _), i in dated_market_ids(Path("data/sessions") / str(d)).items()
        )
    write_json(
        FOLDER / "overlap/verification.json",
        {
            "dates": tests,
            "vintage_transition": {
                "isin": "INE875R01011",
                "symbol": "BALKRISHNA",
                "open_effective": "2020-01-02",
                "circular": json.loads((FOLDER / "raw" / (letter + ".source.json")).read_bytes()),
                "files": vintage,
            },
        },
    )
    cache.client.close()


if __name__ == "__main__":
    check()
