# STACKS: Universal Personal Library & Estate Document Substrate
## Specification v1.5 (FOUNDATION FREEZE; supersedes v1.4 and Addendum A by incorporation)

Standalone implementation contract. Scope frozen through the phase-4 acceptance tests; pilot results set weights, budgets, and model choices. Context: Stacks is the document system of record for a broader multi-agent estate ("CEO" system) reached through a central capability broker; the estate's domain services own business state; Stacks owns evidence, catalog, provenance, and citations.

---

## Changes in v1.5 (foundation review; final pre-build revision)

1. **Ownership boundary defined (0.1).** Stacks owns originals/evidence, catalog, provenance, extraction results, citations, and rebuildable derivatives. Domain services own current business state, commitments, and decisions. The broker owns permissions and delegated authority; the independent collector owns audit history. Every record has one authoritative writer.
2. **Version identity in phase 1 (4.1).** Four layers: logical document -> revision -> representation (copy) -> immutable asset. The quality tournament selects the most readable REPRESENTATION of a revision; it never selects between revisions. Authority pins (which revision is approved for use) are externally asserted records; retrieval and workflows honor them.
3. **Adequacy defect fixed (6.1).** Adequacy is tested on PRIMARY measurements (text adequacy on TQ, image adequacy on IF); blended fitness scores are for ranking only. Page-level gates added: required-content pages below a hard image-fidelity floor block USABLE regardless of averages.
4. **Document-default binding (6.4).** The document-default view binds to ONE copy. Serving pages from different copies happens only through confirmed logical-page alignments.
5. **Two-axis access model (3.2).** Domain scope and sensitivity are independent; principals hold per-domain sensitivity ceilings plus verb scopes. Dedup shares bytes at the asset layer without merging document-level permissions, business context, or authority status. Endpoint egress controls added.
6. **Write/mutation contract + recovery protocol (3.5).** Authenticated actor, idempotency key, expected-prior-version, policy check, audit event, explicit conflicts; transactional outbox coordinating SQLite with Qdrant; generation activation as build -> verify -> activate with stale-worker rejection and restart reconciliation; stated RPO/RTO.
7. **Authority, claims, and derivations as substrate seams (4.4, 6.10).** Authority records writable only by authorized principals; documents evidence their own existence, never the truth of their assertions; derivation links make generated summaries traceable and stale-able. Business-truth semantics remain owned by domain services.
8. **Bitemporal dates (4.1):** effective/document date vs system-learned date on every document; late-arriving evidence never revives or reorders current state by recency of ingestion.
9. **Citation locator contract generalized (9.1):** citation = (document, revision, representation, locator); PDF page/region is the first locator type; sheet/cell, message/span, record/sync-token types reserved.
10. **Grouping identity tightened (6.2):** auto-confirm additionally requires matching issuer and language; cross-domain contexts are never auto-merged; identical bytes may legitimately exist as separate documents in separate business contexts.
11. **Extraction labeling corrected (9.2):** OCR/VLM agreement is labeled corroborated-extraction with evidence pointers and uncertainty, never "fact". Risk flags (e.g. jbig2_unknown_history) propagate through derivative lineage; our lossless recompression never cleanses an inherited risk.
12. **Comparability keyed on full analysis configuration hash** (code version + settings + weights), not code version alone (6.1).
13. **Legal hold split (6.7):** hold guarantees preservation of the held evidence and blocks destruction; it does not freeze which copy or revision is the operational default.
14. **Pilot extended (11):** manual-rescan acceptance test retained; a small governance corpus (~15 documents) added with six behavioral tests (draft cannot displace approved revision; old email cannot revive a retired decision; a vendor claim cannot manufacture approval; cross-domain restrictions survive delegation, caches, and old links; concurrency, interrupted indexing, and restore preserve accepted state; summaries remain traceable to evidence).
15. **Integration seams (former Addendum A) incorporated** (3.6): service identity, token module isolation, ext.* namespace, extensible provenance classes with writer restrictions, signed export bundles (phase 7), retrieval trust rule, JSONL audit stream, canonical-assignment provenance, config root + migration runbook, LAN-only posture with scoped NAS credentials, PHI refusal at intake.
16. Carried intact from v1.4: text-provenance model with no score floors; screening vs comprehensive assessment with recorded coverage; REPROCESS before RESCAN; acquisition coverage as evidence; PROVISIONAL vs CONFIRMED canonical; graduated autonomy with precision/recall split and evidence-only inheritance; preservation program (fixity cycles, veraPDF + bitwise fidelity, regenerability, drilled restores); golden query set; access events; pilot-first phasing.
17. **Open pre-build decision (owner):** reconcile the existing ahpp-wellsgardner retrieval component with this implementation: reuse, replace, or retire, recorded in this spec before kickoff.

## v1.5.1 Patch Log (sixth review round; applied in place)

P1. Capacity model: lossless-compressed raw format required, per-session storage projection, capacity panel, volume-full as a planned add-storage event (3.1).
P2. OCR language handling: detection, per-document language, jpn/jpn_vert required, TQ null (never penalized) when language unknown/unsupported (6.1).
P3. REPROCESS executes before text-dimension selection for image-adequate candidates, so TQ measures achievable, not as-shipped, quality (6.4).
P4. Single-copy completeness defined: N=1 -> unknown, missing_fraction 0, no penalty, floor inapplicable (6.3).
P5. Multi-entity documents: domain is a set (primary + additional readable domains); filter passes if any granted domain admits the sensitivity (3.2).
P6. Two-level page gate: <50 blocks USABLE; 50-adequacy on required-content pages forces a "degraded critical page" label; pages_below_adequacy stored per dimension (6.1).
P7. Authority conflict precedence + visible-conflict rule (4.4).
P8. Every revision's default representation is indexed; revision in chunk metadata; pinned/current ranked above superseded, superseded badged (9.2).
P9. Hourly RPO via WAL shipping / incremental online backup API; VACUUM INTO nightly only (3.5).
P10. Schema evolution discipline: schema_version, forward-only additive migrations, snapshot-before-migrate, rehearsed on restored snapshots (3.5).
P11. Audit stream hash-chained per line; collector verifies continuity (3.6).
P12. Signing keys separated: token key vs bundle key, distinct rotation, excluded from routine backups, documented escrow (3.2, 3.6).
P13. Phase 1 split into 1a (reaches searchable) and 1b (adds behavior); all schema ships in 1a migrations (11).
P14. Boundary wording tightened: relevance is a query-document pair property; Stacks may never store a document-level score of worth (0.2).
P15. Text page gate mirrors the image gate on required-content pages, evaluated post-REPROCESS; TQ-null (language-unknown) pages never gate (6.1).
P16. document_default resolution rule when text and schematic winners differ: dual-adequate copy with highest composite, else dominant-dimension winner (6.4).
P17. Effective DPI defined by the image's rendered placement width (from its page transform), not the MediaBox width (6.1).

---

## 0. Position in the Estate

### 0.1 Ownership table (normative)
| Information | Authoritative owner |
|---|---|
| Original documents, captures, evidence, file versions | Stacks (immutable asset store) |
| Catalog, identity, provenance, extraction results, citations | Stacks |
| Current project/business state, commitments, approved drawings, decisions | Owning domain service |
| Permissions, delegated authority, external actions | Capability broker + protected policy (Stacks token module is the interim issuer and the broker's future swap point) |
| Audit history | Independent audit collector (Stacks emits; collector owns) |
| Embeddings, thumbnails, generated summaries, caches | Rebuildable derivatives with provenance (Stacks) |

One search interface may span these; every record still has exactly one authoritative writer. Stacks stores and serves; it does not decide business questions.

### 0.2 What Stacks asserts and what it never asserts
Stacks asserts: this asset exists, has this hash, arrived from this source at this time, contains this extracted text with this measured quality, and carries these externally-asserted labels from these principals. Stacks never asserts: that a statement inside a document is true, that an approval occurred because a document claims it, or that a payment happened because an invoice exists. Those judgments belong to domain services, recorded back as authority/ext records if desired. **Boundary of intelligence:** Stacks judges condition (legibility, completeness, fidelity of a representation), findability (query relevance of retrieval results, subject cataloging), and custody (provenance, versions, who asserted what). It never judges epistemic value: credibility, worthiness, truth, or fitness-to-act. That intelligence lives in the agents; it rides on Stacks through ext.*, authority_records, and derivations, and no feature that scores documents by credibility or importance may be added to Stacks itself. Relevance is a property of a query-document pair, computed per query and discarded; Stacks may rank results for a query and may never store a standing document-level score of worth, quality-for-purpose, or importance.

---

## 1. Overview

Stacks is a self-hosted, LAN-only application: the owner's universal document library and the estate's document substrate. It holds acquired archives (arcade and pinball manuals, schematics, magazines), personal ebook backups, books, and the owner's own living documents (project files, business records, email-sourced documents). It evaluates every representation's fitness for OCR-accurate retrieval, keeps the best representation of each revision, manages a physical rescanning workflow, and serves everything through access-controlled hybrid search to humans (web UI) and agents (MCP) with durable citations.

**Operator context:** single power user; online arcade parts retailer with a physical shop; large physical manual/schematic collection; two-station scanning rig (sheet-fed + camera); multiple business entities; local AI agent fleet; comfortable with Docker, Python, self-hosting.

**Hosting:** one Apple Silicon Mac mini runs all compute (interim host; Studio later via the migration runbook). Synology NAS (16 TB RAID) holds files via SMB/NFS and receives backups. Clients are LAN browsers.

**Success criteria:**
1. Any document findable in under 10 seconds from any shop computer, within the caller's grants.
2. Per representation: honest, evidence-cited state (image fidelity, text quality, completeness, coverage) with computed recommended action; provisional results badged.
3. A rescan queue reflecting reality (worst pages; sources-checked evidence; ownership/demand/improvement/effort priority).
4. Owner scans compete automatically within their revision and replace representations when better, provenance intact.
5. Agents retrieve accurate text and imagery via MCP with domain/sensitivity enforcement, durable citations, fetchable resources; authority pins are honored; no unlabeled machine interpretation.

**Acceptance tests (end of phase 4; gate bulk acquisition):** (a) the manual-rescan loop: find the correct revision, open the schematic sheet, identify the defect, rescan the sheet, produce the replacement copy via operator-directed page replacement, retrieve it through search with provenance; (b) the six governance tests (section 11, phase 4).

---

## 2. Goals and Non-Goals

### Goals
Mirror reachable public arcade/pinball sources locally (selected documents held as local originals). Measure image fidelity and text quality per page with explicit uncertainty. Select best representations per revision with auditable rules and operator override. Full scan-assembly pipeline with lineage and operator-directed page replacement. One library: subjects (hierarchical tags), domain scope, sensitivity, capture profile all orthogonal. Graduated autonomy for tagging and grouping. Hybrid RAG with domain/sensitivity filtering; schematic retrieval functional on OCR labels, enhanced by labeled VLM interpretation; MCP with write contract; golden-set-gated retrieval. Preservation: fixity, conformance + bitwise fidelity, regenerability, drilled restores, legal hold. Versioned living documents, email/office ingestion (phase 7). Shop mode UI.

### Non-Goals
Not public; no re-serving of third-party files; remote access, if ever, via Tailscale. **No PHI or patient-derived clinical material, ever** (mechanically refused at intake). No lossy JBIG2 in any output. No deletion of losing copies, raw captures, or superseded versions; legal hold blocks destruction absolutely. No cloud dependency for core function; endpoints configurable with egress controls, default local. Not a DRM-removal tool. **Stacks is not the business-truth engine:** it stores evidence and labels; domain services decide.

---

## 3. Architecture

### 3.1 Topology
Mac mini: Docker Compose (OrbStack/Docker Desktop): FastAPI app, worker processes, Qdrant; launchd autostart under a **dedicated service account** (never the logged-in user); wired ethernet; sleep disabled. NAS: `/volume1/stacks/{archive,uploads/{raw,masters,access,hot,hot-library},ingest,intake-office,intake-email,thumbs,backups}`; **paths are role-scoped only (lifecycle: intake, raw, master, access, cache, backup) and carry NO organizational meaning: inside each role folder, files live under asset IDs/hashes, never under game, title, domain, or subject names; a committed file never moves; domains, subjects, and sensitivity live exclusively in the database, so any reorganization is a database operation with zero file moves; human-legible names exist only in intake/staging areas (hot folders, harvest layouts) and end at ingest commit;** Hyper Backup covers uploads/, intake-*/, backups/ off-volume; per-source-class backup decision recorded (volatile third-party backed up; IA re-fetchable); Btrfs scrub enabled. **Databases local SSD only** (`~/stacks-data`): SQLite (+FTS5) + Qdrant; startup guard refuses SQLite on network paths. **Mount identity verified** (marker/UUID); on loss: jobs pause, banner, auto-resume. **NAS credentials:** Stacks-scoped share only, never admin; read-only credential/mount for serving, separate write credential/mount for ingest; serving code references the read-only mount exclusively. Bind LAN only; no WAN exposure. **Capacity model:** raw captures are accepted ONLY in lossless-compressed formats (TIFF LZW/ZIP, PNG, or lossless JPEG-XL); uncompressed intake is refused with a conversion hint. Every session finalization records a storage projection (bytes by artifact class); the dashboard carries a capacity panel with a projection curve; and because no-deletion and legal hold forbid reclamation, volume-full is a PLANNED event: a trigger threshold (default 80% of volume) opens an add-storage task, never an outage.

### 3.2 Principals and access (phase 1)
Two independent axes on every document:
- **domain**: a SET per document: one primary domain plus optional additional readable domains, from enumerable scopes (archive, paradise, starcade, retrolodge, personal, ...). Cross-entity documents (a lease between two entities, shared insurance, a vendor agreement covering shop and arcade) carry every entitled domain; document_applicability remains pure metadata and never grants access.
- **sensitivity**: open | internal | confidential | restricted.
A principal holds a grant map domain -> max sensitivity, plus **verbs** (read/search; submit; ext-write; restricted-provenance-write; admin). Examples: owner = all domains -> restricted, all verbs; shop session = archive -> internal; agent tokens = explicit maps, minimal verbs. Access passes when the principal's ceiling for AT LEAST ONE of the document's domains admits the document's sensitivity, evaluated by the **central auth filter** on every surface (FTS, vector results, thumbnails, recents, downloads, queues, page/image/region endpoints, MCP), **at fetch time, on all generations and previously issued links**. Qdrant payload filters per token as defense in depth; the vector store is not the boundary. Classification may only suggest equal-or-more-restrictive placement; personal-source documents default (personal, restricted) until reviewed; widening exposure (declassification, domain move, taxonomy move) requires explicit owner confirmation; tag removal never widens access.
**Token module (broker seam):** issuance/verify/revoke behind one module; signed short-lived tokens using a DEDICATED token-signing Ed25519 key (frequent rotation; distinct from the long-lived bundle-signing key; both keys excluded from routine backups that ship to the NAS, with a documented separate escrow); actor/token ID in every log line and audit event; the module is the swap point for the estate broker (interim issuer until the broker exists).
**Egress controls:** embedding/tagging/VLM endpoints come from a pinned allowlist in config; changing an endpoint requires owner confirmation and emits an audit event; documents above a configured (domain, sensitivity) threshold are never sent to non-local endpoints regardless of settings.

### 3.3 Stack
Python 3.11+, FastAPI, Jinja2 + HTMX, SQLite + FTS5 (chosen for operational simplicity; technical-token retrieval verified in the pilot; tokenizer configurable), Qdrant (rebuildable index; sized by chunks, order 10^6+; benchmarked on the pilot). Libraries: PyMuPDF, OpenCV, NumPy, Tesseract, OCRmyPDF (+ jbig2enc lossless), internetarchive, requests + BeautifulSoup, veraPDF. Model endpoints: OpenAI-compatible URL + model env vars each; embedding model + preprocessing version recorded per chunk; model change = controlled full rebuild.

### 3.4 Jobs and processes
Jobs table: atomic claim + expiring leases, heartbeats, bounded retries, idempotent outputs. Analyzer/processing in separate worker processes (PyMuPDF thread-safety); threads only for I/O downloads. Concurrency bounded by memory and CPU (default 6; env-tunable); OCR subprocess threads limited; fold-out renders in a separate budget (default 1). SQLite: WAL, generous busy_timeout, short transactions, workers write directly; enforced rule: no long-running write transactions; portable schema.

### 3.5 Writes, coordination, recovery (phase 1)
- **Mutation contract (API and MCP submit):** every authoritative change carries authenticated actor, idempotency key, expected prior version, passes the policy check, commits atomically, and emits an audit event. Version mismatch returns an explicit conflict (current version included); never a silent overwrite. Replayed idempotency keys return the original result.
- **Transactional outbox:** any change requiring work in another system (Qdrant chunk ops, thumbnail/cache invalidation, audit forwarding) writes an outbox row in the SAME SQLite transaction; a dispatcher applies rows with retries; restart reconciliation replays unapplied rows. No dual-write without the outbox.
- **Generation activation (replaces "insert N+1, delete N"):** build candidate chunk set for generation N+1 (inactive) -> verify (counts, spot retrieval, auth labels) -> activate by flipping the document's generation pointer in one SQLite transaction (+outbox) -> garbage-collect N. Workers carry the generation they serve; stale workers are rejected at the pointer; interrupted activations reconcile from the outbox on restart.
- **RPO/RTO (settings, stated):** SQLite: hourly incremental protection via WAL shipping or the online backup API (constant cost as the DB grows) + nightly `VACUUM INTO` snapshot to NAS retained 14; target RPO <= 1 h, RTO <= 4 h via the runbook. Files: written directly to NAS (RAID immediately); off-volume RPO = Hyper Backup schedule (daily). Qdrant: nightly snapshot; rebuildable from SQLite + assets regardless. Restore drill: pilot, then yearly, exercising the migration runbook (stop, restore snapshots, remount NAS, repoint DNS/mDNS, start; any additional step is an architecture defect).
- **Schema evolution:** a schema_version table; forward-only, numbered migrations under version control; additive-only rule (new tables/columns freely; renames and drops only via an explicit deprecation cycle with data-preserving backfill); every migration rehearsed against a restored production snapshot before application; **no migration runs without a verified fresh snapshot**. The host runbook covers moving the system; this covers evolving it.

### 3.6 Integration seams (from Addendum A, normative)
`ext.*` namespaced JSON on documents: stored, round-tripped, exported, never interpreted; writable only by ext-write tokens; can never alter Stacks-interpreted fields. Provenance/source classes are data with per-class writer restrictions (future system-observed-outcome class writable only by its designated service token). **Signed export bundles** (phase 7; ingredients guaranteed from phase 1): subject/query -> versioned read-only bundle (manifest of IDs, revisions, hashes, provenance, domain/sensitivity + files + detached Ed25519 signature). Bundles are **versioned and reproducible**: the manifest pins exact revisions and asset hashes, manifest serialization is canonical (sorted keys, stable ordering), and packing is deterministic (fixed timestamps and entry order), so the same inputs yield the same bundle bytes and hash, and any re-export can be verified against a prior signature. The bundle-signing key is distinct from the token key: long-lived (verifiability for years), independently rotated, excluded from routine backups, escrowed separately. **JSONL append-only audit stream**, **tamper-evident: each line carries the hash of the previous line (chain continuity verified by the collector; a chain break is itself an alarmed event)**, (stable schema; actor/token ID; events: ingest commit, canonical assignment, authority record writes, domain/sensitivity change, declassification, token issuance/revocation, legal-hold set/lift and blocked attempts, endpoint changes, PHI refusals, fetches of confidential/restricted documents), rotated, backed up, forwarder-ready. Config root holds all host-specific config; migration runbook maintained with the spec. **PHI refusal at intake:** classifier flag -> ingest refused at every domain/sensitivity; **fail-closed: on uncertainty, refuse** (a borderline refusal costs a manual re-review; a slip-through costs the exclusion guarantee); event logged (path + reason code, never content), operator alerted; no override path into Stacks; corrected false positives resubmit after outside review, correction recorded.

---

## 4. Data Model

### 4.1 Identity layers (phase 1)
- **documents** (logical record): title; issuer/manufacturer; language; identity numbers; extensible metadata JSON per subject schema; subjects; domain; sensitivity; legal_hold; **effective_date** (when the content speaks as of / was issued) and **recorded_at** (when Stacks learned of it): late-arriving evidence never reorders current state by ingestion recency. **document_applicability**: many-to-many document<->game (mame_name) and document<->entity.
- **revisions**: content versions of a document (Rev A, Rev B, amendment); ordered; a revision is never displaced by another revision through quality selection. **Current-revision pointer** is workflow state (phase 7 for living documents); **authority pins** (4.4) may designate the approved revision at any time.
- **copies** (representations of one revision): source; format; page count; checksums (SHA-256 canonical; MD5 retained where published); superseded; primary state facts (assessment coverage incl. analysis-config hash; completeness_status; per-dimension IF/TQ summaries; risk flags incl. inherited ones); derived fitness scores; recommended_action; canonical assignments (document_default; alignment-served alternates) with confirming actor, timestamp, content hash, supersedes (a record never makes itself canonical; a confirmed decision does; provisional assignments record the system rule as actor); provenance; conformance/fidelity/fixity status.
- **file_assets**: immutable stored bytes with lineage (raw -> master -> access -> OCR outputs), producing tool + version each; multiple source URLs per asset; retrievable by ID indefinitely. **Dedup operates here:** identical bytes stored once; distinct documents in distinct domains may reference the same asset; permissions, domain, sensitivity, and authority live on documents/copies, never on assets.
- **pages** + **page_alignments**: per-copy page identities (PDF index, printed label, sheet id, partial-capture flag); cross-copy logical-page alignments (system-proposed, human-confirmed).

### 4.2 Evidence and operations
**analysis_runs** (full analysis configuration hash = code version + settings + weights; coverage; per-page IF/TQ metrics + page type; scores; comparable only within config hash). **physical_holdings** (owned originals: revision, condition, missing sheets, shelf/bin). **acquisition_coverage** (source x document/category: when, outcome found / not-found / failed / unexamined; RESCAN cites it). **access_events** (analytics; owner-only; retention setting). **jobs** (lease fields). **outbox** (3.5). **sources** (class, config, politeness, writer restriction). **settings**. Dedup registry keyed SHA-256.

### 4.3 Organization and autonomy records
**subjects** (hierarchical; parent queries include descendants; user-editable) + **document_subjects** (provenance human|auto, decision link). **group_decisions** (auto-confirm log, reversible, trust-tracked). **tag_decisions** + **subject_trust** (precision and recall separately; bucket minimums; inheritance = examples/estimates only).

### 4.4 Authority and derivation (substrate seams, phase 1 schema)
- **authority_records**: externally asserted, append-only: authenticated principal, exact object (document/revision/copy), status (e.g. approved-for-use, retired), scope, effective_date, recorded_at, supersedes. Writable only by principals holding the authority verb for the domain (owner, or designated domain-service tokens). A document containing the words "approved" creates no authority record. Retrieval and workflows honor pins: a pinned revision is what document-level and reorder-facing requests resolve to, regardless of newer or cleaner representations elsewhere; unpinned documents resolve to current-revision/default rules with the pin's absence visible. **Conflict resolution:** when records from different principals assert overlapping pins on the same object, precedence is: the configured principal precedence order (settings; owner outranks all), then latest recorded_at within the effective window; a record arriving with an earlier effective_date never silently displaces a later-effective one. Overlaps not resolvable by these rules surface as a VISIBLE conflict on the object (UI badge + audit event + review queue entry) and retrieval reports the conflict rather than resolving silently; one authoritative writer per record is enforced here or it is enforced nowhere.
- **derivations**: document -> depends-on -> documents/citations, writable with submit verb. When an underlying document gains a new revision or its authority changes, dependents are marked stale (event emitted); regeneration is the owner's/estate's job. An agent-generated summary is a document with source class agent-generated and its derivation links; a summary of a summary shows its actual dependency chain and is never counted as an independent source.

---

## 5. Acquisition

Private local analysis/use only; UI links back to origin for third-party documents; nothing re-served.

| Source | Adapter | Notes |
|---|---|---|
| TAMA (arcade-museum.com), all categories incl. pinball | table_scrape | Paginated tables; politeness 2 s index / 3 s PDF, floor 1 s; resumable; download to temp, hash, commit atomically. |
| Internet Archive: arcademanuals + mega-items + approved pinball collections | ia_collection | Official `internetarchive` library/endpoints only; per-file metadata (incl. MD5); derivatives associated at FILE level in multi-document items; derivatives are triage only; every selected document gets its original downloaded and assessed; poor derivative OCR + good image metadata -> REPROCESS candidacy, not RESCAN. |
| Open directories (pdf.textfiles.com/manuals/ARCADE, stardustarcade, crazykong) | open_directory | Recursive index crawler; per-source config. |
| Progetto SNAPS | pack_import | Packs + MAME short-name index (game-identity authority). |
| Pinout/DIP text databases | text_ingest | Structured text tied to mame_name; RAG-ready. |
| Cloud-share dumps, one-off sites | ingest_folder | Unpack, hash, dedup, analyze, operator label. |
| Owner scans | my_scan | Section 8. |
| Office files (phase 7) | office_ingest | Original retained as master; normalized PDF + extracted text with native-structure metadata (9.1). |
| Email documents (phase 7) | email_intake | Attachments + body-as-context-page; message identity, sender/recipients, dates, thread links, quoted-vs-new spans; sensitivity/domain by sender rules, default (personal, restricted). |

Dedup: hash in temp before commit; SHA-256 registry; byte-identical files share one asset, documents stay per-context (4.1). Coverage discipline: every check writes acquisition_coverage; persistent failures surfaced, never silent absence. IA first (checksums seed matching). Excluded/cautioned: IPDB link-only unless terms permit; commercial vendors: buy, not scrape.

---

## 6. Measurement, Identity, Selection

### 6.1 Measurement (screening vs assessment; primary measurements; adequacy)
**Modes:** screening (up to 16 evenly sampled pages; provisional; sampled indices recorded; unsampled pages UNASSESSED) and comprehensive assessment (all pages; required for CONFIRMED status, USABLE, sheet-level entries; demand-prioritized: requested, owned originals, rescan candidates, likely winners, contested groups within 10 effective points, operator-opened; unassessed files viewable with status visible).
**Per-page primaries, recorded separately:** page type (native-text / ocr-layer / image-only / mixed; content class blank / text / schematic / mixed; table regions recorded); **image-fidelity metrics** (effective DPI = image pixel width / the image's RENDERED PLACEMENT width in inches, taken from its transformation on the page: full-page scans compute as before, partial-page images are no longer mismeasured against the MediaBox; bpc; Laplacian sharpness on grayscale render at min(native,300) DPI, 45 MP cap; RMS contrast; background speckle; skew); **text-quality metrics** (per-word confidence distribution; small-text confidence, bbox <=18 px; refdes-token confidence for patterns like R12/C45/U7 and part numbers carrying 0/O 1/l/I 5/S 8/B; native-text pages: extraction-quality checks instead). An embedded text layer never exempts image assessment; **no score floors**. **OCR language:** language is detected on first pages and set per document (operator-overridable; defaults from the identity language field); required language data installed for the corpus (eng, jpn, jpn_vert at minimum; a large fraction of arcade source material is Japanese); when a document's OCR language is unknown or unsupported, **TQ is null (unknown), never a penalty**, per the missing-measurement rule: confident garbage from the wrong language model must not condemn a physically sound scan to REPROCESS/RESCAN loops.
**Derived per-page scores (0-100):** image_fidelity_score: 100 minus: DPI below target (300 schematic-class / 220 text-class) proportional up to -40; <150 DPI additional -10; sharpness <120 -15; speckle >6 -8; skew >1.5 deg -6; 1-bit on schematic-class -12. text_quality_score: 100 minus: mean confidence <80 proportional up to -30; low-confidence fraction >15% up to -15; small-text <70 -10; refdes <75 -12; native-text garble up to -40.
**Document-level:** IF_text/TQ_text over assessed text+mixed pages; IF_schem/TQ_schem over schematic+mixed. **Adequacy (primary, per dimension): image adequate = IF >= 75; text adequate = TQ >= 70** (settings). **Fitness (ranking only): score_text = 0.5 IF_text + 0.5 TQ_text; score_schematic = 0.7 IF_schem + 0.3 TQ_schem**; null when no pages of the class. Grades A>=88 B>=78 C>=68 D>=55 F, per dimension, display only. **Page gates (two-level):** an assessed required-content page (schematic-class in schematic-implied documents; operator-designated critical pages) with image_fidelity_score below 50 blocks USABLE; between 50 and the image-adequacy threshold (75) it forces a visible **"degraded critical page"** label on the document without blocking. **pages_below_adequacy** is stored per dimension as a plain count and shown on the document detail page. **Text page gate (mirror of the image gate):** an assessed required-content page whose text_quality_score, evaluated POST-REPROCESS (after any automatic re-OCR), is below 50 blocks USABLE; between 50 and the text-adequacy threshold (70) it forces the "degraded critical page" label; TQ-null pages (unknown/unsupported OCR language) never gate: unknown is not failed. Every deduction emits a plain-language reason with evidence. Missing measurements are null and shown as unknown, never imputed.
**Ground truth:** pilot builds a manually transcribed set (small labels, component values, ambiguous characters, ~20 real pages); thresholds calibrated against transcription accuracy; retained for regression. OCR confidence is diagnostic, never proof.
**Risk flags (no numeric deduction; propagate through lineage):** jbig2_unknown_history on acquired JBIG2 without lossless provenance: surfaced in UI/MCP; blocks CONFIRMED text-default status without recorded operator acknowledgment; our recompression never clears an inherited flag. Own-pipeline JBIG2 records lossless provenance, no flag.

### 6.2 Grouping and revisions
Fuzzy match (identity numbers, mame_name/author, normalized title) PROPOSES. **Auto-confirm** requires: identical SHA-256, or same manufacturer document number + same revision + same issuer + same language + page counts within 2%; logged reversible, trust-tracked. **Never auto-merged:** across domains, across languages, or where identity evidence is partial. Different revisions are related documents under one logical document; equivalence merges are knowing operator actions. Applicability links documents to many games/entities.

### 6.3 State facts and recommended_action
Independent facts: assessment coverage (with config hash); completeness_status (confirmed / known-incomplete / unknown; signals: robust expected length = highest page count corroborated within 5% by a second copy else second-highest; printed labels; critical-content check: schematic-implied documents with zero schematic-class pages are known-incomplete; clues, not proof; **single-representation groups (N=1): completeness_status = unknown, missing_fraction = 0, no effective-score penalty, the <80% floor inapplicable (no expected length exists to measure against)**; operator override recorded); per-dimension image adequacy and text adequacy (primary tests per 6.1); page-gate status; risk flags. **recommended_action computed:** image adequate + text adequate + page gates pass + completeness confirmed-or-unknown-with-critical-pass (unknown labeled "unverified") -> USABLE; image adequate + text inadequate -> REPROCESS (local re-OCR/re-optimization; always evaluated before RESCAN); image inadequate on this and every group copy among sources successfully checked (comprehensive coverage) -> RESCAN (cites acquisition_coverage; carries this copy's worst-pages list); known-incomplete -> INCOMPLETE combined with the above, never hiding them; screening-only -> provisional badging, UNASSESSED beyond examined pages.

### 6.4 Selection: representations within a revision
Selection NEVER crosses revisions (4.1) and never overrides an authority pin (4.4). **REPROCESS runs before selection:** for image-adequate, text-inadequate selection candidates, local re-OCR executes automatically before the text-dimension selection is finalized (at screening-winner determination and again at confirmation), so TQ measures ACHIEVABLE quality rather than as-shipped OCR; otherwise the tournament systematically prefers copies that arrived with good OCR over physically better copies. Per revision group and dimension, comparable analysis-config hashes only:
- Tiers: Tier 1 = completeness confirmed (or unknown + critical-content pass) AND adequate for the dimension; Tier 2 = otherwise; Tier 2 becomes document-default only when Tier 1 is empty, labeled incomplete-fallback or recorded override.
- Within tier: effective = fitness - 1.5 x (missing_fraction x 100); <80% expected length cannot win outright; highest effective wins with >=3 displacement margin; tie-breakers: median DPI, non-1-bit schematic pages (schematic dimension), source priority my_scan > ia > tama > open_dir; operator override recorded.
- **document_default is ONE copy** (its pages serve all document-level requests). **When the text and schematic winners differ, document_default resolves as:** the copy adequate in BOTH dimensions with the highest composite fitness; if no copy is dual-adequate, the winner of the document's dominant content class (schematic-implied documents -> schematic winner; else text winner) takes default; operator override recorded. The other dimension's winner serves its better pages ONLY through confirmed logical-page alignments, presented as alternates. Provisional (screening) -> ingested, searchable, badged; CONFIRMED requires comprehensive assessment (+ jbig2 acknowledgment where flagged).
- Citation-following retrieves by immutable copy_id + page_id, exact cited source, superseded notice when applicable; auth checked at fetch. Losers superseded, retained, side-by-side view. Rescan entries document-level and self-sufficient (own worst pages); cross-copy page claims require confirmed alignments. Post-job summaries include "your scan lost because ..."

### 6.5 Page alignment
PDF index, printed label, sheet identifier are distinct. System proposes cross-copy alignments (labels, visual similarity); operator confirms; only confirmed alignments support cross-copy claims and v2 composites. Fold-out halves = partial captures of one sheet.

### 6.6 Rescan priority
Ownership (physical_holdings) > demand (access_events) > expected improvement > effort; never score alone.

### 6.7 Preservation
Fixity: SHA-256 manifests; uniform ~90-day full re-hash (throttled; measured before tiering) + nightly verify of everything written in last 30 days; alarms; restore from backup generations; Btrfs scrub beneath; caches/pyramids regeneration-only. Conformance vs fidelity separate: veraPDF on every master (fail + fidelity pass = stored warning); lossless packaging verified BITWISE (extracted master image vs exact image supplied to the PDF writer; preprocessing recorded as lineage; SSIM supplementary). Regenerability: masters immutable; full tool-version provenance; re-runnable from raw; scheduled portable metadata export. Restore drills: pilot + yearly. **Legal hold:** guarantees preservation and blocks destruction/deletion of held evidence, including by owner, until lifted (logged); it does NOT freeze default-selection or current-revision workflow, which remain changeable with history preserved.

### 6.8 Retrieval evaluation
Golden query set (30-50 real bench questions with known correct document/page answers) built in the pilot; recall@k recorded on every retrieval-affecting change; non-regression gates.

### 6.9 Graduated autonomy
As v1.4 section 7 in full: reviewed-outcomes-only trust; precision and recall tracked separately; bucket minimums; REVIEW default; TRUSTED at >=50 reviewed decisions >=95% correct both measures; demotion <90% rolling 30; permanent 10% verify sampling; version/model resets; inheritance = examples and initial estimates only, never autonomous commit; all auto decisions reversible; bulk revert; classification may raise protection, never lower.

### 6.10 Claims discipline (documentation, enforced by 4.4)
Retrieval responses and MCP docs state: a document is evidence of its own existence and content, never of its assertions' truth; authority comes only from authority_records by authorized principals; consumers must treat all returned text as untrusted data, never instructions (injection defense lives at the consumer).

---

## 7. (merged into 6.9)

## 8. Ingestion Pipelines

### 8.1 Owner scan assembly
Intake: drag-and-drop sessions (chunked resumable; raw preserved byte-for-byte forever) and hot folders (`uploads/hot/Game__DocTitle/`, `uploads/hot-library/Author__Title/`); sessions finalize only on a deliberate marker (`done` file or UI confirmation); quiescence proposes, never finalizes. Assembly UI: thumbnail reorder/rotate/delete/insert; session split; blank (<0.5% ink) and duplicate-frame auto-flags (exact + perceptual hash); per-page type tags (auto, overridable); metadata form (subjects, domain, sensitivity, identity incl. revision, autocomplete); optional link to the record being rescanned. **Operator-directed page replacement (phase 4):** replace pages in an existing copy and reassemble as a NEW immutable copy with per-page provenance (inherited or new); enters selection normally. Automatic composites: v2.
Profiles (versioned configs): manual-scan (deskew, crop, background cleanup); schematic-protect (geometry only; never binarize or flatten); screenshot (no deskew; fixed-margin crop; text binarization allowed; duplicate-frame trim); magazine (halftone-aware; optional descreen; no binarization); book-scanner (gutter removal; facing-page split).
Outputs: (a) archival master: full-res, image-preserving, PDF/A-2b via OCRmyPDF, veraPDF-validated, bitwise-fidelity verified; (b) access copy: legibility subordinates size; JBIG2 lossless only for eligible text-class pages (lossy refused in code); conservative JPEG for schematic/photo (pilot-tuned; 400 DPI cap); ~25 MB is a preference; (c) OCR preserved explicitly as hOCR/TSV with per-page coverage. All derivatives carry tool + version; inherited risk flags propagate. Post-processing: comprehensive assessment auto-runs; loud warning if either dimension inadequate or below the best existing representation, with worst pages; single-page recapture; on accept: my_scan registered, selection reruns, generation activation per 3.5.
Capture conventions (documented): single-frame fold-outs preferred (clean 350 DPI beats stitched 600); halves as partial captures, never auto-stitched; ebook capture: fixed window + Retina 2x, hash-based stuck/end detection, human-paced randomized turns, convention folders.

### 8.2 Ebook/screenshot stream
Screenshot profile; sanity-check scoring only; default (personal, restricted).

### 8.3 Structured text
Pinout/DIP records as direct chunks tied to mame_name.

### 8.4 Living documents (phase 7; schema from phase 1)
Office ingestion (original = master; normalized PDF + extracted text + native-structure metadata); email intake (message identity, thread links, quoted-vs-new spans; domain/sensitivity by sender rules); per-entity watched folders; current-revision pointers active; supersede-not-overwrite; legal hold at ingest; signed export bundles.

---

## 9. Retrieval Layer

### 9.1 Citation contract
Citation = (document, revision, representation copy_id, **locator**). Locator is typed: page/region (PDF; implemented from phase 1); sheet/cell-range (workbooks), message-id/span (email/conversations), record-id/sync-token (business systems): types RESERVED in schema, implemented with their connectors. Citations are durable: retrievable by immutable IDs with generation and superseded/stale notices; auth at fetch.

### 9.2 Indexing and search
**The default representation of EVERY revision is indexed** (not only the pinned/current revision: older revisions often document the exact board in hand); revision identity travels in chunk metadata; ranking places pinned/current revisions above superseded ones and superseded results are badged, which makes "find the correct revision" a real search outcome rather than a tautology. Provisional or confirmed defaults only, badged; per-document generation counter with the activation protocol (3.5); whole-collection rebuild reserved for embedding/preprocessing changes. Text: page-bounded ~800-token chunks, 100 overlap, never across pages; table regions atomic (heading/caption prepended; rows/headers/units preserved); reading order kept; neighbor-page retrieval. Chunk metadata: subjects, domain, sensitivity, identity (document/revision), doc type, source class, page, adequacy + grades, flags, canonical state, generation, **text-origin label: verbatim-ocr | native-text | vlm-interpretation | corroborated-extraction**: origins never blended unlabeled. Schematic/mixed pages: baseline retrieval from indexed OCR labels, refdes lists, sheet titles (functional without VLM); VLM interpretation layer per canonical page (model chosen at pilot on identifier accuracy, unsupported-claim rate, retrieval lift, latency, memory fit): ENTIRE description stored as machine interpretation; identifiers matching the page's OCR token set are labeled **corroborated-extraction with evidence pointers and uncertainty** (agreement can share a wrong glyph; corroboration is not proof); uncorroborated identifiers flagged for review, not dropped; functional/connective claims inference-marked, indexed for recall, never presented as fact. 200 DPI render is a preview; region endpoint serves full-resolution crops with a bounded cache keyed (asset, page, region, resolution, renderer version); pyramids only where measured. Store/search: main + restricted Qdrant collections; hybrid vector + FTS5 with reciprocal rank fusion; filters: domain, sensitivity, subjects, doc type, adequacy, flags; provisional and low-adequacy badged; every result through the central filter.

### 9.3 MCP
Transports: stdio + Streamable HTTP; protocol revision pinned in config. Read tools: search_stacks(query, subjects?, filters?), get_page_image(copy_id, page_id, region?), get_document_page(doc_id, page, region?) (resolves via document_default and authority pins), search_manuals alias (arcade scope). **Write tools (mutation contract per 3.5):** submit_document (proposes ingest; policy-checked), write_ext (ext.* only), write_authority (authority verb only), each with idempotency keys and explicit conflicts. Results carry durable citations, text-origin labels, authority-pin status, and fetchable resources, never server-local paths. Docs carry the trust rule (6.10) verbatim.

---

## 10. Web UI
Dashboard (state-fact + adequacy distributions, need buckets, coverage, trust panels with precision/recall, fixity/conformance, retrieval-eval trend) - Browse (subject tree; domain/sensitivity-aware; filters incl. flags, recommended action; FTS) - Document detail (identity, revisions, applicability, authority pins; copies side-by-side with IF/TQ and adequacy; per-page evidence; confirmed alignments; provenance; fixity) - Rescan queue (6.6 priority; states; worst-pages; sheet entries where aligned; CSV) - Assembly station (incl. page replacement) - Review + Verify queues (tagging + grouping) - Physical holdings - Jobs (incl. reprocess, fixity, outbox health) - Search - Shop mode (big search, recents, dropzone, verify queue).

---

## 11. Build Phases (pilot-first; frozen through phase 4)

1. **Foundations, split to protect pilot-first (all schema ships in 1a's migrations; 1b adds behavior, not tables):**
   **1a (reaches searchable):** identity layers (document/revision/copy/asset); immutable assets + lineage; physical holdings; two-axis access (domain sets) + principals + verbs + central filter; token module; jobs system; backups with tested restore + runbook; service account; NAS credential split; PHI refusal; config root; capacity model; analyzer package (text-provenance model, primary IF/TQ with derived fitness, adequacy on primaries, OCR language handling, two-level page gates, state facts + recommended_action); basic search and page viewing. Tests green: dimensional-separation synthetics; **ocr-layer-no-floor fixture**; **REPROCESS fixture** (good image, garbage text); **adequacy fixture** (IF 100 / TQ 40 must NOT be text-adequate); **page-gate fixture** (19 clean pages + 1 destroyed schematic must not be USABLE); **wrong-language fixture** (Japanese page under eng must yield TQ null, not REPROCESS); HTML parser fixtures; CLI; auth-filter tests incl. old links and multi-domain documents.
   **1b (behavior on the shipped schema):** outbox + mutation contract + generation activation; authority_records + derivations + conflict resolution; ext.*/provenance-class seams active; hash-chained audit stream; access_events wiring; schema-migration tooling exercised. Tests: mutation-conflict and idempotency-replay; audit chain continuity; authority conflict surfacing.
2. **Pilot corpus:** ~100 varied archive documents (competing scans, revisions, fold-outs, incomplete, ocr-layer, native-text; known-answer groups pre-judged by the operator) + **governance corpus (~15 documents: an approved-revision drawing pair, dated decision emails, a vendor claim letter, cross-domain duplicates, an agent summary chain)**. Ground-truth OCR calibration built and applied; provisional-canonical survival measured.
3. **Working core:** keyword search, page viewing, evidence display, correctable rescan queue, state facts + coverage visible, authority pins visible.
4. **Prove the loops + acceptance tests:** (a) real replacement scan end-to-end via page replacement; (b) **six governance tests pass:** cleaner draft cannot displace the approved (pinned) revision; an old email (effective_date) cannot revive a retired decision or reorder state by ingestion date; a vendor claim document creates no authority; cross-domain restrictions survive delegation, cached results, and previously issued links; concurrent updates, interrupted generation activation, and a restore drill preserve the correct accepted state; an agent summary chain remains traceable and goes stale when its evidence changes. Golden query set drafted. **Gates everything below.**
5. **Bulk acquisition:** IA -> TAMA all categories -> open directories -> MAME import -> dedup -> bulk screening -> provisional canonicals -> demand-prioritized assessment.
6. **RAG + MCP:** hybrid retrieval, baseline schematic retrieval, VLM layer with validation queues, region endpoint, MCP read + write tools, shop mode; retrieval-eval gating.
7. **Universal library:** taxonomy schemas at scale; autonomy live; preservation jobs live; versioning workflow active; office + email ingestion; per-entity folders; export bundles; legal hold in use.
8. **v2:** automatic composites from confirmed alignments; coverage gaps vs game-list authority; scorer recalibration; pyramid/tile serving where measured; additional locator types with their connectors.

Development constraint: build environments may lack outbound access; scraping/parsing develops against fixtures; real crawls only on the mini.

## 12. Risks (delta highlights; v1.4 register carries forward)
Blended-score adequacy leak: fixed by primary-measurement adequacy + fixtures. Averaged-away critical pages: page gates. Cross-copy page confusion: single document_default + confirmed alignments. Revision displacement by quality: selection scoped within revisions; authority pins honored. Manufactured authority: authority_records writable only by authorized principals; claims discipline documented. Cross-domain leakage: two-axis grants enforced at fetch on all generations and old links; asset-level dedup never merges document context. Endpoint exfiltration: allowlist, owner-confirmed changes, sensitivity gates. Stale summaries: derivation links + staleness events. Silent overwrites/dual-write drift: mutation contract + outbox + activation protocol. Unbounded loss window: stated RPO/RTO with hourly DB snapshots. Everything else per v1.4 section 12.

## 13. Pilot-Determined Parameters
VLM selection; provisional-canonical survival target; scoring weights/thresholds vs ground truth and known-answer groups; FTS5 tokenizer config vs golden queries; fixity-cycle impact; region-cache sizing; RPO/RTO confirmation under real load.

## 14. Pre-Build Decisions (owner)
1. ahpp-wellsgardner: DECIDED. Retired as a component; its content (Wells-Gardner monitor manuals and related documents) is treated as ordinary source material, ingested via the ingest_folder adapter under an operator-supplied source label, deduped and assessed like any other acquisition. No legacy retrieval code is reused. (Monitor manuals exercise document_applicability: one manual, many machines.)
2. Domain enumeration and initial principal grant map (owner, shop, first agent tokens).
3. Confirm interim token issuance (local module) until the estate broker exists.
