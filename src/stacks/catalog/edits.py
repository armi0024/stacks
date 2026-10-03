"""Concrete catalog mutations (SPEC 3.5 / 3.6 / 6.7), built on the contract
engine. Policy checks live HERE (verbs, widening confirmation, ext.* key
discipline); versioning, idempotency, atomicity, and audit live in
core.mutations.

- ext.* writes: writable only by ext-write tokens; stored and round-tripped,
  never interpreted; CAN NEVER alter Stacks-interpreted fields (enforced by
  construction: only ext.-prefixed keys inside the metadata JSON are touched).
- Access changes: classification may only move equal-or-more-restrictive;
  widening exposure (lower sensitivity, new domains) requires the admin verb
  AND explicit confirmation, and is audited as declassification.
- Legal hold: set/lift requires admin; unauthorized attempts are themselves
  audited (legal-hold-blocked) — a blocked attempt is evidence.
"""

from __future__ import annotations

import json
import sqlite3

from stacks.auth.filter import require_verb
from stacks.auth.model import SENSITIVITY_RANK, AuthError, Principal
from stacks.core import audit
from stacks.core.ids import new_id
from stacks.core.mutations import MutationError, MutationResult, mutate


def ext_write(
    conn: sqlite3.Connection,
    principal: Principal,
    document_id: str,
    values: dict,
    *,
    expected_version: int,
    idempotency_key: str,
    token_id: str | None = None,
) -> MutationResult:
    require_verb(principal, "ext-write")
    bad = [k for k in values if not k.startswith("ext.")]
    if bad:
        raise MutationError(f"ext-write may only touch ext.* keys; refused: {bad}")

    def apply(c: sqlite3.Connection) -> dict:
        meta = json.loads(
            c.execute("SELECT metadata FROM documents WHERE id = ?", (document_id,)).fetchone()[
                "metadata"
            ]
        )
        for k, v in values.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        c.execute("UPDATE documents SET metadata = ? WHERE id = ?", (json.dumps(meta), document_id))
        return {"keys": sorted(values)}

    return mutate(
        conn, principal=principal, operation="ext-write", object_kind="document",
        object_id=document_id, expected_version=expected_version,
        idempotency_key=idempotency_key, apply_fn=apply, token_id=token_id,
        audit_event="ext-write", audit_detail={"keys": sorted(values)},
    )


def set_access(
    conn: sqlite3.Connection,
    principal: Principal,
    document_id: str,
    *,
    domains_allowed: tuple,
    sensitivity: str | None = None,
    primary_domain: str | None = None,
    additional_domains: tuple | None = None,
    confirm_widen: bool = False,
    expected_version: int,
    idempotency_key: str,
    token_id: str | None = None,
) -> MutationResult:
    require_verb(principal, "submit")
    row = conn.execute(
        "SELECT primary_domain, additional_domains, sensitivity FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise MutationError(f"document not found: {document_id}")
    new_sens = sensitivity or row["sensitivity"]
    if new_sens not in SENSITIVITY_RANK:
        raise MutationError(f"unknown sensitivity {new_sens!r}")
    new_primary = primary_domain or row["primary_domain"]
    new_extra = tuple(additional_domains) if additional_domains is not None else tuple(
        json.loads(row["additional_domains"])
    )
    for d in (new_primary, *new_extra):
        if d not in domains_allowed:
            raise MutationError(f"unknown domain {d!r}")

    old_domains = {row["primary_domain"], *json.loads(row["additional_domains"])}
    new_domains = {new_primary, *new_extra}
    widens = (
        SENSITIVITY_RANK[new_sens] < SENSITIVITY_RANK[row["sensitivity"]]
        or bool(new_domains - old_domains)
    )
    if widens:
        # widening exposure requires explicit owner confirmation (3.2)
        if not principal.has_verb("admin"):
            raise AuthError(f"principal {principal.name} may not widen exposure")
        if not confirm_widen:
            raise MutationError("widening exposure requires confirm_widen=true")

    def apply(c: sqlite3.Connection) -> dict:
        c.execute(
            "UPDATE documents SET sensitivity = ?, primary_domain = ?, additional_domains = ?"
            " WHERE id = ?",
            (new_sens, new_primary, json.dumps(list(new_extra)), document_id),
        )
        return {
            "sensitivity": [row["sensitivity"], new_sens],
            "domains": [sorted(old_domains), sorted(new_domains)],
            "widened": widens,
        }

    return mutate(
        conn, principal=principal, operation="set-access", object_kind="document",
        object_id=document_id, expected_version=expected_version,
        idempotency_key=idempotency_key, apply_fn=apply, token_id=token_id,
        audit_event="declassification" if widens else "access-change",
        audit_detail={"from": {"sensitivity": row["sensitivity"], "domains": sorted(old_domains)},
                      "to": {"sensitivity": new_sens, "domains": sorted(new_domains)}},
    )


def set_legal_hold(
    conn: sqlite3.Connection,
    principal: Principal,
    document_id: str,
    hold: bool,
    *,
    expected_version: int,
    idempotency_key: str,
    token_id: str | None = None,
) -> MutationResult:
    if not principal.has_verb("admin"):
        with conn:  # the blocked attempt is itself an audited event (3.6)
            audit.emit(conn, {
                "event": "legal-hold-blocked", "actor": principal.name, "token_id": token_id,
                "object": {"kind": "document", "id": document_id},
                "detail": {"attempted": "set" if hold else "lift"},
            })
        raise AuthError(f"principal {principal.name} may not change legal hold")

    def apply(c: sqlite3.Connection) -> dict:
        c.execute("UPDATE documents SET legal_hold = ? WHERE id = ?", (int(hold), document_id))
        return {"legal_hold": hold}

    return mutate(
        conn, principal=principal, operation="legal-hold", object_kind="document",
        object_id=document_id, expected_version=expected_version,
        idempotency_key=idempotency_key, apply_fn=apply, token_id=token_id,
        audit_event="legal-hold-set" if hold else "legal-hold-lifted",
    )


def add_revision(
    conn: sqlite3.Connection,
    principal: Principal,
    document_id: str,
    *,
    label: str,
    effective_date: str | None = None,
    expected_version: int,
    idempotency_key: str,
    token_id: str | None = None,
) -> MutationResult:
    """A new content version of the document. Dependents of the document are
    marked stale (4.4): regeneration is the owner's/estate's job, not ours."""
    require_verb(principal, "submit")
    rev_id = new_id("rev")

    def apply(c: sqlite3.Connection) -> dict:
        nxt = c.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 AS n FROM revisions WHERE document_id = ?",
            (document_id,),
        ).fetchone()["n"]
        c.execute(
            "INSERT INTO revisions(id, document_id, label, ordinal, effective_date, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (rev_id, document_id, label, nxt, effective_date),
        )
        from stacks.catalog.authority import mark_dependents_stale

        stale = mark_dependents_stale(
            c, "document", document_id, reason="new-revision", actor=principal.name
        )
        return {"revision_id": rev_id, "ordinal": nxt, "dependents_marked_stale": stale}

    return mutate(
        conn, principal=principal, operation="add-revision", object_kind="document",
        object_id=document_id, expected_version=expected_version,
        idempotency_key=idempotency_key, apply_fn=apply, token_id=token_id,
        audit_detail={"revision_id": rev_id, "label": label},
    )
