"""Config root (SPEC 3.6): all host-specific configuration in one directory,
so moving hosts is the runbook's job, not a code change.

Layout of the config root:
    config.json   -- paths, domains, thresholds (this module)
    keys/         -- token-signing + bundle-signing keys (EXCLUDED from
                     routine backups per SPEC P12; escrowed separately)

Guards (SPEC 3.1):
- the SQLite database must live on a local filesystem, never a network path;
- the files root (NAS) must prove its identity via a marker file before any
  job writes to it: a dropped mount shows an empty local dir, and jobs must
  never write to an unverified path (DECISIONS.md environment facts).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MOUNT_MARKER_NAME = ".stacks-volume"
DEFAULT_DOMAINS = ("archive", "paradise", "starcade", "retrolodge", "personal")
SENSITIVITIES = ("open", "internal", "confidential", "restricted")
NETWORK_FS_TYPES = ("smbfs", "nfs", "afpfs", "webdav", "cifs")


class ConfigError(Exception):
    pass


class MountUnverified(Exception):
    """The files root does not prove its identity: writes must not proceed."""


@dataclass
class StacksConfig:
    config_root: Path
    db_path: Path
    files_root: Path  # role dirs (archive/uploads/ingest/...) live under this
    mount_id: str  # expected content of the marker file at files_root
    domains: tuple[str, ...] = DEFAULT_DOMAINS
    capacity_threshold: float = 0.80
    token_ttl_seconds: int = 3600

    @property
    def keys_dir(self) -> Path:
        return self.config_root / "keys"

    def to_json(self) -> dict:
        return {
            "db_path": str(self.db_path),
            "files_root": str(self.files_root),
            "mount_id": self.mount_id,
            "domains": list(self.domains),
            "capacity_threshold": self.capacity_threshold,
            "token_ttl_seconds": self.token_ttl_seconds,
        }


def network_mount_points() -> list[str]:
    """Mount points of network filesystems on this host (best effort)."""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    points = []
    for line in out.splitlines():
        # "//user@host/share on /Volumes/x (smbfs, ...)"
        if " on " not in line or "(" not in line:
            continue
        mount_point = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
        fstype = line.rsplit("(", 1)[1].split(",", 1)[0].strip()
        if fstype in NETWORK_FS_TYPES:
            points.append(mount_point)
    return points


def assert_db_path_local(db_path: Path, network_points: list[str] | None = None) -> None:
    points = network_mount_points() if network_points is None else network_points
    resolved = str(Path(db_path).resolve())
    for p in points:
        if resolved == p or resolved.startswith(p.rstrip("/") + "/"):
            raise ConfigError(
                f"refusing SQLite on a network path: {resolved} is under network mount {p} "
                "(SPEC 3.1: databases live on local SSD only)"
            )


def verify_mount(cfg: StacksConfig) -> None:
    """Raise MountUnverified unless the files root carries the expected marker."""
    marker = cfg.files_root / MOUNT_MARKER_NAME
    try:
        content = marker.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise MountUnverified(
            f"files root {cfg.files_root} has no readable {MOUNT_MARKER_NAME} marker ({e}): "
            "the mount may have dropped; jobs pause and must not write here"
        ) from e
    if content != cfg.mount_id:
        raise MountUnverified(
            f"files root marker mismatch: expected {cfg.mount_id!r}, found {content!r}: "
            "wrong volume mounted; jobs must not write here"
        )


def init_config_root(
    config_root: Path,
    db_path: Path,
    files_root: Path,
    mount_id: str,
    domains: tuple[str, ...] = DEFAULT_DOMAINS,
    write_marker: bool = False,
) -> StacksConfig:
    """Create a config root (and optionally stamp the files-root marker)."""
    cfg = StacksConfig(
        config_root=Path(config_root),
        db_path=Path(db_path),
        files_root=Path(files_root),
        mount_id=mount_id,
        domains=tuple(domains),
    )
    cfg.config_root.mkdir(parents=True, exist_ok=True)
    cfg.keys_dir.mkdir(parents=True, exist_ok=True)
    (cfg.config_root / "config.json").write_text(
        json.dumps(cfg.to_json(), indent=2), encoding="utf-8"
    )
    if write_marker:
        cfg.files_root.mkdir(parents=True, exist_ok=True)
        (cfg.files_root / MOUNT_MARKER_NAME).write_text(mount_id, encoding="utf-8")
    return cfg


def load_config(config_root: str | Path | None = None) -> StacksConfig:
    root = Path(config_root or os.environ.get("STACKS_CONFIG", ""))
    if not str(root):
        raise ConfigError("no config root: pass a path or set STACKS_CONFIG")
    path = root / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    return StacksConfig(
        config_root=root,
        db_path=Path(data["db_path"]),
        files_root=Path(data["files_root"]),
        mount_id=data["mount_id"],
        domains=tuple(data.get("domains", DEFAULT_DOMAINS)),
        capacity_threshold=data.get("capacity_threshold", 0.80),
        token_ttl_seconds=data.get("token_ttl_seconds", 3600),
    )
