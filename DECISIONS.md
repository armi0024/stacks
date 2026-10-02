# Pre-build decisions (SPEC.md section 14, resolved)
- Domains: archive, paradise, starcade, retrolodge, personal
- Grants: owner -> all domains, restricted, all verbs.
  shop session -> archive, internal, read/search only.
  No agent tokens until phase 6.
- Token issuance: local module is interim issuer until the estate broker exists.
- ahpp-wellsgardner: retired as component; content via ingest_folder.
# Environment facts
- Host: Apple Silicon Mac mini. NAS mounted (manually for now) at
  /Users/Shared/stacks-rw (rw). ro mount blocked by macOS
  single-SMB-session; enforce read-only via :ro Docker bind at deploy.
- IA harvest COMPLETE: 4754 items at /Users/Shared/stacks-rw/archive/ia/
  ({identifier}/ folders with files + {identifier}_meta.json + coverage.csv).
- Auto-mount not yet configured: a reboot drops the mount and the path
  goes empty-local. The mount-identity guard (SPEC 3.1) is therefore
  priority, and jobs must never write to an unverified path.
