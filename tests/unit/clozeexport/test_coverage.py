"""
The comprehensibility gate.
"""

from lute.clozeexport.coverage import is_comprehensible, summarize


def _items(paragraph):
    return [ti for s in paragraph for ti in s.items]


def test_words_bucket_by_status(known_paragraphs):
    "Unknown, learning and known words are counted separately."
    paragraph = known_paragraphs("I saw a big dog", {"big": 3, "dog": 0})[0]
    coverage = summarize(_items(paragraph))
    assert coverage.unknown == {"dog"}
    assert coverage.learning == {"big"}
    assert coverage.known == {"i", "saw", "a"}


def test_target_term_does_not_count_against_the_budget(known_paragraphs):
    "The word being tested is allowed to be unknown -- that's the point."
    paragraph = known_paragraphs("I saw a big dog", {"big": 3})[0]
    big = [ti for ti in _items(paragraph) if ti.text_lc == "big"][0]
    coverage = summarize(_items(paragraph), {big.term.id})
    assert coverage.learning == set()


def test_repeated_words_count_once(known_paragraphs):
    "A sentence repeating one hard word is one word's worth of difficulty."
    paragraph = known_paragraphs("a dog and a dog and a dog", {"dog": 3})[0]
    coverage = summarize(_items(paragraph))
    assert coverage.learning_count == 1


def test_unrecognised_status_counts_as_learning(known_paragraphs):
    "An odd custom status makes a span harder, never silently free."
    paragraph = known_paragraphs("I saw a dog", {"dog": 42})[0]
    coverage = summarize(_items(paragraph))
    assert coverage.learning == {"dog"}


def test_gate_respects_budgets(known_paragraphs):
    "Unknown and learning budgets are enforced independently."
    paragraph = known_paragraphs("I saw a big red dog", {"big": 3, "red": 3})[0]
    coverage = summarize(_items(paragraph))
    assert not is_comprehensible(coverage, max_unknown=0, max_learning=1)
    assert is_comprehensible(coverage, max_unknown=0, max_learning=2)
