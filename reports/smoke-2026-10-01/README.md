# Smoke-run baseline — 2026-10-01

Milestone 1 acceptance record: `stacks-analyze` (commit `ba38fa2`,
analysis_config_hash `2d1607d2e7ef...`, full hash inside each report) run in
comprehensive assess mode against the IA ORIGINAL PDF of five operator-named
items copied from `/Users/Shared/stacks-rw/archive/ia/` (read-only source;
derivative `_text.pdf` files were not assessed, per SPEC 5: derivatives are
triage only).

The pilot diffs against these reports after scorer recalibration. Scores are
comparable only within the recorded analysis_config_hash (SPEC 6.1 /
v1.5 change 12); post-recalibration runs will carry a different hash and the
diff is the point.

| Report | Language | Action |
|---|---|---|
| 18_wheeler_std_wiring_diagrams.json | detected rus (OSD misfire) -> TQ null | USABLE [text quality unknown] |
| 18_wheeler_std_wiring_diagrams.lang-eng.json | forced --lang eng | REPROCESS (TQ 48: lettering genuinely hard) |
| 1942_schematics_capcom.json | detected rus (OSD misfire) -> TQ null | RESCAN_CANDIDATE (pages 7/8/15 below hard floor) |
| 1945-i-ii-the-arcade-game-manual-french.json | French ran under eng (finding 2) | USABLE |
| 4_player_football_tm-139_1st_printing.json | eng | USABLE [degraded critical page, jbig2_unknown_history] |
| after_burner_owner_s_manual_420-5798-0.json | detected jpn (cover-art OSD misfire; English manual) | RESCAN_CANDIDATE |

Findings and dispositions (owner, 2026-10-01):
1. OSD on schematic-heavy/cover first pages misdetects (rus/jpn above):
   Milestone 2 adds OSD voting across text-class pages + identity-language
   default; operator override retained. TQ-null behavior on misdetection is
   correct per SPEC P2 (IA's own pipeline committed to Cyrillic garbage on
   18 Wheeler; Stacks reported unknown instead).
2. Latin-script languages were invisible (French ran under eng with
   confident-looking output): fra/deu/spa/ita added to the default available
   set + wordlist-based Latin-script detection; eng fallback only when
   ambiguous, language used always recorded.
3. Wiring diagrams under-classify as text-class (long-straight-line heuristic):
   documented limitation; threshold calibration belongs to the pilot
   ground-truth set. --schematic-implied now also raises the DPI target for
   the whole document, not only the critical-content check.
4. Note: the "1945 I & II (french)" IA item is a PlayStation 2 game manual,
   not an arcade manual.
