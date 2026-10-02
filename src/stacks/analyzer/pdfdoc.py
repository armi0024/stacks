"""PyMuPDF access layer: pages, text spans, image placements, renders.

Effective DPI is defined by the image's RENDERED PLACEMENT width taken from
its transformation on the page, never the MediaBox width (SPEC P17): full-page
scans compute as before, partial-page images are not mismeasured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pymupdf


@dataclass
class PlacedImage:
    xref: int
    width_px: int
    height_px: int
    bpc: int | None
    bbox_pts: tuple[float, float, float, float]  # placement rect on the page
    effective_dpi: float | None  # px width / (placement width in inches)
    area_pts: float


@dataclass
class Word:
    text: str
    bbox_pts: tuple[float, float, float, float]


@dataclass
class PageData:
    index: int  # 0-based PDF index
    width_pt: float
    height_pt: float
    words: list[Word] = field(default_factory=list)
    visible_chars: int = 0
    invisible_chars: int = 0
    images: list[PlacedImage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


class PdfDocument:
    def __init__(self, path: str):
        self.path = path
        self.doc = pymupdf.open(path)

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def load_page(self, index: int) -> PageData:
        page = self.doc[index]
        rect = page.rect
        data = PageData(index=index, width_pt=rect.width, height_pt=rect.height)
        try:
            for w in page.get_text("words"):
                x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
                if text.strip():
                    data.words.append(Word(text=text, bbox_pts=(x0, y0, x1, y1)))
        except Exception as e:  # noqa: BLE001 - robust per-page analysis
            data.errors.append(f"text extraction failed: {e}")
        try:
            vis, invis = self._count_char_visibility(page)
            data.visible_chars, data.invisible_chars = vis, invis
        except Exception as e:  # noqa: BLE001
            data.errors.append(f"texttrace failed: {e}")
            # fall back: treat all extracted chars as visible
            data.visible_chars = sum(len(w.text) for w in data.words)
        try:
            data.images = self._placed_images(page)
        except Exception as e:  # noqa: BLE001
            data.errors.append(f"image info failed: {e}")
        return data

    @staticmethod
    def _count_char_visibility(page: pymupdf.Page) -> tuple[int, int]:
        visible = invisible = 0
        for span in page.get_texttrace():
            n = len(span.get("chars", ()))
            # text render mode 3 = neither fill nor stroke = invisible OCR layer
            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                invisible += n
            else:
                visible += n
        return visible, invisible

    @staticmethod
    def _placed_images(page: pymupdf.Page) -> list[PlacedImage]:
        out: list[PlacedImage] = []
        for info in page.get_image_info(xrefs=True):
            bbox = info["bbox"]
            x0, y0, x1, y1 = bbox
            placed_w_in = abs(x1 - x0) / 72.0
            width_px = info.get("width") or 0
            eff_dpi = (width_px / placed_w_in) if placed_w_in > 1e-6 and width_px else None
            out.append(
                PlacedImage(
                    xref=info.get("xref", 0),
                    width_px=width_px,
                    height_px=info.get("height") or 0,
                    bpc=info.get("bpc"),
                    bbox_pts=(x0, y0, x1, y1),
                    effective_dpi=eff_dpi,
                    area_pts=abs(x1 - x0) * abs(y1 - y0),
                )
            )
        return out

    def render_gray(
        self, index: int, dpi: float, mp_cap: float = 45.0
    ) -> tuple[np.ndarray | None, float, str | None]:
        """Render a page to grayscale at `dpi`, capped at `mp_cap` megapixels.

        Returns (array, actual_dpi, error).
        """
        page = self.doc[index]
        rect = page.rect
        w_in, h_in = rect.width / 72.0, rect.height / 72.0
        actual = dpi
        mp = (w_in * actual) * (h_in * actual) / 1e6
        if mp > mp_cap:
            actual = math.sqrt(mp_cap * 1e6 / (w_in * h_in))
        try:
            pix = page.get_pixmap(dpi=round(actual), colorspace=pymupdf.csGRAY, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            return arr.copy(), actual, None
        except Exception as e:  # noqa: BLE001
            return None, actual, f"render failed: {e}"

    def native_image_dpi(self, page_data: PageData) -> float | None:
        """Dominant (largest-placement) raster's effective DPI, or None."""
        if not page_data.images:
            return None
        dominant = max(page_data.images, key=lambda im: im.area_pts)
        return dominant.effective_dpi
