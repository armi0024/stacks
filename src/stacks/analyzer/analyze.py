"""Orchestrator: one PDF in, a full analysis result out."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from stacks.analyzer import (
    content_class as cc,
    gates,
    image_fidelity,
    ocr,
    provenance,
    sampling,
    scoring,
    structural,
    text_quality as tq_mod,
)
from stacks.analyzer.actions import Completeness, Recommendation, assess_completeness, recommend
from stacks.analyzer.config import AnalyzerSettings, analysis_config_hash
from stacks.analyzer.pdfdoc import PdfDocument


@dataclass
class LanguageInfo:
    detected: str | None
    used: str | None
    supported: bool
    reason: str


@dataclass
class PageResult:
    index: int
    assessed: bool = False
    page_type: str | None = None
    content_class: str | None = None
    content: cc.ContentResult | None = None
    if_metrics: image_fidelity.IFMetrics | None = None
    if_score: float | None = None
    if_deductions: list = field(default_factory=list)
    tq: tq_mod.TQMetrics | None = None
    tq_as_shipped_score: float | None = None
    tq_deductions: list = field(default_factory=list)
    tq_achievable: tq_mod.TQMetrics | None = None
    tq_achievable_score: float | None = None
    tq_achievable_deductions: list = field(default_factory=list)
    extracted_text: str | None = None
    text_origin: str | None = None  # native-text | verbatim-ocr (9.2 labels)
    errors: list[str] = field(default_factory=list)

    @property
    def tq_score(self) -> float | None:
        """Effective TQ: post-REPROCESS (achievable) when measured, else as-shipped."""
        if self.tq_achievable_score is not None:
            return self.tq_achievable_score
        return self.tq_as_shipped_score

    @property
    def tq_is_null(self) -> bool:
        if self.tq_achievable is not None:
            return self.tq_achievable.null
        return self.tq is None or self.tq.null


@dataclass
class AnalysisResult:
    path: str
    sha256: str
    page_count: int
    mode: str
    sampled_indices: list[int]
    config_hash: str
    settings: AnalyzerSettings
    language: LanguageInfo
    structural: structural.StructuralFindings
    pages: list[PageResult]
    scores: scoring.DocScores
    gate_result: gates.GateResult
    completeness: Completeness
    recommendation: Recommendation
    schematic_implied: bool


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_language(
    pdf: PdfDocument, indices: list[int], override: str | None, s: AnalyzerSettings
) -> LanguageInfo:
    installed = ocr.installed_languages()

    def support(lang: str, origin: str) -> LanguageInfo:
        if lang not in s.available_languages:
            return LanguageInfo(
                lang, None, False,
                f"language '{lang}' ({origin}) is not in the configured available set "
                f"{sorted(s.available_languages)}: TQ is null (unknown), never penalized",
            )
        if lang not in installed:
            return LanguageInfo(
                lang, None, False,
                f"language '{lang}' ({origin}) has no installed Tesseract data: "
                "TQ is null (unknown), never penalized",
            )
        return LanguageInfo(lang, lang, True, f"language '{lang}' ({origin})")

    if override:
        return support(override, "operator override")

    probe = indices[: s.language_detect_pages]
    text_parts: list[str] = []
    image_pages: list[int] = []
    for idx in probe:
        pd = pdf.load_page(idx)
        if pd.words:
            text_parts.append(pd.text)
        elif pd.images:
            image_pages.append(idx)

    detected: str | None = None
    origin = "detected on first pages"
    if text_parts:
        joined = " ".join(text_parts)
        detected = ocr.detect_language_from_text(joined)
        if detected == "eng" and not ocr.detect_latin_language(joined)[1]:
            origin = "Latin script, ambiguous wordlist vote: eng fallback"
    if detected is None and image_pages and ocr.tesseract_available():
        renders = []
        for idx in image_pages:
            gray, _dpi, err = pdf.render_gray(idx, 200.0, s.render_mp_cap)
            if gray is not None and err is None:
                renders.append(gray)
        script = ocr.detect_script(renders[0]) if renders else None
        if script == "Latin":
            # Latin script alone doesn't name a language: OCR probe pages
            # under eng and vote on the words (disposition 3). Near-textless
            # pages (covers) vote ambiguous, so keep probing.
            detected, origin = "eng", "Latin script, ambiguous wordlist vote: eng fallback"
            if "eng" in ocr.installed_languages():
                for gray in renders:
                    try:
                        words = ocr.ocr_tsv(gray, "eng")
                    except (RuntimeError, OSError):
                        continue
                    lang, confident = ocr.detect_latin_language(
                        " ".join(w.text for w in words)
                    )
                    if confident:
                        detected = lang
                        origin = "Latin script, wordlist vote on OCR probe"
                        break
        elif script:
            detected = ocr.script_to_language(script)
            origin = f"OSD script '{script}' on first image page"
    if detected is None:
        return LanguageInfo(
            None, None, False,
            "document language could not be determined from first pages: "
            "TQ is null (unknown), never penalized",
        )
    return support(detected, origin)


def _embedded_source(pd) -> str:
    total = pd.visible_chars + pd.invisible_chars
    if total and pd.invisible_chars / total >= provenance.INVISIBLE_DOMINANT:
        return "embedded-ocr-layer"
    return "native-text"


def analyze_page(
    pdf: PdfDocument,
    index: int,
    lang: LanguageInfo,
    s: AnalyzerSettings,
    measure_achievable: bool,
    force_schematic_target: bool = False,
) -> PageResult:
    res = PageResult(index=index, assessed=True)
    pd = pdf.load_page(index)
    res.errors.extend(pd.errors)
    res.page_type = provenance.classify_page_type(pd)

    native_dpi = pdf.native_image_dpi(pd)
    target_dpi = min(native_dpi, s.render_dpi_cap) if native_dpi else s.render_dpi_cap
    gray, actual_dpi, render_err = pdf.render_gray(index, target_dpi, s.render_mp_cap)
    if render_err:
        res.errors.append(render_err)

    dominant = max(pd.images, key=lambda im: im.area_pts) if pd.images else None
    bpc = dominant.bpc if dominant else None

    if gray is None:
        res.content_class = "unknown"
        res.tq = tq_mod.null_tq("none", "page could not be rendered")
        return res

    scale = actual_dpi / 72.0
    boxes_px = [
        (w.bbox_pts[0] * scale, w.bbox_pts[1] * scale, w.bbox_pts[2] * scale, w.bbox_pts[3] * scale)
        for w in pd.words
    ]
    res.content = cc.classify_content(gray, boxes_px, s.blank_ink_fraction)
    res.content_class = res.content.content_class

    res.if_metrics = image_fidelity.measure(gray, native_dpi, bpc, actual_dpi)
    if res.content_class != "blank":
        res.if_score, res.if_deductions = scoring.score_image_fidelity(
            res.if_metrics, res.content_class, s, force_schematic_target
        )

    # embedded text is retrievable even on near-blank pages
    if pd.words:
        res.extracted_text = pd.text
        res.text_origin = (
            "verbatim-ocr" if _embedded_source(pd) == "embedded-ocr-layer" else "native-text"
        )

    # --- text quality ---
    if res.content_class == "blank":
        res.tq = tq_mod.null_tq("none", "blank page: nothing to measure")
        return res

    if pd.words:
        source = _embedded_source(pd)
        lang_code = lang.used or lang.detected
        res.tq = tq_mod.from_embedded(
            [w.text for w in pd.words], source, lang_code, s.suspect_garble_fraction
        )
        if (
            measure_achievable
            and source == "embedded-ocr-layer"
            and lang.supported
            and ocr.tesseract_available()
        ):
            try:
                words = ocr.ocr_tsv(gray, lang.used)
                res.tq_achievable = tq_mod.from_ocr(words, s.small_text_px, s.low_conf_threshold)
            except (RuntimeError, OSError) as e:
                res.errors.append(f"achievable re-OCR failed: {e}")
    else:
        # image-only (or empty) page: fresh OCR is the only way to measure TQ
        if not lang.supported:
            res.tq = tq_mod.null_tq("ocr-fresh", lang.reason)
        elif not ocr.tesseract_available():
            res.tq = tq_mod.null_tq("ocr-fresh", "tesseract is not available on this host")
        else:
            try:
                words = ocr.ocr_tsv(gray, lang.used)
                res.tq = tq_mod.from_ocr(words, s.small_text_px, s.low_conf_threshold)
                res.extracted_text = " ".join(w.text for w in words) or None
                res.text_origin = "verbatim-ocr" if res.extracted_text else None
            except (RuntimeError, OSError) as e:
                res.errors.append(f"OCR failed: {e}")
                res.tq = tq_mod.null_tq("ocr-fresh", f"OCR failed: {e}")

    res.tq_as_shipped_score, res.tq_deductions = scoring.score_text_quality(res.tq, s)
    if res.tq_achievable is not None:
        res.tq_achievable_score, res.tq_achievable_deductions = scoring.score_text_quality(
            res.tq_achievable, s
        )
    return res


def analyze_pdf(
    path: str,
    settings: AnalyzerSettings | None = None,
    mode: str = "assess",
    lang_override: str | None = None,
    measure_achievable: bool = False,
    critical_pages: set[int] | None = None,
    schematic_implied_flag: bool = False,
) -> AnalysisResult:
    s = settings or AnalyzerSettings()
    critical_pages = critical_pages or set()
    with PdfDocument(path) as pdf:
        struct = structural.scan(pdf.doc)
        n = pdf.page_count
        if mode == "screening":
            indices = sampling.screening_sample(n, s.screening_max_pages)
        else:
            indices = list(range(n))
        lang = resolve_language(pdf, indices, lang_override, s)
        pages = [
            analyze_page(pdf, i, lang, s, measure_achievable, schematic_implied_flag)
            for i in indices
        ]

    schematic_implied = schematic_implied_flag or any(
        p.content_class == "schematic" for p in pages
    )
    basis = "post_reprocess" if measure_achievable else "as_shipped"
    gate_result = gates.evaluate(pages, schematic_implied, critical_pages, basis, s)
    scores = scoring.rollup(pages, s)
    completeness = assess_completeness(schematic_implied, scores.schem_page_count)

    worst = sorted(
        ((p.index, p.if_score) for p in pages if p.if_score is not None),
        key=lambda t: t[1],
    )
    suspect_pages = [
        p.index for p in pages if p.tq and p.tq.suspect and p.tq_achievable is None
    ]
    tq_unknown_reason = lang.reason if not lang.supported else None

    recommendation = recommend(
        scores,
        gate_result,
        completeness,
        mode,
        worst,
        tq_unknown_reason,
        suspect_pages,
        struct.risk_flags,
    )
    return AnalysisResult(
        path=path,
        sha256=_sha256(path),
        page_count=n,
        mode=mode,
        sampled_indices=indices,
        config_hash=analysis_config_hash(s),
        settings=s,
        language=lang,
        structural=struct,
        pages=pages,
        scores=scores,
        gate_result=gate_result,
        completeness=completeness,
        recommendation=recommendation,
        schematic_implied=schematic_implied,
    )
