"""
Sentence/clause splitting.
"""

from lute.clozeexport.units import render_items, split_into_clauses


def test_paragraphs_and_sentences_are_split(render_paragraphs):
    "Sentences group by sentence number, paragraphs by the ¶ marker."
    content = "I saw a dog. It barked.\nThen it left."
    paragraphs = render_paragraphs(content)
    texts = [[s.text for s in para] for para in paragraphs]
    assert texts == [["I saw a dog.", "It barked."], ["Then it left."]]


def test_sentences_rejoin_to_the_source(render_paragraphs):
    "Joining a paragraph's sentences reproduces the original text."
    content = "I laughed a lot. It was funny, really."
    paragraph = render_paragraphs(content)[0]
    items = [ti for s in paragraph for ti in s.items]
    assert render_items(items) == content


def test_word_count_ignores_punctuation(render_paragraphs):
    "Only real words count towards the length budget."
    paragraph = render_paragraphs("I laughed a lot, really.")[0]
    assert paragraph[0].word_count == 5


def test_multiword_term_counts_its_words(render_paragraphs, make_terms):
    "A displayed multi-word term is several words, not one."
    make_terms({"a lot": 1})
    paragraph = render_paragraphs("I laughed a lot.")[0]
    assert paragraph[0].word_count == 4


def test_clause_split_on_punctuation(render_paragraphs):
    "Punctuation stays with the clause it closes."
    paragraph = render_paragraphs("I went home, and then I slept.")[0]
    clauses = split_into_clauses(paragraph[0])
    assert [c.text for c in clauses] == ["I went home,", "and then I slept."]


def test_clause_split_on_conjunction(render_paragraphs):
    "A conjunction opens the clause it introduces."
    paragraph = render_paragraphs("I went home because I was tired")[0]
    clauses = split_into_clauses(paragraph[0], {"because"})
    assert [c.text for c in clauses] == ["I went home", "because I was tired"]


def test_clauses_rejoin_to_the_sentence(render_paragraphs):
    "Splitting never loses or invents text."
    content = "I went home, and because I was tired, I slept."
    sentence = render_paragraphs(content)[0][0]
    clauses = split_into_clauses(sentence, {"because", "and"})
    items = [ti for c in clauses for ti in c.items]
    assert render_items(items) == content


def test_unsplittable_sentence_is_returned_whole(render_paragraphs):
    "No boundaries means no split."
    sentence = render_paragraphs("I laughed a lot.")[0][0]
    assert split_into_clauses(sentence, {"because"}) == [sentence]


def test_clause_boundary_flags(render_paragraphs):
    "Only the outer clauses sit on real sentence boundaries."
    sentence = render_paragraphs("I went home, and then I slept.")[0][0]
    first, last = split_into_clauses(sentence)
    assert (first.starts_sentence, first.ends_sentence) == (True, False)
    assert (last.starts_sentence, last.ends_sentence) == (False, True)
