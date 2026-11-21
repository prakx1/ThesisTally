"""Dated monetary policy decisions from the Reserve Bank of India."""

from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .market import sha

INDEX = "https://www.rbi.org.in/scripts/annualpolicy.aspx"


class TextBlocks(HTMLParser):
    def __init__(self, tags):
        super().__init__()
        self.tags = tags
        self.active = None
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if self.active is None and tag in self.tags:
            self.active = tag
            self.href = dict(attrs).get("href") or ""
            self.words = []

    def handle_data(self, data):
        if self.active:
            self.words.append(data)

    def handle_endtag(self, tag):
        if tag == self.active:
            self.blocks.append((tag, self.href, " ".join("".join(self.words).split())))
            self.active = None


def last_statement(client, day):
    index = client.get(INDEX)
    index.raise_for_status()
    listing = TextBlocks({"a", "b"})
    listing.feed(index.text)
    published = None
    candidates = []
    for tag, href, title in listing.blocks:
        if tag == "b":
            try:
                published = datetime.strptime(title, "%b %d, %Y").date()
            except ValueError:
                pass  # Section headings appear between the publication date and the link.
        elif published and 0 < (day - published).days <= 90:
            name = title.lower().replace("’", "'")
            url = urljoin(INDEX, href)
            if (
                name.startswith(("governor's statement", "resolution of the monetary policy"))
                and urlsplit(url).scheme == "https"
                and urlsplit(url).hostname in {"rbi.org.in", "www.rbi.org.in"}
            ):
                candidates.append((published, name.startswith("governor"), title, url))
    if not candidates:
        return []
    published, _, title, url = max(candidates)
    release = client.get(url)
    release.raise_for_status()
    paragraphs = TextBlocks({"p"})
    paragraphs.feed(release.text)
    for _, _, paragraph in paragraphs.blocks:
        if "repo rate" in paragraph.lower() and "per cent" in paragraph.lower():
            return [
                {
                    "title": title,
                    "published": published.isoformat(),
                    "url": url,
                    "description": paragraph,
                    "sha256": sha(release.content),
                    "index_url": INDEX,
                    "index_sha256": sha(index.content),
                }
            ]
    return []
