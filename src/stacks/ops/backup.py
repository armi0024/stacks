"""Database protection (SPEC 3.5 / P9).

- HOURLY incremental protection via the SQLite online backup API (constant
  cost as the DB grows): `incremental_backup` copies the live database to a
  work file and atomically replaces the previous hourly copy.
- NIGHTLY `VACUUM INTO` snapshot, retained N generations (default 14),
  integrity-verified at creation.
- `restore` copies a snapshot back and verifies it — the same path the
  restore drill exercises.

Targets: RPO <= 1 h, RTO <= 4 h via the runbook. Keys under the config
root's keys/ are EXCLUDED from these backups (P12) — escrow is manual and
documented in the runbook.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


class BackupError(Exception):
    pass


def _verify(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if ok != "ok":
        raise BackupError(f"backup failed integrity check: {path}")


def nightly_snapshot(db_path: str | Path, dest_dir: str | Path, retain: int = 14) -> Path:
    db_path, dest_dir = Path(db_path), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{db_path.stem}.{stamp}.sqlite"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{db_path.stem}.{stamp}-{n}.sqlite"
        n += 1
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    _verify(dest)
    snapshots = sorted(dest_dir.glob(f"{db_path.stem}.*.sqlite"))
    for old in snapshots[:-retain]:
        old.unlink()
    return dest


def incremental_backup(db_path: str | Path, dest_path: str | Path) -> Path:
    """Online backup API: safe against concurrent writers, constant cost."""
    db_path, dest_path = Path(db_path), Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_suffix(dest_path.suffix + ".tmp")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _verify(tmp)
    os.replace(tmp, dest_path)
    return dest_path


def restore(snapshot_path: str | Path, db_path: str | Path) -> None:
    """Restore a snapshot to db_path (target must not exist: restoring over
    a live database is a runbook decision, not a default)."""
    snapshot_path, db_path = Path(snapshot_path), Path(db_path)
    if db_path.exists():
        raise BackupError(
            f"refusing to overwrite existing database {db_path}: move it aside first (runbook)"
        )
    _verify(snapshot_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + ".restoring")
    tmp.write_bytes(snapshot_path.read_bytes())
    _verify(tmp)
    os.replace(tmp, db_path)
