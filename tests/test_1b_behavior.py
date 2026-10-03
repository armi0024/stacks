"""Phase 1b behavior tests (SPEC 11.1b): mutation conflict and idempotency
replay; audit chain continuity; authority conflict surfacing; plus the
activation-protocol and derivation-staleness behavior they rest on."""

from __future__ import annotations

import json

import pytest

from stacks.auth.model import AuthError, create_principal, get_principal_by_name
from stacks.catalog import authority as authority_mod
from stacks.catalog import edits
from stacks.catalog.ingest import ingest_pdf
from stacks.core import audit, db as dbmod, outbox
from stacks.core.audit import audit_path, verify_chain
from stacks.core.config import init_config_root
from stacks.core.ids import new_id
from stacks.core.mutations import ConflictError, MutationError, mutate
from stacks.core.runtime import reconcile
from stacks.index import generations
from stacks.search.fts import search
from tests.test_ingest_search import make_manual


@pytest.fixture()
def env(tmp_path):
    cfg = init_config_root(
        tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
        mount_id="vol-test", write_marker=True,
    )
    conn = dbmod.open_database(cfg.db_path, check_local=False)
    yield conn, cfg
    conn.close()


@pytest.fixture()
def doc(env, tmp_path):
    conn, cfg = env
    owner = get_principal_by_name(conn, "owner")
    pdf = make_manual(tmp_path / "m.pdf",
                      ["Zaxxon service manual covering the deflection transformer and fuse F3."])
    res = ingest_pdf(conn, cfg, owner, pdf, title="Zaxxon Manual", primary_domain="archive",
                     sensitivity="internal", language="eng")
    return conn, cfg, owner, res


def _svc(conn, name, verbs, grants=None):
    return create_principal(
        conn, new_id("prin"), name, "service",
        grants if grants is not None else {"archive": "restricted"}, verbs,
    )


# ------------------------------------------------------- mutation contract

class TestMutationContract:
    def test_version_conflict_is_explicit(self, doc):
        conn, cfg, owner, res = doc
        with pytest.raises(ConflictError) as e:
            edits.ext_write(conn, owner, res.document_id, {"ext.ceo.note": "x"},
                            expected_version=41, idempotency_key="k-conflict")
        assert e.value.current_version == 1 and e.value.expected == 41
        # nothing applied, nothing versioned
        row = conn.execute("SELECT row_version, metadata FROM documents WHERE id=?",
                           (res.document_id,)).fetchone()
        assert row["row_version"] == 1 and json.loads(row["metadata"]) == {}

    def test_idempotency_replay_returns_original(self, doc):
        conn, cfg, owner, res = doc
        first = edits.ext_write(conn, owner, res.document_id, {"ext.ceo.note": "x"},
                                expected_version=1, idempotency_key="k-replay")
        again = edits.ext_write(conn, owner, res.document_id, {"ext.ceo.note": "x"},
                                expected_version=1, idempotency_key="k-replay")
        assert not first.replayed and again.replayed
        assert again.result == first.result and again.new_version == first.new_version
        # applied exactly once
        assert conn.execute("SELECT row_version FROM documents WHERE id=?",
                            (res.document_id,)).fetchone()["row_version"] == 2

    def test_failed_apply_leaves_nothing(self, doc):
        conn, cfg, owner, res = doc
        before = conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"]

        def boom(c):
            c.execute("UPDATE documents SET title='clobbered' WHERE id=?", (res.document_id,))
            raise RuntimeError("mid-mutation crash")

        with pytest.raises(RuntimeError):
            mutate(conn, principal=owner, operation="test", object_kind="document",
                   object_id=res.document_id, expected_version=1,
                   idempotency_key="k-crash", apply_fn=boom)
        row = conn.execute("SELECT title, row_version FROM documents WHERE id=?",
                           (res.document_id,)).fetchone()
        assert row["title"] == "Zaxxon Manual" and row["row_version"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"] == before
        assert conn.execute("SELECT COUNT(*) AS n FROM mutations").fetchone()["n"] == 0

    def test_ext_write_discipline(self, doc):
        conn, cfg, owner, res = doc
        with pytest.raises(MutationError):
            edits.ext_write(conn, owner, res.document_id, {"title": "sneaky"},
                            expected_version=1, idempotency_key="k-bad-key")
        shop = get_principal_by_name(conn, "shop-session")
        with pytest.raises(AuthError):
            edits.ext_write(conn, shop, res.document_id, {"ext.x": 1},
                            expected_version=1, idempotency_key="k-no-verb")
        # ext.* lands in metadata only; interpreted fields untouched
        edits.ext_write(conn, owner, res.document_id,
                        {"ext.ceo.decision": "D-77"}, expected_version=1,
                        idempotency_key="k-ok")
        row = conn.execute("SELECT * FROM documents WHERE id=?", (res.document_id,)).fetchone()
        assert json.loads(row["metadata"])["ext.ceo.decision"] == "D-77"
        assert row["title"] == "Zaxxon Manual" and row["sensitivity"] == "internal"


class TestAccessAndHold:
    def test_widening_requires_admin_and_confirmation(self, doc):
        conn, cfg, owner, res = doc
        curator = _svc(conn, "curator", ["read", "search", "submit"])
        with pytest.raises(AuthError):
            edits.set_access(conn, curator, res.document_id, domains_allowed=cfg.domains,
                             sensitivity="open", expected_version=1,
                             idempotency_key="k-widen-denied")
        with pytest.raises(MutationError):
            edits.set_access(conn, owner, res.document_id, domains_allowed=cfg.domains,
                             sensitivity="open", expected_version=1,
                             idempotency_key="k-widen-unconfirmed")
        out = edits.set_access(conn, owner, res.document_id, domains_allowed=cfg.domains,
                               sensitivity="open", confirm_widen=True, expected_version=1,
                               idempotency_key="k-widen-ok")
        assert out.result["widened"] is True
        reconcile(conn, cfg)
        lines = audit_path(cfg.config_root).read_text().splitlines()
        assert any(json.loads(ln)["event"] == "declassification" for ln in lines)

    def test_narrowing_needs_no_confirmation(self, doc):
        conn, cfg, owner, res = doc
        curator = _svc(conn, "curator2", ["read", "search", "submit"])
        out = edits.set_access(conn, curator, res.document_id, domains_allowed=cfg.domains,
                               sensitivity="confidential", expected_version=1,
                               idempotency_key="k-narrow")
        assert out.result["widened"] is False

    def test_legal_hold_blocked_attempt_is_audited(self, doc):
        conn, cfg, owner, res = doc
        curator = _svc(conn, "curator3", ["read", "search", "submit"])
        with pytest.raises(AuthError):
            edits.set_legal_hold(conn, curator, res.document_id, True,
                                 expected_version=1, idempotency_key="k-hold-denied")
        edits.set_legal_hold(conn, owner, res.document_id, True,
                             expected_version=1, idempotency_key="k-hold")
        assert conn.execute("SELECT legal_hold FROM documents WHERE id=?",
                            (res.document_id,)).fetchone()["legal_hold"] == 1
        reconcile(conn, cfg)
        events = [json.loads(ln)["event"]
                  for ln in audit_path(cfg.config_root).read_text().splitlines()]
        assert "legal-hold-blocked" in events and "legal-hold-set" in events


# ------------------------------------------------------------- audit chain

class TestAuditChain:
    def _events(self, cfg):
        return audit_path(cfg.config_root)

    def test_chain_continuity(self, doc):
        conn, cfg, owner, res = doc
        edits.ext_write(conn, owner, res.document_id, {"ext.a": 1},
                        expected_version=1, idempotency_key="k1")
        edits.ext_write(conn, owner, res.document_id, {"ext.b": 2},
                        expected_version=2, idempotency_key="k2")
        reconcile(conn, cfg)
        report = verify_chain(self._events(cfg))
        assert report.ok and report.lines >= 4  # ingest, canonical, activation, 2 writes

    def test_tampered_line_detected(self, doc):
        conn, cfg, owner, res = doc
        edits.ext_write(conn, owner, res.document_id, {"ext.a": 1},
                        expected_version=1, idempotency_key="k1")
        reconcile(conn, cfg)
        path = self._events(cfg)
        lines = path.read_text().splitlines()
        victim = json.loads(lines[1])
        victim["actor"] = "nobody"  # rewrite history
        lines[1] = json.dumps(victim, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        report = verify_chain(path)
        assert not report.ok and report.break_line == 2 and "tampered" in report.reason

    def test_removed_line_breaks_chain(self, doc):
        conn, cfg, owner, res = doc
        edits.ext_write(conn, owner, res.document_id, {"ext.a": 1},
                        expected_version=1, idempotency_key="k1")
        reconcile(conn, cfg)
        path = self._events(cfg)
        lines = path.read_text().splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n")
        assert not verify_chain(path).ok

    def test_audit_rides_the_outbox(self, doc):
        """Audit intent commits with the change; the stream materializes on
        dispatch — and a crash before dispatch replays on reconcile."""
        conn, cfg, owner, res = doc
        pend_before = outbox.status(conn)["pending"]
        edits.ext_write(conn, owner, res.document_id, {"ext.a": 1},
                        expected_version=1, idempotency_key="k1")
        assert outbox.status(conn)["pending"] == pend_before + 1
        # "crash" here: nothing dispatched yet; a fresh process reconciles
        reconcile(conn, cfg)
        assert outbox.status(conn)["pending"] == 0
        assert verify_chain(self._events(cfg)).ok


# ------------------------------------------------- authority and conflicts

class TestAuthority:
    def test_vendor_claim_cannot_manufacture_approval(self, doc):
        conn, cfg, owner, res = doc
        vendor = _svc(conn, "vendor", ["read", "search", "submit"])  # no authority verb
        with pytest.raises(AuthError):
            authority_mod.write_authority(conn, vendor, object_kind="document",
                                          object_id=res.document_id, status="approved-for-use",
                                          idempotency_key="k-vendor")
        nograant = _svc(conn, "other-domain-svc", ["authority", "read"], grants={"paradise": "restricted"})
        with pytest.raises(AuthError):
            authority_mod.write_authority(conn, nograant, object_kind="document",
                                          object_id=res.document_id, status="approved-for-use",
                                          idempotency_key="k-nogrhealth")

    def test_owner_outranks_services(self, doc):
        conn, cfg, owner, res = doc
        svc = _svc(conn, "svc-a", ["authority", "read"])
        authority_mod.write_authority(conn, svc, object_kind="document",
                                      object_id=res.document_id, status="approved-for-use",
                                      idempotency_key="k-svc")
        authority_mod.write_authority(conn, owner, object_kind="document",
                                      object_id=res.document_id, status="retired",
                                      idempotency_key="k-owner")
        r = authority_mod.resolve(conn, "document", res.document_id)
        assert not r.conflict and r.winner["principal"] == "owner" and r.winner["status"] == "retired"

    def test_earlier_effective_never_displaces_later(self, doc):
        conn, cfg, owner, res = doc
        svc = _svc(conn, "svc-b", ["authority", "read"])
        authority_mod.write_authority(conn, svc, object_kind="document",
                                      object_id=res.document_id, status="approved-for-use",
                                      effective_date="2026-06-01", idempotency_key="k1")
        authority_mod.write_authority(conn, svc, object_kind="document",
                                      object_id=res.document_id, status="retired",
                                      effective_date="2025-01-01", idempotency_key="k2")
        r = authority_mod.resolve(conn, "document", res.document_id)
        assert not r.conflict
        assert r.winner["effective_date"] == "2026-06-01"  # late-arriving old news loses

    def test_conflict_surfaces_visibly(self, doc):
        conn, cfg, owner, res = doc
        a = _svc(conn, "svc-c", ["authority", "read", "search"])
        b = _svc(conn, "svc-d", ["authority", "read", "search"])
        authority_mod.write_authority(conn, a, object_kind="document",
                                      object_id=res.document_id, status="approved-for-use",
                                      effective_date="2026-09-01", idempotency_key="k-a")
        out = authority_mod.write_authority(conn, b, object_kind="document",
                                            object_id=res.document_id, status="retired",
                                            effective_date="2026-01-01", idempotency_key="k-b")
        assert out["conflict"] is True
        r = authority_mod.resolve(conn, "document", res.document_id)
        assert r.conflict and r.winner is None and len(r.conflicting) == 2
        # review queue entry
        job = conn.execute(
            "SELECT * FROM jobs WHERE kind='review-authority-conflict'").fetchone()
        assert job is not None
        # audit event
        reconcile(conn, cfg)
        events = [json.loads(ln)["event"]
                  for ln in audit_path(cfg.config_root).read_text().splitlines()]
        assert "authority-conflict" in events

    def test_supersede_resolves(self, doc):
        conn, cfg, owner, res = doc
        a = _svc(conn, "svc-e", ["authority", "read"])
        first = authority_mod.write_authority(conn, a, object_kind="document",
                                              object_id=res.document_id, status="approved-for-use",
                                              effective_date="2026-09-01", idempotency_key="k1")
        authority_mod.write_authority(conn, a, object_kind="document",
                                      object_id=res.document_id, status="retired",
                                      effective_date="2026-10-01",
                                      supersedes=first["record_id"], idempotency_key="k2")
        r = authority_mod.resolve(conn, "document", res.document_id)
        assert not r.conflict and r.winner["status"] == "retired"

    def test_write_authority_idempotent_replay(self, doc):
        conn, cfg, owner, res = doc
        out1 = authority_mod.write_authority(conn, owner, object_kind="document",
                                             object_id=res.document_id, status="approved-for-use",
                                             idempotency_key="k-same")
        out2 = authority_mod.write_authority(conn, owner, object_kind="document",
                                             object_id=res.document_id, status="approved-for-use",
                                             idempotency_key="k-same")
        assert out2.get("replayed") and out2["record_id"] == out1["record_id"]
        n = conn.execute("SELECT COUNT(*) AS n FROM authority_records").fetchone()["n"]
        assert n == 1


# ------------------------------------------------------ pins in retrieval

class TestPinnedSearch:
    def _second_revision_with_copy(self, conn, owner, res, text):
        out = edits.add_revision(conn, owner, res.document_id, label="Rev B",
                                 expected_version=1, idempotency_key="k-rev-b")
        rev_b = out.result["revision_id"]
        copy_id = new_id("cp")
        with conn:
            conn.execute(
                "INSERT INTO copies(id, revision_id, source_class, format, sha256,"
                " provenance, created_at) VALUES (?, ?, 'ia', 'pdf', ?, '{}', datetime('now'))",
                (copy_id, rev_b, new_id("sha")),
            )
            conn.execute(
                "INSERT INTO pages(id, copy_id, pdf_index, page_type, content_class,"
                " text_origin, extracted_text)"
                " VALUES (?, ?, 0, 'native-text', 'text', 'native-text', ?)",
                (new_id("pg"), copy_id, text),
            )
        return rev_b

    def test_pinned_revision_ranks_above_and_badges(self, doc):
        conn, cfg, owner, res = doc
        rev_b = self._second_revision_with_copy(
            conn, owner, res, "Rev B deflection transformer notes, updated values.")
        generations.reindex_document(conn, res.document_id, actor="test")
        # pin Rev A (the ingested revision) as approved-for-use
        authority_mod.write_authority(conn, owner, object_kind="revision",
                                      object_id=res.revision_id, status="approved-for-use",
                                      idempotency_key="k-pin")
        hits = search(conn, owner, "transformer", limit=10)
        assert {h.revision_id for h in hits} == {res.revision_id, rev_b}
        assert hits[0].revision_id == res.revision_id and "pinned" in hits[0].badges
        rev_b_hits = [h for h in hits if h.revision_id == rev_b]
        assert all("superseded-revision" in h.badges for h in rev_b_hits)

    def test_conflicting_pin_is_reported_not_resolved(self, doc):
        conn, cfg, owner, res = doc
        a = _svc(conn, "svc-f", ["authority", "read", "search"])
        b = _svc(conn, "svc-g", ["authority", "read", "search"])
        authority_mod.write_authority(conn, a, object_kind="revision",
                                      object_id=res.revision_id, status="approved-for-use",
                                      effective_date="2026-09-01", idempotency_key="k-a")
        authority_mod.write_authority(conn, b, object_kind="revision",
                                      object_id=res.revision_id, status="retired",
                                      effective_date="2026-01-01", idempotency_key="k-b")
        hits = search(conn, owner, "transformer", limit=10)
        assert hits and all("authority-conflict" in h.badges for h in hits)


# -------------------------------------------------- generation activation

class TestGenerations:
    def test_ingest_activates_generation_one(self, doc):
        conn, cfg, owner, res = doc
        assert generations.active_generation(conn, res.document_id) == 1
        n = conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=? AND generation=1",
                         (res.document_id,)).fetchone()["n"]
        assert n > 0

    def test_rebuild_flips_pointer_and_gc_runs_via_outbox(self, doc):
        conn, cfg, owner, res = doc
        out = generations.reindex_document(conn, res.document_id, actor="test")
        assert out["generation"] == 2
        assert generations.active_generation(conn, res.document_id) == 2
        # old generation still present until dispatch (GC rides the outbox)
        old = conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=? AND generation=1",
                           (res.document_id,)).fetchone()["n"]
        assert old > 0
        reconcile(conn, cfg)
        old = conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=? AND generation=1",
                           (res.document_id,)).fetchone()["n"]
        assert old == 0

    def test_interrupted_activation_reconciles(self, doc):
        """Crash between pointer flip and GC: reconcile finishes the job and
        the accepted state is preserved."""
        conn, cfg, owner, res = doc
        gen, _ = generations.build_generation(conn, res.document_id)
        generations.verify_generation(conn, res.document_id, gen)
        generations.activate_generation(conn, res.document_id, gen, actor="test")
        # crash here: outbox row pending, both generations on disk
        hits = search(conn, owner, "transformer", limit=5)
        assert hits  # search already serves ONLY the new active generation
        reconcile(conn, cfg)  # restart
        gens = [r["generation"] for r in conn.execute(
            "SELECT DISTINCT generation FROM chunks WHERE document_id=?", (res.document_id,))]
        assert gens == [gen]
        assert search(conn, owner, "transformer", limit=5)

    def test_stale_worker_rejected_at_the_pointer(self, doc):
        conn, cfg, owner, res = doc
        gen, _ = generations.build_generation(conn, res.document_id)  # worker A builds 2
        generations.reindex_document(conn, res.document_id, actor="worker-b")  # B lands 2 first
        with pytest.raises(generations.StaleGenerationError):
            generations.activate_generation(conn, res.document_id, gen, actor="worker-a")
        with pytest.raises(generations.StaleGenerationError):
            generations.assert_current(conn, res.document_id, 1)

    def test_inactive_generations_invisible_to_search(self, doc):
        conn, cfg, owner, res = doc
        with conn:
            conn.execute(
                "INSERT INTO chunks(id, document_id, revision_id, copy_id, page_id,"
                " page_number, generation, seq, text, metadata)"
                " SELECT ?, document_id, revision_id, copy_id, page_id, page_number,"
                " 99, 0, 'zzzmarker unreleased draft text', '{}'"
                " FROM chunks WHERE document_id=? LIMIT 1",
                (new_id("ch"), res.document_id),
            )
        assert search(conn, owner, "zzzmarker", limit=5) == []


# ------------------------------------------------------------ derivations

class TestDerivations:
    def test_summary_chain_traceable_and_goes_stale(self, doc, tmp_path):
        conn, cfg, owner, res = doc
        summary = ingest_pdf(conn, cfg, owner,
                             make_manual(tmp_path / "s.pdf", ["Summary of the Zaxxon manual."]),
                             title="Zaxxon Summary", primary_domain="archive",
                             sensitivity="internal", language="eng")
        authority_mod.add_derivation(conn, owner, document_id=summary.document_id,
                                     depends_on_kind="document", depends_on_id=res.document_id,
                                     idempotency_key="k-dv")
        rows = authority_mod.stale_derivations(conn, summary.document_id)
        assert len(rows) == 1 and rows[0]["stale"] == 0
        # authority change on the dependency marks the dependent stale
        authority_mod.write_authority(conn, owner, object_kind="document",
                                      object_id=res.document_id, status="retired",
                                      idempotency_key="k-retire")
        rows = authority_mod.stale_derivations(conn, summary.document_id)
        assert rows[0]["stale"] == 1
        reconcile(conn, cfg)
        events = [json.loads(ln)["event"]
                  for ln in audit_path(cfg.config_root).read_text().splitlines()]
        assert "derivation-stale" in events and "derivation-added" in events

    def test_new_revision_marks_dependents_stale(self, doc, tmp_path):
        conn, cfg, owner, res = doc
        summary = ingest_pdf(conn, cfg, owner,
                             make_manual(tmp_path / "s2.pdf", ["Another summary."]),
                             title="Summary 2", primary_domain="archive",
                             sensitivity="internal", language="eng")
        authority_mod.add_derivation(conn, owner, document_id=summary.document_id,
                                     depends_on_kind="document", depends_on_id=res.document_id,
                                     idempotency_key="k-dv2")
        out = edits.add_revision(conn, owner, res.document_id, label="Rev B",
                                 expected_version=1, idempotency_key="k-revb")
        assert out.result["dependents_marked_stale"] == 1
        assert authority_mod.stale_derivations(conn, summary.document_id)[0]["stale"] == 1
