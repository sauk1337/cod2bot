"""Feed / API / HTML parsers. Each returns a list of NewsItem."""
from __future__ import annotations

import email.utils
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "rss1": "http://purl.org/rss/1.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}


@dataclass
class NewsItem:
    guid: str
    url: str
    title: str
    description: str = ""
    image: str | None = None
    published: str | None = None  # UTC "YYYY-MM-DD HH:MM:SS"
    tags: list = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return hashlib.sha1(f"{self.title}\n{self.description}".encode("utf-8")).hexdigest()


class ParseError(Exception):
    pass


_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def to_utc(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(:\d{2})?)", value)
            if not m:
                return None
            dt = datetime.fromisoformat(f"{m.group(1)} {m.group(2)}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _text(el, path: str) -> str:
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def detect_feed(body: bytes) -> str | None:
    head = body[:2000].lower()
    if b"<rss" in head or b"<rdf:rdf" in head:
        return "rss"
    if b"<feed" in head and b"http://www.w3.org/2005/atom" in head:
        return "atom"
    return None


def parse_feed(body: bytes, base_url: str = "") -> list[NewsItem]:
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom."""
    if b"<!ENTITY" in body[:5000]:
        raise ParseError("feeds with DTD entities are refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ParseError(f"invalid XML: {e}") from e
    items: list[NewsItem] = []
    tag = root.tag.lower()
    if tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate" and l.get("href"):
                    link = l.get("href")
                    break
            link = urljoin(base_url, link)
            title = strip_html(_text(e, "atom:title"))
            summary = _text(e, "atom:summary") or _text(e, "atom:content")
            image = None
            th = e.find("media:thumbnail", NS)
            if th is not None:
                image = th.get("url")
            if not image:
                m = _IMG_RE.search(summary or "")
                image = m.group(1) if m else None
            guid = _text(e, "atom:id") or link
            published = to_utc(_text(e, "atom:published") or _text(e, "atom:updated"))
            tags = [c.get("term", "") for c in e.findall("atom:category", NS) if c.get("term")]
            if title and link:
                items.append(NewsItem(guid, link, title, strip_html(summary)[:600], image, published, tags))
        return items
    channel_items = root.findall("./channel/item") or root.findall("rss1:item", NS)
    for e in channel_items:
        title = strip_html(e.findtext("title") or _text(e, "rss1:title"))
        link = (e.findtext("link") or _text(e, "rss1:link") or "").strip()
        link = urljoin(base_url, link)
        desc_raw = e.findtext("description") or _text(e, "rss1:description") or _text(e, "content:encoded")
        image = None
        for path in ("media:thumbnail", "media:content"):
            m_el = e.find(path, NS)
            if m_el is not None and m_el.get("url"):
                image = m_el.get("url")
                break
        enc = e.find("enclosure")
        if not image and enc is not None and (enc.get("type") or "").startswith("image"):
            image = enc.get("url")
        if not image:
            m = _IMG_RE.search(desc_raw or "")
            image = m.group(1) if m else None
        guid = (e.findtext("guid") or "").strip() or link
        published = to_utc(e.findtext("pubDate") or _text(e, "dc:date"))
        tags = [(c.text or "").strip() for c in e.findall("category") if c.text]
        if title and link:
            items.append(NewsItem(guid, link, title, strip_html(desc_raw)[:600], image, published, tags))
    return items


def parse_fpschallenge(data: dict, link_template: str, categories: list[str],
                       provider_tz=timezone.utc) -> list[NewsItem]:
    """FPSChallenge /api/v1/news JSON: {items:[{title, slug, foreword, image,
    categoryName, updatedAt}], currentPage, lastPage, total}."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ParseError("unexpected FPSChallenge API format (no 'items')")
    wanted = {c.lower() for c in categories}
    out = []
    for it in data["items"]:
        if not isinstance(it, dict) or not it.get("slug") or not it.get("title"):
            continue
        cat = str(it.get("categoryName") or "")
        if wanted and cat.lower() not in wanted:
            continue
        published = None
        if it.get("updatedAt"):
            try:
                dt = datetime.fromisoformat(str(it["updatedAt"])).replace(tzinfo=provider_tz)
                published = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                published = None
        out.append(NewsItem(
            guid=str(it["slug"]),
            url=link_template.format(slug=it["slug"]),
            title=strip_html(str(it["title"])),
            description=strip_html(str(it.get("foreword") or ""))[:600],
            image=it.get("image") or None,
            published=published,
            tags=[cat] if cat else [],
        ))
    return out


def parse_github_releases(data) -> list[NewsItem]:
    """GitHub REST API /repos/{owner}/{repo}/releases (drafts skipped)."""
    if not isinstance(data, list):
        raise ParseError("unexpected GitHub API format (expected a list)")
    out = []
    for r in data:
        if not isinstance(r, dict) or r.get("draft") or not r.get("html_url"):
            continue
        title = r.get("name") or r.get("tag_name") or "release"
        if r.get("prerelease"):
            title += " (pre-release)"
        body = re.sub(r"[#*`>_\[\]]", " ", r.get("body") or "")
        out.append(NewsItem(str(r.get("id") or r["html_url"]), r["html_url"], strip_html(title),
                            strip_html(body)[:600], None, to_utc(r.get("published_at") or r.get("created_at")),
                            ["release"]))
    return out


def parse_html(body: str, pattern: str, base_url: str) -> list[NewsItem]:
    """Generic HTML scraping with a configured regex (named groups url, title,
    optional date, image, desc). Kept deliberately simple and explicit."""
    try:
        rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error as e:
        raise ParseError(f"invalid item_pattern: {e}") from e
    if "url" not in rx.groupindex or "title" not in rx.groupindex:
        raise ParseError("item_pattern needs (?P<url>...) and (?P<title>...)")
    out, seen = [], set()
    for m in rx.finditer(body):
        link = urljoin(base_url, html.unescape(m.group("url").strip()))
        if link in seen:
            continue
        seen.add(link)
        g = m.groupdict()
        title = strip_html(g.get("title"))
        if not title:
            continue
        out.append(NewsItem(link, link, title, strip_html(g.get("desc"))[:600],
                            urljoin(base_url, g["image"]) if g.get("image") else None,
                            to_utc(g.get("date")), []))
    return out
