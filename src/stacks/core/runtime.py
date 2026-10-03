"""Outbox wiring: the one place that knows every handler.

Startup reconciliation (SPEC 3.5) is a dispatch of whatever is pending —
replaying unapplied rows after a crash is the same operation as a normal
dispatch, so there is no separate recovery code path to drift.
"""

from __future__ import annotations

import sqlite3

from stacks.core import audit, outbox
from stacks.core.config import StacksConfig
from stacks.index import generations


def default_handlers(cfg: StacksConfig) -> dict:
    return {
        "audit": audit.make_handler(audit.audit_path(cfg.config_root)),
        **generations.handlers(),
    }


def reconcile(conn: sqlite3.Connection, cfg: StacksConfig, limit: int = 1000) -> outbox.DispatchReport:
    return outbox.dispatch(conn, default_handlers(cfg), limit=limit)
