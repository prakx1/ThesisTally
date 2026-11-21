"""Append-only research evidence and independent background work."""

import json
import os
from datetime import datetime
from pathlib import Path

from . import market

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_reports (
 report_id INTEGER PRIMARY KEY, call_id INTEGER REFERENCES calls(id), symbol TEXT NOT NULL, created_at TEXT NOT NULL,
 agent TEXT NOT NULL, agent_version TEXT NOT NULL, llm_model TEXT NOT NULL, rating TEXT, confidence INTEGER,
 key_points_json TEXT NOT NULL, red_flags_json TEXT NOT NULL, filings_used_json TEXT NOT NULL, report_path TEXT,
 tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL, cost_inr REAL NOT NULL, runtime_s REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_calls (
 id INTEGER PRIMARY KEY, call_id INTEGER REFERENCES calls(id), ts TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL,
 model TEXT NOT NULL, prompt_hash TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
 cost_inr REAL, response_json TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS ai_verdicts (
 id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id), ts TEXT NOT NULL, report_id INTEGER,
 decision TEXT NOT NULL, confidence INTEGER, red_flags_json TEXT NOT NULL,summary TEXT NOT NULL, prompt_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_work (call_id INTEGER PRIMARY KEY REFERENCES calls(id), status TEXT, touched REAL);
CREATE TABLE IF NOT EXISTS ai_reservations (id INTEGER PRIMARY KEY, day TEXT NOT NULL, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS filing_extracts (id INTEGER PRIMARY KEY, filing_id INTEGER REFERENCES filings(filing_id),
 model TEXT NOT NULL, prompt_version TEXT NOT NULL, locator TEXT NOT NULL, source_hash TEXT NOT NULL, extract_json TEXT NOT NULL,
 UNIQUE(filing_id,model,prompt_version,locator,source_hash));
"""


def now():
    return datetime.now(market.IST).isoformat(timespec="seconds")


def configured_provider(root):
    choice = os.getenv("RADAR_AI_PROVIDER")
    if choice is None:
        local = Path(root) / "research-provider.txt"
        choice = local.read_text() if local.is_file() else "codex"
    return choice.strip().lower()


class ResearchDisabled(market.MarketError):
    pass


def check_enabled(root):
    if configured_provider(root) == "off":
        raise ResearchDisabled("AI research is off.")


def disabled(db):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    return configured_provider(Path(path).parent) == "off"


def queue(db, run, day):
    if run != "observations" or disabled(db):
        return
    db.execute(
        """INSERT OR IGNORE INTO ai_work SELECT c.id,'queued',0 FROM calls c
             WHERE c.run=? AND c.day=? AND c.allocation='portfolio'
             AND NOT EXISTS (SELECT 1 FROM research_reports r WHERE r.call_id=c.id)""",
        (run, day),
    )


def audit(db):
    from .journal import metrics

    rows = db.execute("""SELECT c.*, v.decision, r.rating FROM calls c LEFT JOIN ai_verdicts v
       ON v.id=(SELECT min(x.id) FROM ai_verdicts x WHERE x.call_id=c.id AND x.decision IN ('approve','downgrade','veto')
       AND x.ts < (SELECT min(f.day)||'T09:15:00' FROM fills f WHERE f.call_id=c.id AND f.side='buy'))
       LEFT JOIN research_reports r ON r.report_id=v.report_id
       WHERE c.run='observations' AND c.allocation IN ('portfolio','ai_veto') AND c.status='closed'""").fetchall()
    scored = [r for r in rows if r["decision"] in {"approve", "veto", "downgrade"}]
    base = metrics(scored)
    outcomes = {}
    for key in ("decision", "rating"):
        outcomes[key] = {
            v: metrics([r for r in scored if r[key] == v])
            for v in sorted({r[key] for r in scored if r[key]})
        }
    kept = metrics([r for r in scored if r["decision"] != "veto"])
    filter_rating = metrics([r for r in scored if r["rating"] in {"buy", "strong_buy"}])
    sufficient = len(scored) >= 100

    def delta(s):
        return s["expectancy_r"] - base["expectancy_r"] if s["closed"] and scored else None

    return {
        "closed_rules_only": metrics(rows),
        "closed_with_prior_verdict": len(scored),
        "comparable_rules_only": base,
        "veto_filter": kept,
        "veto_mean_r_delta": delta(kept),
        "buy_rating_filter": filter_rating,
        "buy_rating_mean_r_delta": delta(filter_rating),
        "minimum_paired_trades": 100,
        "sample_sufficient": sufficient,
        "may_enforce": bool(
            sufficient
            and delta(kept) is not None
            and delta(kept) > 0
            and any(r["decision"] == "veto" for r in scored)
        ),
        "outcomes": outcomes,
        "monthly_cost_inr": dict(
            db.execute("SELECT substr(ts,1,7),sum(cost_inr) FROM ai_calls GROUP BY 1")
        ),
        "monthly_model_usage": [
            dict(r)
            for r in db.execute(
                """SELECT substr(ts,1,7) AS month, provider, model, count(*) AS requests,
                   sum(tokens_in) AS input_tokens, sum(tokens_out) AS output_tokens,
                   sum(cost_inr) AS metered_api_inr FROM ai_calls WHERE purpose!='research_status'
                   GROUP BY 1,2,3 ORDER BY 1,2,3"""
            )
        ],
    }


def enforce(db, run, day):
    if (
        run != "observations"
        or os.getenv("RADAR_AI_VETO_ENFORCED", "false").lower() != "true"
        or disabled(db)
        or not audit(db)["may_enforce"]
    ):
        return
    from .journal import event

    for row in db.execute(
        """SELECT c.id FROM calls c JOIN ai_verdicts v ON v.id=(SELECT min(x.id) FROM ai_verdicts x WHERE x.call_id=c.id AND x.decision IN ('approve','downgrade','veto'))
        WHERE c.run=? AND c.allocation='portfolio' AND c.status='confirmed' AND v.decision='veto' AND substr(v.ts,1,10)<?""",
        (run, day),
    ):
        db.execute("UPDATE calls SET allocation='ai_veto' WHERE id=?", (row[0],))
        event(
            db,
            row[0],
            day,
            "allocation_changed",
            "Confirmed plan moved to shadow by qualified AI veto.",
        )


def verdict(db, call_id, report_id, decision, confidence, flags, summary, version):
    db.execute(
        "INSERT INTO ai_verdicts(call_id,ts,report_id,decision,confidence,red_flags_json,summary,prompt_version) VALUES(?,?,?,?,?,?,?,?)",
        (call_id, now(), report_id, decision, confidence, json.dumps(flags), summary, version),
    )


def drain(root):
    from .research_worker import start_pending

    start_pending(root)
