"""Principals: grant maps (domain -> max sensitivity) plus verbs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

SENSITIVITY_RANK = {"open": 0, "internal": 1, "confidential": 2, "restricted": 3}

VERBS = (
    "read", "search", "submit", "ext-write",
    "restricted-provenance-write", "authority", "admin",
)


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    kind: str
    grants: dict  # domain -> max sensitivity
    verbs: frozenset
    active: bool

    def has_verb(self, verb: str) -> bool:
        return verb in self.verbs

    def ceiling(self, domain: str) -> int | None:
        s = self.grants.get(domain)
        return SENSITIVITY_RANK[s] if s in SENSITIVITY_RANK else None


def _from_row(row: sqlite3.Row) -> Principal:
    return Principal(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        grants=json.loads(row["grants"]),
        verbs=frozenset(json.loads(row["verbs"])),
        active=bool(row["active"]),
    )


def get_principal(conn: sqlite3.Connection, principal_id: str) -> Principal:
    row = conn.execute("SELECT * FROM principals WHERE id = ?", (principal_id,)).fetchone()
    if row is None:
        raise AuthError(f"unknown principal: {principal_id}")
    return _from_row(row)


def get_principal_by_name(conn: sqlite3.Connection, name: str) -> Principal:
    row = conn.execute("SELECT * FROM principals WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise AuthError(f"unknown principal: {name}")
    return _from_row(row)


def create_principal(
    conn: sqlite3.Connection,
    principal_id: str,
    name: str,
    kind: str,
    grants: dict,
    verbs: list[str],
) -> Principal:
    for d, s in grants.items():
        if s not in SENSITIVITY_RANK:
            raise AuthError(f"bad sensitivity {s!r} for domain {d!r}")
    for v in verbs:
        if v not in VERBS:
            raise AuthError(f"unknown verb {v!r}")
    with conn:
        conn.execute(
            "INSERT INTO principals(id, name, kind, grants, verbs, active, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, datetime('now'))",
            (principal_id, name, kind, json.dumps(grants), json.dumps(verbs)),
        )
    return get_principal(conn, principal_id)
