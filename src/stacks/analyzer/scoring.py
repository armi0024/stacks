"""Derived scores (SPEC 6.1): per-page IF/TQ 0-100, document rollups,
adequacy on PRIMARY measurements, fitness blends (ranking only), grades
(display only). Every deduction emits a plain-language reason with evidence.
Missing measurements are null — unknown, never imputed, never penalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stacks.analyzer.config import AnalyzerSettings
from stacks.analyzer.image_fidelity import IFMetrics
from stacks.analyzer.text_quality import TQMetrics


@dataclass
class Deduction:
    code: str
    points: float
    reason: str


SCHEMATIC_CLASSES = ("schematic", "mixed")
TEXT_CLASSES = ("text", "mixed")


def score_image_fidelity(
    m: IFMetrics,
    content_class: str,
    s: AnalyzerSettings,
    force_schematic_target: bool = False,
) -> tuple[float, list[Deduction]]:
    """force_schematic_target: operator asserted the document is
    schematic-implied (--schematic-implied / identity doc type), so every page
    is held to the schematic DPI target even when the content classifier
    calls it text-class (wiring diagrams under-classify; disposition 4)."""
    deds: list[Deduction] = []
    schematic_class = content_class in SCHEMATIC_CLASSES
    use_schematic_target = schematic_class or force_schematic_target
    target = s.dpi_target_schematic if use_schematic_target else s.dpi_target_text
    if m.effective_dpi is not None and m.effective_dpi < target:
        pts = s.dpi_deduction_max * (target - m.effective_dpi) / target
        if schematic_class:
            target_why = "schematic-class pages"
        elif force_schematic_target:
            target_why = "all pages of an operator-asserted schematic-implied document"
        else:
            target_why = "text-class pages"
        deds.append(
            Deduction(
                "dpi_below_target",
                pts,
                f"effective DPI {m.effective_dpi:.0f} (from rendered placement) is below the "
                f"{target:.0f} DPI target for {target_why}",
            )
        )
        if m.effective_dpi < s.dpi_low_threshold:
            deds.append(
                Deduction(
                    "dpi_very_low",
                    s.dpi_low_deduction,
                    f"effective DPI {m.effective_dpi:.0f} is below {s.dpi_low_threshold:.0f}",
                )
            )
    if m.sharpness is not None and m.sharpness < s.sharpness_min:
        deds.append(
            Deduction(
                "low_sharpness",
                s.sharpness_deduction,
                f"Laplacian sharpness {m.sharpness:.0f} is below {s.sharpness_min:.0f} (blurry capture)",
            )
        )
    if m.speckle_index is not None and m.speckle_index > s.speckle_max:
        deds.append(
            Deduction(
                "speckle",
                s.speckle_deduction,
                f"background speckle {m.speckle_index:.1f} exceeds {s.speckle_max:.0f} "
                "(noisy background per 100k pixels)",
            )
        )
    if m.skew_deg is not None and m.skew_deg > s.skew_max_deg:
        deds.append(
            Deduction(
                "skew", s.skew_deduction, f"page skew {m.skew_deg:.1f} deg exceeds {s.skew_max_deg} deg"
            )
        )
    if m.is_1bit and schematic_class:
        deds.append(
            Deduction(
                "onebit_schematic",
                s.onebit_schematic_deduction,
                "1-bit (bitonal) image on a schematic-class page loses line/gray detail",
            )
        )
    score = max(0.0, 100.0 - sum(d.points for d in deds))
    return score, deds


def score_text_quality(m: TQMetrics, s: AnalyzerSettings) -> tuple[float | None, list[Deduction]]:
    if m.null:
        return None, []
    deds: list[Deduction] = []
    if m.mean_conf is not None and m.mean_conf < s.conf_mean_target:
        pts = s.conf_mean_deduction_max * (s.conf_mean_target - m.mean_conf) / s.conf_mean_target
        deds.append(
            Deduction(
                "low_mean_confidence",
                pts,
                f"mean OCR confidence {m.mean_conf:.0f} is below {s.conf_mean_target:.0f}",
            )
        )
    if m.low_conf_fraction is not None and m.low_conf_fraction > s.low_conf_frac_target:
        span = s.low_conf_frac_full_at - s.low_conf_frac_target
        pts = s.low_conf_frac_deduction_max * min(
            1.0, (m.low_conf_fraction - s.low_conf_frac_target) / span
        )
        deds.append(
            Deduction(
                "low_confidence_fraction",
                pts,
                f"{m.low_conf_fraction:.0%} of words fall below confidence "
                f"{s.low_conf_threshold:.0f} (threshold {s.low_conf_frac_target:.0%})",
            )
        )
    if m.small_text_conf is not None and m.small_text_conf < s.small_text_conf_min:
        deds.append(
            Deduction(
                "small_text",
                s.small_text_deduction,
                f"small text (bbox <= {s.small_text_px:.0f} px, {m.small_text_count} words) "
                f"averages confidence {m.small_text_conf:.0f}, below {s.small_text_conf_min:.0f}",
            )
        )
    if m.refdes_conf is not None and m.refdes_conf < s.refdes_conf_min:
        deds.append(
            Deduction(
                "refdes_tokens",
                s.refdes_deduction,
                f"reference-designator/part-number tokens ({m.refdes_count}) average confidence "
                f"{m.refdes_conf:.0f}, below {s.refdes_conf_min:.0f} (0/O 1/l/I 5/S 8/B risk)",
            )
        )
    if m.garble_fraction is not None and m.garble_fraction > 0:
        pts = s.garble_deduction_max * min(1.0, m.garble_fraction / 0.8)
        if pts >= 1.0:
            deds.append(
                Deduction(
                    "extraction_garble",
                    pts,
                    f"{m.garble_fraction:.0%} of the {m.source} text fails extraction-quality "
                    "checks (implausible words)",
                )
            )
    score = max(0.0, 100.0 - sum(d.points for d in deds))
    return score, deds


def grade(score: float | None, s: AnalyzerSettings) -> str | None:
    if score is None:
        return None
    if score >= s.grade_a:
        return "A"
    if score >= s.grade_b:
        return "B"
    if score >= s.grade_c:
        return "C"
    if score >= s.grade_d:
        return "D"
    return "F"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass
class DocScores:
    if_text: float | None = None
    tq_text: float | None = None
    if_schem: float | None = None
    tq_schem: float | None = None
    image_adequate_text: bool | None = None
    text_adequate_text: bool | None = None
    image_adequate_schem: bool | None = None
    text_adequate_schem: bool | None = None
    fitness_text: float | None = None
    fitness_schem: float | None = None
    grades: dict = field(default_factory=dict)
    text_page_count: int = 0
    schem_page_count: int = 0

    def image_adequate(self) -> bool | None:
        """Adequate in every dimension that exists; None if nothing measured."""
        dims = [d for d in (self.image_adequate_text, self.image_adequate_schem) if d is not None]
        return all(dims) if dims else None

    def text_adequate(self) -> bool | None:
        dims = [d for d in (self.text_adequate_text, self.text_adequate_schem) if d is not None]
        return all(dims) if dims else None


def rollup(pages: list, s: AnalyzerSettings) -> DocScores:
    """pages: PageResult-like objects with .content_class, .assessed,
    .if_score, .tq_score (gate-basis score used for rollups)."""
    out = DocScores()
    text_pages = [p for p in pages if p.assessed and p.content_class in TEXT_CLASSES]
    schem_pages = [p for p in pages if p.assessed and p.content_class in SCHEMATIC_CLASSES]
    out.text_page_count = len(text_pages)
    out.schem_page_count = len(schem_pages)

    out.if_text = _mean([p.if_score for p in text_pages if p.if_score is not None])
    out.tq_text = _mean([p.tq_score for p in text_pages if p.tq_score is not None])
    out.if_schem = _mean([p.if_score for p in schem_pages if p.if_score is not None])
    out.tq_schem = _mean([p.tq_score for p in schem_pages if p.tq_score is not None])

    # Adequacy: primary measurements only (SPEC v1.5 change 3); never from blends.
    out.image_adequate_text = None if out.if_text is None else out.if_text >= s.image_adequacy_threshold
    out.text_adequate_text = None if out.tq_text is None else out.tq_text >= s.text_adequacy_threshold
    out.image_adequate_schem = (
        None if out.if_schem is None else out.if_schem >= s.image_adequacy_threshold
    )
    out.text_adequate_schem = (
        None if out.tq_schem is None else out.tq_schem >= s.text_adequacy_threshold
    )

    # Fitness blends: RANKING ONLY, never adequacy.
    if out.if_text is not None and out.tq_text is not None:
        out.fitness_text = s.text_fitness_if_weight * out.if_text + s.text_fitness_tq_weight * out.tq_text
    if out.if_schem is not None and out.tq_schem is not None:
        out.fitness_schem = (
            s.schem_fitness_if_weight * out.if_schem + s.schem_fitness_tq_weight * out.tq_schem
        )
    out.grades = {
        "if_text": grade(out.if_text, s),
        "tq_text": grade(out.tq_text, s),
        "if_schem": grade(out.if_schem, s),
        "tq_schem": grade(out.tq_schem, s),
    }
    return out
