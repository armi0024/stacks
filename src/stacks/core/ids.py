"""ID and hashing helpers.

IDs are time-ordered (sortable) with a random suffix: `<prefix>_<ts36><rand>`.
Assets are identified by content (SHA-256); row IDs never encode meaning.
"""

from __future__ import annotations

import hashlib
import secrets
import time


def new_id(prefix: str) -> str:
    ts = int(time.time() * 1000)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    enc = ""
    while ts:
        ts, rem = divmod(ts, 36)
        enc = chars[rem] + enc
    return f"{prefix}_{enc}{secrets.token_hex(6)}"


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def md5_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
