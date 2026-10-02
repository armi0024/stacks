"""HTML parsers for the scrape adapters (SPEC 5), developed against fixtures.

- TAMA (arcade-museum.com): paginated tables of documents with links.
- Open directories (apache/nginx-style indexes): recursive file listings.

Parsers return plain data; politeness, resumability, and download/commit
belong to the adapters (phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin


@dataclass(frozen=True)
class TamaRow:
    title: str
    href: str
    extra: tuple = ()  # remaining cell texts (category, size, ...)


@dataclass(frozen=True)
class DirEntry:
    name: str
    href: str
    is_dir: bool


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, str | None]]] = []  # cells of (text, href)
        self._in_row = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._cell_href: str | None = None
        self._row: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_row, self._row = True, []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell, self._cell_text, self._cell_href = True, [], None
        elif tag == "a" and self._in_cell:
            href = dict(attrs).get("href")
            if href and self._cell_href is None:
                self._cell_href = href

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._row.append((" ".join("".join(self._cell_text).split()), self._cell_href))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)


def parse_tama_table(html: str, base_url: str = "") -> list[TamaRow]:
    """Rows whose first linked cell points at a document."""
    parser = _TableParser()
    parser.feed(html)
    out: list[TamaRow] = []
    for row in parser.rows:
        linked = [(text, href) for text, href in row if href]
        if not linked:
            continue  # header or decoration row
        title, href = linked[0]
        if not title:
            continue
        extras = tuple(text for text, h in row if text and (text, h) != (title, href))
        out.append(TamaRow(title=title, href=urljoin(base_url, href), extra=extras))
    return out


class _IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def parse_open_directory(html: str, base_url: str = "") -> list[DirEntry]:
    """Apache/nginx-style index: files and subdirectories, parent links and
    sort toggles excluded."""
    parser = _IndexParser()
    parser.feed(html)
    out: list[DirEntry] = []
    for href, text in parser.links:
        if not href or href.startswith(("?", "#")) or href.startswith(("http://", "https://")):
            continue
        if href in ("../", "..", "/") or text.lower() in ("parent directory", ".."):
            continue
        is_dir = href.endswith("/")
        name = unquote(href.rstrip("/").rsplit("/", 1)[-1])
        out.append(DirEntry(name=name, href=urljoin(base_url, href), is_dir=is_dir))
    return out
