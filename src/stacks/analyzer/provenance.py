"""Per-page text provenance: native-text | ocr-layer | image-only | mixed.

Invisible text (PDF render mode 3) over a page-covering raster is an OCR
layer; visible text with no significant raster is native text. An embedded
text layer never exempts image assessment (SPEC 6.1, no score floors).
"""

from __future__ import annotations

from stacks.analyzer.pdfdoc import PageData

MIN_CHARS = 10
IMAGE_COVER_SIGNIFICANT = 0.10
IMAGE_COVER_DOMINANT = 0.50
INVISIBLE_DOMINANT = 0.80


def image_coverage(page: PageData) -> float:
    page_area = page.width_pt * page.height_pt
    if page_area <= 0:
        return 0.0
    covered = sum(im.area_pts for im in page.images)
    return min(1.0, covered / page_area)


def classify_page_type(page: PageData) -> str:
    cover = image_coverage(page)
    total_chars = page.visible_chars + page.invisible_chars
    has_text = total_chars >= MIN_CHARS
    if not has_text:
        return "image-only" if cover >= IMAGE_COVER_SIGNIFICANT else "native-text"
    invisible_frac = page.invisible_chars / total_chars
    if invisible_frac >= INVISIBLE_DOMINANT and cover >= IMAGE_COVER_DOMINANT:
        return "ocr-layer"
    if cover >= IMAGE_COVER_SIGNIFICANT:
        return "mixed"
    return "native-text"
