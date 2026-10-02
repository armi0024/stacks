"""Two-level page gate unit tests (SPEC P6/P15) on synthetic page objects."""

from __future__ import annotations

from dataclasses import dataclass

from stacks.analyzer import gates
from stacks.analyzer.config import AnalyzerSettings

S = AnalyzerSettings()


@dataclass
class FakePage:
    index: int
    content_class: str
    if_score: float | None
    tq_score: float | None
    assessed: bool = True
    tq_is_null: bool = False


def test_schematic_pages_required_when_implied():
    pages = [FakePage(0, "text", 40, 90), FakePage(1, "schematic", 40, 90)]
    r = gates.evaluate(pages, schematic_implied=True, critical_pages=set(), basis="as_shipped", s=S)
    assert r.required_pages == [1]
    assert [h.page_index for h in r.blocking] == [1]


def test_no_gate_when_not_implied_and_no_critical():
    pages = [FakePage(0, "schematic", 10, 10)]
    r = gates.evaluate(pages, schematic_implied=False, critical_pages=set(), basis="as_shipped", s=S)
    assert not r.required_pages and not r.blocking


def test_operator_critical_pages_always_required():
    pages = [FakePage(0, "text", 30, 90)]
    r = gates.evaluate(pages, schematic_implied=False, critical_pages={0}, basis="as_shipped", s=S)
    assert r.required_pages == [0]
    assert r.blocks_usable


def test_two_levels_image():
    pages = [
        FakePage(0, "schematic", 49.9, None, tq_is_null=True),
        FakePage(1, "schematic", 50.0, None, tq_is_null=True),
        FakePage(2, "schematic", 74.9, None, tq_is_null=True),
        FakePage(3, "schematic", 75.0, None, tq_is_null=True),
    ]
    r = gates.evaluate(pages, True, set(), "as_shipped", S)
    assert [h.page_index for h in r.blocking] == [0]
    assert sorted(h.page_index for h in r.degraded) == [1, 2]


def test_text_gate_mirrors_image_gate():
    pages = [
        FakePage(0, "schematic", 100, 49.0),
        FakePage(1, "schematic", 100, 60.0),
        FakePage(2, "schematic", 100, 70.0),
    ]
    r = gates.evaluate(pages, True, set(), "post_reprocess", S)
    text_blocking = [h for h in r.blocking if h.dimension == "text"]
    text_degraded = [h for h in r.degraded if h.dimension == "text"]
    assert [h.page_index for h in text_blocking] == [0]
    assert [h.page_index for h in text_degraded] == [1]
    assert r.basis == "post_reprocess"


def test_tq_null_pages_never_gate():
    pages = [FakePage(0, "schematic", 100, None, tq_is_null=True)]
    r = gates.evaluate(pages, True, set(), "as_shipped", S)
    assert not r.blocking and not r.degraded


def test_unassessed_pages_never_gate():
    pages = [FakePage(0, "schematic", 10, 10, assessed=False)]
    r = gates.evaluate(pages, True, set(), "as_shipped", S)
    assert not r.blocking


def test_pages_below_adequacy_counts_all_assessed():
    pages = [
        FakePage(0, "text", 74, 69),
        FakePage(1, "text", 76, 71),
        FakePage(2, "schematic", 10, None, tq_is_null=True),
    ]
    r = gates.evaluate(pages, False, set(), "as_shipped", S)
    assert r.pages_below_adequacy == {"image": 2, "text": 1}
