"""HTTP mutation contract (SPEC 3.5 over the wire): 409 carries the current
version, replays return the original result, policy failures are 403, and
sensitive fetches audit."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from stacks.app.api import create_app
from stacks.auth.tokens import ensure_token_key, issue
from stacks.core import db as dbmod
from stacks.core.audit import audit_path, verify_chain
from stacks.core.config import init_config_root
from tests.test_ingest_search import make_manual


@pytest.fixture()
def api(tmp_path):
    cfg = init_config_root(
        tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
        mount_id="vol-test", write_marker=True,
    )
    conn = dbmod.open_database(cfg.db_path, check_local=False)
    ensure_token_key(cfg.keys_dir)
    from stacks.auth.model import get_principal_by_name
    from stacks.catalog.ingest import ingest_pdf

    owner = get_principal_by_name(conn, "owner")
    res = ingest_pdf(conn, cfg, owner,
                     make_manual(tmp_path / "m.pdf", ["Dig Dug power supply manual."]),
                     title="Dig Dug Manual", primary_domain="archive",
                     sensitivity="internal", language="eng")
    owner_token = issue(conn, cfg.keys_dir, "prin_owner")
    shop_token = issue(conn, cfg.keys_dir, "prin_shop")
    client = TestClient(create_app(cfg, check_local_db=False))
    yield conn, cfg, client, res, owner_token, shop_token
    conn.close()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestMutationEndpoints:
    def test_ext_write_conflict_and_replay(self, api):
        conn, cfg, client, res, owner_token, _ = api
        url = f"/documents/{res.document_id}/ext"
        body = {"values": {"ext.ceo.note": "n1"}, "expected_version": 1,
                "idempotency_key": "api-k1"}
        r = client.post(url, json=body, headers=_auth(owner_token))
        assert r.status_code == 200 and r.json()["new_version"] == 2

        stale = dict(body, idempotency_key="api-k2")  # same expected_version, now stale
        r = client.post(url, json=stale, headers=_auth(owner_token))
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["current_version"] == 2 and detail["expected"] == 1

        r = client.post(url, json=body, headers=_auth(owner_token))  # replay of k1
        assert r.status_code == 200 and r.json()["replayed"] is True

    def test_policy_failures_are_403(self, api):
        conn, cfg, client, res, _, shop_token = api
        r = client.post(f"/documents/{res.document_id}/ext",
                        json={"values": {"ext.x": 1}, "expected_version": 1,
                              "idempotency_key": "api-k3"},
                        headers=_auth(shop_token))
        assert r.status_code == 403
        r = client.post(f"/documents/{res.document_id}/legal-hold",
                        json={"hold": True, "expected_version": 1,
                              "idempotency_key": "api-k4"},
                        headers=_auth(shop_token))
        assert r.status_code == 403

    def test_authority_write_and_visible_conflict(self, api):
        conn, cfg, client, res, owner_token, _ = api
        from stacks.auth.model import create_principal
        from stacks.core.ids import new_id

        for name in ("svc-x", "svc-y"):
            create_principal(conn, new_id("prin"), name, "service",
                             {"archive": "restricted"}, ["authority", "read"])
        from stacks.auth.model import get_principal_by_name
        from stacks.catalog.authority import write_authority

        write_authority(conn, get_principal_by_name(conn, "svc-x"),
                        object_kind="document", object_id=res.document_id,
                        status="approved-for-use", effective_date="2026-09-01",
                        idempotency_key="svc-x-k")
        write_authority(conn, get_principal_by_name(conn, "svc-y"),
                        object_kind="document", object_id=res.document_id,
                        status="retired", effective_date="2026-01-01",
                        idempotency_key="svc-y-k")
        r = client.get(f"/authority/document/{res.document_id}", headers=_auth(owner_token))
        assert r.status_code == 200
        body = r.json()
        assert body["conflict"] is True and body["winner"] is None
        # owner settles it through the endpoint
        r = client.post("/authority",
                        json={"object_kind": "document", "object_id": res.document_id,
                              "status": "approved-for-use", "idempotency_key": "owner-k"},
                        headers=_auth(owner_token))
        assert r.status_code == 200 and r.json()["conflict"] is False
        r = client.get(f"/authority/document/{res.document_id}", headers=_auth(owner_token))
        assert r.json()["winner"]["principal"] == "owner"

    def test_document_detail_reports_pin_state(self, api):
        conn, cfg, client, res, owner_token, _ = api
        r = client.get(f"/documents/{res.document_id}", headers=_auth(owner_token))
        assert r.json()["authority"] == {"pinned_revision": None, "conflict": False}
        client.post("/authority",
                    json={"object_kind": "revision", "object_id": res.revision_id,
                          "status": "approved-for-use", "idempotency_key": "pin-k"},
                    headers=_auth(owner_token))
        r = client.get(f"/documents/{res.document_id}", headers=_auth(owner_token))
        assert r.json()["authority"]["pinned_revision"] == res.revision_id

    def test_sensitive_fetch_audited(self, api):
        conn, cfg, client, res, owner_token, _ = api
        with conn:
            conn.execute("UPDATE documents SET sensitivity='restricted' WHERE id=?",
                         (res.document_id,))
        client.get(f"/documents/{res.document_id}", headers=_auth(owner_token))
        from stacks.core.runtime import reconcile

        reconcile(conn, cfg)
        lines = audit_path(cfg.config_root).read_text().splitlines()
        events = [json.loads(ln) for ln in lines]
        sens = [e for e in events if e["event"] == "sensitive-fetch"]
        assert sens and sens[0]["actor"] == "owner" and sens[0]["token_id"].startswith("tok_")
        assert verify_chain(audit_path(cfg.config_root)).ok

    def test_derivation_endpoint(self, api):
        conn, cfg, client, res, owner_token, _ = api
        r = client.post("/derivations",
                        json={"document_id": res.document_id,
                              "depends_on_kind": "revision",
                              "depends_on_id": res.revision_id,
                              "idempotency_key": "dv-k"},
                        headers=_auth(owner_token))
        assert r.status_code == 200 and r.json()["derivation_id"].startswith("dv_")
        r2 = client.post("/derivations",
                         json={"document_id": res.document_id,
                               "depends_on_kind": "revision",
                               "depends_on_id": res.revision_id,
                               "idempotency_key": "dv-k"},
                         headers=_auth(owner_token))
        assert r2.json().get("replayed") is True
