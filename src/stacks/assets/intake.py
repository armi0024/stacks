"""Intake guards (SPEC 3.1/3.6): PHI refusal, lossless-raw check, capacity.

PHI refusal is mechanical and FAIL-CLOSED: on uncertainty, refuse. A
borderline refusal costs a manual re-review; a slip-through costs the
exclusion guarantee. The event logs path + reason code, never content;
there is NO override path into Stacks — corrected false positives resubmit
after outside review.

Raw captures are accepted ONLY in lossless-compressed formats (TIFF LZW/ZIP,
PNG, lossless JPEG XL); uncompressed intake is refused with a conversion
hint. Volume-full is a PLANNED event: crossing the capacity threshold opens
an add-storage task, never an outage.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from stacks.core.config import StacksConfig
from stacks.core.ids import new_id

# ------------------------------------------------------------------ PHI refusal

_PHI_STRONG = (
    "medical record number", "mrn", "patient name", "icd-10", "icd10",
    "hipaa", "protected health information", "diagnosis code", "discharge summary",
    "prescription", "dosage", "patient id", "date of birth",
)
_PHI_WEAK = (
    "patient", "diagnosis", "clinical", "physician", "symptom",
    "treatment plan", "medical history", "lab result",
)

CONVERSION_HINT = "convert to TIFF (LZW/ZIP), PNG, or lossless JPEG XL and resubmit"


class PhiRefused(Exception):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(
            f"intake refused: {reason_code} (PHI exclusion is mechanical and fail-closed; "
            "no override path exists; resubmit only after outside review)"
        )


@dataclass(frozen=True)
class RawCheck:
    ok: bool
    format: str | None
    reason: str | None = None
    hint: str | None = None


def _count_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    low = text.lower()
    for term in terms:
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", low):
            found.append(term)
    return found


def phi_screen(text_sample: str) -> tuple[bool, str | None]:
    """(refused, reason_code). Fail-closed: a single weak indicator refuses."""
    strong = _count_terms(text_sample, _PHI_STRONG)
    if strong:
        return True, f"phi-strong-indicator:{strong[0].replace(' ', '-')}"
    weak = _count_terms(text_sample, _PHI_WEAK)
    if len(weak) >= 2:
        return True, "phi-multiple-weak-indicators"
    if len(weak) == 1:
        return True, f"phi-uncertain:{weak[0].replace(' ', '-')}"
    return False, None


def enforce_phi_refusal(conn: sqlite3.Connection, path: str, text_sample: str) -> None:
    """Refuse at every domain/sensitivity; log path + reason, never content."""
    refused, reason = phi_screen(text_sample)
    if refused:
        from stacks.core import audit

        with conn:
            conn.execute(
                "INSERT INTO phi_refusals(id, path, reason_code, at)"
                " VALUES (?, ?, ?, datetime('now'))",
                (new_id("phi"), path, reason),
            )
            audit.emit(conn, {
                "event": "phi-refusal", "actor": "system:phi-screen",
                "object": {"kind": "path", "id": path},
                "detail": {"reason_code": reason},
            })
        raise PhiRefused(reason)


# ------------------------------------------------------- lossless-raw formats

_TIFF_LOSSLESS_COMPRESSIONS = {5: "LZW", 8: "ZIP/Deflate", 32946: "Deflate"}
_TIFF_UNCOMPRESSED = {1}


def _tiff_compression(path: Path) -> int | None:
    with open(path, "rb") as fh:
        head = fh.read(8)
        if len(head) < 8:
            return None
        if head[:2] == b"II":
            endian = "<"
        elif head[:2] == b"MM":
            endian = ">"
        else:
            return None
        if struct.unpack(endian + "H", head[2:4])[0] != 42:
            return None
        (ifd_offset,) = struct.unpack(endian + "I", head[4:8])
        fh.seek(ifd_offset)
        (n_entries,) = struct.unpack(endian + "H", fh.read(2))
        for _ in range(n_entries):
            entry = fh.read(12)
            tag, _ftype, _count = struct.unpack(endian + "HHI", entry[:8])
            if tag == 259:  # Compression
                return struct.unpack(endian + "H", entry[8:10])[0]
    return None


def check_raw_capture(path: str | Path) -> RawCheck:
    p = Path(path)
    with open(p, "rb") as fh:
        magic = fh.read(12)
    if magic.startswith(b"\x89PNG\r\n\x1a\n"):
        return RawCheck(ok=True, format="PNG (lossless)")
    if magic.startswith(b"\xff\x0a") or magic[4:12] == b"JXL \r\n\x87\n":
        return RawCheck(ok=True, format="JPEG XL (verify lossless mode at pilot)")
    if magic.startswith(b"\xff\xd8\xff"):
        return RawCheck(
            ok=False, format="JPEG",
            reason="JPEG is lossy: not acceptable as a raw capture format",
            hint=CONVERSION_HINT,
        )
    if magic[:2] in (b"II", b"MM"):
        comp = _tiff_compression(p)
        if comp in _TIFF_LOSSLESS_COMPRESSIONS:
            return RawCheck(ok=True, format=f"TIFF {_TIFF_LOSSLESS_COMPRESSIONS[comp]}")
        if comp in _TIFF_UNCOMPRESSED:
            return RawCheck(
                ok=False, format="TIFF uncompressed",
                reason="uncompressed TIFF refused: raw captures must be lossless-COMPRESSED (P1)",
                hint=CONVERSION_HINT,
            )
        return RawCheck(
            ok=False, format=f"TIFF compression={comp}",
            reason="TIFF compression is not a recognized lossless scheme",
            hint=CONVERSION_HINT,
        )
    return RawCheck(
        ok=False, format=None,
        reason="unrecognized raw capture format",
        hint=CONVERSION_HINT,
    )


# -------------------------------------------------------------------- capacity

def record_projection(conn: sqlite3.Connection, session: str, bytes_by_class: dict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO storage_projections(id, session, recorded_at, bytes_by_class)"
            " VALUES (?, ?, datetime('now'), ?)",
            (new_id("sp"), session, json.dumps(bytes_by_class)),
        )


def check_capacity(
    conn: sqlite3.Connection,
    cfg: StacksConfig,
    usage: tuple[int, int] | None = None,
) -> dict:
    """usage = (used_bytes, total_bytes); measured from the files root when
    omitted. Crossing the threshold opens ONE add-storage job (planned
    event), idempotent on the threshold crossing."""
    if usage is None:
        du = shutil.disk_usage(cfg.files_root)
        used, total = du.used, du.total
    else:
        used, total = usage
    fraction = used / total if total else 0.0
    status = {"used": used, "total": total, "fraction": fraction, "threshold": cfg.capacity_threshold}
    if fraction >= cfg.capacity_threshold:
        from stacks.jobs.queue import enqueue

        job_id = enqueue(
            conn,
            kind="add-storage",
            payload={"fraction": round(fraction, 4), "files_root": str(cfg.files_root)},
            idempotency_key=f"add-storage:{cfg.files_root}",
        )
        status["add_storage_job"] = job_id
    return status
