"""Hash-chained JSONL audit stream (SPEC 3.6 / P11).

Append-only, tamper-evident: each line carries the SHA-256 of the previous
line's hash plus its own canonical body; the collector (here: `verify_chain`)
verifies continuity, and a chain break is itself an alarmed event. Rotation
preserves the chain: a new file's first line chains from the rotated file's
last hash.

Events reach the stream THROUGH THE OUTBOX ('audit' rows), so audit intent
commits atomically with the change it describes; the dispatcher appends.
Event payloads carry actor/token ID and never carry document content.

Stable event names (3.6): ingest-commit, canonical-assignment,
authority-write, access-change, declassification, token-issued,
token-revoked, legal-hold-set, legal-hold-lifted, legal-hold-blocked,
endpoint-change, phi-refusal, sensitive-fetch, ext-write, mutation,
derivation-added, derivation-stale, generation-activated.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

GENESIS = "genesis"
AUDIT_FILE = "audit.jsonl"
DEFAULT_ROTATE_BYTES = 64 * 1024 * 1024


class AuditError(Exception):
    pass


def audit_path(config_root: str | Path) -> Path:
    return Path(config_root) / "audit" / AUDIT_FILE


def _canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _line_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canonical(body)).encode("utf-8")).hexdigest()


def _last_hash(path: Path) -> str:
    """Hash of the final line, or the rotated predecessor's, or GENESIS."""
    candidates = [path] + sorted(
        path.parent.glob(path.name + ".*"),
        key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
        reverse=True,
    )
    for f in candidates:
        if f.exists() and f.stat().st_size:
            with f.open("rb") as fh:
                tail = fh.readlines()[-1]
            try:
                return json.loads(tail)["hash"]
            except (ValueError, KeyError) as e:
                raise AuditError(f"unreadable final audit line in {f}: {e}") from e
    return GENESIS


def append_event(
    path: str | Path, event: dict, rotate_bytes: int = DEFAULT_ROTATE_BYTES
) -> dict:
    """Append one event; returns the full line written (with prev/hash).

    `event` must carry at least: event (name), actor; token_id, object, and
    detail are conventional. Content never goes in the stream.
    """
    path = Path(path)
    if "event" not in event or "actor" not in event:
        raise AuditError("audit events require 'event' and 'actor'")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > rotate_bytes:
        rotated = path.parent / f"{path.name}.{sum(1 for _ in path.parent.glob(path.name + '.*')) + 1}"
        path.rename(rotated)
    prev = _last_hash(path)
    body = dict(event)
    body.setdefault("at", _now_iso())
    body["prev"] = prev
    line = dict(body)
    line["hash"] = _line_hash(prev, body)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")
    return line


def _now_iso() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class ChainReport:
    ok: bool
    lines: int
    break_line: int | None = None  # 1-based line number of the first bad link
    reason: str | None = None


def verify_chain(path: str | Path, expect_prev: str = GENESIS) -> ChainReport:
    """Recompute every link; a break is reported with its line number.

    `expect_prev` lets rotated segments be verified in sequence: pass the
    previous segment's final hash.
    """
    path = Path(path)
    if not path.exists():
        return ChainReport(ok=True, lines=0)
    prev = expect_prev
    n = 0
    with path.open("r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, start=1):
            try:
                line = json.loads(raw)
                claimed = line.pop("hash")
            except (ValueError, KeyError):
                return ChainReport(False, n, n, "unparseable line")
            if line.get("prev") != prev:
                return ChainReport(False, n, n, "prev-hash mismatch (chain break)")
            if _line_hash(prev, line) != claimed:
                return ChainReport(False, n, n, "line hash mismatch (tampered)")
            prev = claimed
    return ChainReport(ok=True, lines=n)


# ---------------------------------------------------------------- outbox glue

def emit(conn: sqlite3.Connection, event: dict) -> None:
    """Enqueue an audit event in the caller's transaction (outbox 'audit')."""
    from stacks.core import outbox

    outbox.enqueue(conn, "audit", event)


def make_handler(path: str | Path):
    """Outbox handler appending events to the chain. Idempotency: replaying
    an already-written event appends a duplicate line, which is harmless to
    the chain and visibly duplicated rather than silently lost — the stream
    errs toward over-recording."""

    def _handler(conn: sqlite3.Connection, payload: dict) -> None:
        append_event(path, payload)

    return _handler
