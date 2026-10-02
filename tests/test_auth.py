from __future__ import annotations

import time

import pytest

from stacks.auth import tokens as tok
from stacks.auth.filter import (
    DocumentAccess,
    check_document_access,
    document_visible,
    filter_documents,
    require_verb,
)
from stacks.auth.model import AuthError, create_principal, get_principal_by_name
from stacks.core import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_database(tmp_path / "db.sqlite", check_local=False)
    yield c
    c.close()


@pytest.fixture()
def owner(conn):
    return get_principal_by_name(conn, "owner")


@pytest.fixture()
def shop(conn):
    return get_principal_by_name(conn, "shop-session")


def acc(primary, sensitivity, extra=()):
    return DocumentAccess(primary_domain=primary, additional_domains=tuple(extra), sensitivity=sensitivity)


class TestCentralFilter:
    def test_owner_sees_everything(self, owner):
        for domain in ("archive", "personal", "paradise"):
            for sens in ("open", "internal", "confidential", "restricted"):
                assert document_visible(owner, acc(domain, sens))

    def test_shop_ceiling(self, shop):
        assert document_visible(shop, acc("archive", "open"))
        assert document_visible(shop, acc("archive", "internal"))
        assert not document_visible(shop, acc("archive", "confidential"))
        assert not document_visible(shop, acc("archive", "restricted"))

    def test_shop_sees_no_other_domain(self, shop):
        assert not document_visible(shop, acc("personal", "open"))
        assert not document_visible(shop, acc("paradise", "internal"))

    def test_multi_domain_any_granted_domain_admits(self, shop):
        # SPEC P5: filter passes if ANY granted domain admits the sensitivity
        assert document_visible(shop, acc("paradise", "internal", extra=("archive",)))
        assert not document_visible(shop, acc("paradise", "confidential", extra=("archive",)))

    def test_applicability_is_not_access(self, shop):
        # a document merely applicable to a shop game, but owned by personal,
        # stays invisible: applicability never grants access
        assert not document_visible(shop, acc("personal", "internal"))

    def test_malformed_sensitivity_fails_closed(self, owner):
        assert not document_visible(owner, acc("archive", "weird"))

    def test_filter_documents_list(self, conn, shop):
        rows = [
            {"primary_domain": "archive", "additional_domains": "[]", "sensitivity": "internal"},
            {"primary_domain": "archive", "additional_domains": "[]", "sensitivity": "restricted"},
            {"primary_domain": "personal", "additional_domains": "[]", "sensitivity": "open"},
        ]
        kept = filter_documents(shop, rows)
        assert len(kept) == 1 and kept[0]["sensitivity"] == "internal"

    def test_verbs(self, shop, owner):
        require_verb(shop, "read")
        require_verb(shop, "search")
        with pytest.raises(AuthError, match="lacks verb"):
            require_verb(shop, "submit")
        require_verb(owner, "authority")

    def test_check_document_access_denies_like_absent(self, conn, shop):
        conn.execute(
            "INSERT INTO documents(id, title, primary_domain, sensitivity, recorded_at,"
            " created_at, updated_at) VALUES ('doc_p', 't', 'personal', 'restricted',"
            " datetime('now'), datetime('now'), datetime('now'))"
        )
        with pytest.raises(AuthError, match="not found"):
            check_document_access(conn, shop, "doc_p")
        with pytest.raises(AuthError, match="not found"):
            check_document_access(conn, shop, "doc_never_existed")


class TestPrincipals:
    def test_create_validates(self, conn):
        with pytest.raises(AuthError, match="bad sensitivity"):
            create_principal(conn, "p1", "x", "agent", {"archive": "secret"}, ["read"])
        with pytest.raises(AuthError, match="unknown verb"):
            create_principal(conn, "p2", "y", "agent", {"archive": "open"}, ["fly"])
        p = create_principal(conn, "p3", "agent-a", "agent", {"archive": "open"}, ["read", "search"])
        assert p.ceiling("archive") == 0 and p.ceiling("personal") is None


class TestTokens:
    def test_roundtrip(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys")
        t = tok.issue(conn, tmp_path / "keys", "prin_shop", ttl_seconds=60)
        principal, tid = tok.verify(conn, tmp_path / "keys", t)
        assert principal.name == "shop-session"
        assert tid.startswith("tok_")

    def test_expiry(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys")
        t = tok.issue(conn, tmp_path / "keys", "prin_shop", ttl_seconds=-1)
        time.sleep(0.01)
        with pytest.raises(tok.TokenError, match="expired"):
            tok.verify(conn, tmp_path / "keys", t)

    def test_revocation(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys")
        t = tok.issue(conn, tmp_path / "keys", "prin_owner", ttl_seconds=60)
        _, tid = tok.verify(conn, tmp_path / "keys", t)
        tok.revoke(conn, tid)
        with pytest.raises(tok.TokenError, match="revoked"):
            tok.verify(conn, tmp_path / "keys", t)

    def test_tamper_rejected(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys")
        t = tok.issue(conn, tmp_path / "keys", "prin_shop", ttl_seconds=60)
        prefix, body, sig = t.split(".")
        import base64, json

        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        payload["sub"] = "prin_owner"  # privilege escalation attempt
        forged_body = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(tok.TokenError, match="bad signature"):
            tok.verify(conn, tmp_path / "keys", f"{prefix}.{forged_body}.{sig}")

    def test_wrong_key_rejected(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys-a")
        tok.ensure_token_key(tmp_path / "keys-b")
        t = tok.issue(conn, tmp_path / "keys-a", "prin_shop", ttl_seconds=60)
        with pytest.raises(tok.TokenError, match="bad signature"):
            tok.verify(conn, tmp_path / "keys-b", t)

    def test_malformed(self, conn, tmp_path):
        tok.ensure_token_key(tmp_path / "keys")
        with pytest.raises(tok.TokenError, match="malformed"):
            tok.verify(conn, tmp_path / "keys", "not-a-token")
