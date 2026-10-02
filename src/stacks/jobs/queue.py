"""Jobs table operations (SPEC 3.4): atomic claim + expiring leases,
heartbeats, bounded retries, idempotent outputs.

A job is claimed by a single UPDATE guarded on status+lease, so two workers
can never hold the same job; an expired lease makes the job claimable again
(the previous worker's heartbeat/complete is rejected by worker_id)."""

from __future__ import annotations

import json
import sqlite3

from stacks.core.ids import new_id

DEFAULT_LEASE_SECONDS = 300


class JobError(Exception):
    pass


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict | None = None,
    priority: int = 0,
    max_attempts: int = 5,
    idempotency_key: str | None = None,
) -> str:
    """Returns the job id; a replayed idempotency key returns the original."""
    if idempotency_key:
        row = conn.execute(
            "SELECT id FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if row:
            return row["id"]
    job_id = new_id("job")
    try:
        with conn:
            conn.execute(
                "INSERT INTO jobs(id, kind, payload, status, priority, idempotency_key,"
                " max_attempts, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?, ?, datetime('now'), datetime('now'))",
                (job_id, kind, json.dumps(payload or {}), priority, idempotency_key, max_attempts),
            )
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if row:
            return row["id"]
        raise
    return job_id


def claim(
    conn: sqlite3.Connection,
    worker_id: str,
    kinds: list[str] | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict | None:
    """Atomically claim the next runnable job; None when nothing is runnable.

    Runnable: queued, or running with an expired lease (crashed worker)."""
    kind_clause = ""
    params: list = []
    if kinds:
        kind_clause = f" AND kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    row = conn.execute(
        "SELECT id FROM jobs WHERE (status = 'queued'"
        " OR (status = 'running' AND lease_until < datetime('now')))"
        + kind_clause
        + " ORDER BY priority DESC, created_at ASC LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    with conn:
        cur = conn.execute(
            "UPDATE jobs SET status='running', worker_id=?, attempts=attempts+1,"
            " lease_until=datetime('now', ?), heartbeat_at=datetime('now'),"
            " updated_at=datetime('now')"
            " WHERE id=? AND (status='queued'"
            " OR (status='running' AND lease_until < datetime('now')))",
            (worker_id, f"{lease_seconds:+d} seconds", row["id"]),
        )
    if cur.rowcount != 1:
        return None  # lost the race; caller retries
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
    return dict(job) | {"payload": json.loads(job["payload"])}


def heartbeat(
    conn: sqlite3.Connection, job_id: str, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE jobs SET heartbeat_at=datetime('now'),"
            " lease_until=datetime('now', ?), updated_at=datetime('now')"
            " WHERE id=? AND worker_id=? AND status='running'",
            (f"{lease_seconds:+d} seconds", job_id, worker_id),
        )
    return cur.rowcount == 1


def complete(conn: sqlite3.Connection, job_id: str, worker_id: str, result: dict | None = None) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE jobs SET status='done', result=?, updated_at=datetime('now')"
            " WHERE id=? AND worker_id=? AND status='running'",
            (json.dumps(result or {}), job_id, worker_id),
        )
    return cur.rowcount == 1


def fail(conn: sqlite3.Connection, job_id: str, worker_id: str, error: str) -> str:
    """Bounded retries: requeue until max_attempts, then dead. Returns the
    resulting status."""
    row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise JobError(f"unknown job {job_id}")
    next_status = "dead" if row["attempts"] >= row["max_attempts"] else "queued"
    with conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, error=?, worker_id=NULL, lease_until=NULL,"
            " updated_at=datetime('now')"
            " WHERE id=? AND worker_id=? AND status='running'",
            (next_status, error, job_id, worker_id),
        )
    if cur.rowcount != 1:
        raise JobError(f"job {job_id} is not running under worker {worker_id}")
    return next_status
