"""Token module (SPEC 3.2): the broker seam.

Issuance/verify/revoke behind this one module; signed short-lived tokens
using a DEDICATED Ed25519 token-signing key (distinct from the long-lived
bundle-signing key, P12; both live under the config root's keys/ dir and are
EXCLUDED from routine backups, with documented escrow — see RUNBOOK). This
module is the swap point for the estate broker; nothing else in Stacks may
mint or parse tokens.

Token format: "st1.<b64url payload>.<b64url signature>" where payload is
canonical JSON {tid, sub, iat, exp}. Every verified token resolves to a
principal; the token ID travels into logs and audit events.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from stacks.auth.model import Principal, get_principal
from stacks.core.ids import new_id

TOKEN_KEY_FILE = "token_signing.pem"  # bundle key (phase 7) is a SEPARATE file
TOKEN_PREFIX = "st1"


class TokenError(Exception):
    pass


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def ensure_token_key(keys_dir: str | Path) -> Path:
    """Create the token-signing key if absent; returns its path.

    Rotation = replace this file (old tokens die at their short expiry);
    the key is never written anywhere else.
    """
    path = Path(keys_dir) / TOKEN_KEY_FILE
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pem)
        path.chmod(0o600)
    return path


def _load_private(keys_dir: str | Path) -> Ed25519PrivateKey:
    pem = (Path(keys_dir) / TOKEN_KEY_FILE).read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TokenError("token signing key is not Ed25519")
    return key


def _public(keys_dir: str | Path) -> Ed25519PublicKey:
    return _load_private(keys_dir).public_key()


def issue(
    conn: sqlite3.Connection,
    keys_dir: str | Path,
    principal_id: str,
    ttl_seconds: int = 3600,
) -> str:
    get_principal(conn, principal_id)  # must exist
    now = int(time.time())
    tid = new_id("tok")
    payload = {"tid": tid, "sub": principal_id, "iat": now, "exp": now + ttl_seconds}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = _load_private(keys_dir).sign(body)
    with conn:
        conn.execute(
            "INSERT INTO tokens(id, principal_id, issued_at, expires_at)"
            " VALUES (?, ?, datetime(?, 'unixepoch'), datetime(?, 'unixepoch'))",
            (tid, principal_id, now, now + ttl_seconds),
        )
    return f"{TOKEN_PREFIX}.{_b64e(body)}.{_b64e(sig)}"


def verify(conn: sqlite3.Connection, keys_dir: str | Path, token: str) -> tuple[Principal, str]:
    """Verify signature, expiry, revocation, and principal; returns
    (principal, token_id). Raises TokenError on any failure."""
    try:
        prefix, body_b64, sig_b64 = token.split(".")
        if prefix != TOKEN_PREFIX:
            raise ValueError("bad prefix")
        body, sig = _b64d(body_b64), _b64d(sig_b64)
    except (ValueError, TypeError) as e:
        raise TokenError(f"malformed token: {e}") from e
    try:
        _public(keys_dir).verify(sig, body)
    except Exception as e:  # noqa: BLE001 - any crypto failure is a bad signature
        raise TokenError("bad signature") from e
    payload = json.loads(body)
    if payload.get("exp", 0) < time.time():
        raise TokenError("token expired")
    tid = payload["tid"]
    row = conn.execute("SELECT revoked_at FROM tokens WHERE id = ?", (tid,)).fetchone()
    if row is None:
        raise TokenError("unknown token id")
    if row["revoked_at"] is not None:
        raise TokenError("token revoked")
    principal = get_principal(conn, payload["sub"])
    if not principal.active:
        raise TokenError("principal inactive")
    return principal, tid


def revoke(conn: sqlite3.Connection, token_id: str) -> None:
    with conn:
        conn.execute(
            "UPDATE tokens SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL",
            (token_id,),
        )
