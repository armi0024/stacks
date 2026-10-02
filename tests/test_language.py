from __future__ import annotations

from stacks.analyzer.analyze import resolve_language
from stacks.analyzer.config import AnalyzerSettings
from stacks.analyzer.ocr import detect_language_from_text
from stacks.analyzer.pdfdoc import PdfDocument


class TestUnicodeDetection:
    def test_english(self):
        assert detect_language_from_text("Check the power supply board before adjustment.") == "eng"

    def test_japanese(self):
        assert detect_language_from_text("この取扱説明書をよくお読みのうえ正しくお使いください") == "jpn"

    def test_too_short_is_unknown(self):
        assert detect_language_from_text("ok") is None

    def test_empty(self):
        assert detect_language_from_text("") is None


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
