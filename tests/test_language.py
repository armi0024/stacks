from __future__ import annotations

import pymupdf

from stacks.analyzer.analyze import analyze_pdf, resolve_language
from stacks.analyzer.config import AnalyzerSettings
from stacks.analyzer.ocr import detect_language_from_text, detect_latin_language
from stacks.analyzer.pdfdoc import PdfDocument
from tests import fixture_builder as fb

FRENCH = ("Ce manuel contient les instructions pour le montage et la mise en "
          "service de la machine. Avant de brancher le câble, vérifiez que "
          "toutes les connexions sont bien serrées et que la tension est correcte.")
GERMAN = ("Diese Anleitung enthält die Hinweise für den Aufbau und die "
          "Inbetriebnahme der Maschine. Vor dem Anschluss ist zu prüfen, dass "
          "alle Verbindungen fest sind und die Spannung mit dem Netz übereinstimmt.")
SPANISH = ("Este manual contiene las instrucciones para el montaje y la puesta "
           "en marcha de la máquina. Antes de conectar el cable, compruebe que "
           "todas las conexiones están bien apretadas y que la tensión es correcta.")
ITALIAN = ("Questo manuale contiene le istruzioni per il montaggio e la messa "
           "in servizio della macchina. Prima di collegare il cavo, verificare "
           "che tutte le connessioni siano ben serrate e che la tensione sia corretta.")


class TestUnicodeDetection:
    def test_english(self):
        assert detect_language_from_text("Check the power supply board before adjustment.") == "eng"

    def test_japanese(self):
        assert detect_language_from_text("この取扱説明書をよくお読みのうえ正しくお使いください") == "jpn"

    def test_too_short_is_unknown(self):
        assert detect_language_from_text("ok") is None

    def test_empty(self):
        assert detect_language_from_text("") is None


class TestLatinVote:
    """Disposition 3: Latin-script languages route to the right tessdata."""

    def test_french(self):
        assert detect_language_from_text(FRENCH) == "fra"

    def test_german(self):
        assert detect_language_from_text(GERMAN) == "deu"

    def test_spanish(self):
        assert detect_language_from_text(SPANISH) == "spa"

    def test_italian(self):
        assert detect_language_from_text(ITALIAN) == "ita"

    def test_english_still_english(self):
        text = ("Check the power supply board and replace the main fuse with "
                "the same type before you adjust the voltage control on this game.")
        assert detect_language_from_text(text) == "eng"

    def test_ambiguous_falls_back_to_eng(self):
        gibberish = "zork blat frob nix quux grue fizz wump torp blem gronk spal"
        lang, confident = detect_latin_language(gibberish)
        assert not confident
        assert detect_language_from_text(gibberish) == "eng"

    def test_french_in_default_available_set(self):
        assert "fra" in AnalyzerSettings().available_languages

    def test_french_image_only_doc_routes_to_fra(self, tmp_path):
        # image-only French pages: OSD says Latin, the OCR-probe vote says fra
        path = str(tmp_path / "french.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=fb.PAGE_W_PT, height=fb.PAGE_H_PT)
        fb.draw_text_page(page, [FRENCH[i : i + 70] for i in range(0, len(FRENCH), 70)] * 6)
        img = fb.render_page_gray(doc, 0, 300.0)
        doc.close()
        doc = pymupdf.open()
        fb.add_image_page(doc, img, dpi=300.0)
        doc.save(path)
        doc.close()
        with PdfDocument(path) as pdf:
            info = resolve_language(pdf, [0], None, AnalyzerSettings())
        assert info.detected == "fra"
        assert info.used == "fra" and info.supported
        assert "wordlist vote" in info.reason

    def test_language_used_recorded_in_report(self, fixtures):
        from stacks.analyzer.report import to_dict

        r = analyze_pdf(fixtures.get("blank_and_text"))
        d = to_dict(r)
        assert d["language"]["used"] == "eng"
        assert d["language"]["reason"]


class TestResolveLanguage:
    def test_override_supported(self, fixtures):
        with PdfDocument(fixtures.get("blank_and_text")) as pdf:
            info = resolve_language(pdf, [0, 1], "eng", AnalyzerSettings())
        assert info.used == "eng" and info.supported

    def test_override_not_in_available_set(self, fixtures):
        with PdfDocument(fixtures.get("blank_and_text")) as pdf:
            info = resolve_language(pdf, [0, 1], "jpn", AnalyzerSettings(available_languages=("eng",)))
        assert info.supported is False and info.used is None
        assert "never penalized" in info.reason

    def test_native_text_detection(self, fixtures):
        with PdfDocument(fixtures.get("blank_and_text")) as pdf:
            info = resolve_language(pdf, [0, 1], None, AnalyzerSettings())
        assert info.detected == "eng" and info.supported

    def test_japanese_image_detection_unsupported_config(self, fixtures):
        with PdfDocument(fixtures.get("wrong_language")) as pdf:
            info = resolve_language(pdf, [0, 1], None, AnalyzerSettings(available_languages=("eng",)))
        assert info.detected != "eng"  # jpn via OSD, or unknown: never silently eng
        assert info.supported is False
