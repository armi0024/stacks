"""Authority records, conflict resolution, and derivations (SPEC 4.4).

Authority is externally asserted and append-only: a document containing the
word "approved" creates no authority record; only authenticated principals
holding the authority verb for the object's domain write here. Retrieval
and workflows honor pins.

Conflict precedence (4.4): the configured principal precedence order
(settings key 'authority_precedence'; owner KIND outranks all), then latest
recorded_at within the effective window; a record arriving with an earlier
effective_date never silently displaces a later-effective one. Overlaps not
resolvable by these rules surface as a VISIBLE conflict: resolution reports
it, an audit event fires, and a review-queue job is created — retrieval
reports the conflict rather than resolving silently.

Derivations make generated documents traceable and stale-able: when an
underlying object gains a new revision or its authority changes, dependents
are marked stale and an event is emitted; regeneration is the estate's job.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from stacks.auth.filter import require_verb
from stacks.auth.model import AuthError, Principal
from stacks.core import audit
from stacks.core.ids import new_id

_RANK_UNLISTED = 1_000_000


class AuthorityError(Exception):
    pass


# --------------------------------------------------------------- resolution

@dataclass
class Resolution:
    winner: dict | None
    conflict: bool
    records: list = field(default_factory=list)
    conflicting: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "winner": self.winner,
            "conflict": self.conflict,
            "records": self.records,
            "conflicting": self.conflicting,
        }


def _precedence(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'principal_precedence'"
    ).fetchone()
    names = json.loads(row["value"]) if row else []
    return {name: i for i, name in enumerate(names)}


def _rank(conn: sqlite3.Connection, principal_name: str, order: dict[str, int]) -> tuple:
    row = conn.execute(
        "SELECT kind FROM principals WHERE name = ?", (principal_name,)
    ).fetchone()
    is_owner = bool(row and row["kind"] == "owner")
    return (0 if is_owner else 1, order.get(principal_name, _RANK_UNLISTED))


def resolve(conn: sqlite3.Connection, object_kind: str, object_id: str) -> Resolution:
    rows = [
        dict(r)
        for r in conn.execute(
            # rowid = arrival order: recorded_at alone has second granularity
            "SELECT *, rowid AS arrival FROM authority_records"
            " WHERE object_kind = ? AND object_id = ? ORDER BY arrival",
            (object_kind, object_id),
        )
    ]
    superseded = {r["supersedes"] for r in rows if r["supersedes"]}
    active = [r for r in rows if r["id"] not in superseded]
    if not active:
        return Resolution(winner=None, conflict=False, records=rows)

    order = _precedence(conn)
    ranked = sorted(active, key=lambda r: _rank(conn, r["principal"], order))
    top_rank = _rank(conn, ranked[0]["principal"], order)
    top = [r for r in ranked if _rank(conn, r["principal"], order) == top_rank]

    def eff(r: dict) -> str:
        return r["effective_date"] or ""

    by_recorded = sorted(top, key=lambda r: (r["recorded_at"], r["arrival"]), reverse=True)
    candidate = by_recorded[0]
    principals = {r["principal"] for r in top}
    if len(principals) == 1:
        # one writer agreeing with itself: the later-effective record stays
        # in force; arrival order breaks ties (earlier-effective never displaces)
        winner = sorted(top, key=lambda r: (eff(r), r["recorded_at"], r["arrival"]))[-1]
        return Resolution(winner=winner, conflict=False, records=rows)
    # multiple principals at equal precedence: latest recorded_at wins ONLY
    # if it is also latest-effective; otherwise the overlap is not silently
    # resolvable and must surface
    latest_eff = max(eff(r) for r in top)
    if eff(candidate) >= latest_eff:
        return Resolution(winner=candidate, conflict=False, records=rows)
    return Resolution(winner=None, conflict=True, records=rows, conflicting=top)


def pinned_revision(conn: sqlite3.Connection, document_id: str) -> tuple[str | None, bool]:
    """(revision_id, conflict) for the document's approved-for-use pin, from
    revision-level authority records. Absence of a pin is visible as None."""
    rev_ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM revisions WHERE document_id = ?", (document_id,))
    ]
    pinned, conflict = None, False
    for rid in rev_ids:
        res = resolve(conn, "revision", rid)
        if res.conflict:
            conflict = True
        elif res.winner and res.winner["status"] == "approved-for-use":
            pinned = rid
    return pinned, conflict


# ------------------------------------------------------------------- writes

def _object_document(conn: sqlite3.Connection, object_kind: str, object_id: str) -> sqlite3.Row:
    if object_kind == "document":
        sql = "SELECT * FROM documents WHERE id = ?"
    elif object_kind == "revision":
        sql = ("SELECT d.* FROM documents d JOIN revisions r ON r.document_id = d.id"
               " WHERE r.id = ?")
    elif object_kind == "copy":
        sql = ("SELECT d.* FROM documents d JOIN revisions r ON r.document_id = d.id"
               " JOIN copies c ON c.revision_id = r.id WHERE c.id = ?")
    else:
        raise AuthorityError(f"unknown object kind {object_kind!r}")
    row = conn.execute(sql, (object_id,)).fetchone()
    if row is None:
        raise AuthorityError(f"{object_kind} not found: {object_id}")
    return row


def _replay(conn: sqlite3.Connection, idempotency_key: str) -> dict | None:
    row = conn.execute(
        "SELECT result FROM mutations WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["result"]) | {"replayed": True}


def write_authority(
    conn: sqlite3.Connection,
    principal: Principal,
    *,
    object_kind: str,
    object_id: str,
    status: str,
    scope: str | None = None,
    effective_date: str | None = None,
    supersedes: str | None = None,
    idempotency_key: str,
    token_id: str | None = None,
) -> dict:
    """Append an authority record; surfaces any resulting conflict.

    Writable only by principals holding the authority verb AND a grant for
    (at least one of) the object's domains — owner, or designated
    domain-service tokens.
    """
    require_verb(principal, "authority")
    doc = _object_document(conn, object_kind, object_id)
    domains = (doc["primary_domain"], *json.loads(doc["additional_domains"]))
    if all(principal.ceiling(d) is None for d in domains):
        raise AuthError(
            f"principal {principal.name} holds no grant for domains {list(domains)}"
        )
    if not idempotency_key:
        raise AuthorityError("idempotency_key is required")
    stored = _replay(conn, idempotency_key)
    if stored is not None:
        return stored

    record_id = new_id("ar")
    if conn.in_transaction:
        raise AuthorityError("write_authority requires no open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO authority_records(id, principal, object_kind, object_id, status,"
            " scope, effective_date, recorded_at, supersedes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)",
            (record_id, principal.name, object_kind, object_id, status,
             scope, effective_date, supersedes),
        )
        resolution = resolve(conn, object_kind, object_id)
        result = {"record_id": record_id, "conflict": resolution.conflict}
        conn.execute(
            "INSERT INTO mutations(id, idempotency_key, actor, token_id, operation,"
            " object_kind, object_id, new_version, result, created_at)"
            " VALUES (?, ?, ?, ?, 'write-authority', ?, ?, NULL, ?, datetime('now'))",
            (new_id("mu"), idempotency_key, principal.name, token_id,
             object_kind, object_id, json.dumps(result)),
        )
        audit.emit(conn, {
            "event": "authority-write", "actor": principal.name, "token_id": token_id,
            "object": {"kind": object_kind, "id": object_id},
            "detail": {"record_id": record_id, "status": status,
                       "effective_date": effective_date, "supersedes": supersedes},
        })
        stale = mark_dependents_stale(
            conn, object_kind, object_id, reason="authority-change", actor=principal.name
        )
        result["dependents_marked_stale"] = stale
        if resolution.conflict:
            # VISIBLE conflict: audit event + review queue entry (4.4)
            audit.emit(conn, {
                "event": "authority-conflict", "actor": principal.name, "token_id": token_id,
                "object": {"kind": object_kind, "id": object_id},
                "detail": {"records": [r["id"] for r in resolution.conflicting]},
            })
            conn.execute(
                "INSERT OR IGNORE INTO jobs(id, kind, payload, status, idempotency_key,"
                " created_at, updated_at)"
                " VALUES (?, 'review-authority-conflict', ?, 'queued', ?,"
                " datetime('now'), datetime('now'))",
                (new_id("job"),
                 json.dumps({"object_kind": object_kind, "object_id": object_id}),
                 f"authority-conflict:{object_kind}:{object_id}"),
            )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "idempotency_key" in str(e):
            stored = _replay(conn, idempotency_key)
            if stored is not None:
                return stored
        raise
    except BaseException:
        conn.rollback()
        raise
    return result


# ---------------------------------------------------------------- derivations

def add_derivation(
    conn: sqlite3.Connection,
    principal: Principal,
    *,
    document_id: str,
    depends_on_kind: str,
    depends_on_id: str,
    citation: dict | None = None,
    idempotency_key: str,
    token_id: str | None = None,
) -> dict:
    """Record that document_id is derived from (depends on) another object.

    An agent-generated summary is a document with its derivation links; a
    summary of a summary shows its actual chain and is never counted as an
    independent source (4.4)."""
    require_verb(principal, "submit")
    if conn.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone() is None:
        raise AuthorityError(f"document not found: {document_id}")
    if depends_on_kind != "citation":
        _object_document(conn, depends_on_kind, depends_on_id)  # must exist
    stored = _replay(conn, idempotency_key)
    if stored is not None:
        return stored

    deriv_id = new_id("dv")
    if conn.in_transaction:
        raise AuthorityError("add_derivation requires no open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO derivations(id, document_id, depends_on_kind, depends_on_id,"
            " citation, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (deriv_id, document_id, depends_on_kind, depends_on_id,
             json.dumps(citation) if citation else None),
        )
        result = {"derivation_id": deriv_id}
        conn.execute(
            "INSERT INTO mutations(id, idempotency_key, actor, token_id, operation,"
            " object_kind, object_id, new_version, result, created_at)"
            " VALUES (?, ?, ?, ?, 'add-derivation', 'document', ?, NULL, ?, datetime('now'))",
            (new_id("mu"), idempotency_key, principal.name, token_id,
             document_id, json.dumps(result)),
        )
        audit.emit(conn, {
            "event": "derivation-added", "actor": principal.name, "token_id": token_id,
            "object": {"kind": "document", "id": document_id},
            "detail": {"depends_on": {"kind": depends_on_kind, "id": depends_on_id}},
        })
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "idempotency_key" in str(e):
            stored = _replay(conn, idempotency_key)
            if stored is not None:
                return stored
        raise
    except BaseException:
        conn.rollback()
        raise
    return result


def mark_dependents_stale(
    conn: sqlite3.Connection,
    depends_on_kind: str,
    depends_on_id: str,
    *,
    reason: str,
    actor: str,
) -> int:
    """Mark fresh dependents of an object stale; runs in the CALLER's
    transaction. Returns the number marked."""
    rows = conn.execute(
        "SELECT id, document_id FROM derivations"
        " WHERE depends_on_kind = ? AND depends_on_id = ? AND stale = 0",
        (depends_on_kind, depends_on_id),
    ).fetchall()
    if not rows:
        return 0
    conn.execute(
        "UPDATE derivations SET stale = 1"
        " WHERE depends_on_kind = ? AND depends_on_id = ? AND stale = 0",
        (depends_on_kind, depends_on_id),
    )
    audit.emit(conn, {
        "event": "derivation-stale", "actor": actor,
        "object": {"kind": depends_on_kind, "id": depends_on_id},
        "detail": {"reason": reason,
                   "documents": sorted({r["document_id"] for r in rows})},
    })
    return len(rows)


def stale_derivations(conn: sqlite3.Connection, document_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM derivations WHERE document_id = ? ORDER BY created_at",
            (document_id,),
        )
    ]
