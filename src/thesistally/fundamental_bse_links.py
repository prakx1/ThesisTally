"""Archive public BSE attachment links that appear in an original official exchange filing."""

import argparse
import gzip
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .discovery_registry import digest, encoded, write_once
from .fundamental_bse_recheck import checked_gzip, references
from .fundamental_nse import ROOT

TOPICS = {
    "statement_of_quarterly_results": r"(?:statement of.{0,55}(?:financial results|financial statements)|financial results.{0,60}(?:quarter|three months))",
    "earnings_release_or_presentation": r"earnings (?:release|presentation)|investor presentation|earnings presentation",
    "board_or_audit_report": r"board (?:meeting|of directors)|auditor['’]?s report|limited review",
    "annual_general_meeting_or_voting": r"annual general meeting|postal ballot|voting results|scrutini[sz]er|e[- ]voting",
    "annual_report": r"\bannual report\b",
    "other_corporate_action": r"shareholding pattern|scheme of arrangement|preferential allotment|change in director|acquisition of|credit rating",
}
CLOCK_CUE = re.compile(
    r"\b(?:disseminat(?:ed|ion) (?:on|date|time|at)|(?:exchange|bse) (?:upload|publish|disseminat)\w* (?:date|time|at)|acknowledg(?:e)?ment (?:number|no\.|date)|bse listing cent(?:er|re) acknowledgment)\b",
    re.IGNORECASE,
)


def public(url):
    p = urlparse(url)
    return (
        p.scheme in {"http", "https"}
        and not p.username
        and not p.password
        and any(
            (p.hostname or "") == d or (p.hostname or "").endswith("." + d)
            for d in ("bseindia.com", "bseindia.in")
        )
    )


def classify(raw):
    if not raw.startswith(b"%PDF"):
        return {
            "content": "non-PDF",
            "search_scope": "No legible statement inspected",
            "topic_cues": [],
            "official_clock_cues": [],
        }
    p = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, check=False, timeout=90
    )
    pages = p.stdout.decode(errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    topics = []
    clocks = []
    for i, txt in enumerate(pages, 1):
        flat = " ".join(txt.split())
        if i <= 4:
            topics.extend(
                {"page": i, "topic": k}
                for k, pattern in TOPICS.items()
                if re.search(pattern, flat, re.IGNORECASE)
            )
        for line in txt.splitlines():
            if CLOCK_CUE.search(line):
                clocks.append({"page": i, "exact_line": line.strip()[:360]})
    return {
        "content": "PDF",
        "pages": len(pages),
        "return_code": p.returncode,
        "readable_pages": sum(len(s.strip()) >= 50 for s in pages),
        "layout_sha256": digest(p.stdout),
        "topic_cues": topics,
        "official_clock_cues": clocks,
        "search_scope": "topic candidates in the first four native-text pages; explicit exchange clock or acknowledgement cues on every native-text page",
        "outcome": "official clock cue requires original evidence review"
        if clocks
        else "BSE public dissemination time unverified",
    }


def terminal(entry):
    status = entry.get("status", 0)
    return "error" not in entry and (
        status == 200 or 400 <= status < 500 and status not in {408, 425, 429}
    )


def document_purpose(raw):
    if not raw.startswith(b"%PDF"):
        return "no verified PDF attachment"
    p = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, timeout=90, check=False
    )
    if p.returncode:
        return "PDF text unavailable"
    pages = [
        " ".join(s.split()) for s in p.stdout.decode(errors="replace").split("\f") if s.strip()
    ]
    if not pages:
        return "PDF text unavailable"
    table = any(
        re.search(r"\b(?:three months|quarter)\b", s, re.IGNORECASE)
        and re.search(r"revenue from operations|total income", s, re.IGNORECASE)
        and re.search(r"earnings? per (?:equity )?share|basic (?:EPS|earnings)", s, re.IGNORECASE)
        and len(re.findall(r"(?<!\w)\(?-?\d[\d,]*\.\d{2}\)?(?!\w)", s)) >= 8
        for s in pages
    )
    if table:
        return "financial statement present; first-publication clock requires separate exchange evidence"
    front = " ".join(pages[:2])
    first = pages[0]
    if re.search(
        r"(?:will be held|is scheduled to be held|has been scheduled|scheduled a meeting).{0,350}(?:consider|approve)",
        front,
        re.IGNORECASE,
    ) or re.search(r"intimation of (?:a |the )?meeting of (?:the )?board", first, re.IGNORECASE):
        return "advance notice of a board meeting"
    if re.search(
        r"(?:share subscription and share purchase agreement|acquisition of equity shares of)\b",
        first,
        re.IGNORECASE,
    ):
        return "equity acquisition or financing notice"
    if re.search(
        r"(?:annual general meeting|postal ballot|voting results|scrutini[sz]er.{0,12}report)",
        first,
        re.IGNORECASE,
    ):
        return "shareholder meeting or voting document"
    if re.search(r"Sub(?:ject)?\s*[.:]\s*press release", first, re.IGNORECASE) and not re.search(
        r"financial results|quarterly results|earnings release", first, re.IGNORECASE
    ):
        return "issuer operational press release"
    if re.search(
        r"\b(?:appointment|resignation) of (?:the |a |an )?(?:managing director|chief executive officer|chief financial officer|director|company secretary)",
        first,
        re.IGNORECASE,
    ):
        return "executive appointment or resignation notice"
    return "document purpose unresolved"


def capture(root, url, client):
    bse = root.parent / "bse"
    key = digest(url.encode())
    done = bse / "linked-sources" / (key + ".json")
    if not public(url) or not references(url)[0]:
        raise ValueError("Expected an official BSE attachment URL")
    if done.exists():
        return json.loads(done.read_bytes())
    for prior in sorted((bse / "linked-source-attempts" / key).glob("*.json")):
        attempt = json.loads(prior.read_bytes())
        if attempt["url"] == url and terminal(attempt):
            checked_gzip(bse / "source-objects" / (attempt["sha256"] + ".gz"), attempt["sha256"])
            write_once(done, encoded(attempt))
            return attempt
    stamp = datetime.now(UTC).isoformat()
    cycle = []
    entry = {"url": url, "attempted_at_utc": stamp}
    try:
        address = url
        for _ in range(5):
            response = client.get(address, follow_redirects=False)
            cycle.append({"url": str(response.url), "status": response.status_code})
            if not response.is_redirect:
                break
            address = urljoin(str(response.url), response.headers["location"])
            if not public(address):
                raise ValueError("BSE attachment redirects to an external host")
        else:
            raise ValueError("BSE attachment exceeded the redirect limit")
        raw = response.content
        sha = digest(raw)
        entry.update(
            requests=cycle,
            status=response.status_code,
            sha256=sha,
            bytes=len(raw),
            server_response_date=response.headers.get("date"),
            resolved_url=str(response.url),
        )
        object_path = bse / "source-objects" / (sha + ".gz")
        if object_path.exists():
            checked_gzip(object_path, sha)
        else:
            write_once(object_path, gzip.compress(raw, compresslevel=5, mtime=0))
        if response.status_code == 200:
            entry.update(classify(raw))
        else:
            entry.update(outcome="BSE attachment unavailable", official_clock_cues=[])
    except (httpx.HTTPError, subprocess.TimeoutExpired, ValueError) as exc:
        entry.update(
            outcome="retrieval or source parsing incomplete",
            error=type(exc).__name__ + ": " + str(exc)[:180],
            requests=cycle,
        )
    archive = bse / "linked-source-attempts" / key / (digest(encoded(entry)) + ".json")
    write_once(archive, encoded(entry))
    if terminal(entry):
        write_once(done, encoded(entry))
    return entry


def observed_urls(root):
    found = set()
    for p in (root.parent / "bse/annotation-cache").glob("*/*/*.json"):
        q = json.loads(p.read_bytes())
        found.update(h["url"] for h in q.get("direct", []))
    return sorted(found)


def summary(data, urls, clocks):
    bse = data / "bse"
    states = {}
    missing = []
    pending = []
    proofs = {}
    for url in sorted(urls):
        key = digest(url.encode())
        p = bse / "linked-sources" / (key + ".json")
        if not p.exists():
            missing.append(url)
            continue
        raw = p.read_bytes()
        r = json.loads(raw)
        if r["url"] != url:
            raise ValueError("BSE archive response is attributed to a different URL")
        payload = checked_gzip(bse / "source-objects" / (r["sha256"] + ".gz"), r["sha256"])
        purpose = document_purpose(payload) if r["status"] == 200 else "attachment unavailable"
        if r.get("official_clock_cues"):
            decision = bse / "linked-clock-reviews" / (key + ".json")
            if not decision.exists():
                pending.append(url)
                continue
            notes = decision.read_bytes()
            d = json.loads(notes)
            proofs[url] = digest(notes)
            if (
                d["url"] != url
                or d["source_sha256"] != r["sha256"]
                or d.get("verified_public_dissemination_at") != clocks.get(url)
            ):
                raise ValueError("Reviewed BSE clock and canonical source ledger differ")
        same_nse = data / "nse/notice-objects" / r["sha256"][:2] / (r["sha256"] + ".gz")
        nse_match = same_nse.is_file()
        if nse_match:
            checked_gzip(same_nse, r["sha256"])
        states[url] = {
            "receipt_sha256": digest(raw),
            "receipt_file": str(p.relative_to(data)),
            "status": r["status"],
            "attachment_sha256": r["sha256"],
            "content": r.get("content"),
            "document_purpose": purpose,
            "matches_an_archived_nse_packet": nse_match,
            "topic_cues": r.get("topic_cues", []),
            "verified_bse_disseminated_at": clocks.get(url),
            "disposition": "verified BSE dissemination"
            if url in clocks
            else "no verified BSE dissemination",
        }
    if pending or missing:
        queue = {"missing_official_sources": missing, "require_dissemination_review": pending}
        stamp = digest(encoded(queue))
        write_once(bse / "pending" / (stamp + ".json"), encoded(queue))
        raise ValueError(
            f"New direct BSE sources require retrieval or original clock review: data/fundamentals/bse/pending/{stamp}.json"
        )
    return {
        "per_link": states,
        "clock_review_sha256": proofs,
        "pdftotext_sha256": digest(Path(shutil.which("pdftotext")).read_bytes()),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pending", type=Path)
    args = p.parse_args()
    urls = set(observed_urls(ROOT))
    if args.pending:
        urls.update(json.loads(args.pending.read_bytes())["missing_official_sources"])
    with httpx.Client(timeout=20) as client:
        for url in sorted(urls):
            r = capture(ROOT, url, client)
            print(r["url"], r.get("status") or r["outcome"], flush=True)


if __name__ == "__main__":
    main()
