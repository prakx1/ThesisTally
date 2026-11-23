import gzip
import json
from datetime import date

import pandas as pd
import pytest

from thesistally import baseline_controls as bc
from thesistally import exchange, market


def test_flags_for_next_entry_use_signal_close_and_bands_use_prior_final(tmp_path):
    first = date(2024, 2, 5)
    assert set(bc.source_urls(first, date(2024, 2, 2))) == {"surveillance.csv"}
    day = date(2024, 2, 6)
    urls = bc.source_urls(day, first)
    assert urls["surveillance.csv"].endswith("REG_IND060224.csv")
    assert urls["security.csv.gz"].endswith("NSE_CM_security_05022024.csv.gz")
    members = pd.DataFrame(
        [{"symbol": "A", "isin": "I", "series": "EQ"}, {"symbol": "B", "isin": "J", "series": "EQ"}]
    )
    flags = b"Symbol,Series,Status,GSM,Long (Long Term ASM),Short (Short Term ASM)\nA,EQ,A,100,100,100\n"
    master = gzip.compress(b"TckrSymb,ISIN,SctySrs,PricRg\nA,I,EQ,80.00-120.00\n")
    raw = {"surveillance.csv": flags, "security.csv.gz": master}
    for name, content in raw.items():
        (tmp_path / name).write_bytes(content)
    decoded = exchange.decode_controls(members, flags, master)
    assert decoded["B"] == {"surveillance": "unknown", "floor": None, "ceiling": None}
    payload = json.dumps(decoded, sort_keys=True).encode()
    (tmp_path / "symbols.json").write_bytes(payload)
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "prior": str(first),
                "symbols_hash": market.sha(payload),
                "files": {
                    name: {"url": urls[name], "sha256": market.sha(data)}
                    for name, data in raw.items()
                },
                "not_published": {},
            }
        )
    )
    assert bc.verify_day(tmp_path, day, first, members)["known_opening_bands"] == 1
    (tmp_path / "security.csv.gz").write_bytes(b"changed")
    with pytest.raises(market.MarketError, match="source mismatch"):
        bc.verify_day(tmp_path, day, first, members)
