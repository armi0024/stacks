"""The six 11.1a contract fixtures (SPEC phase 1a) plus the JBIG2 structural
fixture (owner addition A). These are the analyzer's acceptance tests."""

from __future__ import annotations

import pytest

from stacks.analyzer.analyze import analyze_pdf
from stacks.analyzer.config import AnalyzerSettings
from stacks.analyzer.report import to_dict


@pytest.fixture(scope="module")
def settings() -> AnalyzerSettings:
    return AnalyzerSettings()


class TestDimensionalSeparation:
    """Pristine text pages + degraded schematic pages: dimensions never bleed."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(fixtures.get("dimensional"), lang_override="eng")

    def test_text_dimension_high(self, result):
        assert result.scores.if_text >= 85
        assert result.scores.tq_text >= 85

    def test_schematic_dimension_low(self, result):
        assert result.scores.if_schem is not None
        assert result.scores.if_schem < 75
        assert result.scores.if_schem < result.scores.if_text

    def test_adequacy_per_dimension(self, result):
        assert result.scores.image_adequate_text is True
        assert result.scores.image_adequate_schem is False
        assert result.scores.image_adequate() is False  # any inadequate dim fails

    def test_page_classes(self, result):
        classes = [p.content_class for p in result.pages]
        assert classes.count("text") == 2
        assert classes.count("schematic") == 2


class TestOcrLayerNoFloor:
    """An embedded text layer never exempts image assessment; no score floors."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(fixtures.get("ocr_layer_no_floor"), lang_override="eng")

    def test_pages_are_ocr_layer(self, result):
        assert all(p.page_type == "ocr-layer" for p in result.pages)

    def test_image_assessment_ran_fully(self, result):
        for p in result.pages:
            m = p.if_metrics
            assert m is not None
            assert m.effective_dpi == pytest.approx(100, abs=3)
            assert m.sharpness is not None

    def test_no_floor_despite_good_text_layer(self, result):
        # the layer itself is clean English...
        assert result.scores.tq_text >= 85
        # ...yet image fidelity scores on its own merits, well below adequacy
        assert all(p.if_score < 60 for p in result.pages)
        assert result.scores.image_adequate() is False
        assert result.recommendation.action == "RESCAN_CANDIDATE"


class TestReprocess:
    """Good image + garbage shipped OCR layer -> REPROCESS, never RESCAN."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(fixtures.get("reprocess"), lang_override="eng")

    def test_image_adequate_text_not(self, result):
        assert result.scores.image_adequate() is True
        assert result.scores.text_adequate() is False

    def test_action_is_reprocess(self, result):
        assert result.recommendation.action == "REPROCESS"

    def test_suspect_layer_message(self, result):
        assert "text layer suspect" in result.recommendation.labels
        joined = " ".join(result.recommendation.reasons)
        assert (
            "text layer suspect; achievable quality unknown; "
            "rerun with --measure-achievable" in joined
        )

    def test_measure_achievable_recovers(self, fixtures):
        r = analyze_pdf(
            fixtures.get("reprocess"), lang_override="eng", measure_achievable=True
        )
        assert r.gate_result.basis == "post_reprocess"
        for p in r.pages:
            assert p.tq_as_shipped_score < 70
            assert p.tq_achievable_score >= 70
        assert r.scores.text_adequate() is True


class TestAdequacyOnPrimaries:
    """High IF / low TQ must NOT be text-adequate, even when the blended
    fitness (ranking only) clears the threshold (SPEC v1.5 change 3)."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(fixtures.get("adequacy"), lang_override="eng")

    def test_image_adequate(self, result):
        assert result.scores.if_text >= 75
        assert result.scores.image_adequate() is True

    def test_text_not_adequate_on_primary(self, result):
        assert result.scores.tq_text < 70
        assert result.scores.text_adequate() is False

    def test_blend_would_have_passed(self, result, settings):
        # the defect v1.5 fixed: a blended score clears adequacy, the primary fails
        assert result.scores.fitness_text > settings.text_adequacy_threshold

    def test_action_is_reprocess(self, result):
        assert result.recommendation.action == "REPROCESS"


class TestPageGate:
    """19 clean pages + 1 destroyed schematic must not be USABLE (SPEC P6);
    a 50..75 critical page degrades but does not block."""

    @pytest.fixture(scope="class")
    def blocked(self, fixtures):
        return analyze_pdf(fixtures.get("page_gate"), lang_override="eng")

    def test_averages_look_fine(self, blocked):
        assert blocked.scores.if_text >= 85
        assert blocked.scores.if_schem >= 75
        assert blocked.scores.image_adequate() is True

    def test_destroyed_page_blocks_usable(self, blocked):
        assert blocked.recommendation.action != "USABLE"
        hits = [h for h in blocked.gate_result.blocking if h.dimension == "image"]
        assert len(hits) == 1
        assert hits[0].page_index == 19
        assert hits[0].score < 50

    def test_pages_below_adequacy_counted(self, blocked):
        assert blocked.gate_result.pages_below_adequacy["image"] >= 1

    def test_degraded_band_labels_without_blocking(self, tmp_path):
        from tests import fixture_builder as fb

        path = str(tmp_path / "page_gate_degraded.pdf")
        fb.build_page_gate(path, destroyed=False)
        r = analyze_pdf(path, lang_override="eng")
        assert not [h for h in r.gate_result.blocking if h.dimension == "image"]
        assert r.recommendation.action == "USABLE"
        assert "degraded critical page" in r.recommendation.labels
        degraded = [h for h in r.gate_result.degraded if h.dimension == "image"]
        assert any(h.page_index == 19 and 50 <= h.score < 75 for h in degraded)


class TestWrongLanguage:
    """Japanese pages under an eng-only configuration: TQ null (unknown),
    never a penalty, never REPROCESS (SPEC P2)."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(
            fixtures.get("wrong_language"),
            settings=AnalyzerSettings(available_languages=("eng",)),
        )

    def test_language_detected_but_unsupported(self, result):
        assert result.language.detected != "eng"
        assert result.language.supported is False
        assert result.language.used is None

    def test_tq_is_null_everywhere(self, result):
        assert all(p.tq_is_null for p in result.pages)
        assert result.scores.tq_text is None
        assert result.scores.text_adequate() is None

    def test_not_reprocess(self, result):
        assert result.recommendation.action == "USABLE"
        assert "text quality unknown" in result.recommendation.labels

    def test_null_pages_never_gate_or_count(self, result):
        assert result.gate_result.pages_below_adequacy["text"] == 0
        assert not result.gate_result.blocking


class TestJbig2RiskFlag:
    """Owner addition A: JBIG2 without lossless provenance -> risk flag,
    no numeric deduction, surfaced in report and reasons."""

    @pytest.fixture(scope="class")
    def result(self, fixtures):
        return analyze_pdf(fixtures.get("jbig2"), lang_override="eng")

    def test_flag_set(self, result):
        assert "jbig2_unknown_history" in result.structural.risk_flags
        assert "JBIG2Decode" in result.structural.filters_seen
        assert "jbig2_unknown_history" in result.recommendation.labels

    def test_no_numeric_deduction(self, result):
        for p in result.pages:
            assert not any("jbig2" in d.code for d in p.if_deductions)
            assert not any("jbig2" in d.code for d in p.tq_deductions)

    def test_surfaced_in_report(self, result):
        d = to_dict(result)
        assert d["structural"]["risk_flags"] == ["jbig2_unknown_history"]
        assert any("JBIG2" in r for r in d["structural"]["reasons"])
