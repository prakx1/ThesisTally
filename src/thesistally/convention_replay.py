"""Repeat the ten original r2 funded windows under the separately versioned conventions."""

import json
from contextlib import closing
from datetime import date
from pathlib import Path

from . import conventions, evaluation, market, pilot
from .convention_market import certify
from .journal import journal
from .pilot_data import write_json
from .signals import rules


def run(root):
    root = Path(root).resolve()
    receipt = certify(root)
    market_root = root / "stage3/market"
    parent = json.loads((root / "baseline/preflight.json").read_bytes())
    sessions = list(map(date.fromisoformat, parent["sessions"]))
    cfg = rules(revision="r2")
    cfg = {
        **cfg,
        "source_strategy_revision": cfg["revision"],
        "execution_convention": conventions.VERSION,
    }
    cfg["config_hash"] = market.sha(json.dumps(cfg, sort_keys=True).encode())
    cfg["revision"] += f"@{conventions.VERSION}"
    output = root / "stage3" / "r2-convention-replay" / receipt["snapshot_sha256"][:16]
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "convention": conventions.VERSION,
        "snapshot_sha256": receipt["snapshot_sha256"],
        "rule_revision": cfg["revision"],
        "config_hash": cfg["config_hash"],
        "windows": [],
    }
    for key, days, _ in evaluation.walk_windows(sessions, 3, 1, None):
        path = output / key / "journal.sqlite"
        terms = market_root / "corporate_actions.json"
        if terms.is_file():
            from .pilot_data import checked_link

            checked_link(terms, path.parent / terms.name)
        run_id = "r2-corrected-portfolio"
        with closing(journal(path)) as db:
            pilot.prefix(db, run_id, days)
            gaps = evaluation.replay_window(db, market_root, days, [(run_id, cfg)])
            count = pilot.prefix(db, run_id, days)
            e = dict(
                db.execute(
                    """SELECT e.kind,COUNT(*) FROM events e JOIN calls c ON c.id=e.call_id WHERE c.run=? AND c.allocation='portfolio' GROUP BY e.kind""",
                    (run_id,),
                )
            )
            case = [
                dict(r)
                for r in db.execute(
                    """SELECT c.symbol,e.day,e.kind,e.detail FROM calls c JOIN events e ON e.call_id=c.id WHERE c.run=? AND c.allocation='portfolio' AND ((c.symbol='INDHOTEL' AND e.day BETWEEN '2021-07-21' AND '2021-07-24') OR (c.symbol='ADANIPORTS' AND e.day='2024-06-04' AND e.kind='quarantine_release'))""",
                    (run_id,),
                )
            ]
            item = {
                "window": key,
                "first": str(days[0]),
                "last": str(days[-1]),
                "expected": len(days),
                "scanned": count,
                "complete": count == len(days),
                "valuation_gaps": gaps,
                "funded_events": e,
                "case_events": case,
            }
            summary["windows"].append(item)
            print(key, count, len(days), gaps, flush=True)
        write_json(output / "result.json", summary)
    summary["complete"] = all(w["complete"] for w in summary["windows"])
    write_json(output / "result.json", summary)
    return summary


if __name__ == "__main__":
    raise SystemExit(0 if run(Path("data"))["complete"] else 1)
