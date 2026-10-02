from __future__ import annotations

import pytest

from stacks.analyzer.ocr import OcrWord
from stacks.analyzer.text_quality import (
    from_embedded,
    from_ocr,
    garble_fraction,
    is_refdes_token,
)


class TestRefdes:
    @pytest.mark.parametrize("tok", ["R12", "C45", "U7", "Q3", "CR5", "TP2", "R101",
                                     "2N3904", "74LS244", "D9,"])
    def test_positive(self, tok):
        assert is_refdes_token(tok)

    @pytest.mark.parametrize("tok", ["the", "Resistor", "123", "R", "-", ""])
    def test_negative(self, tok):
        assert not is_refdes_token(tok)


class TestGarble:
    def test_clean_english_low(self):
        text = ("check the power supply board and replace the main fuse with the "
                "same type before you adjust the voltage control").split()
        assert garble_fraction(text) < 0.2

    def test_consonant_garbage_high(self):
        text = "qwrtz psdfg hjklx cvbnm zzxqw kjfhg wrtzp dfghj".split()
        assert garble_fraction(text) > 0.8

    def test_too_few_tokens_is_none(self):
        assert garble_fraction(["only", "four", "words", "here"]) is None

    def test_numbers_and_refdes_not_counted(self):
        assert garble_fraction(["R12", "100", "4.7K"]) is None


def ocr_words(specs):
    return [OcrWord(text=t, conf=c, left=0, top=0, width=40, height=h) for t, c, h in specs]


class TestFromOcr:
    def test_buckets(self):
        words = ocr_words(
            [("the", 90, 40), ("power", 80, 40), ("supply", 50, 40),
             ("R12", 70, 40), ("C45", 60, 40),
             ("tiny", 65, 15), ("label", 55, 12)]
        )
        m = from_ocr(words, small_text_px=18, low_conf_threshold=60)
        assert m.word_count == 7
        assert m.mean_conf == pytest.approx((90 + 80 + 50 + 70 + 60 + 65 + 55) / 7)
        assert m.low_conf_fraction == pytest.approx(2 / 7)  # conf < 60: 50, 55
        assert m.small_text_count == 2
        assert m.small_text_conf == pytest.approx(60)
        assert m.refdes_count == 2
        assert m.refdes_conf == pytest.approx(65)
        assert not m.null

    def test_too_few_words_is_null(self):
        m = from_ocr(ocr_words([("a", 90, 40)] * 4), 18, 60)
        assert m.null and "too little text" in m.null_reason


class TestFromEmbedded:
    def test_good_english_native(self):
        m = from_embedded(
            "check the power supply board and replace the fuse".split(),
            "native-text", "eng", 0.30,
        )
        assert not m.null and not m.suspect
        assert m.garble_fraction < 0.2

    def test_garbage_ocr_layer_suspect_with_message(self):
        m = from_embedded(
            "qwrtz psdfg hjklx cvbnm zzxqw kjfhg".split(),
            "embedded-ocr-layer", "eng", 0.30,
        )
        assert m.suspect
        assert any("rerun with --measure-achievable" in n for n in m.notes)

    def test_garbage_native_text_not_suspect_labeled(self):
        # suspect labeling (addition B) is about shipped OCR layers specifically
        m = from_embedded(
            "qwrtz psdfg hjklx cvbnm zzxqw kjfhg".split(), "native-text", "eng", 0.30
        )
        assert not m.suspect and m.garble_fraction > 0.8

    def test_unsupported_language_is_null_never_penalized(self):
        m = from_embedded(["何か", "日本語", "の", "テキスト", "です", "ね"], "native-text", "jpn", 0.30)
        assert m.null
        assert "unknown, not penalized" in m.null_reason
