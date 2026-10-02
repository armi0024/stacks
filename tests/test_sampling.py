from __future__ import annotations

from stacks.analyzer.sampling import screening_sample


def test_small_docs_fully_sampled():
    assert screening_sample(10, 16) == list(range(10))
    assert screening_sample(16, 16) == list(range(16))


def test_large_doc_even_sample():
    s = screening_sample(100, 16)
    assert len(s) == 16
    assert s[0] == 0 and s[-1] == 99
    assert s == sorted(set(s))
    gaps = [b - a for a, b in zip(s, s[1:])]
    assert max(gaps) - min(gaps) <= 2  # evenly spaced


def test_deterministic():
    assert screening_sample(537, 16) == screening_sample(537, 16)


def test_single_page_budget():
    assert screening_sample(50, 1) == [0]
