# Stacks operations runbook (phase 1a)

Maintained with the spec (SPEC 3.6). The restore procedure below is the
whole migration story: stop, restore snapshots, remount NAS, repoint
DNS/mDNS, start. **Any additional required step is an architecture defect.**

## Layout

| Thing | Where | Why |
|---|---|---|
| Config root | `$STACKS_CONFIG` (e.g. `~/stacks-config`) | all host-specific config (3.6) |
| SQLite DB | local SSD only (e.g. `~/stacks-data/stacks.sqlite`) | startup guard refuses network paths (3.1) |
| Files root | NAS mount (e.g. `/Users/Shared/stacks-rw/stacks`) | role dirs, hash-addressed, no organizational meaning |
| Keys | `<config root>/keys/` | token-signing key; bundle key (phase 7) separate; **both EXCLUDED from backups** (P12) |
| DB snapshots | NAS `backups/` | nightly `VACUUM INTO`, retain 14 |
| Hourly DB copy | NAS `backups/` | online backup API, constant cost (P9) |

## Provisioning (once per volume)

1. `stacks-admin --config-root <root> init --db <local-ssd-path> --files-root <nas-path> --mount-id <uuid> --write-marker`
   - `--write-marker` stamps `.stacks-volume` on the NAS volume. Run it ONLY
     when provisioning a new volume; the marker is how a dropped mount is
     detected (an empty local dir has no marker -> jobs pause, never write).
2. Escrow `keys/token_signing.pem` (printed copy or offline medium, owner
   custody). It is deliberately absent from every backup set.
3. Service account (SPEC 3.1): create a dedicated macOS user (e.g. `_stacks`),
   install the launchd plist under that account, never the logged-in user.
   Wired ethernet; sleep disabled.
4. NAS credential split (SPEC 3.1 / DECISIONS): one read-only credential for
   serving, one write credential for ingest. macOS allows a single SMB
   session per server+user, so enforce read-only at the Docker layer with a
   `:ro` bind mount for the serving container; the ingest worker gets the rw
   bind. Serving code references the ro mount exclusively.

## Backups (schedule under the service account)

- Hourly: `stacks-admin backup incremental --dest <nas>/backups`
  (RPO <= 1 h)
- Nightly: `stacks-admin backup snapshot --dest <nas>/backups --retain 14`
- Files are written directly to the NAS (RAID immediately); off-volume
  protection is Hyper Backup's schedule (daily), covering uploads/,
  intake-*/, backups/. Keys are excluded; escrow is manual (above).
- Qdrant (phase 6+): nightly snapshot; always rebuildable from SQLite+assets.

## Restore / host migration (RTO <= 4 h)

1. Stop the services (`launchctl bootout` the stacks jobs).
2. Restore the newest DB snapshot:
   `stacks-admin restore <snapshot.sqlite> --db <local-ssd-path>`
   (refuses to overwrite an existing DB: move the damaged file aside first).
3. Remount the NAS at the configured files root; confirm the
   `.stacks-volume` marker matches (`stacks-admin capacity` will fail the
   mount guard otherwise). NEVER re-stamp the marker during a restore.
4. Copy the escrowed keys into `<config root>/keys/` (or accept that all
   outstanding tokens die and reissue — tokens are short-lived by design).
5. Repoint DNS/mDNS to the new host.
6. Start services; run `stacks-admin search <known-query>` as a smoke check.

Drill cadence: pilot, then yearly (SPEC 6.7). The automated restore test in
`tests/test_ops_api.py` exercises snapshot -> mutate -> restore -> verify on
every run.

## Schema migrations (P10)

- Forward-only, numbered, additive-only files in `src/stacks/migrations/`.
- The runner refuses to migrate an existing DB without taking and verifying
  a fresh snapshot (`migration-snapshots/` beside the DB).
- Rehearse every new migration against a restored production snapshot
  before applying it to the live DB:
  `stacks-admin restore <snap> --db /tmp/rehearsal.sqlite && python -c "from stacks.core.db import open_database; open_database('/tmp/rehearsal.sqlite', check_local=False)"`

## Mount loss behavior

Marker missing or mismatched -> `MountUnverified` -> ingest/asset writes
refuse, jobs pause. After remounting, operations resume with no manual
reconciliation (committed files are content-addressed; a half-written temp
file is never adopted without a hash match).

## PHI refusals

`phi_refusals` table logs path + reason code (never content). There is no
override: corrected false positives are resubmitted after outside review.
Review the table when a legitimate document is refused; adjust the
classifier only through a code change with tests.
