from __future__ import annotations

import json

import pymupdf
import pytest

from stacks.assets.intake import PhiRefused
from stacks.auth.model import AuthError, get_principal_by_name
from stacks.catalog.ingest import ingest_pdf
from stacks.core import db as dbmod
from stacks.core.config import init_config_root
from stacks.search.fts import search
from tests import fixture_builder as fb


def make_manual(path, lines):
    doc = pymupdf.open()
    page = doc.new_page(width=fb.PAGE_W_PT, height=fb.PAGE_H_PT)
    fb.draw_text_page(page, lines)
    page2 = doc.new_page(width=fb.PAGE_W_PT, height=fb.PAGE_H_PT)
    fb.draw_text_page(page2, fb.english_paragraphs(20))
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def env(tmp_path):
    cfg = init_config_root(
        tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
        mount_id="vol-test", write_marker=True,
    )
    conn = dbmod.open_database(cfg.db_path, check_local=False)
    owner = get_principal_by_name(conn, "owner")
    shop = get_principal_by_name(conn, "shop-session")
    yield conn, cfg, owner, shop
    conn.close()


@pytest.fixture()
def manual_pdf(tmp_path):
    return make_manual(
        tmp_path / "galaga.pdf",
        ["Galaga service manual covering the isolation transformer and",
         "the wiring harness for connector J1 and fuse F1 on the power supply."],
    )


class TestIngest:
    def test_commit_creates_catalog_rows(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        res = ingest_pdf(
            conn, cfg, owner, manual_pdf,
            title="Galaga Service Manual", primary_domain="archive",
            sensitivity="internal", source_class="ingest_folder", language="eng",
        )
        assert not res.duplicate
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (res.document_id,)).fetchone()
        assert doc["title"] == "Galaga Service Manual"
        copy = conn.execute("SELECT * FROM copies WHERE id=?", (res.copy_id,)).fetchone()
        assert copy["page_count"] == 2
        assert copy["assessment_mode"] == "screening"
        assert copy["is_document_default"] == 1
        assert copy["default_state"] == "provisional"
        assert copy["default_actor"] == "system:first-copy-rule"
        pages = conn.execute("SELECT * FROM pages WHERE copy_id=? ORDER BY pdf_index", (res.copy_id,)).fetchall()
        assert len(pages) == 2
        assert pages[0]["text_origin"] == "native-text"
        assert "transformer" in pages[0]["extracted_text"]
        assert conn.execute("SELECT COUNT(*) FROM analysis_runs WHERE copy_id=?", (res.copy_id,)).fetchone()[0] == 1
        proj = conn.execute("SELECT bytes_by_class FROM storage_projections").fetchone()
        assert json.loads(proj[0])["master"] > 0

    def test_duplicate_same_context(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        r1 = ingest_pdf(conn, cfg, owner, manual_pdf, title="Galaga", primary_domain="archive",
                        sensitivity="internal", language="eng")
        r2 = ingest_pdf(conn, cfg, owner, manual_pdf, title="Galaga again", primary_domain="archive",
                        sensitivity="internal", language="eng")
        assert r2.duplicate and r2.copy_id == r1.copy_id
        assert conn.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1

    def test_same_bytes_different_context_share_asset_not_document(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        r1 = ingest_pdf(conn, cfg, owner, manual_pdf, title="Shop copy", primary_domain="archive",
                        sensitivity="internal", language="eng")
        r2 = ingest_pdf(conn, cfg, owner, manual_pdf, title="Arcade copy", primary_domain="paradise",
                        sensitivity="confidential", language="eng")
        assert not r2.duplicate
        assert r2.asset_id == r1.asset_id  # one asset, shared bytes
        assert r2.document_id != r1.document_id  # separate business contexts
        assert conn.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 1

    def test_phi_refused_creates_nothing(self, env, tmp_path):
        conn, cfg, owner, _ = env
        bad = make_manual(tmp_path / "phi.pdf",
                          ["Patient Name: John Smith, MRN 445-221, date of birth 1970-01-01"])
        with pytest.raises(PhiRefused):
            ingest_pdf(conn, cfg, owner, bad, title="x", primary_domain="personal",
                       sensitivity="restricted", language="eng")
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM phi_refusals").fetchone()[0] == 1

    def test_submit_verb_required(self, env, manual_pdf):
        conn, cfg, _, shop = env
        with pytest.raises(AuthError, match="lacks verb"):
            ingest_pdf(conn, cfg, shop, manual_pdf, title="x", primary_domain="archive",
                       sensitivity="internal", language="eng")

    def test_unknown_domain_rejected(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        from stacks.catalog.ingest import IngestError

        with pytest.raises(IngestError, match="unknown domain"):
            ingest_pdf(conn, cfg, owner, manual_pdf, title="x", primary_domain="warehouse",
                       sensitivity="internal", language="eng")

    def test_applicability_recorded(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        res = ingest_pdf(conn, cfg, owner, manual_pdf, title="Galaga", primary_domain="archive",
                         sensitivity="internal", language="eng",
                         applicability=(("game", "galaga"), ("game", "gallag")))
        rows = conn.execute(
            "SELECT ref FROM document_applicability WHERE document_id=? ORDER BY ref",
            (res.document_id,),
        ).fetchall()
        assert [r["ref"] for r in rows] == ["galaga", "gallag"]


class TestSearch:
    def test_find_by_technical_token(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        res = ingest_pdf(conn, cfg, owner, manual_pdf, title="Galaga Service Manual",
                         primary_domain="archive", sensitivity="internal", language="eng")
        hits = search(conn, owner, "isolation transformer")
        assert hits and hits[0].document_id == res.document_id
        assert hits[0].page_number == 1
        assert "transformer" in hits[0].snippet.lower()
        assert hits[0].citation["locator"] == {"type": "page", "page": 1}
        assert "provisional" in hits[0].badges

    def test_central_filter_on_search_surface(self, env, manual_pdf, tmp_path):
        conn, cfg, owner, shop = env
        ingest_pdf(conn, cfg, owner, manual_pdf, title="Shop manual", primary_domain="archive",
                   sensitivity="internal", language="eng")
        secret = make_manual(tmp_path / "s.pdf",
                             ["The secret isolation transformer ledger for the estate."])
        ingest_pdf(conn, cfg, owner, secret, title="Private notes", primary_domain="personal",
                   sensitivity="restricted", language="eng")
        owner_hits = search(conn, owner, "transformer")
        shop_hits = search(conn, shop, "transformer")
        assert {h.title for h in owner_hits} == {"Shop manual", "Private notes"}
        assert {h.title for h in shop_hits} == {"Shop manual"}

    def test_multi_domain_document_found_via_any_granted_domain(self, env, tmp_path):
        conn, cfg, owner, shop = env
        shared = make_manual(tmp_path / "lease.pdf",
                             ["Shared insurance certificate for the arcade flyback stock."])
        ingest_pdf(conn, cfg, owner, shared, title="Shared insurance", primary_domain="paradise",
                   additional_domains=("archive",), sensitivity="internal", language="eng")
        assert [h.title for h in search(conn, shop, "flyback")] == ["Shared insurance"]

    def test_search_verb_required(self, env, conn=None):
        c, cfg, owner, _ = env
        from stacks.auth.model import create_principal

        p = create_principal(c, "p_nosearch", "no-search", "agent", {"archive": "open"}, ["read"])
        with pytest.raises(AuthError, match="lacks verb"):
            search(c, p, "anything")

    def test_bad_fts_syntax_fallback(self, env, manual_pdf):
        conn, cfg, owner, _ = env
        ingest_pdf(conn, cfg, owner, manual_pdf, title="Galaga", primary_domain="archive",
                   sensitivity="internal", language="eng")
        assert search(conn, owner, 'AND NOT (((') == []  # no crash
