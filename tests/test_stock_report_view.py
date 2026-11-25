import csv
import io
import json

from rich.console import Console
from rich.pager import SystemPager
from typer.testing import CliRunner

from thesistally import cli, research
from thesistally.stock_report import render_stock_report


def sample():
    return {
        "symbol": "SBIN",
        "as_of": "2026-11-20",
        "call_id": None,
        "report": {
            "rating": "hold",
            "confidence": 3,
            "analyst_summaries": ["Loan growth improved [bold]year on year[/bold]."],
            "bull_case": "Deposits grew.",
            "bear_case": "Credit costs rose.",
            "risk_view": "Watch the next quarter.",
            "red_flags": ["Margin pressure"],
            "filing_ids": [7],
        },
        "verdict": {
            "decision": "downgrade",
            "confidence": 2,
            "summary": "Funding costs rose.",
            "red_flags": ["Margin pressure", "Concentration"],
        },
        "filings": [
            {
                "id": 7,
                "type": "financial_results",
                "date": "2026-11-06",
                "url": "https://example.org/7",
            },
            {"id": 9, "type": "announcement", "url": "https://example.org/9"},
        ],
    }


def test_report_wraps_plain_text_and_preserves_citations_at_narrow_width():
    doc = sample()
    doc["report"]["risk_view"] = "Review \x1b[2J and [reverse]costs[/reverse]."
    buf = io.StringIO()
    Console(file=buf, width=34, color_system=None).print(render_stock_report(doc))
    text = buf.getvalue()
    compact = " ".join(text.split())
    assert "SBIN" in text and "2026-11-20" in text
    assert "Rating Hold" in compact and "Confidence 3/5" in compact
    assert all(
        heading in text for heading in ("Analyst views", "Bull case", "Bear case", "Cited filings")
    )
    assert "Shadow decision Downgrade" in compact
    assert "[bold]year on year[/bold]" in compact and "\x1b" not in text
    assert text.count("Margin pressure") == 1 and "Concentration" in text
    assert "https://example.org/7" in text
    assert "https://example.org/9" not in text
    assert max(map(len, text.splitlines())) <= 34


def test_default_and_machine_formats_run_the_requested_analysis(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    doc = sample()
    runs = []

    def fresh(root, symbol, as_of):
        runs.append((root, symbol, as_of))
        return doc

    monkeypatch.setattr(research, "run_report", fresh)
    result = runner.invoke(cli.app, ["research", "SBIN"])
    assert result.exit_code == 0, result.output
    assert "Risk review" in result.output and "Credit costs rose." in result.output
    assert "{" not in result.output
    raw = runner.invoke(cli.app, ["research", "SBIN", "--json"])
    assert raw.exit_code == 0 and json.loads(raw.output) == doc
    csv_result = runner.invoke(cli.app, ["research", "SBIN", "--csv"])
    assert csv_result.exit_code == 0
    assert (
        json.loads(next(csv.DictReader(io.StringIO(csv_result.output)))["report"]) == doc["report"]
    )
    bad_flags = runner.invoke(cli.app, ["research", "SBIN", "--json", "--csv"])
    assert bad_flags.exit_code != 0
    assert runs == [(tmp_path, "SBIN", None)] * 3


def test_saved_cli_reads_report_with_research_disabled_and_no_network(tmp_path, monkeypatch):
    folder = tmp_path / "research" / "SBIN"
    folder.mkdir(parents=True)
    (folder / "2026-11-21T12-00-00+05-30-demo.json").write_text(json.dumps(sample()))
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    monkeypatch.setenv("RADAR_AI_PROVIDER", "off")

    def unexpected_run(*args):
        raise AssertionError("Reading a report must not run fresh analysis")

    monkeypatch.setattr(research, "run_report", unexpected_run)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["research", "sbin", "--saved", "--date", "2026-11-20"])
    assert result.exit_code == 0 and "Funding costs rose." in result.output
    missed = runner.invoke(cli.app, ["research", "SBIN", "--saved", "--date", "2026-11-19"])
    assert missed.exit_code != 0 and "No saved report for SBIN on 2026-11-19" in missed.output
    numeric = runner.invoke(cli.app, ["research", "42", "--saved"])
    assert numeric.exit_code != 0 and "Use an NSE stock symbol" in numeric.output


def test_research_status_is_readable():
    output = io.StringIO()
    console = Console(file=output, width=100, color_system=None)
    console.print(render_stock_report({"status": "disabled", "target": "SBIN"}))
    assert "Research is disabled" in output.getvalue()
    console.print(
        render_stock_report(
            {"status": "ai_unavailable", "symbol": "SBIN", "reason": "Codex is offline."}
        )
    )
    assert "Codex is offline." in output.getvalue()


def test_interactive_cli_opens_report_in_the_terminal_pager(tmp_path, monkeypatch):
    folder = tmp_path / "research" / "SBIN"
    folder.mkdir(parents=True)
    (folder / "2026-11-21T12-00-00+05-30-demo.json").write_text(json.dumps(sample()))
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    monkeypatch.setattr(cli, "out", Console(force_terminal=True, width=80))
    pages = []
    monkeypatch.setattr(SystemPager, "show", lambda self, value: pages.append(value))
    result = CliRunner().invoke(cli.app, ["research", "SBIN", "--saved"])
    assert result.exit_code == 0, result.output
    assert len(pages) == 1 and "SBIN" in pages[0] and "Cited filings" in pages[0]
