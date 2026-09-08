"""
Helpers for the cloze export tests.

The tests work on real rendered TextItems (rather than hand-built fakes) so
they exercise the same tokenizing and status overlay the export itself gets.
"""

import pytest

from lute.clozeexport.lexicons import anaphora_words, conjunction_words
from lute.clozeexport.options import ClozeOptions
from lute.clozeexport.units import paragraphs_of_sentences
from lute.db import db
from lute.models.term import Term
from lute.read.render.service import Service as RenderService


@pytest.fixture(name="make_terms")
def fixture_make_terms(english):
    "Create saved terms with the given statuses: make_terms({'dog': 1, ...})."

    def _make(statuses):
        ret = {}
        for text, status in statuses.items():
            term = Term(english, text)
            term.status = status
            db.session.add(term)
            ret[text] = term
        db.session.commit()
        return ret

    return _make


@pytest.fixture(name="render_paragraphs")
def fixture_render_paragraphs(english):
    "Render a string into paragraphs of sentence Units."

    def _render(content):
        service = RenderService(db.session)
        textitems = service.get_textitems(content, english)
        return paragraphs_of_sentences(textitems)

    return _render


@pytest.fixture(name="known_paragraphs")
def fixture_known_paragraphs(english, render_paragraphs):
    """
    Render content where every word is already known, bar the given exceptions.

    Almost every span test needs "the reader knows all of this except X", and
    saying so word by word buries the point of the test.
    """

    def _render(content, statuses=None):
        statuses = statuses or {}
        words = {
            english.get_lowercase(t.token)
            for t in english.get_parsed_tokens(content)
            if t.is_word
        }
        for word in sorted(words):
            status = statuses.get(word, 99)
            if status == 0:
                continue  # genuinely unknown: no saved term at all
            term = Term(english, word)
            term.status = status
            db.session.add(term)
        db.session.commit()
        return render_paragraphs(content)

    return _render


@pytest.fixture(name="opts")
def fixture_opts():
    "Default options, with the English word lists loaded."
    return ClozeOptions(
        anaphora_words=anaphora_words("English"),
        conjunction_words=conjunction_words("English"),
    )


def find_item(paragraph, text_lc):
    "The (sentence_index, TextItem) for the first occurrence of a word."
    for i, sentence in enumerate(paragraph):
        for item in sentence.word_items:
            if item.text_lc == text_lc:
                return i, item
    raise ValueError(f'No word "{text_lc}" in the paragraph.')
