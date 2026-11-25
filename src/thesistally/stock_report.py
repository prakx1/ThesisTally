"""The common human-readable stock report for the CLI and terminal dashboard."""

import re

from rich import box
from rich.console import Group, RenderableType
from rich.constrain import Constrain
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text


def _text(value, style=""):
    # Research and exchange text is content. Keep it out of Rich markup and
    # remove terminal control characters, preserving line breaks and tabs.
    return Text(re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(value)), style=style)


def _heading(label):
    return Rule(Text(label, style="bold cyan"), align="left", style="dim cyan")


def render_stock_report(payload) -> RenderableType:
    """Render a generated or archived research payload without altering its contents."""
    symbol = payload.get("symbol") or payload.get("target", "")
    call_id = payload.get("call_id")
    title = f"{symbol}  ·  Stock research" if symbol else "Stock research"
    if call_id is not None:
        title += f"  ·  Call #{call_id}"
    header = [_text(title, "bold cyan")]
    if payload.get("as_of"):
        header.append(_text(f"Market close {payload['as_of']}"))

    report = payload.get("report")
    if not report:
        message = (
            "Research is disabled. Enable it with radar research-mode on."
            if payload.get("status") == "disabled"
            else payload.get("reason") or "A research report is unavailable."
        )
        return Group(*header, _text(message))

    rating = str(report.get("rating") or "Unreported").replace("_", " ").title()
    header.append(_text(f"Rating {rating}  ·  Confidence {report.get('confidence', '—')}/5"))
    lines = [Panel(Group(*header), box=box.ROUNDED, border_style="cyan", padding=(0, 1))]

    verdict = payload.get("verdict") or {}
    if verdict:
        lines.extend(
            [_heading("Risk review"), _text(verdict.get("summary") or "No summary recorded.")]
        )
        if verdict.get("decision"):
            decision = verdict["decision"].replace("_", " ").capitalize()
            lines.append(
                _text(
                    f"Shadow decision {decision}  ·  Confidence {verdict.get('confidence', '—')}/5",
                    "dim",
                )
            )

    sections = [
        ("Analyst views", report.get("analyst_summaries", [])),
        ("Bull case", report.get("bull_case")),
        ("Bear case", report.get("bear_case")),
        ("Risk assessment", report.get("risk_view")),
    ]
    for label, content in sections:
        if content:
            lines.extend([Text(), _heading(label)])
            if isinstance(content, list):
                lines.extend(_text(f"• {item}") for item in content)
            else:
                lines.append(_text(content))

    flags = dict.fromkeys([*report.get("red_flags", []), *verdict.get("red_flags", [])])
    lines.extend([Text(), _heading("Risk flags")])
    lines.extend(_text(f"• {flag}") for flag in flags)
    if not flags:
        lines.append(_text("No flags recorded in the report."))

    lines.extend([Text(), _heading("Cited filings")])
    filings = {filing["id"]: filing for filing in payload.get("filings", [])}
    cited = report.get("filing_ids", [])
    if not cited:
        lines.append(_text("No filings cited in this report."))
    for filing_id in dict.fromkeys(cited):
        filing = filings.get(filing_id, {})
        name = (filing.get("type") or "Filing").replace("_", " ").capitalize()
        lines.append(_text(f"#{filing_id}  {name}  {filing.get('date', '')}".rstrip(), "bold"))
        lines.append(_text(filing.get("url") or "Source URL unavailable."))
    return Constrain(Group(*lines), width=100)
