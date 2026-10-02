"""SQLite layer: connection factory and forward-only migrations.

Rules (SPEC 3.4/3.5): WAL, generous busy_timeout, short transactions, workers
write directly, portable schema. Migrations are numbered, forward-only,
additive-only; every migration against an existing database takes and
verifies a fresh snapshot first — no migration runs without one.
"""

from __future__ import annotations

import re
import sqlite3
import time
from importlib import resources
from pathlib import Path

from stacks.core.config import assert_db_path_local

MIGRATIONS_PACKAGE = "stacks.migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


def connect(db_path: str | Path, check_local: bool = True) -> sqlite3.Connection:
    if check_local:
        assert_db_path_local(Path(db_path))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _available_migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) sorted ascending."""
    out = []
    for entry in resources.files(MIGRATIONS_PACKAGE).iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    out.sort()
    if len({v for v, _, _ in out}) != len(out):
        raise MigrationError("duplicate migration version numbers")
    return out


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    v = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    return v or 0


def _snapshot_and_verify(conn: sqlite3.Connection, db_path: Path) -> Path:
    """Fresh snapshot of the live DB, verified, before any migration applies."""
    snap_dir = db_path.parent / "migration-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / f"{db_path.stem}.pre-migration.{int(time.time())}.sqlite"
    conn.execute("VACUUM INTO ?", (str(snap),))
    check = sqlite3.connect(str(snap))
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        raise MigrationError(f"pre-migration snapshot failed integrity check: {snap}")
    return snap


def migrate(conn: sqlite3.Connection, db_path: str | Path | None = None) -> list[int]:
    """Apply pending migrations; returns the versions applied.

    CONVENTION: every migration script must be idempotent (IF NOT EXISTS /
    INSERT OR IGNORE only) — executescript commits as it goes, so a crash
    between a script and its version record recovers by re-running.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    applied: list[int] = []
    version = current_version(conn)
    pending = [(v, n, s) for v, n, s in _available_migrations() if v > version]
    if not pending:
        return applied
    if version > 0:
        if db_path is None:
            raise MigrationError("existing database: db_path required for the pre-migration snapshot")
        _snapshot_and_verify(conn, Path(db_path))
    for v, name, sql in pending:
        try:
            conn.executescript(sql)
            with conn:
                conn.execute(
                    "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, datetime('now'))",
                    (v, name),
                )
        except sqlite3.Error as e:
            raise MigrationError(f"migration {name} failed: {e}") from e
        applied.append(v)
    return applied


def open_database(db_path: str | Path, check_local: bool = True) -> sqlite3.Connection:
    conn = connect(db_path, check_local=check_local)
    migrate(conn, db_path)
    return conn
