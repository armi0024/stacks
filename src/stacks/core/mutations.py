"""The mutation contract (SPEC 3.5), one engine for API and MCP submit.

Every authoritative change carries: authenticated actor, idempotency key,
expected prior version; passes its policy check (the caller's verb/grant
checks run BEFORE this engine); commits atomically; and emits an audit
event (through the outbox, in the same transaction). A version mismatch
returns an explicit conflict carrying the current version — never a silent
overwrite. Replayed idempotency keys return the original result.

apply_fn runs INSIDE the open transaction: it must execute statements on
the given connection directly and must not commit, roll back, or open a
`with conn:` block of its own.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

from stacks.auth.model import Principal
from stacks.core import audit
from stacks.core.ids import new_id

VERSIONED = {"document": "documents", "copy": "copies"}


class MutationError(Exception):
    pass


class ConflictError(MutationError):
    """Expected-prior-version mismatch; carries the current version."""

    def __init__(self, object_kind: str, object_id: str, current_version: int, expected: int):
        self.object_kind = object_kind
        self.object_id = object_id
        self.current_version = current_version
        self.expected = expected
        super().__init__(
            f"version conflict on {object_kind} {object_id}:"
            f" expected {expected}, current {current_version}"
        )


@dataclass
class MutationResult:
    operation: str
    object_kind: str
    object_id: str
    new_version: int
    result: dict
    replayed: bool = False

    def to_json(self) -> dict:
        return {
            "operation": self.operation,
            "object_kind": self.object_kind,
            "object_id": self.object_id,
            "new_version": self.new_version,
            "result": self.result,
            "replayed": self.replayed,
        }


def current_version(conn: sqlite3.Connection, object_kind: str, object_id: str) -> int:
    table = VERSIONED.get(object_kind)
    if table is None:
        raise MutationError(f"unversioned object kind: {object_kind}")
    row = conn.execute(f"SELECT row_version FROM {table} WHERE id = ?", (object_id,)).fetchone()
    if row is None:
        raise MutationError(f"{object_kind} not found: {object_id}")
    return row["row_version"]


def _stored(row: sqlite3.Row) -> MutationResult:
    return MutationResult(
        operation=row["operation"],
        object_kind=row["object_kind"],
        object_id=row["object_id"],
        new_version=row["new_version"],
        result=json.loads(row["result"]),
        replayed=True,
    )


def mutate(
    conn: sqlite3.Connection,
    *,
    principal: Principal,
    operation: str,
    object_kind: str,
    object_id: str,
    expected_version: int,
    idempotency_key: str,
    apply_fn: Callable[[sqlite3.Connection], dict | None],
    token_id: str | None = None,
    audit_event: str = "mutation",
    audit_detail: dict | None = None,
) -> MutationResult:
    if not idempotency_key:
        raise MutationError("idempotency_key is required")
    replay = conn.execute(
        "SELECT * FROM mutations WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if replay is not None:
        return _stored(replay)

    if conn.in_transaction:
        raise MutationError("mutate() requires a connection with no open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = current_version(conn, object_kind, object_id)
        if current != expected_version:
            raise ConflictError(object_kind, object_id, current, expected_version)
        result = apply_fn(conn) or {}
        table = VERSIONED[object_kind]
        stamp = ", updated_at = datetime('now')" if table == "documents" else ""
        cur = conn.execute(
            f"UPDATE {table} SET row_version = row_version + 1{stamp}"
            " WHERE id = ? AND row_version = ?",
            (object_id, expected_version),
        )
        if cur.rowcount != 1:  # impossible under BEGIN IMMEDIATE, but fail loudly
            raise ConflictError(
                object_kind, object_id, current_version(conn, object_kind, object_id), expected_version
            )
        new_version = expected_version + 1
        conn.execute(
            "INSERT INTO mutations(id, idempotency_key, actor, token_id, operation,"
            " object_kind, object_id, new_version, result, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                new_id("mu"), idempotency_key, principal.name, token_id, operation,
                object_kind, object_id, new_version, json.dumps(result),
            ),
        )
        audit.emit(conn, {
            "event": audit_event,
            "actor": principal.name,
            "token_id": token_id,
            "operation": operation,
            "object": {"kind": object_kind, "id": object_id},
            "new_version": new_version,
            "idempotency_key": idempotency_key,
            **({"detail": audit_detail} if audit_detail else {}),
        })
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "idempotency_key" in str(e):  # lost a replay race to a concurrent writer
            row = conn.execute(
                "SELECT * FROM mutations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if row is not None:
                return _stored(row)
        raise
    except BaseException:
        conn.rollback()
        raise
    return MutationResult(operation, object_kind, object_id, new_version, result)
