"""Hand-computed checks of the SPEC 6.1 deduction formulas and rollups."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from stacks.analyzer.config import AnalyzerSettings
from stacks.analyzer.image_fidelity import IFMetrics
from stacks.analyzer.scoring import (
    grade,
    rollup,
    score_image_fidelity,
    score_text_quality,
)
from stacks.analyzer.text_quality import TQMetrics, null_tq

S = AnalyzerSettings()


def ifm(**kw) -> IFMetrics:
    base = dict(
        effective_dpi=None, bpc=8, is_1bit=False, sharpness=1000.0,
        rms_contrast=50.0, speckle_index=0.0, skew_deg=0.0, render_dpi=300.0,
    )
    base.update(kw)
    return IFMetrics(**base)


class TestImageFidelityFormula:
    def test_perfect_page(self):
        score, deds = score_image_fidelity(ifm(effective_dpi=300), "text", S)
        assert score == 100 and not deds

    def test_dpi_proportional_text_target(self):
        score, deds = score_image_fidelity(ifm(effective_dpi=150), "text", S)
        assert score == pytest.approx(100 - 40 * (220 - 150) / 220)
        assert [d.code for d in deds] == ["dpi_below_target"]

    def test_dpi_below_150_additional(self):
        score, _ = score_image_fidelity(ifm(effective_dpi=100), "text", S)
        assert score == pytest.approx(100 - 40 * (220 - 100) / 220 - 10)

    def test_schematic_target_is_300(self):
        score, _ = score_image_fidelity(ifm(effective_dpi=250), "schematic", S)
        assert score == pytest.approx(100 - 40 * (300 - 250) / 300)
        score_t, _ = score_image_fidelity(ifm(effective_dpi=250), "text", S)
        assert score_t == 100  # 250 >= 220

    def test_sharpness_speckle_skew(self):
        score, deds = score_image_fidelity(
            ifm(effective_dpi=300, sharpness=100, speckle_index=7, skew_deg=2.0), "text", S
        )
        assert score == pytest.approx(100 - 15 - 8 - 6)
        assert {d.code for d in deds} == {"low_sharpness", "speckle", "skew"}

    def test_onebit_only_on_schematic_class(self):
        s_schem, _ = score_image_fidelity(ifm(effective_dpi=300, bpc=1, is_1bit=True), "schematic", S)
        s_text, _ = score_image_fidelity(ifm(effective_dpi=300, bpc=1, is_1bit=True), "text", S)
        assert s_schem == 88 and s_text == 100

    def test_all_deductions_stack(self):
        score, deds = score_image_fidelity(
            ifm(effective_dpi=20, sharpness=0, speckle_index=99, skew_deg=10, bpc=1, is_1bit=True),
            "schematic", S,
        )
        expected = 100 - (40 * (300 - 20) / 300 + 10 + 15 + 8 + 6 + 12)
        assert score == pytest.approx(expected)
        assert len(deds) == 6

    def test_force_schematic_target_on_text_class(self):
        # disposition 4: asserted schematic-implied docs hold every page to 300
        plain, _ = score_image_fidelity(ifm(effective_dpi=250), "text", S)
        forced, deds = score_image_fidelity(
            ifm(effective_dpi=250), "text", S, force_schematic_target=True
        )
        assert plain == 100
        assert forced == pytest.approx(100 - 40 * (300 - 250) / 300)
        assert "schematic-implied" in deds[0].reason

    def test_missing_dpi_never_penalized(self):
        score, deds = score_image_fidelity(ifm(effective_dpi=None), "text", S)
        assert score == 100 and not deds

    def test_every_deduction_has_reason(self):
        _, deds = score_image_fidelity(
            ifm(effective_dpi=100, sharpness=50, speckle_index=9, skew_deg=3), "schematic", S
        )
        assert all(d.reason for d in deds)


class TestTextQualityFormula:
    def test_confidence_proportional(self):
        m = TQMetrics(source="ocr-fresh", word_count=50, mean_conf=40.0)
        score, _ = score_text_quality(m, S)
        assert score == pytest.approx(100 - 30 * (80 - 40) / 80)

    def test_low_conf_fraction_scales_to_full(self):
        m = TQMetrics(source="ocr-fresh", word_count=50, low_conf_fraction=0.50)
        score, _ = score_text_quality(m, S)
        assert score == pytest.approx(85)
        m2 = TQMetrics(source="ocr-fresh", word_count=50, low_conf_fraction=0.10)
        assert score_text_quality(m2, S)[0] == 100

    def test_small_text_and_refdes(self):
        m = TQMetrics(
            source="ocr-fresh", word_count=50,
            small_text_conf=60.0, small_text_count=5,
            refdes_conf=70.0, refdes_count=4,
        )
        score, deds = score_text_quality(m, S)
        assert score == pytest.approx(100 - 10 - 12)
        assert {d.code for d in deds} == {"small_text", "refdes_tokens"}

    def test_garble_up_to_40(self):
        m = TQMetrics(source="native-text", word_count=50, garble_fraction=0.8)
        assert score_text_quality(m, S)[0] == pytest.approx(60)
        m2 = TQMetrics(source="native-text", word_count=50, garble_fraction=0.4)
        assert score_text_quality(m2, S)[0] == pytest.approx(80)

    def test_null_is_unknown_not_zero(self):
        score, deds = score_text_quality(null_tq("ocr-fresh", "x"), S)
        assert score is None and deds == []

    def test_missing_metrics_never_penalized(self):
        m = TQMetrics(source="ocr-fresh", word_count=50)  # everything None
        assert score_text_quality(m, S)[0] == 100


class TestGrades:
    @pytest.mark.parametrize(
        "score,expected",
        [(100, "A"), (88, "A"), (87.9, "B"), (78, "B"), (77.9, "C"),
         (68, "C"), (67.9, "D"), (55, "D"), (54.9, "F"), (0, "F")],
    )
    def test_boundaries(self, score, expected):
        assert grade(score, S) == expected

    def test_null(self):
        assert grade(None, S) is None


@dataclass
class FakePage:
    content_class: str
    if_score: float | None
    tq_score: float | None
    assessed: bool = True
    index: int = 0
    tq_is_null: bool = False


class TestRollup:
    def test_dimensions_do_not_bleed(self):
        pages = [
            FakePage("text", 90, 95),
            FakePage("schematic", 40, 50),
        ]
        sc = rollup(pages, S)
        assert sc.if_text == 90 and sc.if_schem == 40
        assert sc.image_adequate_text is True
        assert sc.image_adequate_schem is False
        assert sc.image_adequate() is False

    def test_mixed_counts_in_both(self):
        sc = rollup([FakePage("mixed", 80, 80)], S)
        assert sc.if_text == 80 and sc.if_schem == 80

    def test_blank_and_unknown_excluded(self):
        sc = rollup([FakePage("blank", None, None), FakePage("unknown", None, None)], S)
        assert sc.if_text is None and sc.if_schem is None
        assert sc.image_adequate() is None

    def test_fitness_weights(self):
        sc = rollup([FakePage("text", 80, 60), FakePage("schematic", 80, 60)], S)
        assert sc.fitness_text == pytest.approx(0.5 * 80 + 0.5 * 60)
        assert sc.fitness_schem == pytest.approx(0.7 * 80 + 0.3 * 60)

    def test_null_tq_pages_excluded_from_tq_mean(self):
        pages = [FakePage("text", 90, 95), FakePage("text", 90, None, tq_is_null=True)]
        sc = rollup(pages, S)
        assert sc.tq_text == 95  # the null page does not drag the mean

    def test_adequacy_is_primary_not_blend(self):
        # IF 100 / TQ 40: blend is 70 (would "pass"), primary must fail
        sc = rollup([FakePage("text", 100, 40)], S)
        assert sc.fitness_text == pytest.approx(70)
        assert sc.text_adequate() is False
        assert sc.image_adequate() is True

    def test_unassessed_pages_excluded(self):
        sc = rollup([FakePage("text", 90, 90), FakePage("text", 10, 10, assessed=False)], S)
        assert sc.if_text == 90
