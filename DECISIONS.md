# Pre-build decisions (SPEC.md section 14, resolved)
- Domains: archive, paradise, starcade, retrolodge, personal
- Grants: owner -> all domains, restricted, all verbs.
  shop session -> archive, internal, read/search only.
  No agent tokens until phase 6.
- Token issuance: local module is interim issuer until the estate broker exists.
- ahpp-wellsgardner: retired as component; content via ingest_folder.
# Phase 1b
- P13 deviation, accepted: 1a's migrations lacked the mutation-idempotency
  ledger, the per-document generation pointer, and the chunk store that
  SPEC 3.5 requires. Migration 0003 ships them ADDITIVELY (P10 discipline),
  which doubles as the "schema-migration tooling exercised" deliverable.
- Authority conflict semantics (4.4 as implemented): owner kind outranks
  all; settings key principal_precedence ranks named principals; among
  equal-rank records from DIFFERENT principals the latest-arrived wins only
  if it is also latest-effective, otherwise a VISIBLE conflict (badge +
  audit event + review job). Within one principal, latest-effective wins;
  arrival order (rowid) breaks ties — recorded_at alone is second-granular.

# Environment facts
- Host: Apple Silicon Mac mini. NAS (192.168.1.71, share "stacks") mounted
  at /Users/Shared/stacks-rw by the com.stacks.mount LaunchDaemon running
  as _stacks — survives reboots, remounts within 60 s. smbfs sessions are
  per-user, so only _stacks sees this mount; ro serving mount still via
  :ro Docker bind at deploy (single-SMB-session limit stands).
- IA harvest COMPLETE and verified post-provisioning: 4754 items at
  /Users/Shared/stacks-rw/archive/ia/ ({identifier}/ folders with files +
  {identifier}_meta.json + coverage.csv; all coverage rows "ok").
- Provisioned 2026-10-02: _stacks (UID 450), .stacks-volume marker
  stamped, hourly/nightly DB backups live (see RUNBOOK "This host").
