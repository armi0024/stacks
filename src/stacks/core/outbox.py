"""Transactional outbox (SPEC 3.5).

Any change requiring work in another system (audit forwarding, chunk GC,
cache invalidation, Qdrant ops in phase 6) writes an outbox row IN THE SAME
SQLite transaction as the change; a dispatcher applies rows with retries;
restart reconciliation replays unapplied rows. No dual-write without the
outbox.

Contract details:
- `enqueue` never commits: it joins the caller's open transaction, so the
  outbox row and the change it describes are atomic.
- Handlers MUST be idempotent: a crash between a handler succeeding and the
  row being marked applied re-runs the handler on reconcile.
- A failing handler never blocks other rows; attempts and last_error are
  recorded and the row stays pending for the next dispatch.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

Handler = Callable[[sqlite3.Connection, dict], None]


@dataclass
class DispatchReport:
    applied: int = 0
    failed: int = 0
    skipped_unknown: int = 0


def enqueue(conn: sqlite3.Connection, kind: str, payload: dict) -> int:
    """Insert an outbox row in the CALLER's transaction (no commit here)."""
    cur = conn.execute(
        "INSERT INTO outbox(kind, payload, created_at) VALUES (?, ?, datetime('now'))",
        (kind, json.dumps(payload)),
    )
    return cur.lastrowid


def pending(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM outbox WHERE applied_at IS NULL ORDER BY id LIMIT ?", (limit,)
    ).fetchall()


def dispatch(
    conn: sqlite3.Connection,
    handlers: dict[str, Handler],
    limit: int = 100,
) -> DispatchReport:
    """Apply pending rows through their handlers; returns counts.

    Also the restart-reconciliation path: replaying unapplied rows after a
    crash is the same operation as a normal dispatch.
    """
    report = DispatchReport()
    for row in pending(conn, limit):
        handler = handlers.get(row["kind"])
        if handler is None:
            report.skipped_unknown += 1
            continue
        try:
            handler(conn, json.loads(row["payload"]))
        except Exception as e:  # noqa: BLE001 - a bad row must not block the rest
            with conn:
                conn.execute(
                    "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                    (f"{type(e).__name__}: {e}", row["id"]),
                )
            report.failed += 1
            continue
        with conn:
            conn.execute(
                "UPDATE outbox SET applied_at = datetime('now'), attempts = attempts + 1,"
                " last_error = NULL WHERE id = ?",
                (row["id"],),
            )
        report.applied += 1
    return report


def status(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN applied_at IS NULL THEN 1 ELSE 0 END) AS pending,"
        " SUM(CASE WHEN applied_at IS NULL AND attempts > 0 THEN 1 ELSE 0 END) AS failing"
        " FROM outbox"
    ).fetchone()
    return {"total": row["total"], "pending": row["pending"] or 0, "failing": row["failing"] or 0}
