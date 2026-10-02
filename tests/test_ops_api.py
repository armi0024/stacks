from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from stacks.app.api import create_app
from stacks.auth.tokens import ensure_token_key, issue
from stacks.core import db as dbmod
from stacks.core.config import init_config_root
from stacks.ops import backup as bkp
from tests.test_ingest_search import make_manual


@pytest.fixture()
def env(tmp_path):
    cfg = init_config_root(
        tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
        mount_id="vol-test", write_marker=True,
    )
    conn = dbmod.open_database(cfg.db_path, check_local=False)
    ensure_token_key(cfg.keys_dir)
    yield conn, cfg
    conn.close()


@pytest.fixture()
def populated(env, tmp_path):
    conn, cfg = env
    from stacks.auth.model import get_principal_by_name
    from stacks.catalog.ingest import ingest_pdf

    owner = get_principal_by_name(conn, "owner")
    pdf = make_manual(tmp_path / "m.pdf",
                      ["Galaga service manual for the isolation transformer and fuse F1."])
    res = ingest_pdf(conn, cfg, owner, pdf, title="Galaga Manual", primary_domain="archive",
                     sensitivity="internal", language="eng")
    owner_token = issue(conn, cfg.keys_dir, "prin_owner")
    shop_token = issue(conn, cfg.keys_dir, "prin_shop")
    app = create_app(cfg, check_local_db=False)
    client = TestClient(app)
    return conn, cfg, client, res, owner_token, shop_token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestApi:
    def test_requires_token(self, populated):
        _, _, client, res, *_ = populated
        assert client.get("/search", params={"q": "x"}).status_code == 401
        assert client.get(f"/documents/{res.document_id}").status_code == 401

    def test_bad_token_rejected(self, populated):
        _, _, client, _, *_ = populated
        r = client.get("/search", params={"q": "x"}, headers=_auth("st1.bogus.bogus"))
        assert r.status_code == 401

    def test_search_and_citation(self, populated):
        _, _, client, res, owner_token, _ = populated
        r = client.get("/search", params={"q": "transformer"}, headers=_auth(owner_token))
        assert r.status_code == 200
        hits = r.json()["hits"]
        assert hits and hits[0]["document_id"] == res.document_id
        assert hits[0]["citation"]["locator"]["type"] == "page"

    def test_document_detail_state_facts(self, populated):
        _, _, client, res, owner_token, _ = populated
        r = client.get(f"/documents/{res.document_id}", headers=_auth(owner_token))
        assert r.status_code == 200
        body = r.json()
        assert body["document"]["title"] == "Galaga Manual"
        copy = body["copies"][0]
        assert copy["is_document_default"] and copy["default_state"] == "provisional"
        assert copy["recommended_action"] is not None

    def test_page_image(self, populated):
        _, _, client, res, owner_token, _ = populated
        r = client.get(f"/copies/{res.copy_id}/pages/1/image", headers=_auth(owner_token))
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert client.get(f"/copies/{res.copy_id}/pages/99/image",
                          headers=_auth(owner_token)).status_code == 404

    def test_shop_allowed_then_old_link_dies_on_reclassification(self, populated):
        """The old-links contract: access is evaluated at fetch time, every
        time — a link that worked keeps re-checking grants."""
        conn, _, client, res, _, shop_token = populated
        url = f"/copies/{res.copy_id}/pages/1/image"
        assert client.get(url, headers=_auth(shop_token)).status_code == 200
        with conn:
            conn.execute("UPDATE documents SET sensitivity='confidential' WHERE id=?",
                         (res.document_id,))
        assert client.get(url, headers=_auth(shop_token)).status_code == 404
        assert client.get(f"/documents/{res.document_id}",
                          headers=_auth(shop_token)).status_code == 404
        r = client.get("/search", params={"q": "transformer"}, headers=_auth(shop_token))
        assert r.json()["hits"] == []

    def test_access_events_recorded(self, populated):
        conn, _, client, res, owner_token, _ = populated
        client.get(f"/documents/{res.document_id}", headers=_auth(owner_token))
        rows = conn.execute(
            "SELECT * FROM access_events WHERE object_kind='document' AND object_id=?",
            (res.document_id,),
        ).fetchall()
        assert rows and rows[0]["actor"] == "owner" and rows[0]["token_id"].startswith("tok_")


class TestBackupRestore:
    def test_snapshot_mutate_restore_drill(self, env, tmp_path):
        conn, cfg = env
        with conn:
            conn.execute("INSERT INTO settings(key, value) VALUES ('drill', '\"before\"')")
        snap = bkp.nightly_snapshot(cfg.db_path, tmp_path / "backups")
        with conn:
            conn.execute("UPDATE settings SET value='\"after\"' WHERE key='drill'")
        restored_path = tmp_path / "restored.sqlite"
        bkp.restore(snap, restored_path)
        rconn = dbmod.connect(restored_path, check_local=False)
        assert json.loads(rconn.execute(
            "SELECT value FROM settings WHERE key='drill'").fetchone()[0]) == "before"
        assert dbmod.current_version(rconn) >= 2  # schema intact
        rconn.close()

    def test_restore_refuses_overwrite(self, env, tmp_path):
        conn, cfg = env
        snap = bkp.nightly_snapshot(cfg.db_path, tmp_path / "backups")
        with pytest.raises(bkp.BackupError, match="refusing to overwrite"):
            bkp.restore(snap, cfg.db_path)

    def test_retention_prunes(self, env, tmp_path):
        _, cfg = env
        for _ in range(4):
            bkp.nightly_snapshot(cfg.db_path, tmp_path / "b", retain=2)
        assert len(list((tmp_path / "b").glob("*.sqlite"))) <= 2

    def test_incremental_online_backup(self, env, tmp_path):
        conn, cfg = env
        with conn:
            conn.execute("INSERT INTO settings(key, value) VALUES ('inc', '1')")
        dest = bkp.incremental_backup(cfg.db_path, tmp_path / "hourly.sqlite")
        rconn = dbmod.connect(dest, check_local=False)
        assert rconn.execute("SELECT value FROM settings WHERE key='inc'").fetchone()[0] == "1"
        rconn.close()
        # second run replaces atomically
        bkp.incremental_backup(cfg.db_path, tmp_path / "hourly.sqlite")


class TestAdminCli:
    def test_init_ingest_search_roundtrip(self, tmp_path, capsys):
        from stacks.app.cli import main

        root = tmp_path / "cfg"
        assert main(["--config-root", str(root), "init",
                     "--db", str(tmp_path / "data" / "db.sqlite"),
                     "--files-root", str(tmp_path / "nas"),
                     "--mount-id", "vol-cli", "--write-marker"]) == 0
        pdf = make_manual(tmp_path / "m.pdf", ["Centipede trackball maintenance and cleaning."])
        import os
        os.environ["STACKS_CONFIG"] = str(root)
        try:
            assert main(["--config-root", str(root), "ingest", pdf,
                         "--title", "Centipede Manual", "--domain", "archive",
                         "--sensitivity", "internal", "--language", "eng"]) == 0
            capsys.readouterr()
            assert main(["--config-root", str(root), "search", "trackball"]) == 0
            out = capsys.readouterr().out
            assert "Centipede Manual" in out and "cite:" in out
        finally:
            del os.environ["STACKS_CONFIG"]

    def test_token_issue_verify_cli(self, tmp_path, capsys):
        from stacks.app.cli import main

        root = tmp_path / "cfg"
        main(["--config-root", str(root), "init",
              "--db", str(tmp_path / "db.sqlite"),
              "--files-root", str(tmp_path / "nas"),
              "--mount-id", "v", "--write-marker"])
        capsys.readouterr()
        assert main(["--config-root", str(root), "issue-token", "--principal", "shop-session"]) == 0
        token = capsys.readouterr().out.strip()
        assert token.startswith("st1.")
