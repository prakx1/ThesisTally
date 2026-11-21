import json
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from thesistally import codex_research, market, research, research_journal, research_worker
from thesistally.journal import journal


def test_codex_uses_plan_login_and_validates_and_accounts_for_output(tmp_path, monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "do-not-inherit")
    monkeypatch.setattr(codex_research.shutil, "which", lambda _: "/fixture/codex")
    responses = iter([0, 1])

    def cli(argv, *, env, cwd, **kwargs):
        assert "OPENAI_API_KEY" not in env and "KITE_ACCESS_TOKEN" not in env
        if "login" in argv:
            return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")
        assert argv[-1] == "-" and "--ephemeral" in argv and "read-only" in argv
        assert json.loads(kwargs["input"])["evidence"] == {"symbol": "COFORGE"}
        schema = json.loads((Path(cwd) / "schema.json").read_text())
        assert schema["additionalProperties"] is False
        rc = next(responses)
        if not rc:
            (Path(cwd) / "answer.json").write_text('{"text":"as-of evidence"}')
        event = {"type": "turn.completed", "usage": {"input_tokens": 17, "output_tokens": 5}}
        return subprocess.CompletedProcess(argv, rc, json.dumps(event) if not rc else "", "")

    monkeypatch.setattr(codex_research.subprocess, "run", cli)
    backend = codex_research.CodexResearch(tmp_path, None)
    payload = {"symbol": "COFORGE"}
    assert (
        backend.structured("test", "review", payload, codex_research.Analysis).text
        == "as-of evidence"
    )
    with pytest.raises(market.MarketError, match="did not complete"):
        backend.structured("test", "review", payload, codex_research.Analysis)
    with journal(tmp_path / "journal.sqlite") as db:
        assert [
            tuple(r)
            for r in db.execute("SELECT provider,tokens_in,cost_inr FROM ai_calls ORDER BY id")
        ] == [("codex-cli", 17, 0), ("codex-cli", None, 0)]
        assert research_journal.audit(db)["monthly_model_usage"][0]["input_tokens"] == 17


def test_codex_runs_inside_upstream_graph_and_reuses_dated_extractions(tmp_path, monkeypatch):
    pytest.importorskip("tradingagents")
    from tradingagents.agents.schemas import PortfolioDecision, ResearchPlan, TraderProposal

    evidence_seen = []

    class Fixture(codex_research.CodexResearch):
        model = "fixture"

        def __init__(self):
            pass

        def structured(self, purpose, instruction, payload, schema):
            if schema == research.BatchExtract:
                evidence_seen.append("extraction")
                return schema(
                    sections=[
                        {
                            "section": 0,
                            "facts": [
                                {
                                    "metric": "guidance",
                                    "value": "steady",
                                    "evidence": "Stable guidance.",
                                },
                                {
                                    "metric": "guidance",
                                    "value": "doubled",
                                    "evidence": "invented quote",
                                },
                            ],
                        }
                    ]
                )
            if schema == codex_research.Analysis:
                if "tool_results" in payload:
                    evidence_seen.append(payload["tool_results"])
                return schema(text="fixed research evidence")
            if schema == ResearchPlan:
                return schema(recommendation="Hold", rationale="review", strategic_actions="wait")
            if schema == TraderProposal:
                return schema(action="Hold", reasoning="review")
            if schema == PortfolioDecision:
                return schema(
                    rating="Hold", executive_summary="hold", investment_thesis="unchanged"
                )
            return schema(
                rating="hold",
                confidence=3,
                analyst_summaries=["dated"],
                bull_case="B",
                bear_case="C",
                risk_view="risk",
                red_flags=[],
                filing_ids=[1],
            )

    with journal(tmp_path / "journal.sqlite") as db:
        with db:
            db.execute(
                "INSERT INTO filings(symbol,exchange,type,filed_at,source_url,sha256,path) VALUES('COFORGE','NSE','results','2026-11-16','https://www.nseindia.com/filing','hash','file')"
            )
        records = [dict(r) for r in db.execute("SELECT * FROM filings")]
        monkeypatch.setattr(
            research.filings,
            "sections",
            lambda *_: [("page 1", "Stable guidance."), ("page 2", "Miscellaneous.")],
        )
        backend = Fixture()
        files = research.extracts(tmp_path, db, records, backend)
        assert files == research.extracts(tmp_path, db, records, backend)
        assert len(files[0]["facts"]) == 1 and files[0]["facts"][0]["locator"] == "page 1"
    ctx = {
        "prices": {"NIFTY 200": [{"close": 123}]},
        "plan": {"entry": 200},
        "macro": {},
        "sectors": {},
        "filings": files,
    }
    report = research.ResearchAgent(backend).run("COFORGE", date(2026, 11, 20), ctx)
    assert report.filing_ids == [1] and evidence_seen.count("extraction") == 1
    assert any(
        isinstance(row, dict) and row.get("get_stock_data") == ctx["prices"]
        for row in evidence_seen
    )
    assert any(
        isinstance(row, dict) and row.get("get_fundamentals") == files for row in evidence_seen
    )


def test_scheduler_uses_local_provider_and_environment_override(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_AI_PROVIDER", raising=False)
    assert research.configured_provider(tmp_path) == "codex"
    from test_research import CFG, DAY, REGIME, calendar, pick

    from thesistally.journal import process

    with journal(tmp_path / "journal.sqlite") as db:
        process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    dispatched = []

    def complete(root, call_id):
        dispatched.append((research.configured_provider(root), call_id))
        with journal(root / "journal.sqlite") as db, db:
            db.execute("UPDATE ai_work SET status='done' WHERE call_id=?", (call_id,))

    monkeypatch.setattr(research, "run_report", complete)
    research_worker.drain_pending(tmp_path)
    research_worker.drain_pending(tmp_path)
    assert dispatched == [("codex", 1)]
    monkeypatch.setenv("RADAR_AI_PROVIDER", "openai")
    assert research.configured_provider(tmp_path) == "openai"


def test_research_mode_off_pauses_work_and_prevents_new_requests(tmp_path, monkeypatch):
    from test_research import CFG, DAY, REGIME, calendar, pick
    from typer.testing import CliRunner

    from thesistally import cli
    from thesistally.journal import process

    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    monkeypatch.delenv("RADAR_AI_PROVIDER", raising=False)
    with journal(tmp_path / "journal.sqlite") as db:
        process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    runner = CliRunner()
    assert "codex" in runner.invoke(cli.app, ["research-mode"]).stdout
    assert "off" in runner.invoke(cli.app, ["research-mode", "off"]).stdout
    assert "disabled" in runner.invoke(cli.app, ["research", "1", "--json"]).stdout
    assert research_worker.start_pending(tmp_path) is None
    research_worker.drain_pending(tmp_path)
    with pytest.raises(research_journal.ResearchDisabled):
        research_journal.check_enabled(tmp_path)
    with journal(tmp_path / "journal.sqlite") as db:
        assert db.execute("SELECT status FROM ai_work").fetchone()[0] == "queued"
        assert db.execute("SELECT count(*) FROM ai_calls").fetchone()[0] == 0
        db.execute("DELETE FROM ai_work")
        research_journal.queue(db, "observations", str(DAY))
        assert db.execute("SELECT count(*) FROM ai_work").fetchone()[0] == 0
    assert "codex" in runner.invoke(cli.app, ["research-mode", "on"]).stdout
    monkeypatch.setenv("RADAR_AI_PROVIDER", "off")
    assert research.configured_provider(tmp_path) == "off"


def test_automatic_scan_starts_only_one_review_across_worker_processes(tmp_path, monkeypatch):
    import os

    from test_research import DAY, REGIME, calendar, pick

    from thesistally import cli

    monkeypatch.delenv("RADAR_AI_PROVIDER", raising=False)
    # A failed login exercises the real subprocess, terminal verdict and recovery paths offline.
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\necho 'Signed out'\nexit 1\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(cli, "load_session", lambda *args: (REGIME, [pick()], calendar(DAY)))
    launched = []
    spawn = research_worker.start_pending

    def launch(*args, **kwargs):
        p = spawn(*args, **kwargs)
        if p:
            launched.append(p)

    monkeypatch.setattr(research_worker, "start_pending", launch)
    assert cli.scan_session(tmp_path, DAY, "observations", None)["detected"] == 1
    assert launched
    research_journal.drain(tmp_path)
    for proc in launched:
        assert proc.wait(timeout=30) == 0
    with journal(tmp_path / "journal.sqlite") as db:
        assert (
            db.execute(
                "SELECT count(*) FROM ai_verdicts WHERE decision='ai_unavailable'"
            ).fetchone()[0]
            == 1
        )
        assert db.execute("SELECT status FROM ai_work").fetchone()[0] == "unavailable"


def test_wrapped_filing_quotes_must_each_match_the_same_source_section():
    filing = {"filing_id": 74, "source_url": "https://www.nseindia.com/fixture"}
    source = "First observation. Another observation."
    fact = research.Fact(
        metric="guidance",
        value="steady",
        evidence="Filing 74 — “First observation.”; “Another observation.”",
    )
    (accepted,) = research.checked_facts([fact], source, filing, "page 2")
    assert accepted["evidence_spans"] == ["First observation.", "Another observation."]
    assert not research.checked_facts([fact], "First observation.", filing, "page 1")
