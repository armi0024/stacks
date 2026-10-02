from __future__ import annotations

import sqlite3

import pytest

from stacks.core import config as cfgmod
from stacks.core import db as dbmod
from stacks.core.ids import new_id, sha256_file


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_database(tmp_path / "stacks.sqlite", check_local=False)
    yield c
    c.close()


class TestMigrations:
    def test_fresh_init_applies_all(self, tmp_path):
        c = dbmod.open_database(tmp_path / "db.sqlite", check_local=False)
        assert dbmod.current_version(c) >= 2
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {
            "documents", "revisions", "copies", "file_assets", "copy_assets", "pages",
            "page_alignments", "analysis_runs", "physical_holdings", "acquisition_coverage",
            "access_events", "jobs", "outbox", "sources", "settings", "subjects",
            "document_subjects", "group_decisions", "tag_decisions", "subject_trust",
            "authority_records", "derivations", "principals", "tokens",
            "storage_projections", "phi_refusals", "schema_version",
        }
        assert expected <= tables
        c.close()

    def test_reopen_is_noop(self, tmp_path):
        p = tmp_path / "db.sqlite"
        dbmod.open_database(p, check_local=False).close()
        c = dbmod.connect(p, check_local=False)
        assert dbmod.migrate(c, p) == []
        c.close()

    def test_seed_principals(self, conn):
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM principals")}
        assert rows["owner"]["kind"] == "owner"
        assert "authority" in rows["owner"]["verbs"]
        assert rows["shop-session"]["grants"] == '{"archive":"internal"}'

    def test_snapshot_verified(self, tmp_path):
        p = tmp_path / "db.sqlite"
        c = dbmod.open_database(p, check_local=False)
        snap = dbmod._snapshot_and_verify(c, p)
        assert snap.exists()
        check = sqlite3.connect(snap)
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        check.close()
        c.close()


class TestImmutabilityTriggers:
    def _asset(self, conn, aid="fa_1"):
        conn.execute(
            "INSERT INTO file_assets(id, sha256, size_bytes, role, rel_path, created_at)"
            " VALUES (?, ?, 1, 'master', 'x', datetime('now'))",
            (aid, "s" + aid),
        )

    def test_file_assets_delete_forbidden(self, conn):
        self._asset(conn)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM file_assets WHERE id='fa_1'")

    def test_authority_append_only(self, conn):
        conn.execute(
            "INSERT INTO authority_records(id, principal, object_kind, object_id, status, recorded_at)"
            " VALUES ('ar_1', 'owner', 'revision', 'rev_x', 'approved-for-use', datetime('now'))"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE authority_records SET status='retired' WHERE id='ar_1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM authority_records WHERE id='ar_1'")

    def test_legal_hold_blocks_document_delete(self, conn):
        conn.execute(
            "INSERT INTO documents(id, title, primary_domain, sensitivity, legal_hold,"
            " recorded_at, created_at, updated_at)"
            " VALUES ('doc_1', 't', 'archive', 'internal', 1,"
            " datetime('now'), datetime('now'), datetime('now'))"
        )
        with pytest.raises(sqlite3.IntegrityError, match="legal hold"):
            conn.execute("DELETE FROM documents WHERE id='doc_1'")


class TestFts:
    def _page(self, conn, pid, text):
        conn.execute(
            "INSERT INTO documents(id, title, primary_domain, sensitivity, recorded_at, created_at, updated_at)"
            " VALUES ('doc_f', 't', 'archive', 'internal', datetime('now'), datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO revisions(id, document_id, recorded_at) VALUES ('rev_f', 'doc_f', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO copies(id, revision_id, source_class, sha256, created_at)"
            " VALUES ('cp_f', 'rev_f', 'ia', 'abc', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO pages(id, copy_id, pdf_index, extracted_text) VALUES (?, 'cp_f', 0, ?)",
            (pid, text),
        )

    def test_insert_and_match(self, conn):
        self._page(conn, "pg_1", "replace the isolation transformer before testing R12")
        rows = conn.execute(
            "SELECT p.id FROM pages_fts f JOIN pages p ON p.rowid = f.rowid"
            " WHERE pages_fts MATCH 'transformer'"
        ).fetchall()
        assert [r["id"] for r in rows] == ["pg_1"]

    def test_technical_tokens_searchable(self, conn):
        self._page(conn, "pg_2", "connector J1 carries AC115-129V through fuse F1 and part 512-1944")
        for q in ('"AC115-129V"', '"512-1944"', "J1"):
            rows = conn.execute("SELECT rowid FROM pages_fts WHERE pages_fts MATCH ?", (q,)).fetchall()
            assert rows, f"no match for {q}"

    def test_update_reindexes(self, conn):
        self._page(conn, "pg_3", "old words here")
        conn.execute("UPDATE pages SET extracted_text='fresh flyback content' WHERE id='pg_3'")
        assert conn.execute("SELECT rowid FROM pages_fts WHERE pages_fts MATCH 'flyback'").fetchall()
        assert not conn.execute("SELECT rowid FROM pages_fts WHERE pages_fts MATCH 'old'").fetchall()


class TestConfig:
    def test_roundtrip(self, tmp_path):
        cfg = cfgmod.init_config_root(
            tmp_path / "cfg", tmp_path / "data" / "db.sqlite", tmp_path / "nas",
            mount_id="vol-123", write_marker=True,
        )
        loaded = cfgmod.load_config(tmp_path / "cfg")
        assert loaded.db_path == cfg.db_path
        assert loaded.mount_id == "vol-123"
        assert loaded.domains == cfgmod.DEFAULT_DOMAINS
        assert cfg.keys_dir.is_dir()

    def test_mount_guard(self, tmp_path):
        cfg = cfgmod.init_config_root(
            tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
            mount_id="vol-123", write_marker=True,
        )
        cfgmod.verify_mount(cfg)  # ok
        (cfg.files_root / cfgmod.MOUNT_MARKER_NAME).write_text("other-volume")
        with pytest.raises(cfgmod.MountUnverified, match="mismatch"):
            cfgmod.verify_mount(cfg)
        (cfg.files_root / cfgmod.MOUNT_MARKER_NAME).unlink()
        with pytest.raises(cfgmod.MountUnverified, match="no readable"):
            cfgmod.verify_mount(cfg)

    def test_db_network_path_refused(self, tmp_path):
        with pytest.raises(cfgmod.ConfigError, match="network path"):
            cfgmod.assert_db_path_local(
                tmp_path / "share" / "db.sqlite", network_points=[str(tmp_path / "share")]
            )
        cfgmod.assert_db_path_local(tmp_path / "local.sqlite", network_points=["/Volumes/nas"])


class TestIds:
    def test_new_id_sortable_unique(self):
        ids = [new_id("doc") for _ in range(50)]
        assert len(set(ids)) == 50
        assert all(i.startswith("doc_") for i in ids)

    def test_sha256_file(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"stacks")
        import hashlib

        assert sha256_file(str(p)) == hashlib.sha256(b"stacks").hexdigest()
