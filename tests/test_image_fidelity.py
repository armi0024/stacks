from __future__ import annotations

import numpy as np
import pytest

from stacks.analyzer import image_fidelity as imf
from stacks.analyzer.analyze import analyze_pdf
from stacks.analyzer.pdfdoc import PdfDocument
from tests import fixture_builder as fb


class TestPlacementDpi:
    """SPEC P17: effective DPI from the rendered placement, not the MediaBox."""

    def test_partial_page_image(self, fixtures):
        with PdfDocument(fixtures.get("partial_placement")) as pdf:
            pd = pdf.load_page(0)
            dpi = pdf.native_image_dpi(pd)
        # 600 px placed at 4.25 in -> ~141 DPI; MediaBox math would say ~70.6
        assert dpi == pytest.approx(600 / 4.25, abs=2)
        assert abs(dpi - 600 / 8.5) > 50

    def test_full_page_image(self, tmp_path):
        import pymupdf

        path = str(tmp_path / "full.pdf")
        doc = pymupdf.open()
        fb.add_image_page(doc, fb.make_text_image(dpi=100.0), dpi=100.0)
        doc.save(path)
        doc.close()
        with PdfDocument(path) as pdf:
            dpi = pdf.native_image_dpi(pdf.load_page(0))
        assert dpi == pytest.approx(100, abs=2)


class TestMetrics:
    def test_sharpness_drops_with_blur(self):
        img = fb.make_text_image(dpi=200.0)
        assert imf.sharpness_laplacian(fb.blur(img, 2.0)) < imf.sharpness_laplacian(img) / 4

    def test_speckle_detects_salt(self):
        img = fb.make_text_image(dpi=200.0)
        assert imf.speckle_index(img) < 6
        assert imf.speckle_index(fb.salt_pepper(img, 0.001)) > 6

    def test_skew_measured(self, fixtures):
        r = analyze_pdf(fixtures.get("skewed"), lang_override="eng")
        m = r.pages[0].if_metrics
        assert m.skew_deg == pytest.approx(2.5, abs=0.7)
        assert any(d.code == "skew" for d in r.pages[0].if_deductions)

    def test_straight_page_no_skew_deduction(self, fixtures):
        r = analyze_pdf(fixtures.get("blank_and_text"), lang_override="eng")
        text_page = r.pages[1]
        assert not any(d.code == "skew" for d in text_page.if_deductions)

    def test_blank_page_not_scored(self, fixtures):
        r = analyze_pdf(fixtures.get("blank_and_text"), lang_override="eng")
        blank = r.pages[0]
        assert blank.content_class == "blank"
        assert blank.if_score is None
        assert blank.tq_is_null

    def test_render_mp_cap(self, tmp_path):
        import pymupdf

        path = str(tmp_path / "big.pdf")
        doc = pymupdf.open()
        doc.new_page(width=36 * 72, height=36 * 72)  # 36x36 in: 300 DPI would be 117 MP
        doc.save(path)
        doc.close()
        with PdfDocument(path) as pdf:
            gray, actual, err = pdf.render_gray(0, 300.0, mp_cap=45.0)
        assert err is None
        assert gray.size <= 45.5e6
        assert actual < 300
