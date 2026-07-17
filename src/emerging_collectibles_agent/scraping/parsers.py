"""HTML parsing / structured-data extraction.

Provides a ladder of extraction methods so the adaptive router can pick the
best one per domain: trafilatura article extraction, readability fallback,
JSON-LD / schema.org, Open Graph / meta, tables, and link harvesting.
"""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ..logging_config import get_logger
from ..util import clean_text, normalize_url

log = get_logger("scraping.parsers")


def parse_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def extract_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return clean_text(og["content"], 300)
    if soup.title and soup.title.string:
        return clean_text(soup.title.string, 300)
    h1 = soup.find("h1")
    return clean_text(h1.get_text(), 300) if h1 else ""


def extract_meta_description(soup: BeautifulSoup) -> str:
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(tag["content"], 500)
    return ""


def extract_published_at(soup: BeautifulSoup) -> str:
    selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "publishdate"}),
        ("meta", {"name": "date"}),
        ("meta", {"itemprop": "datePublished"}),
        ("time", {}),
    ]
    for tag, attrs in selectors:
        el = soup.find(tag, attrs=attrs)
        if el:
            val = el.get("content") or el.get("datetime") or el.get_text()
            val = clean_text(val, 40)
            if val and re.search(r"\d{4}", val):
                return val
    return ""


def extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            out.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                out.extend([d for d in data["@graph"] if isinstance(d, dict)])
            else:
                out.append(data)
    return out


def extract_article_text(html: str) -> tuple[str, str]:
    """Return (text, method). Try trafilatura, then readability, then soup."""
    # trafilatura
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False,
                                    include_tables=True, favor_recall=True)
        if text and len(text) > 120:
            return clean_text(text, 20000), "trafilatura"
    except Exception as exc:
        log.debug("trafilatura failed: %s", exc)
    # readability
    try:
        from readability import Document

        doc = Document(html)
        summary_html = doc.summary()
        text = BeautifulSoup(summary_html, "lxml").get_text(" ")
        if text and len(text) > 120:
            return clean_text(text, 20000), "readability"
    except Exception as exc:
        log.debug("readability failed: %s", exc)
    # plain soup
    soup = parse_soup(html)
    for bad in soup(["script", "style", "nav", "footer", "header", "aside"]):
        bad.decompose()
    text = soup.get_text(" ")
    return clean_text(text, 20000), "beautifulsoup"


def extract_tables(soup: BeautifulSoup, limit: int = 20) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for tbl in soup.find_all("table")[:limit]:
        rows = []
        for tr in tbl.find_all("tr"):
            cells = [clean_text(td.get_text(), 120) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def extract_links(soup: BeautifulSoup, base_url: str, limit: int = 400) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        full = normalize_url(href, base_url)
        if full.startswith("http") and full not in seen:
            seen.add(full)
            out.append(full)
        if len(out) >= limit:
            break
    return out


def parse_page(html: str, url: str) -> dict[str, Any]:
    """One-shot parse returning all structured signals for a page."""
    soup = parse_soup(html)
    text, method = extract_article_text(html)
    return {
        "title": extract_title(soup),
        "meta_description": extract_meta_description(soup),
        "published_at": extract_published_at(soup),
        "text": text,
        "extraction_method": f"httpx+{method}",
        "jsonld": extract_jsonld(soup),
        "tables": extract_tables(soup),
        "links": extract_links(soup, url),
    }
