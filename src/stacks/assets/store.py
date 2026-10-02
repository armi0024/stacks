"""Immutable, content-addressed asset store (SPEC 3.1/4.1).

Paths are role-scoped only and carry NO organizational meaning: under each
role dir, files live by hash (`<role>/<sha[:2]>/<sha><ext>`), never under
game/title/domain names. A committed file never moves. Dedup operates here:
identical bytes are stored once (the sha256 UNIQUE column is the registry);
documents in distinct contexts may reference the same asset, and permissions
never live on assets.

Every write verifies the mount identity first (jobs never write to an
unverified path) and lands via temp-file + atomic rename; an existing path
is never overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from stacks.core.config import StacksConfig, verify_mount
from stacks.core.ids import md5_file, new_id, sha256_file


class AssetError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    id: str
    sha256: str
    md5: str | None
    size_bytes: int
    role: str
    rel_path: str
    media_type: str | None
    parent_asset_id: str | None


def _from_row(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        sha256=row["sha256"],
        md5=row["md5"],
        size_bytes=row["size_bytes"],
        role=row["role"],
        rel_path=row["rel_path"],
        media_type=row["media_type"],
        parent_asset_id=row["parent_asset_id"],
    )


class AssetStore:
    def __init__(self, conn: sqlite3.Connection, cfg: StacksConfig):
        self.conn = conn
        self.cfg = cfg

    def get(self, asset_id: str) -> Asset:
        row = self.conn.execute("SELECT * FROM file_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise AssetError(f"unknown asset: {asset_id}")
        return _from_row(row)

    def find_by_sha256(self, sha: str) -> Asset | None:
        row = self.conn.execute("SELECT * FROM file_assets WHERE sha256 = ?", (sha,)).fetchone()
        return _from_row(row) if row else None

    def abs_path(self, asset: Asset) -> Path:
        return self.cfg.files_root / asset.rel_path

    def put_file(
        self,
        src_path: str | Path,
        role: str,
        media_type: str | None = None,
        parent_asset_id: str | None = None,
        source_urls: list[str] | None = None,
        producer_tool: str | None = None,
        producer_version: str | None = None,
        keep_md5: bool = False,
    ) -> tuple[Asset, bool]:
        """Store a file; returns (asset, created). Dedup by SHA-256:
        existing bytes return the existing asset with source URLs merged."""
        src = Path(src_path)
        sha = sha256_file(str(src))
        existing = self.find_by_sha256(sha)
        if existing is not None:
            if source_urls:
                self._merge_source_urls(existing.id, source_urls)
            return existing, False

        verify_mount(self.cfg)  # never write to an unverified path
        ext = src.suffix.lower()
        rel = Path(role) / sha[:2] / f"{sha}{ext}"
        dest = self.cfg.files_root / rel
        if dest.exists():
            # bytes on disk but no DB row: verify before adopting
            if sha256_file(str(dest)) != sha:
                raise AssetError(f"hash collision/corruption at {dest}: refusing to overwrite")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + f".tmp-{os.getpid()}")
            shutil.copyfile(src, tmp)
            if sha256_file(str(tmp)) != sha:
                tmp.unlink(missing_ok=True)
                raise AssetError(f"copy verification failed for {src}")
            os.rename(tmp, dest)

        asset_id = new_id("fa")
        with self.conn:
            self.conn.execute(
                "INSERT INTO file_assets(id, sha256, md5, size_bytes, role, rel_path,"
                " media_type, parent_asset_id, producer_tool, producer_version,"
                " source_urls, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (
                    asset_id,
                    sha,
                    md5_file(str(src)) if keep_md5 else None,
                    src.stat().st_size,
                    role,
                    str(rel),
                    media_type,
                    parent_asset_id,
                    producer_tool,
                    producer_version,
                    json.dumps(source_urls or []),
                ),
            )
        return self.get(asset_id), True

    def _merge_source_urls(self, asset_id: str, urls: list[str]) -> None:
        row = self.conn.execute(
            "SELECT source_urls FROM file_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        merged = list(dict.fromkeys(json.loads(row["source_urls"]) + urls))
        with self.conn:
            self.conn.execute(
                "UPDATE file_assets SET source_urls = ? WHERE id = ?",
                (json.dumps(merged), asset_id),
            )

    def lineage(self, asset_id: str) -> list[Asset]:
        """Chain from this asset back to its root (raw capture)."""
        chain = [self.get(asset_id)]
        while chain[-1].parent_asset_id:
            chain.append(self.get(chain[-1].parent_asset_id))
        return chain
