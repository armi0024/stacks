"""Keyword search over indexed page text (phase 1a: FTS5 only; hybrid
vector+FTS arrives in phase 6 behind the same filter).

EVERY hit passes the central auth filter at fetch time; the index is never
the boundary. Hits carry durable citations (document, revision, copy,
page locator) and honest badges (provisional, adequacy, risk flags,
superseded)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from stacks.auth.filter import DocumentAccess, document_visible, require_verb
from stacks.auth.model import Principal


@dataclass
class SearchHit:
    document_id: str
    title: str
    revision_id: str
    revision_label: str | None
    copy_id: str
    page_id: str
    page_number: int  # 1-based pdf position
    snippet: str
    badges: list = field(default_factory=list)

    @property
    def citation(self) -> dict:
        return {
            "document": self.document_id,
            "revision": self.revision_id,
            "representation": self.copy_id,
            "locator": {"type": "page", "page": self.page_number},
        }


_HIT_SQL = """
SELECT
  d.id AS document_id, d.title, d.primary_domain, d.additional_domains, d.sensitivity,
  r.id AS revision_id, r.label AS revision_label,
  c.id AS copy_id, c.assessment_mode, c.default_state, c.superseded,
  c.image_adequate, c.text_adequate, c.risk_flags, c.action_labels,
  p.id AS page_id, p.pdf_index,
  snippet(pages_fts, 0, '[', ']', ' ... ', 12) AS snip,
  bm25(pages_fts) AS rank
FROM pages_fts f
JOIN pages p ON p.rowid = f.rowid
JOIN copies c ON c.id = p.copy_id
JOIN revisions r ON r.id = c.revision_id
JOIN documents d ON d.id = r.document_id
WHERE pages_fts MATCH ?
ORDER BY rank
LIMIT ?
"""


def _badges(row: sqlite3.Row) -> list:
    badges = []
    if row["assessment_mode"] == "screening" or row["default_state"] == "provisional":
        badges.append("provisional")
    if row["superseded"]:
        badges.append("superseded")
    if row["image_adequate"] == 0:
        badges.append("image-inadequate")
    if row["text_adequate"] == 0:
        badges.append("text-inadequate")
    badges.extend(json.loads(row["risk_flags"] or "[]"))
    for label in json.loads(row["action_labels"] or "[]"):
        if label not in badges:
            badges.append(label)
    return badges


def search(
    conn: sqlite3.Connection,
    principal: Principal,
    query: str,
    limit: int = 20,
) -> list[SearchHit]:
    require_verb(principal, "search")
    fetch = max(limit * 5, 50)  # over-fetch: the auth filter trims after
    try:
        rows = conn.execute(_HIT_SQL, (query, fetch)).fetchall()
    except sqlite3.OperationalError:
        # user text that is not valid FTS syntax: retry as a quoted phrase
        phrase = '"' + query.replace('"', '""') + '"'
        rows = conn.execute(_HIT_SQL, (phrase, fetch)).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        if not document_visible(principal, DocumentAccess.from_row(row)):
            continue  # the central filter, at fetch time, on every surface
        hits.append(
            SearchHit(
                document_id=row["document_id"],
                title=row["title"],
                revision_id=row["revision_id"],
                revision_label=row["revision_label"],
                copy_id=row["copy_id"],
                page_id=row["page_id"],
                page_number=row["pdf_index"] + 1,
                snippet=row["snip"],
                badges=_badges(row),
            )
        )
        if len(hits) >= limit:
            break
    return hits
