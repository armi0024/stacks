"""Deterministic synthetic-PDF builder for the 11.1a contract fixtures.

Everything is generated (never committed): vector pages rendered to images,
then degraded with controlled knobs (DPI, blur, noise, skew, glyph damage,
1-bit) so each fixture hits a known scoring band.
"""

from __future__ import annotations

import random

import cv2
import numpy as np
import pymupdf

PAGE_W_PT, PAGE_H_PT = 612.0, 792.0  # US letter
PAGE_W_IN, PAGE_H_IN = PAGE_W_PT / 72.0, PAGE_H_PT / 72.0

ENGLISH_SENTENCES = [
    "The power supply board must be checked before any adjustment is made.",
    "Remove the monitor assembly and inspect the ground wire for damage.",
    "Replace the main fuse only with the same type and rating as the original.",
    "Adjust the voltage control until the test point reads five volts exactly.",
    "The coin switch connects to the control board through the wiring harness.",
    "Check each connector for bent pins before you install the new board.",
    "This game uses a standard vertical monitor with an isolation transformer.",
    "Clean the player controls and test every switch for correct operation.",
]

GARBAGE_WORDS = None  # built lazily, deterministic


def _garbage_text(word_count: int = 120) -> str:
    rng = random.Random(42)
    consonants = "qwrtzpsdfghjklxcvbnm"
    words = [
        "".join(rng.choice(consonants) for _ in range(rng.randint(3, 9)))
        for _ in range(word_count)
    ]
    return " ".join(words)


def english_paragraphs(n_lines: int = 40) -> list[str]:
    return [ENGLISH_SENTENCES[i % len(ENGLISH_SENTENCES)] for i in range(n_lines)]


JAPANESE_LINES = [
    "この取扱説明書をよくお読みのうえ、正しくお使いください。",
    "電源を入れる前に、すべての配線を確認してください。",
    "基板の交換は必ず電源を切ってから行ってください。",
    "モニターの調整は専門の技術者が行う必要があります。",
    "コイン投入口の清掃は定期的に実施してください。",
    "故障の際は販売店までご連絡ください。保証書を添えてください。",
]


# ---------------------------------------------------------------- vector pages


def draw_text_page(page: pymupdf.Page, lines: list[str], fontsize: float = 11.0,
                   render_mode: int = 0, small_text: list[str] | None = None,
                   refdes_line: str | None = None) -> None:
    y = 60.0
    for line in lines:
        if y > PAGE_H_PT - 60:
            break
        page.insert_text((54, y), line, fontsize=fontsize, render_mode=render_mode)
        y += fontsize * 1.6
    if refdes_line:
        page.insert_text((54, y + 10), refdes_line, fontsize=fontsize, render_mode=render_mode)
        y += fontsize * 2.5
    if small_text:
        for st in small_text:
            page.insert_text((54, y), st, fontsize=4.0, render_mode=render_mode)
            y += 8.0


def draw_schematic_page(page: pymupdf.Page, labels: bool = True) -> None:
    """Line-art grid + components + sparse refdes labels: schematic-class."""
    shape = page.new_shape()
    # grid of horizontal and vertical "circuit" lines
    for i in range(6):
        y = 100 + i * 110
        shape.draw_line(pymupdf.Point(50, y), pymupdf.Point(560, y))
    for i in range(5):
        x = 70 + i * 115
        shape.draw_line(pymupdf.Point(x, 80), pymupdf.Point(x, 720))
    # diagonals and component circles
    shape.draw_line(pymupdf.Point(70, 100), pymupdf.Point(530, 650))
    shape.draw_line(pymupdf.Point(530, 100), pymupdf.Point(70, 650))
    for i in range(8):
        shape.draw_circle(pymupdf.Point(90 + i * 60, 400), 14)
    shape.finish(width=2.0, color=(0, 0, 0))
    shape.commit()
    if labels:
        # title block of real words keeps OCR confidence representative
        title = ["POWER SUPPLY SECTION", "SHEET TWO OF FIVE",
                 "SEE PARTS LIST FOR VALUES", "ALL RESISTORS IN OHMS"]
        for i, line in enumerate(title):
            page.insert_text((330, 740 + i * 13), line, fontsize=10)
        labels_txt = ["R12", "C45", "U7", "Q3", "CR5", "TP2",
                      "R101", "C22", "L4", "D9", "J1", "F1"]
        for i, lab in enumerate(labels_txt):
            x = 85 + (i % 4) * 115
            y = 165 + (i // 4) * 110
            page.insert_text((x, y), lab, fontsize=14)


def render_page_gray(doc: pymupdf.Document, index: int, dpi: float) -> np.ndarray:
    pix = doc[index].get_pixmap(dpi=round(dpi), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def make_text_image(dpi: float = 300.0, lines: list[str] | None = None,
                    fontsize: float = 11.0, small_text: list[str] | None = None,
                    refdes_line: str | None = None) -> np.ndarray:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    draw_text_page(page, lines or english_paragraphs(), fontsize=fontsize,
                   small_text=small_text, refdes_line=refdes_line)
    img = render_page_gray(doc, 0, dpi)
    doc.close()
    return img


def make_schematic_image(dpi: float = 300.0, labels: bool = True) -> np.ndarray:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    draw_schematic_page(page, labels=labels)
    img = render_page_gray(doc, 0, dpi)
    doc.close()
    return img


def make_japanese_image(dpi: float = 300.0) -> np.ndarray:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    y = 70.0
    for i in range(44):
        page.insert_text((54, y), JAPANESE_LINES[i % len(JAPANESE_LINES)],
                         fontname="japan", fontsize=12)
        y += 16.0
    img = render_page_gray(doc, 0, dpi)
    doc.close()
    return img


# ---------------------------------------------------------------- degradations


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


def salt_pepper(img: np.ndarray, density: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = img.copy()
    mask = rng.random(img.shape) < density
    out[mask] = 0
    return out


def rotate(img: np.ndarray, degrees: float) -> np.ndarray:
    h, w = img.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(img, m, (w, h), borderValue=255)


def glyph_damage(img: np.ndarray, fraction: float, seed: int = 11,
                 box: int = 10) -> np.ndarray:
    """Erase random small blocks over inked areas: sharp but OCR-hostile."""
    rng = np.random.default_rng(seed)
    out = img.copy()
    ys, xs = np.where(out < 160)
    if len(ys) == 0:
        return out
    n = int(len(ys) * fraction / (box * box))
    idx = rng.integers(0, len(ys), size=n)
    for i in idx:
        y, x = ys[i], xs[i]
        out[y : y + box, x : x + box] = 255
    return out


def to_1bit(img: np.ndarray) -> np.ndarray:
    _, b = cv2.threshold(img, 160, 255, cv2.THRESH_BINARY)
    return b


# ---------------------------------------------------------------- page emitters


def add_image_page(doc: pymupdf.Document, img: np.ndarray, dpi: float,
                   bilevel: bool = False) -> pymupdf.Page:
    """Full-page placement: effective DPI == image DPI."""
    h, w = img.shape
    page = doc.new_page(width=w / dpi * 72.0, height=h / dpi * 72.0)
    params = [cv2.IMWRITE_PNG_BILEVEL, 1] if bilevel else []
    ok, png = cv2.imencode(".png", img, params)
    assert ok
    page.insert_image(page.rect, stream=png.tobytes())
    return page


def add_partial_image_page(doc: pymupdf.Document, img: np.ndarray,
                           placement_width_in: float) -> pymupdf.Page:
    """Image placed at a fraction of the page width (placement-DPI test)."""
    h, w = img.shape
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    pw = placement_width_in * 72.0
    ph = pw * h / w
    ok, png = cv2.imencode(".png", img)
    assert ok
    page.insert_image(pymupdf.Rect(72, 72, 72 + pw, 72 + ph), stream=png.tobytes())
    return page


def add_ocr_layer(page: pymupdf.Page, text_lines: list[str], fontsize: float = 11.0) -> None:
    """Invisible (render mode 3) text layer over the page image."""
    scale = page.rect.width / PAGE_W_PT
    y = 60.0 * scale
    for line in text_lines:
        if y > page.rect.height - 40:
            break
        page.insert_text((54 * scale, y), line, fontsize=fontsize * scale, render_mode=3)
        y += fontsize * 1.6 * scale


# ---------------------------------------------------------------- fixtures


def build_dimensional(path: str) -> None:
    """2 pristine native-text pages + 2 degraded schematic image pages."""
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
        draw_text_page(page, english_paragraphs())
    schem = make_schematic_image(dpi=120.0)
    for _ in range(2):
        add_image_page(doc, schem, dpi=120.0)
    doc.save(path)
    doc.close()


def build_ocr_layer_no_floor(path: str) -> None:
    """Low-fidelity scan images carrying a good English OCR text layer."""
    doc = pymupdf.open()
    img = blur(make_text_image(dpi=100.0), sigma=1.4)
    for _ in range(2):
        page = add_image_page(doc, img, dpi=100.0)
        add_ocr_layer(page, english_paragraphs())
    doc.save(path)
    doc.close()


def build_reprocess(path: str) -> None:
    """Sharp 300 DPI scan images + garbage embedded OCR layer."""
    doc = pymupdf.open()
    img = make_text_image(dpi=300.0)
    garbage = _garbage_text().split()
    lines = [" ".join(garbage[i : i + 10]) for i in range(0, len(garbage), 10)]
    for _ in range(2):
        page = add_image_page(doc, img, dpi=300.0)
        add_ocr_layer(page, lines)
    doc.save(path)
    doc.close()


def build_adequacy(path: str, damage: float = 1.6) -> None:
    """Sharp 300 DPI pages whose damaged glyphs OCR with low confidence:
    IF ~100, TQ well below adequacy."""
    doc = pymupdf.open()
    img = make_text_image(
        dpi=300.0,
        small_text=["spare fuse 3A type AGC in holder F2 near line filter"] * 4,
        refdes_line="R12 C45 U7 Q3 CR5 TP2 R101 C22 2N3904 74LS244",
    )
    damaged = glyph_damage(img, fraction=damage)
    for _ in range(2):
        add_image_page(doc, damaged, dpi=300.0)
    doc.save(path)
    doc.close()


def build_page_gate(path: str, destroyed: bool = True) -> None:
    """15 clean text pages + 4 clean schematic pages + 1 bad schematic page.
    destroyed=True -> IF < 50 (blocks); False -> IF in 50..75 (degraded label)."""
    doc = pymupdf.open()
    text_img = make_text_image(dpi=300.0)
    for _ in range(15):
        add_image_page(doc, text_img, dpi=300.0)
    schem = make_schematic_image(dpi=300.0)
    for _ in range(4):
        add_image_page(doc, schem, dpi=300.0)
    if destroyed:
        bad = make_schematic_image(dpi=75.0)
        bad = salt_pepper(bad, 0.004)
        bad = to_1bit(bad)
        add_image_page(doc, bad, dpi=75.0, bilevel=True)
    else:
        mid = make_schematic_image(dpi=140.0)
        add_image_page(doc, mid, dpi=140.0)
    doc.save(path)
    doc.close()


def build_wrong_language(path: str) -> None:
    """Clean 300 DPI Japanese image-only pages."""
    doc = pymupdf.open()
    img = make_japanese_image(dpi=300.0)
    for _ in range(2):
        add_image_page(doc, img, dpi=300.0)
    doc.save(path)
    doc.close()


def build_jbig2(path: str) -> None:
    """Valid pages, then the image stream's Filter doctored to JBIG2Decode."""
    doc = pymupdf.open()
    img = to_1bit(make_text_image(dpi=150.0))
    add_image_page(doc, img, dpi=150.0, bilevel=True)
    doc.save(path)
    doc.close()
    doc = pymupdf.open(path)
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype")[1] == "/Image":
            doc.xref_set_key(xref, "Filter", "/JBIG2Decode")
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()


def build_partial_placement(path: str) -> None:
    """600 px wide image placed at 4.25 in: placement DPI ~141, MediaBox ~70."""
    doc = pymupdf.open()
    img = cv2.resize(make_text_image(dpi=100.0), (600, 776))
    add_partial_image_page(doc, img, placement_width_in=4.25)
    doc.save(path)
    doc.close()


def build_skewed(path: str, degrees: float = 2.5) -> None:
    doc = pymupdf.open()
    img = rotate(make_text_image(dpi=200.0), degrees)
    add_image_page(doc, img, dpi=200.0)
    doc.save(path)
    doc.close()


def build_blank_and_text(path: str) -> None:
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)  # blank
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    draw_text_page(page, english_paragraphs())
    doc.save(path)
    doc.close()


def build_many_pages(path: str, n: int = 40) -> None:
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
        page.insert_text((54, 70), f"Page {i + 1} of the long manual.", fontsize=11)
    doc.save(path)
    doc.close()
