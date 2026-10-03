"""Generation activation (SPEC 3.5): build -> verify -> activate -> GC.

Replaces "insert N+1, delete N". The candidate chunk set for generation N+1
is built INACTIVE (search never sees it), verified (counts, spot retrieval,
auth-label consistency), then activated by flipping the document's
generation pointer in one SQLite transaction together with an outbox row
that garbage-collects generation N. Workers carry the generation they
serve; a stale worker is rejected at the pointer. Interrupted activations
reconcile from the outbox on restart; interrupted BUILDS leave only
inactive chunks, which the next build clears.

Chunking (9.2, phase-1 shape): the default representation of EVERY revision
is indexed; chunks are page-bounded, ~800 whitespace tokens with 100
overlap, never across pages; revision identity travels in chunk metadata.
"""

from __future__ import annotations

import json
import sqlite3

from stacks.core import audit, outbox
from stacks.core.ids import new_id

CHUNK_TOKENS = 800
CHUNK_OVERLAP = 100


class GenerationError(Exception):
    pass


class StaleGenerationError(GenerationError):
    """The generation a worker carries is not the document's active one."""


def chunk_text(text: str, max_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    tokens = text.split()
    if not tokens:
        return []
    if len(tokens) <= max_tokens:
        return [" ".join(tokens)]
    step = max_tokens - overlap
    return [" ".join(tokens[i : i + max_tokens]) for i in range(0, len(tokens) - overlap, step)]


def active_generation(conn: sqlite3.Connection, document_id: str) -> int:
    row = conn.execute(
        "SELECT active_generation FROM document_generations WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    return row["active_generation"] if row else 0


def assert_current(conn: sqlite3.Connection, document_id: str, generation: int) -> None:
    """Serving-side stale-worker rejection: the pointer is the authority."""
    current = active_generation(conn, document_id)
    if generation != current:
        raise StaleGenerationError(
            f"document {document_id}: generation {generation} is stale (active {current})"
        )


def _revision_default_copy(conn: sqlite3.Connection, revision_id: str) -> sqlite3.Row | None:
    """The copy indexed for a revision: the document default when it lives in
    this revision, else the best-available deterministic stand-in (full
    within-revision selection is 6.4 behavior arriving with the pilot)."""
    return conn.execute(
        "SELECT * FROM copies WHERE revision_id = ?"
        " ORDER BY is_document_default DESC, superseded ASC,"
        " COALESCE(fitness_text, -1) DESC, created_at ASC, id ASC LIMIT 1",
        (revision_id,),
    ).fetchone()


def build_generation(conn: sqlite3.Connection, document_id: str) -> tuple[int, int]:
    """Build the INACTIVE candidate chunk set; returns (generation, chunks)."""
    if conn.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone() is None:
        raise GenerationError(f"document not found: {document_id}")
    target = active_generation(conn, document_id) + 1
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO document_generations(document_id, active_generation, updated_at)"
            " VALUES (?, 0, datetime('now'))",
            (document_id,),
        )
        # clear leftovers from any interrupted prior build (inactive, unseen)
        conn.execute(
            "DELETE FROM chunks WHERE document_id = ? AND generation >= ?",
            (document_id, target),
        )
        total = 0
        revisions = conn.execute(
            "SELECT * FROM revisions WHERE document_id = ? ORDER BY ordinal", (document_id,)
        ).fetchall()
        for rev in revisions:
            copy = _revision_default_copy(conn, rev["id"])
            if copy is None:
                continue
            pages = conn.execute(
                "SELECT * FROM pages WHERE copy_id = ? AND extracted_text IS NOT NULL"
                " AND extracted_text != '' ORDER BY pdf_index",
                (copy["id"],),
            ).fetchall()
            for page in pages:
                meta = {
                    "revision_label": rev["label"],
                    "revision_ordinal": rev["ordinal"],
                    "source_class": copy["source_class"],
                    "superseded_copy": bool(copy["superseded"]),
                    "text_origin": page["text_origin"],
                    "default_state": copy["default_state"],
                }
                for seq, piece in enumerate(chunk_text(page["extracted_text"])):
                    conn.execute(
                        "INSERT INTO chunks(id, document_id, revision_id, copy_id, page_id,"
                        " page_number, generation, seq, text, metadata)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (new_id("ch"), document_id, rev["id"], copy["id"], page["id"],
                         page["pdf_index"] + 1, target, seq, piece, json.dumps(meta)),
                    )
                    total += 1
    return target, total


def verify_generation(conn: sqlite3.Connection, document_id: str, generation: int) -> dict:
    """Counts, spot retrieval, auth-label consistency; raises on failure."""
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ? AND generation = ?",
        (document_id, generation),
    ).fetchone()["n"]
    has_text = conn.execute(
        "SELECT COUNT(*) AS n FROM pages p JOIN copies c ON c.id = p.copy_id"
        " JOIN revisions r ON r.id = c.revision_id"
        " WHERE r.document_id = ? AND p.extracted_text IS NOT NULL AND p.extracted_text != ''",
        (document_id,),
    ).fetchone()["n"]
    if has_text and count == 0:
        raise GenerationError(f"document {document_id} gen {generation}: no chunks built from {has_text} text pages")
    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks ch"
        " LEFT JOIN revisions r ON r.id = ch.revision_id AND r.document_id = ch.document_id"
        " WHERE ch.document_id = ? AND ch.generation = ? AND r.id IS NULL",
        (document_id, generation),
    ).fetchone()["n"]
    if orphans:
        raise GenerationError(f"document {document_id} gen {generation}: {orphans} chunks with broken identity")
    spot_ok = True
    sample = conn.execute(
        "SELECT rowid, text FROM chunks WHERE document_id = ? AND generation = ? LIMIT 1",
        (document_id, generation),
    ).fetchone()
    if sample is not None:
        token = next((t for t in sample["text"].split() if len(t) >= 4 and t.isalnum()), None)
        if token:
            hit = conn.execute(
                "SELECT f.rowid FROM chunks_fts f JOIN chunks ch ON ch.rowid = f.rowid"
                " WHERE chunks_fts MATCH ? AND ch.document_id = ? AND ch.generation = ? LIMIT 1",
                (f'"{token}"', document_id, generation),
            ).fetchone()
            spot_ok = hit is not None
    if not spot_ok:
        raise GenerationError(f"document {document_id} gen {generation}: spot retrieval failed")
    return {"chunks": count, "text_pages": has_text, "spot_retrieval": spot_ok}


def activate_generation(
    conn: sqlite3.Connection, document_id: str, generation: int, actor: str
) -> None:
    """Flip the pointer in ONE transaction (+outbox GC row + audit event).

    Stale-worker rejection: only active+1 may activate; a worker that built
    against a pointer that has since moved is rejected here."""
    if conn.in_transaction:
        raise GenerationError("activate_generation requires no open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = active_generation(conn, document_id)
        if generation != current + 1:
            raise StaleGenerationError(
                f"document {document_id}: cannot activate {generation}, active is {current}"
            )
        conn.execute(
            "UPDATE document_generations SET active_generation = ?, updated_at = datetime('now')"
            " WHERE document_id = ?",
            (generation, document_id),
        )
        outbox.enqueue(conn, "chunks-gc", {"document_id": document_id})
        audit.emit(conn, {
            "event": "generation-activated", "actor": actor,
            "object": {"kind": "document", "id": document_id},
            "detail": {"generation": generation},
        })
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _gc_handler(conn: sqlite3.Connection, payload: dict) -> None:
    """Delete chunks below the ACTIVE pointer (read at apply time, so the
    handler is idempotent and correct however late it runs)."""
    doc = payload["document_id"]
    with conn:
        conn.execute(
            "DELETE FROM chunks WHERE document_id = ?"
            " AND generation < (SELECT active_generation FROM document_generations"
            "                   WHERE document_id = ?)",
            (doc, doc),
        )


def handlers() -> dict:
    return {"chunks-gc": _gc_handler}


def reindex_document(conn: sqlite3.Connection, document_id: str, actor: str) -> dict:
    """build -> verify -> activate; GC applies on the next dispatch."""
    generation, count = build_generation(conn, document_id)
    report = verify_generation(conn, document_id, generation)
    activate_generation(conn, document_id, generation, actor)
    return {"generation": generation, **report}
