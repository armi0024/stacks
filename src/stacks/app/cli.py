"""stacks-admin: operator CLI for the phase-1a foundations.

Local administrative tool: it operates as a named principal (default owner)
and still routes every read through the central filter — there is no
unfiltered path, not even for the CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stacks.core import db as dbmod
from stacks.core.config import init_config_root, load_config


def _conn(cfg):
    return dbmod.open_database(cfg.db_path)


def cmd_init(args) -> int:
    cfg = init_config_root(
        Path(args.config_root), Path(args.db), Path(args.files_root),
        mount_id=args.mount_id, write_marker=args.write_marker,
    )
    conn = _conn(cfg)
    conn.close()
    from stacks.auth.tokens import ensure_token_key

    ensure_token_key(cfg.keys_dir)
    print(f"initialized: config={cfg.config_root} db={cfg.db_path} files={cfg.files_root}")
    print("keys/ holds the token-signing key: EXCLUDED from backups; escrow it (RUNBOOK.md)")
    return 0


def cmd_issue_token(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.auth.model import get_principal_by_name
    from stacks.auth.tokens import issue

    principal = get_principal_by_name(conn, args.principal)
    token = issue(conn, cfg.keys_dir, principal.id, ttl_seconds=args.ttl)
    conn.close()
    print(token)
    return 0


def cmd_revoke_token(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.auth.tokens import revoke

    revoke(conn, args.token_id)
    conn.close()
    print(f"revoked {args.token_id}")
    return 0


def cmd_create_principal(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.auth.model import create_principal
    from stacks.core.ids import new_id

    p = create_principal(
        conn, new_id("prin"), args.name, args.kind,
        json.loads(args.grants), args.verbs.split(","),
    )
    conn.close()
    print(f"created principal {p.name} ({p.id})")
    return 0


def cmd_ingest(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.auth.model import get_principal_by_name
    from stacks.catalog.ingest import ingest_pdf

    principal = get_principal_by_name(conn, args.as_principal)
    res = ingest_pdf(
        conn, cfg, principal, args.pdf,
        title=args.title, primary_domain=args.domain,
        additional_domains=tuple(args.additional_domains.split(",")) if args.additional_domains else (),
        sensitivity=args.sensitivity, source_class=args.source_class,
        language=args.language, mode=args.mode,
    )
    conn.close()
    status = "DUPLICATE of" if res.duplicate else "committed"
    print(f"{status} document={res.document_id} copy={res.copy_id} action={res.recommended_action}")
    return 0


def cmd_search(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.auth.model import get_principal_by_name
    from stacks.search.fts import search

    principal = get_principal_by_name(conn, args.as_principal)
    hits = search(conn, principal, args.query, limit=args.limit)
    for h in hits:
        badges = f" [{', '.join(h.badges)}]" if h.badges else ""
        print(f"{h.title} — p.{h.page_number}{badges}")
        print(f"  {h.snippet}")
        print(f"  cite: doc={h.document_id} rev={h.revision_id} copy={h.copy_id} page={h.page_number}")
    if not hits:
        print("no results (within your grants)")
    conn.close()
    return 0


def cmd_backup(args) -> int:
    cfg = load_config(args.config_root)
    from stacks.ops.backup import incremental_backup, nightly_snapshot

    if args.kind == "snapshot":
        dest = nightly_snapshot(cfg.db_path, args.dest, retain=args.retain)
    else:
        dest = incremental_backup(cfg.db_path, Path(args.dest) / f"{cfg.db_path.stem}.hourly.sqlite")
    print(f"backup written: {dest}")
    return 0


def cmd_restore(args) -> int:
    from stacks.ops.backup import restore

    restore(args.snapshot, args.db)
    print(f"restored {args.snapshot} -> {args.db}")
    return 0


def cmd_capacity(args) -> int:
    cfg = load_config(args.config_root)
    conn = _conn(cfg)
    from stacks.assets.intake import check_capacity

    status = check_capacity(conn, cfg)
    conn.close()
    print(json.dumps(status, indent=2))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from stacks.app.api import create_app

    cfg = load_config(args.config_root)
    uvicorn.run(create_app(cfg), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stacks-admin")
    p.add_argument("--config-root", default=None, help="config root (or STACKS_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create config root, database, keys")
    s.add_argument("--db", required=True)
    s.add_argument("--files-root", required=True)
    s.add_argument("--mount-id", required=True)
    s.add_argument("--write-marker", action="store_true",
                   help="stamp the files-root identity marker (first provisioning only)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("issue-token")
    s.add_argument("--principal", required=True)
    s.add_argument("--ttl", type=int, default=3600)
    s.set_defaults(fn=cmd_issue_token)

    s = sub.add_parser("revoke-token")
    s.add_argument("--token-id", required=True)
    s.set_defaults(fn=cmd_revoke_token)

    s = sub.add_parser("create-principal")
    s.add_argument("--name", required=True)
    s.add_argument("--kind", required=True, choices=["session", "agent", "service"])
    s.add_argument("--grants", required=True, help='JSON {"domain": "sensitivity"}')
    s.add_argument("--verbs", required=True, help="comma-separated")
    s.set_defaults(fn=cmd_create_principal)

    s = sub.add_parser("ingest")
    s.add_argument("pdf")
    s.add_argument("--title", required=True)
    s.add_argument("--domain", required=True)
    s.add_argument("--additional-domains", default="")
    s.add_argument("--sensitivity", required=True)
    s.add_argument("--source-class", default="ingest_folder")
    s.add_argument("--language", default=None)
    s.add_argument("--mode", choices=["screening", "assess"], default="screening")
    s.add_argument("--as", dest="as_principal", default="owner")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--as", dest="as_principal", default="owner")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("backup")
    s.add_argument("kind", choices=["snapshot", "incremental"])
    s.add_argument("--dest", required=True)
    s.add_argument("--retain", type=int, default=14)
    s.set_defaults(fn=cmd_backup)

    s = sub.add_parser("restore")
    s.add_argument("snapshot")
    s.add_argument("--db", required=True)
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("capacity")
    s.set_defaults(fn=cmd_capacity)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8780)
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
