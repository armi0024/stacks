"""Two-level page gates (SPEC P6/P15) on required-content pages.

Required-content pages are schematic-class pages in schematic-implied
documents plus operator-designated critical pages. Image gate: score < 50
blocks USABLE; 50..adequacy forces a visible "degraded critical page" label.
Text gate mirrors it, evaluated on the post-REPROCESS basis when a re-OCR was
run (--measure-achievable), otherwise as-shipped with the basis recorded.
TQ-null pages never gate: unknown is not failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stacks.analyzer.config import AnalyzerSettings


@dataclass
class GateHit:
    page_index: int
    dimension: str  # image | text
    score: float
    reason: str


@dataclass
class GateResult:
    basis: str  # as_shipped | post_reprocess
    required_pages: list[int] = field(default_factory=list)
    blocking: list[GateHit] = field(default_factory=list)
    degraded: list[GateHit] = field(default_factory=list)
    pages_below_adequacy: dict = field(default_factory=dict)

    @property
    def blocks_usable(self) -> bool:
        return bool(self.blocking)

    @property
    def degraded_critical_page(self) -> bool:
        return bool(self.degraded)


def evaluate(
    pages: list,
    schematic_implied: bool,
    critical_pages: set[int],
    basis: str,
    s: AnalyzerSettings,
) -> GateResult:
    """pages: PageResult-like with .index, .assessed, .content_class,
    .if_score, .tq_score, .tq_is_null."""
    result = GateResult(basis=basis)
    required: set[int] = set(critical_pages)
    if schematic_implied:
        required.update(
            p.index for p in pages if p.assessed and p.content_class == "schematic"
        )
    result.required_pages = sorted(required)

    assessed = [p for p in pages if p.assessed]
    result.pages_below_adequacy = {
        "image": sum(
            1 for p in assessed if p.if_score is not None and p.if_score < s.image_adequacy_threshold
        ),
        "text": sum(
            1 for p in assessed if p.tq_score is not None and p.tq_score < s.text_adequacy_threshold
        ),
    }

    for p in assessed:
        if p.index not in required:
            continue
        label = f"page {p.index + 1}"
        if p.if_score is not None:
            if p.if_score < s.page_gate_block_threshold:
                result.blocking.append(
                    GateHit(
                        p.index,
                        "image",
                        p.if_score,
                        f"{label}: required-content page image fidelity {p.if_score:.0f} is below "
                        f"the hard floor {s.page_gate_block_threshold:.0f} — blocks USABLE "
                        "regardless of document averages",
                    )
                )
            elif p.if_score < s.image_adequacy_threshold:
                result.degraded.append(
                    GateHit(
                        p.index,
                        "image",
                        p.if_score,
                        f"{label}: required-content page image fidelity {p.if_score:.0f} is between "
                        f"{s.page_gate_block_threshold:.0f} and the adequacy threshold "
                        f"{s.image_adequacy_threshold:.0f} — degraded critical page",
                    )
                )
        if p.tq_score is not None and not p.tq_is_null:
            if p.tq_score < s.page_gate_block_threshold:
                result.blocking.append(
                    GateHit(
                        p.index,
                        "text",
                        p.tq_score,
                        f"{label}: required-content page text quality {p.tq_score:.0f} "
                        f"({basis.replace('_', '-')}) is below the hard floor "
                        f"{s.page_gate_block_threshold:.0f} — blocks USABLE",
                    )
                )
            elif p.tq_score < s.text_adequacy_threshold:
                result.degraded.append(
                    GateHit(
                        p.index,
                        "text",
                        p.tq_score,
                        f"{label}: required-content page text quality {p.tq_score:.0f} is between "
                        f"{s.page_gate_block_threshold:.0f} and the adequacy threshold "
                        f"{s.text_adequacy_threshold:.0f} — degraded critical page",
                    )
                )
    return result
