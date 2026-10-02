"""State facts and recommended_action (SPEC 6.3), standalone scope.

Standalone there is no group, no acquisition coverage, and N=1 (P4):
completeness is unknown with missing_fraction 0 and no penalty, the <80%
floor inapplicable; the critical-content check still applies. RESCAN cannot
be fully asserted without group/coverage evidence, so image-inadequate
representations get RESCAN_CANDIDATE with the pending check named.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stacks.analyzer.gates import GateResult
from stacks.analyzer.scoring import DocScores


@dataclass
class Completeness:
    status: str  # confirmed | known-incomplete | unknown
    missing_fraction: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    action: str  # USABLE | REPROCESS | RESCAN_CANDIDATE
    labels: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    provisional: bool = False


def assess_completeness(schematic_implied: bool, schem_page_count: int) -> Completeness:
    c = Completeness(status="unknown", missing_fraction=0.0)
    c.reasons.append(
        "single representation (N=1): completeness unknown, missing_fraction 0, "
        "no penalty, expected-length floor inapplicable (SPEC P4)"
    )
    if schematic_implied and schem_page_count == 0:
        c.status = "known-incomplete"
        c.reasons.append(
            "document is schematic-implied but contains zero schematic-class pages: "
            "known-incomplete (critical-content check; a clue, not proof)"
        )
    return c


def recommend(
    scores: DocScores,
    gates: GateResult,
    completeness: Completeness,
    mode: str,
    worst_pages: list[tuple[int, float]],
    tq_unknown_reason: str | None,
    suspect_pages: list[int],
    risk_flags: list[str],
) -> Recommendation:
    rec = Recommendation(action="USABLE", provisional=(mode == "screening"))
    img_ok = scores.image_adequate()
    txt_ok = scores.text_adequate()

    def worst_pages_text() -> str:
        return ", ".join(f"page {i + 1} (IF {sc:.0f})" for i, sc in worst_pages[:5])

    image_gate_blocks = [h for h in gates.blocking if h.dimension == "image"]
    text_gate_blocks = [h for h in gates.blocking if h.dimension == "text"]

    if img_ok is False or image_gate_blocks:
        rec.action = "RESCAN_CANDIDATE"
        if img_ok is False:
            rec.reasons.append(
                "image fidelity is inadequate on this representation; a full RESCAN decision "
                "needs the group/coverage check (every group copy inadequate among sources "
                "successfully checked), which is pending in standalone analysis"
            )
        for h in image_gate_blocks:
            rec.reasons.append(h.reason)
        if worst_pages:
            rec.reasons.append(f"worst pages: {worst_pages_text()}")
    elif txt_ok is False or text_gate_blocks:
        rec.action = "REPROCESS"
        if txt_ok is False:
            rec.reasons.append(
                "image is adequate but text quality is inadequate: local re-OCR / "
                "re-optimization should be attempted before any rescan"
            )
        for h in text_gate_blocks:
            rec.reasons.append(h.reason)
    else:
        rec.action = "USABLE"
        rec.reasons.append("image and text are adequate and page gates pass")
        if txt_ok is None:
            rec.labels.append("text quality unknown")
            rec.reasons.append(
                tq_unknown_reason
                or "text quality could not be measured: unknown, not failed (never penalized)"
            )
        if completeness.status == "unknown":
            rec.labels.append("unverified")
            rec.reasons.append('completeness is unknown: labeled "unverified", not penalized')

    if completeness.status == "known-incomplete":
        rec.labels.append("INCOMPLETE")
        rec.reasons.extend(completeness.reasons[1:])

    if gates.degraded_critical_page:
        rec.labels.append("degraded critical page")
        for h in gates.degraded:
            rec.reasons.append(h.reason)

    if suspect_pages:
        rec.labels.append("text layer suspect")
        pages_txt = ", ".join(str(i + 1) for i in suspect_pages[:10])
        rec.reasons.append(
            f"embedded OCR layer on page(s) {pages_txt} fails extraction-quality checks: "
            "text layer suspect; achievable quality unknown; rerun with --measure-achievable"
        )

    for flag in risk_flags:
        rec.labels.append(flag)

    if rec.provisional:
        rec.labels.append("provisional")
        rec.reasons.append(
            "screening mode: sampled pages only; unsampled pages are UNASSESSED and this "
            "result is provisional until comprehensive assessment"
        )
    return rec
