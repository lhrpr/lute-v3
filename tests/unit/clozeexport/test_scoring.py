"""
Span selection: the behaviours the whole feature exists for.
"""

from lute.clozeexport.scoring import select_span
from tests.unit.clozeexport.conftest import find_item


def _select(paragraph, word, opts):
    "Choose a span for the first occurrence of `word`."
    index, item = find_item(paragraph, word)
    return select_span(paragraph, index, [item], {item.term.id}, opts)


def test_short_sentence_pulls_in_its_context(known_paragraphs, opts):
    "A four-word sentence is useless alone, so the one before it comes too."
    content = "Last night I watched a comedy film with my children. I laughed a lot."
    paragraph = known_paragraphs(content, {"laughed": 2})[0]
    span, _ = _select(paragraph, "laughed", opts)
    assert span.text == content
    assert span.mode == "sentence"


def test_a_sentence_of_the_right_length_stands_alone(known_paragraphs, opts):
    "No context is pulled in when the sentence already works."
    content = (
        "The dog barked at the postman. Last night I watched a funny "
        "comedy film with my children."
    )
    paragraph = known_paragraphs(content, {"comedy": 2})[0]
    span, _ = _select(paragraph, "comedy", opts)
    assert span.text == ("Last night I watched a funny comedy film with my children.")


def test_context_is_not_pulled_across_an_unknown_word(known_paragraphs, opts):
    "Expanding into text the reader can't read is worse than a short card."
    content = "The postman brought a strange parcel. I laughed a lot."
    paragraph = known_paragraphs(content, {"laughed": 2, "parcel": 0})[0]
    span, _ = _select(paragraph, "laughed", opts)
    assert span.text == "I laughed a lot."


def test_no_span_when_the_sentence_itself_is_too_hard(known_paragraphs, opts):
    "Two unknown words beside the target means there's no card to make."
    content = "I laughed at the strange enormous parcel."
    paragraph = known_paragraphs(content, {"laughed": 2, "strange": 0, "enormous": 0})[
        0
    ]
    span, _ = _select(paragraph, "laughed", opts)
    assert span is None


def test_long_sentence_is_cut_to_clauses(known_paragraphs, opts):
    "A monster sentence is reduced to the clauses around the target."
    content = (
        "When I got home from the office that evening, tired and hungry "
        "after a very long day, I watched a comedy film with my children, "
        "and then I went straight to bed without eating anything at all."
    )
    paragraph = known_paragraphs(content, {"comedy": 2})[0]
    span, needs_review = _select(paragraph, "comedy", opts)
    assert span.mode == "clause"
    assert "comedy" in span.text
    assert span.word_count < paragraph[0].word_count
    assert needs_review, "clause cuts are the ones worth an LLM opinion"


def test_a_clause_cut_does_not_open_on_a_conjunction(known_paragraphs, opts):
    """
    "and hungry after a long day" points at a clause we've excluded.

    Every clause cut is a fragment, so the fragment penalty says nothing about
    which cut to make; the word the span opens with is what decides.
    """
    content = (
        "When I got home from the office that evening, tired and hungry "
        "after a very long day at work, I watched a film with my children, "
        "and then I went straight to bed without eating anything at all."
    )
    paragraph = known_paragraphs(content, {"hungry": 2})[0]
    span, _ = _select(paragraph, "hungry", opts)
    assert span.mode == "clause"
    assert span.text == "tired and hungry after a very long day at work,"


def test_a_chosen_span_is_always_verbatim_source_text(known_paragraphs, opts):
    "Whatever we choose, it's a slice of the book -- never reworded."
    content = (
        "When I got home that evening, tired and hungry, I watched a "
        "comedy film with my children, and then I went to bed."
    )
    paragraph = known_paragraphs(content, {"comedy": 2})[0]
    span, _ = _select(paragraph, "comedy", opts)
    assert span.text in content


def test_anaphoric_opening_pulls_in_the_previous_sentence(known_paragraphs, opts):
    "'But he ...' is leaning on something we'd otherwise leave out."
    content = (
        "My brother had promised to arrive before the party started. "
        "But he was late again, and everyone had to wait."
    )
    paragraph = known_paragraphs(content, {"late": 2})[0]
    span, _ = _select(paragraph, "late", opts)
    assert span.text == content


def test_cloze_hides_every_occurrence_of_the_target(known_paragraphs, opts):
    "Leaving a second copy visible would give the answer away."
    content = "The dog barked and then the dog barked again at the postman."
    paragraph = known_paragraphs(content, {"barked": 2})[0]
    index, item = find_item(paragraph, "barked")
    span, _ = select_span(paragraph, index, [item], {item.term.id}, opts)
    targets = [
        ti for ti in span.items if ti.term is not None and ti.term.id == item.term.id
    ]
    cloze = span.cloze_text(targets, opts)
    assert cloze.count("{{c1::barked}}") == 2
    assert "barked" not in cloze.replace("{{c1::barked}}", "")


def test_length_band_is_soft(known_paragraphs, opts):
    "A slightly-too-long complete sentence beats cutting it up."
    content = (
        "I watched a very funny comedy film with my two children and my "
        "brother at the cinema in town yesterday evening."
    )
    paragraph = known_paragraphs(content, {"comedy": 2})[0]
    span, _ = _select(paragraph, "comedy", opts)
    assert span.word_count > opts.max_words
    assert span.text == content
