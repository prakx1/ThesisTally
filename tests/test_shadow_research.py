from datetime import date

import httpx
import pytest
from test_research import CFG, DAY, REGIME, calendar, pick

from thesistally import market, research, research_journal
from thesistally.journal import journal, process


def test_rbi_dated_fallback_selects_prior_statement_and_preserves_source():
    from thesistally import rbi

    def fixture(request):
        if str(request.url) == rbi.INDEX:
            body = (
                '<b>Nov 20, 2026</b><a href="/today">Governor’s Statement: current</a>'
                "<b>Oct 07, 2026</b><b>Policy</b>"
                '<a href="/published">Governor’s Statement: archive</a>'
            )
        else:
            assert request.url.path == "/published"
            body = "<p>The MPC retained the policy <b>repo rate</b> at 5 per cent.</p>"
        return httpx.Response(200, text=body)

    with httpx.Client(transport=httpx.MockTransport(fixture)) as client:
        (observation,) = rbi.last_statement(client, date(2026, 11, 20))
    assert observation["published"] == "2026-10-07"
    assert "5 per cent" in observation["description"] and len(observation["sha256"]) == 64


def test_per_request_budget_and_audit_before_fill(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    monkeypatch.setenv("RADAR_AI_DAILY_BUDGET_INR", "2")
    db = journal(tmp_path / "journal.sqlite")
    process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    b = research.Budget(tmp_path, 1)
    assert b.measured("fixture", "small", lambda: ("ok", 100, 25, {"test": True})) == "ok"
    assert db.execute("select tokens_in from ai_calls").fetchone()[0] == 100
    with pytest.raises(market.MarketError, match="budget"):
        b.measured("blocked", "X" * 90000, lambda: pytest.fail("unfunded provider request"))
    with db:
        db.execute("UPDATE calls SET status='closed',net=100,outcome_r=1 WHERE id=1")
        db.execute(
            "INSERT INTO fills(call_id,day,side,qty,price,fees) VALUES(1,'2026-10-02','buy',1,99,0)"
        )
        for day, verdict in (
            ("2026-10-01T20:30:00+05:30", "approve"),
            ("2026-10-03T15:00:00+05:30", "veto"),
        ):
            db.execute(
                "INSERT INTO ai_verdicts(call_id,ts,decision,confidence,red_flags_json,summary,prompt_version) VALUES(1,?,?,4,'[]','','test')",
                (day, verdict),
            )
    a = research_journal.audit(db)
    assert a["closed_with_prior_verdict"] == 1 and not a["may_enforce"]
    assert list(a["outcomes"]["decision"]) == ["approve"]
    with db:
        db.execute("DELETE FROM ai_verdicts WHERE decision='approve'")
    assert research_journal.audit(db)["closed_with_prior_verdict"] == 0


def test_upstream_graph_uses_indian_tools(tmp_path, monkeypatch):
    pytest.importorskip("tradingagents")
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    used = []

    def generate(self, messages, **kwargs):
        tools = kwargs.get("tools") or []
        calls = []
        if tools and not any(isinstance(m, ToolMessage) for m in messages):
            tool = tools[0]["function"]
            name = tool["name"]
            args = {
                k: ("COFORGE" if k in {"symbol", "ticker", "company_name"} else "2026-11-20")
                for k in tool["parameters"].get("required", [])
            }
            calls = [{"name": name, "args": args, "id": "local"}]
        for m in messages:
            if isinstance(m, ToolMessage):
                used.append(m.content)
        msg = AIMessage(
            content="Only dated Indian observations are available.",
            tool_calls=calls,
            usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)

    class Offline:
        def measured(self, purpose, prompt, invoke):
            return invoke()[0]

        def structured(self, purpose, instruction, payload, schema):
            return schema(
                rating="hold",
                confidence=2,
                analyst_summaries=["Dated local inputs"],
                bull_case="Growth",
                bear_case="Risk",
                risk_view="Wait",
                red_flags=[],
                filing_ids=[7],
            )

    ctx = {
        "prices": {"NIFTY 200": [{"close": 123}]},
        "plan": {"atr": 20},
        "macro": {},
        "sectors": [],
        "filings": [{"id": 7, "url": "https://www.nseindia.com/fixture"}],
    }
    answer = research.ResearchAgent(Offline()).run("COFORGE", date(2026, 11, 20), ctx)
    assert (
        answer.rating == "hold"
        and any("NIFTY 200" in s for s in used)
        and any("nseindia" in s for s in used)
    )
