"""Run the isolated 209-DVR-share lifecycle fixture on the certified 2024 tape."""

import json
import math
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from thesistally import corporate, exchange, market, pilot
from thesistally.journal import advance_from_frame, journal, record
from thesistally.signals import Candidate, attach_following, rules


def fixture(work):
    root = work / "market"
    days, proof = pilot.preflight_inputs(root, work / "preflight.json")
    cfg = rules(revision="r1")
    isin = "IN9155A01020"
    terms = corporate.actions(root)[isin]
    seed_date = date(2024, 8, 28)
    stop = 700.0  # Documented illustrative level in docs/current-design.md.
    entry = corporate.quote(root, seed_date, isin)["close"]
    target = entry + cfg["exits"]["target_r"] * (entry - stop)
    seed = Candidate("TATAMTRDVR", isin, "Auto", "trend_pullback", entry, stop, target, {})
    fixtures = work / "fixtures"
    fixtures.mkdir(exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="dvr-209-", dir=fixtures))
    run = "fixture-dvr-209"
    day_marks = []
    with closing(journal(dest / "journal.sqlite")) as db:
        with db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)", (run, cfg["revision"], 1e6, 1e6))
            record(db, run, str(seed_date), {"enabled": True}, seed, cfg, 209, "portfolio")
        for day in days:
            if not seed_date < day <= date.fromisoformat(terms["tradable"]):
                continue
            # Only the predecessor and successor are needed. Across suspension
            # attach_following uses the same conversion as a primary scan.
            ctx = exchange.read_context(root, day, verify_sources=True)
            frames, members = {}, {}
            for symbol_isin in (isin, terms["successor_isin"]):
                quote = corporate.quote(root, day, symbol_isin)
                if quote and quote["symbol"] in ctx:
                    symbol = quote["symbol"]
                    frame = pd.DataFrame(
                        [{**quote, "fast": float("nan"), "trail": float("nan")}], index=[day]
                    )
                    frame.attrs["exchange"] = ctx[symbol]
                    frames[symbol] = frame
                    members[symbol_isin] = symbol
            row = db.execute("SELECT * FROM calls").fetchone()
            attach_following(root, day, cfg, [row], frames, members)
            with db:
                advance_from_frame(db, row, str(day), frames, cfg)
            state = corporate.state(db, row["id"]) or {}
            day_marks.append(
                {
                    "day": str(day),
                    "phase": state.get("phase"),
                    "frozen_value": state.get("frozen_value"),
                }
            )
        c = dict(db.execute("SELECT * FROM calls").fetchone())
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id")]
        for e in events:
            if e["kind"] == "corporate_action":
                e["detail"] = json.loads(e["detail"])
        fills = [
            dict(r)
            for r in db.execute(
                "SELECT f.*,s.isin FROM fills f JOIN fill_securities s ON s.fill_id=f.id ORDER BY f.id"
            )
        ]
        cash = db.execute("SELECT sum(amount) FROM corporate_cash").fetchone()[0]
        stages = {e["detail"]["stage"]: e for e in events if e["kind"] == "corporate_action"}
        buy, sell = fills
        assert c["status"] == "closed"
        assert buy["day"] == "2024-08-29" and buy["qty"] == 209 and buy["isin"] == isin
        assert (
            sell["day"] == terms["tradable"]
            and sell["qty"] == 146
            and sell["isin"] == terms["successor_isin"]
        )
        assert math.isclose(sell["price"], 970.8 * (1 - cfg["cost"]["slippage_bps"] / 10000))
        assert [s for s in stages] == ["suspended", "converted", "credited", "tradable"]
        converted = stages["converted"]["detail"]
        assert converted["fraction"] == 0.3 and converted["effective_date"] == "2024-09-01"
        for k, v in converted["translated_levels"].items():
            assert math.isclose(c[k] * 10 / 7, v)
        credited = stages["credited"]["detail"]
        assert credited["flagged"] and credited["fallback_date"] == "2024-09-02"
        assert credited["price_per_share"] == 1092.65 and cash == 327.80
        final_predecessor = 209 * 768.65
        for m in day_marks:
            if m["phase"] in {"suspended", "entitled", "credited"}:
                assert math.isclose(
                    m["frozen_value"] + (cash if m["phase"] == "credited" else 0), final_predecessor
                )
        original_risk = (buy["price"] - stop) * buy["qty"]
        assert math.isclose(c["outcome_r"], c["net"] / original_risk)
        manifest = root / "sources" / "compulsory-actions" / isin / "sources.json"
        sources = json.loads(manifest.read_bytes())
        assert all(
            market.sha((manifest.parent / k).read_bytes()) == v["sha256"]
            for k, v in sources.items()
        )
        payload = {
            "label": "Seeded plumbing fixture, excluded from all pilot results",
            "seed": "209 shares, illustrative INR 700 stop, Aug 28 observed close, published 2R rule. No detector signal or sizing inference.",
            "start": str(seed_date),
            "end": terms["tradable"],
            **proof,
            "sources": sources,
            "original_levels": {k: c[k] for k in ("entry", "buy", "stop", "target")},
            "fills": fills,
            "events": events,
            "daily_freeze": day_marks,
            "net_inr": c["net"],
            "net_r": c["outcome_r"],
            "zero_fraction_cash_delta_inr": -cash,
            "net_without_fraction_inr": c["net"] - cash,
            "net_without_fraction_r": (c["net"] - cash) / original_risk,
        }
    pilot.write_json(dest / "trace.json", payload)
    return dest / "trace.json"


if __name__ == "__main__":
    print(fixture(Path("data/pilot").resolve()))
