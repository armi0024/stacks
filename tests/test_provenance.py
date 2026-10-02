from __future__ import annotations

import pymupdf

from stacks.analyzer.pdfdoc import PdfDocument
from stacks.analyzer.provenance import classify_page_type
from tests import fixture_builder as fb


def _page_type(tmp_path, build) -> str:
    path = str(tmp_path / "doc.pdf")
    doc = pymupdf.open()
    build(doc)
    doc.save(path)
    doc.close()
    with PdfDocument(path) as pdf:
        return classify_page_type(pdf.load_page(0))


def test_native_text(tmp_path):
    def build(doc):
        page = doc.new_page(width=fb.PAGE_W_PT, height=fb.PAGE_H_PT)
        fb.draw_text_page(page, fb.english_paragraphs())

    assert _page_type(tmp_path, build) == "native-text"


def test_image_only(tmp_path):
    def build(doc):
        fb.add_image_page(doc, fb.make_text_image(dpi=150.0), dpi=150.0)

    assert _page_type(tmp_path, build) == "image-only"


def test_ocr_layer(tmp_path):
    def build(doc):
        page = fb.add_image_page(doc, fb.make_text_image(dpi=150.0), dpi=150.0)
        fb.add_ocr_layer(page, fb.english_paragraphs())

    assert _page_type(tmp_path, build) == "ocr-layer"


def test_mixed_visible_text_over_image(tmp_path):
    def build(doc):
        page = fb.add_image_page(doc, fb.make_schematic_image(dpi=150.0), dpi=150.0)
        scale = page.rect.width / fb.PAGE_W_PT
        y = 60.0 * scale
        for line in fb.english_paragraphs(12):
            page.insert_text((54 * scale, y), line, fontsize=11 * scale)  # visible
            y += 18 * scale

    assert _page_type(tmp_path, build) == "mixed"
