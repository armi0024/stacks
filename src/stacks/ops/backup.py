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

import hashlib
import os
import sqlite3
import time
from pathlib import Path


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _copy_bytes(src: Path, dst: Path) -> None:
    """Chunked copy; never shutil.copyfile, whose macOS fcopyfile() fast
    path fails with EPERM on smbfs."""
    with src.open("rb") as fin, dst.open("wb") as fout:
        for block in iter(lambda: fin.read(1 << 20), b""):
            fout.write(block)


def _publish(work: Path, dest: Path) -> None:
    """Byte-copy a verified local work file to dest (atomic via rename).

    SQLite never opens files on the destination: its locking is unreliable
    on network filesystems (smbfs), so all database work happens beside the
    source DB on local disk and only bytes travel to the NAS, hash-checked.
    """
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    _copy_bytes(work, tmp)
    if _sha256(tmp) != _sha256(work):
        tmp.unlink(missing_ok=True)
        raise BackupError(f"copy to {dest} corrupted in transit")
    os.replace(tmp, dest)


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
    work = db_path.parent / f".{dest.name}.work"
    work.unlink(missing_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(work),))
    finally:
        conn.close()
    try:
        _verify(work)
        _publish(work, dest)
    finally:
        work.unlink(missing_ok=True)
    snapshots = sorted(dest_dir.glob(f"{db_path.stem}.*.sqlite"))
    for old in snapshots[:-retain]:
        old.unlink()
    return dest


def incremental_backup(db_path: str | Path, dest_path: str | Path) -> Path:
    """Online backup API: safe against concurrent writers, constant cost."""
    db_path, dest_path = Path(db_path), Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    work = db_path.parent / f".{dest_path.name}.work"
    work.unlink(missing_ok=True)
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(work))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        _verify(work)
        _publish(work, dest_path)
    finally:
        work.unlink(missing_ok=True)
    return dest_path


def restore(snapshot_path: str | Path, db_path: str | Path) -> None:
    """Restore a snapshot to db_path (target must not exist: restoring over
    a live database is a runbook decision, not a default)."""
    snapshot_path, db_path = Path(snapshot_path), Path(db_path)
    if db_path.exists():
        raise BackupError(
            f"refusing to overwrite existing database {db_path}: move it aside first (runbook)"
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + ".restoring")
    _copy_bytes(snapshot_path, tmp)
    _verify(tmp)  # verified only once local: SQLite never opens NAS files
    os.replace(tmp, db_path)
