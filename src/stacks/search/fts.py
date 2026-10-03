"""Keyword search over the generation-versioned chunk store (phase 1b;
hybrid vector+FTS arrives in phase 6 behind the same filter).

Search sees ONLY each document's ACTIVE generation (the activation pointer
is joined into the query, so a half-built generation N+1 is invisible and a
flip is atomic). The default representation of every revision is indexed;
hits on a revision other than the authority-pinned one rank below pinned
hits and are badged, which makes "find the correct revision" a real search
outcome (9.2). EVERY hit passes the central auth filter at fetch time; the
index is never the boundary. Hits carry durable citations and honest badges
(provisional, adequacy, risk flags, superseded, authority-conflict)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from stacks.auth.filter import DocumentAccess, document_visible, require_verb
from stacks.auth.model import Principal
from stacks.catalog.authority import pinned_revision


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
  ch.page_id, ch.page_number,
  snippet(chunks_fts, 0, '[', ']', ' ... ', 12) AS snip,
  bm25(chunks_fts) AS rank
FROM chunks_fts f
JOIN chunks ch ON ch.rowid = f.rowid
JOIN document_generations g
  ON g.document_id = ch.document_id AND g.active_generation = ch.generation
JOIN copies c ON c.id = ch.copy_id
JOIN revisions r ON r.id = ch.revision_id
JOIN documents d ON d.id = ch.document_id
WHERE chunks_fts MATCH ?
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
    pins: dict[str, tuple[str | None, bool]] = {}
    ranked: list[tuple[int, int, SearchHit]] = []
    for order, row in enumerate(rows):
        if not document_visible(principal, DocumentAccess.from_row(row)):
            continue  # the central filter, at fetch time, on every surface
        doc_id = row["document_id"]
        if doc_id not in pins:
            pins[doc_id] = pinned_revision(conn, doc_id)
        pinned, conflict = pins[doc_id]
        badges = _badges(row)
        if conflict and "authority-conflict" not in badges:
            badges.append("authority-conflict")  # report, never resolve silently
        if pinned is not None:
            if row["revision_id"] == pinned:
                badges.insert(0, "pinned")
                tier = 0
            else:
                badges.append("superseded-revision")
                tier = 1
        else:
            tier = 0  # no pin: absence is visible, not penalized
        ranked.append((tier, order, SearchHit(
            document_id=doc_id,
            title=row["title"],
            revision_id=row["revision_id"],
            revision_label=row["revision_label"],
            copy_id=row["copy_id"],
            page_id=row["page_id"],
            page_number=row["page_number"],
            snippet=row["snip"],
            badges=badges,
        )))
    ranked.sort(key=lambda t: (t[0], t[1]))  # pinned/current above superseded
    return [hit for _, _, hit in ranked[:limit]]
