"""Ingest commit: file -> asset -> document/revision/copy -> analysis -> index.

The commit path (ingest_folder-style, SPEC 5/8):
  1. submit verb required; domains/sensitivity validated against config
  2. PHI refusal on the first-pages text sample (fail-closed; image-only
     documents carry no sample at this stage - screening happens again when
     OCR text exists)
  3. SHA-256 dedup: identical bytes share ONE asset; identical bytes in the
     SAME document context are a duplicate submission; in a DIFFERENT
     context they are a separate document referencing the same asset (4.1)
  4. asset stored content-addressed (mount verified)
  5. document + revision + copy created; analyzer runs (screening default);
     per-page state + extracted text recorded (FTS triggers index it)
  6. first copy of a document becomes the PROVISIONAL document_default by
     system rule (a record never makes itself canonical; the rule is the
     recorded actor)
  7. storage projection recorded for the session
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pymupdf

from stacks.analyzer.analyze import analyze_pdf
from stacks.analyzer.config import AnalyzerSettings
from stacks.assets.intake import enforce_phi_refusal, record_projection
from stacks.assets.store import AssetStore
from stacks.auth.filter import require_verb
from stacks.auth.model import SENSITIVITY_RANK, AuthError, Principal
from stacks.core import audit
from stacks.core.config import StacksConfig
from stacks.core.ids import new_id, sha256_file
from stacks.index import generations


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    document_id: str
    revision_id: str
    copy_id: str
    asset_id: str
    duplicate: bool  # same bytes already committed in this document context
    recommended_action: str | None


def _text_sample(path: str, max_pages: int = 3, max_chars: int = 20000) -> str:
    doc = pymupdf.open(path)
    parts: list[str] = []
    try:
        for page in doc.pages(0, min(max_pages, doc.page_count)):
            parts.append(page.get_text())
            if sum(len(p) for p in parts) > max_chars:
                break
    finally:
        doc.close()
    return " ".join(parts)[:max_chars]


def ingest_pdf(
    conn: sqlite3.Connection,
    cfg: StacksConfig,
    principal: Principal,
    path: str,
    *,
    title: str,
    primary_domain: str,
    sensitivity: str,
    additional_domains: tuple = (),
    source_class: str = "ingest_folder",
    language: str | None = None,
    revision_label: str | None = None,
    source_urls: list[str] | None = None,
    applicability: tuple = (),  # ((kind, ref), ...)
    mode: str = "screening",
    analyzer_settings: AnalyzerSettings | None = None,
    session: str | None = None,
) -> IngestResult:
    require_verb(principal, "submit")
    for d in (primary_domain, *additional_domains):
        if d not in cfg.domains:
            raise IngestError(f"unknown domain {d!r}; configured: {list(cfg.domains)}")
    if sensitivity not in SENSITIVITY_RANK:
        raise IngestError(f"unknown sensitivity {sensitivity!r}")
    if principal.ceiling(primary_domain) is None:
        raise AuthError(f"principal {principal.name} holds no grant for domain {primary_domain!r}")

    enforce_phi_refusal(conn, path, _text_sample(path))

    sha = sha256_file(path)
    dup = conn.execute(
        "SELECT c.id AS copy_id, r.id AS revision_id, d.id AS document_id, c.recommended_action"
        " FROM copies c JOIN revisions r ON r.id = c.revision_id"
        " JOIN documents d ON d.id = r.document_id"
        " WHERE c.sha256 = ? AND d.primary_domain = ?",
        (sha, primary_domain),
    ).fetchone()
    store = AssetStore(conn, cfg)
    if dup is not None:
        asset = store.find_by_sha256(sha)
        if source_urls and asset:
            store._merge_source_urls(asset.id, source_urls)
        return IngestResult(
            document_id=dup["document_id"],
            revision_id=dup["revision_id"],
            copy_id=dup["copy_id"],
            asset_id=asset.id if asset else "",
            duplicate=True,
            recommended_action=dup["recommended_action"],
        )

    asset, _created = store.put_file(
        path, role="master", media_type="application/pdf",
        source_urls=source_urls, keep_md5=True,
    )

    doc_id, rev_id, copy_id = new_id("doc"), new_id("rev"), new_id("cp")
    with conn:
        conn.execute(
            "INSERT INTO documents(id, title, language, primary_domain, additional_domains,"
            " sensitivity, recorded_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'))",
            (doc_id, title, language, primary_domain, json.dumps(list(additional_domains)), sensitivity),
        )
        for kind, ref in applicability:
            conn.execute(
                "INSERT OR IGNORE INTO document_applicability(document_id, kind, ref) VALUES (?, ?, ?)",
                (doc_id, kind, ref),
            )
        conn.execute(
            "INSERT INTO revisions(id, document_id, label, ordinal, recorded_at)"
            " VALUES (?, ?, ?, 0, datetime('now'))",
            (rev_id, doc_id, revision_label or "base"),
        )
        conn.execute(
            "INSERT INTO copies(id, revision_id, source_class, format, sha256, md5,"
            " provenance, created_at)"
            " VALUES (?, ?, ?, 'pdf', ?, ?, ?, datetime('now'))",
            (copy_id, rev_id, source_class, sha, asset.md5,
             json.dumps({"ingested_by": principal.name, "path_hint": "intake"})),
        )
        conn.execute(
            "INSERT INTO copy_assets(copy_id, asset_id, relation) VALUES (?, ?, 'primary')",
            (copy_id, asset.id),
        )
        audit.emit(conn, {
            "event": "ingest-commit", "actor": principal.name,
            "object": {"kind": "document", "id": doc_id},
            "detail": {"copy_id": copy_id, "asset_id": asset.id, "sha256": sha,
                       "source_class": source_class, "domain": primary_domain,
                       "sensitivity": sensitivity},
        })

    _analyze_and_record(conn, cfg, copy_id, store.abs_path(asset), mode, language, analyzer_settings)
    _assign_provisional_default(conn, doc_id, copy_id)
    record_projection(conn, session or f"ingest:{copy_id}", {"master": asset.size_bytes})
    # index through the activation protocol: generation 1 built inactive,
    # verified, then activated — search never sees a partial index (3.5)
    generations.reindex_document(conn, doc_id, actor=principal.name)

    action = conn.execute(
        "SELECT recommended_action FROM copies WHERE id = ?", (copy_id,)
    ).fetchone()["recommended_action"]
    return IngestResult(doc_id, rev_id, copy_id, asset.id, False, action)


def _analyze_and_record(
    conn: sqlite3.Connection,
    cfg: StacksConfig,
    copy_id: str,
    pdf_path,
    mode: str,
    language: str | None,
    settings: AnalyzerSettings | None,
) -> None:
    from stacks.analyzer.report import to_dict

    # the catalog's language field is the IDENTITY default, not an override:
    # confident detection may still disagree with it (disposition 2)
    result = analyze_pdf(
        str(pdf_path), settings=settings, mode=mode, identity_language=language,
    )
    report = to_dict(result)
    sc = result.scores
    with conn:
        conn.execute(
            "INSERT INTO analysis_runs(id, copy_id, config_hash, mode, coverage, report, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (new_id("ar"), copy_id, result.config_hash, mode,
             json.dumps(report["coverage"]), json.dumps(report)),
        )
        conn.execute(
            "UPDATE copies SET page_count=?, assessment_mode=?, analysis_config_hash=?,"
            " completeness_status=?, missing_fraction=?,"
            " if_text=?, tq_text=?, if_schem=?, tq_schem=?,"
            " image_adequate=?, text_adequate=?, fitness_text=?, fitness_schem=?,"
            " pages_below_adequacy=?, risk_flags=?, recommended_action=?, action_labels=?"
            " WHERE id=?",
            (
                result.page_count, mode, result.config_hash,
                result.completeness.status, result.completeness.missing_fraction,
                sc.if_text, sc.tq_text, sc.if_schem, sc.tq_schem,
                None if sc.image_adequate() is None else int(sc.image_adequate()),
                None if sc.text_adequate() is None else int(sc.text_adequate()),
                sc.fitness_text, sc.fitness_schem,
                json.dumps(result.gate_result.pages_below_adequacy),
                json.dumps(result.structural.risk_flags),
                result.recommendation.action,
                json.dumps(result.recommendation.labels),
                copy_id,
            ),
        )
        for p in result.pages:
            conn.execute(
                "INSERT INTO pages(id, copy_id, pdf_index, page_type, content_class,"
                " if_score, tq_score, tq_null_reason, text_origin, extracted_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("pg"), copy_id, p.index, p.page_type, p.content_class,
                    p.if_score, p.tq_score,
                    (p.tq.null_reason if p.tq and p.tq.null else None),
                    p.text_origin, p.extracted_text,
                ),
            )


def _assign_provisional_default(conn: sqlite3.Connection, doc_id: str, copy_id: str) -> None:
    existing = conn.execute(
        "SELECT c.id FROM copies c JOIN revisions r ON r.id = c.revision_id"
        " WHERE r.document_id = ? AND c.is_document_default = 1",
        (doc_id,),
    ).fetchone()
    if existing is not None:
        return
    with conn:
        conn.execute(
            "UPDATE copies SET is_document_default=1, default_state='provisional',"
            " default_actor='system:first-copy-rule', default_at=datetime('now')"
            " WHERE id=?",
            (copy_id,),
        )
        audit.emit(conn, {
            "event": "canonical-assignment", "actor": "system:first-copy-rule",
            "object": {"kind": "copy", "id": copy_id},
            "detail": {"document_id": doc_id, "state": "provisional"},
        })
