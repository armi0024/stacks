"""HTTP API (1a: search and page viewing; 1b: the mutation contract).

Every endpoint authenticates a Bearer token through the token module and
routes results through the central filter AT FETCH TIME — including
previously issued links: a URL that worked yesterday re-checks access today.
Denial is indistinguishable from absence (404). Binding to LAN interfaces is
deployment configuration; the app itself never exposes server-local paths.

Write endpoints carry the mutation contract (3.5): idempotency key +
expected prior version in every body; a version mismatch is HTTP 409 with
the current version; a replayed idempotency key returns the original result
with replayed=true. Fetches of confidential/restricted documents are
audited (3.6). Startup reconciles the outbox; each write dispatches it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from stacks.auth import tokens as token_module
from stacks.auth.filter import check_document_access, record_access_event
from stacks.auth.model import AuthError, Principal
from stacks.catalog import authority as authority_mod
from stacks.catalog import edits
from stacks.core import audit, db as dbmod
from stacks.core.config import StacksConfig
from stacks.core.mutations import ConflictError, MutationError
from stacks.core.runtime import reconcile
from stacks.search.fts import search as fts_search

PREVIEW_DPI = 200  # 9.2: the preview render; full-res region endpoint is phase 6
SENSITIVE = ("confidential", "restricted")


class ExtWriteBody(BaseModel):
    values: dict
    expected_version: int
    idempotency_key: str


class AccessBody(BaseModel):
    sensitivity: str | None = None
    primary_domain: str | None = None
    additional_domains: list[str] | None = None
    confirm_widen: bool = False
    expected_version: int
    idempotency_key: str


class LegalHoldBody(BaseModel):
    hold: bool
    expected_version: int
    idempotency_key: str


class RevisionBody(BaseModel):
    label: str
    effective_date: str | None = None
    expected_version: int
    idempotency_key: str


class AuthorityBody(BaseModel):
    object_kind: str
    object_id: str
    status: str
    scope: str | None = None
    effective_date: str | None = None
    supersedes: str | None = None
    idempotency_key: str


class DerivationBody(BaseModel):
    document_id: str
    depends_on_kind: str
    depends_on_id: str
    citation: dict | None = None
    idempotency_key: str


def create_app(cfg: StacksConfig, check_local_db: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # restart reconciliation: replay unapplied outbox rows (3.5)
        conn = dbmod.connect(cfg.db_path, check_local=check_local_db)
        try:
            reconcile(conn, cfg)
        finally:
            conn.close()
        yield

    app = FastAPI(title="stacks", version="0.2.0", lifespan=lifespan)

    @contextmanager
    def db():
        conn = dbmod.connect(cfg.db_path, check_local=check_local_db)
        try:
            yield conn
        finally:
            conn.close()

    def authenticate(request: Request) -> tuple[Principal, str]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        with db() as conn:
            try:
                return token_module.verify(conn, cfg.keys_dir, header.split(" ", 1)[1])
            except token_module.TokenError as e:
                raise HTTPException(status_code=401, detail=str(e)) from e

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/search")
    def search_endpoint(q: str, limit: int = 20, auth=Depends(authenticate)):
        principal, _tid = auth
        with db() as conn:
            try:
                hits = fts_search(conn, principal, q, limit=min(limit, 100))
            except AuthError as e:
                raise HTTPException(status_code=403, detail=str(e)) from e
            return {
                "query": q,
                "hits": [
                    {
                        "document_id": h.document_id,
                        "title": h.title,
                        "revision_id": h.revision_id,
                        "revision_label": h.revision_label,
                        "copy_id": h.copy_id,
                        "page_number": h.page_number,
                        "snippet": h.snippet,
                        "badges": h.badges,
                        "citation": h.citation,
                    }
                    for h in hits
                ],
            }

    @app.get("/documents/{document_id}")
    def document_detail(document_id: str, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            try:
                check_document_access(conn, principal, document_id, verb="read")
            except AuthError as e:
                raise HTTPException(status_code=404, detail="not found") from e
            doc = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            revisions = conn.execute(
                "SELECT * FROM revisions WHERE document_id=? ORDER BY ordinal", (document_id,)
            ).fetchall()
            copies = conn.execute(
                "SELECT c.* FROM copies c JOIN revisions r ON r.id=c.revision_id"
                " WHERE r.document_id=? ORDER BY c.created_at",
                (document_id,),
            ).fetchall()
            pins = conn.execute(
                "SELECT * FROM authority_records WHERE object_kind='document' AND object_id=?"
                " ORDER BY recorded_at",
                (document_id,),
            ).fetchall()
            record_access_event(conn, principal.name, tid, "read", "document", document_id)
            if doc["sensitivity"] in SENSITIVE:
                with conn:  # fetches of confidential/restricted are audited (3.6)
                    audit.emit(conn, {
                        "event": "sensitive-fetch", "actor": principal.name, "token_id": tid,
                        "object": {"kind": "document", "id": document_id},
                        "detail": {"sensitivity": doc["sensitivity"]},
                    })
            pinned, pin_conflict = authority_mod.pinned_revision(conn, document_id)
            return {
                "authority": {"pinned_revision": pinned, "conflict": pin_conflict},
                "document": {
                    "id": doc["id"], "title": doc["title"], "language": doc["language"],
                    "primary_domain": doc["primary_domain"],
                    "additional_domains": json.loads(doc["additional_domains"]),
                    "sensitivity": doc["sensitivity"],
                    "legal_hold": bool(doc["legal_hold"]),
                    "effective_date": doc["effective_date"], "recorded_at": doc["recorded_at"],
                },
                "revisions": [dict(r) for r in revisions],
                "copies": [
                    {
                        "id": c["id"], "revision_id": c["revision_id"],
                        "source_class": c["source_class"], "page_count": c["page_count"],
                        "assessment_mode": c["assessment_mode"],
                        "completeness_status": c["completeness_status"],
                        "if_text": c["if_text"], "tq_text": c["tq_text"],
                        "if_schem": c["if_schem"], "tq_schem": c["tq_schem"],
                        "image_adequate": c["image_adequate"], "text_adequate": c["text_adequate"],
                        "pages_below_adequacy": json.loads(c["pages_below_adequacy"]),
                        "risk_flags": json.loads(c["risk_flags"]),
                        "recommended_action": c["recommended_action"],
                        "action_labels": json.loads(c["action_labels"]),
                        "is_document_default": bool(c["is_document_default"]),
                        "default_state": c["default_state"],
                        "superseded": bool(c["superseded"]),
                    }
                    for c in copies
                ],
                "authority_records": [dict(p) for p in pins],
            }

    @app.get("/copies/{copy_id}/pages/{page_number}/image")
    def page_image(copy_id: str, page_number: int, auth=Depends(authenticate)):
        import pymupdf

        principal, tid = auth
        with db() as conn:
            row = conn.execute(
                "SELECT d.id AS document_id FROM copies c"
                " JOIN revisions r ON r.id=c.revision_id JOIN documents d ON d.id=r.document_id"
                " WHERE c.id=?",
                (copy_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="not found")
            try:
                # auth at fetch, on previously issued links too
                check_document_access(conn, principal, row["document_id"], verb="read")
            except AuthError as e:
                raise HTTPException(status_code=404, detail="not found") from e
            asset = conn.execute(
                "SELECT fa.rel_path FROM copy_assets ca JOIN file_assets fa ON fa.id=ca.asset_id"
                " WHERE ca.copy_id=? AND ca.relation='primary'",
                (copy_id,),
            ).fetchone()
            if asset is None:
                raise HTTPException(status_code=404, detail="not found")
            record_access_event(
                conn, principal.name, tid, "read", "page",
                f"{copy_id}:{page_number}",
            )
        pdf_path = cfg.files_root / asset["rel_path"]
        try:
            doc = pymupdf.open(str(pdf_path))
            try:
                if not 1 <= page_number <= doc.page_count:
                    raise HTTPException(status_code=404, detail="not found")
                pix = doc[page_number - 1].get_pixmap(dpi=PREVIEW_DPI)
                png = pix.tobytes("png")
            finally:
                doc.close()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail="render failed") from e
        return Response(content=png, media_type="image/png")

    # ------------------------------------------------ mutation contract (3.5)

    def _mutation_errors(fn):
        """Run a mutation callable; map contract errors to HTTP."""
        try:
            return fn()
        except ConflictError as e:
            raise HTTPException(status_code=409, detail={
                "error": "version-conflict",
                "object_kind": e.object_kind, "object_id": e.object_id,
                "current_version": e.current_version, "expected": e.expected,
            }) from e
        except (MutationError, authority_mod.AuthorityError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except AuthError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    def _require_document(conn, principal, document_id):
        try:
            check_document_access(conn, principal, document_id, verb="read")
        except AuthError as e:
            raise HTTPException(status_code=404, detail="not found") from e

    @app.post("/documents/{document_id}/ext")
    def ext_write_endpoint(document_id: str, body: ExtWriteBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            _require_document(conn, principal, document_id)
            res = _mutation_errors(lambda: edits.ext_write(
                conn, principal, document_id, body.values,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key, token_id=tid,
            ))
            reconcile(conn, cfg)
            return res.to_json()

    @app.post("/documents/{document_id}/access")
    def access_endpoint(document_id: str, body: AccessBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            _require_document(conn, principal, document_id)
            res = _mutation_errors(lambda: edits.set_access(
                conn, principal, document_id, domains_allowed=cfg.domains,
                sensitivity=body.sensitivity, primary_domain=body.primary_domain,
                additional_domains=tuple(body.additional_domains) if body.additional_domains is not None else None,
                confirm_widen=body.confirm_widen,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key, token_id=tid,
            ))
            reconcile(conn, cfg)
            return res.to_json()

    @app.post("/documents/{document_id}/legal-hold")
    def legal_hold_endpoint(document_id: str, body: LegalHoldBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            _require_document(conn, principal, document_id)
            try:
                res = _mutation_errors(lambda: edits.set_legal_hold(
                    conn, principal, document_id, body.hold,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key, token_id=tid,
                ))
            finally:
                reconcile(conn, cfg)  # a blocked attempt still audits
            return res.to_json()

    @app.post("/documents/{document_id}/revisions")
    def add_revision_endpoint(document_id: str, body: RevisionBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            _require_document(conn, principal, document_id)
            res = _mutation_errors(lambda: edits.add_revision(
                conn, principal, document_id, label=body.label,
                effective_date=body.effective_date,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key, token_id=tid,
            ))
            reconcile(conn, cfg)
            return res.to_json()

    @app.post("/authority")
    def write_authority_endpoint(body: AuthorityBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            res = _mutation_errors(lambda: authority_mod.write_authority(
                conn, principal, object_kind=body.object_kind, object_id=body.object_id,
                status=body.status, scope=body.scope, effective_date=body.effective_date,
                supersedes=body.supersedes,
                idempotency_key=body.idempotency_key, token_id=tid,
            ))
            reconcile(conn, cfg)
            return res

    @app.get("/authority/{object_kind}/{object_id}")
    def authority_resolution(object_kind: str, object_id: str, auth=Depends(authenticate)):
        principal, _tid = auth
        with db() as conn:
            try:
                doc = authority_mod._object_document(conn, object_kind, object_id)
            except authority_mod.AuthorityError as e:
                raise HTTPException(status_code=404, detail="not found") from e
            _require_document(conn, principal, doc["id"])
            return authority_mod.resolve(conn, object_kind, object_id).to_json()

    @app.post("/derivations")
    def add_derivation_endpoint(body: DerivationBody, auth=Depends(authenticate)):
        principal, tid = auth
        with db() as conn:
            _require_document(conn, principal, body.document_id)
            res = _mutation_errors(lambda: authority_mod.add_derivation(
                conn, principal, document_id=body.document_id,
                depends_on_kind=body.depends_on_kind, depends_on_id=body.depends_on_id,
                citation=body.citation,
                idempotency_key=body.idempotency_key, token_id=tid,
            ))
            reconcile(conn, cfg)
            return res

    return app
