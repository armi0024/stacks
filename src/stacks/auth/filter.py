"""THE central auth filter (SPEC 3.2).

Every surface — FTS results, vector results, thumbnails, recents, downloads,
queues, page/image/region endpoints, MCP — routes its results through
`document_visible` / `filter_documents` AT FETCH TIME, on all generations and
previously issued links. There is exactly one enforcement point; stores and
indexes are never the boundary.

Access passes when the principal's ceiling for AT LEAST ONE of the document's
domains (primary + additional, P5) admits the document's sensitivity.
Verb checks are separate: reading requires 'read', searching 'search'.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from stacks.auth.model import SENSITIVITY_RANK, AuthError, Principal


@dataclass(frozen=True)
class DocumentAccess:
    """The access-relevant facts of a document, independent of row shape."""

    primary_domain: str
    additional_domains: tuple
    sensitivity: str

    @classmethod
    def from_row(cls, row) -> "DocumentAccess":
        extra = row["additional_domains"]
        return cls(
            primary_domain=row["primary_domain"],
            additional_domains=tuple(json.loads(extra) if isinstance(extra, str) else extra or ()),
            sensitivity=row["sensitivity"],
        )

    @property
    def domains(self) -> tuple:
        return (self.primary_domain, *self.additional_domains)


def document_visible(principal: Principal, access: DocumentAccess) -> bool:
    if not principal.active:
        return False
    needed = SENSITIVITY_RANK.get(access.sensitivity)
    if needed is None:
        return False  # malformed sensitivity: fail closed
    for domain in access.domains:
        ceiling = principal.ceiling(domain)
        if ceiling is not None and ceiling >= needed:
            return True
    return False


def filter_documents(principal: Principal, rows, access_of=DocumentAccess.from_row) -> list:
    """Post-filter any result rows carrying document access facts."""
    return [r for r in rows if document_visible(principal, access_of(r))]


def require_verb(principal: Principal, verb: str) -> None:
    if not principal.active:
        raise AuthError(f"principal {principal.name} is inactive")
    if not principal.has_verb(verb):
        raise AuthError(f"principal {principal.name} lacks verb {verb!r}")


def check_document_access(
    conn: sqlite3.Connection, principal: Principal, document_id: str, verb: str = "read"
) -> DocumentAccess:
    """Fetch-time check for a single document; raises AuthError when denied.

    Deny carries no distinction between 'absent' and 'forbidden': callers
    surface not-found either way, so access probing leaks nothing.
    """
    require_verb(principal, verb)
    row = conn.execute(
        "SELECT primary_domain, additional_domains, sensitivity FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise AuthError(f"document not found: {document_id}")
    access = DocumentAccess.from_row(row)
    if not document_visible(principal, access):
        raise AuthError(f"document not found: {document_id}")
    return access


def record_access_event(
    conn: sqlite3.Connection,
    actor: str,
    token_id: str | None,
    verb: str,
    object_kind: str,
    object_id: str,
    detail: str | None = None,
) -> None:
    from stacks.core.ids import new_id

    with conn:
        conn.execute(
            "INSERT INTO access_events(id, actor, token_id, verb, object_kind, object_id, at, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
            (new_id("ae"), actor, token_id, verb, object_kind, object_id, detail),
        )
