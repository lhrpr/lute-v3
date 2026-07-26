"""
Parallel reading service tests.

Exercises pairing and anchor orientation against a real db, since the
orientation rules only matter in combination with the stored pair.
"""

import pytest

from lute.db import db
from lute.models.book import Book, BookSection
from lute.parallel.service import Service


def _make_book(language, title, page_texts):
    "Create and save a book with the given pages."
    book = Book(title, language)
    for i, text in enumerate(page_texts):
        page = book.add_page_after(i)
        page.book = book
        page.text = text
    db.session.add(book)
    db.session.commit()
    return book


def _add_sections(book, titles_and_pages, token_count=1000):
    "Attach sections to a book."
    for order, (title, start_page) in enumerate(titles_and_pages):
        section = BookSection()
        section.bk_id = book.id
        section.order = order
        section.title = title
        section.start_page = start_page
        section.token_count = token_count
        db.session.add(section)
    db.session.commit()


@pytest.fixture(name="pair_books")
def fixture_pair_books(app_context, spanish, english):
    "A Spanish and an English book of differing lengths."
    es = _make_book(spanish, "Libro", [f"pagina {i}." for i in range(1, 11)])
    en = _make_book(english, "Book", [f"page {i}." for i in range(1, 21)])
    return es, en


def test_unpaired_book_has_no_companion(pair_books):
    es, _ = pair_books
    assert Service(db.session).get_companion(es) is None


def test_pairing_creates_a_companion_both_ways(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en)

    assert service.get_companion(es).book.id == en.id
    assert service.get_companion(en).book.id == es.id


def test_anchors_are_inverted_when_read_from_the_far_side(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en, anchors=[(4, 8)])

    # Reading the primary: page 4 -> 8.
    assert service.get_companion(es).page_for(4) == 8
    # Reading the companion: page 8 -> 4.
    assert service.get_companion(en).page_for(8) == 4


def test_set_anchor_from_primary_side(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en)

    service.set_anchor(es, 3, 7)

    assert service.get_companion(es).page_for(3) == 7
    assert service.get_companion(en).page_for(7) == 3


def test_set_anchor_from_companion_side(pair_books):
    "An anchor set while reading the far side must store right way round."
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en)

    service.set_anchor(en, 7, 3)

    assert service.get_companion(en).page_for(7) == 3
    assert service.get_companion(es).page_for(3) == 7


def test_clear_anchor_from_either_side(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en, anchors=[(3, 7)])

    service.clear_anchor(en, 7)

    assert service.get_companion(es).anchors == []


def test_pairing_replaces_an_existing_pair(pair_books, app_context, spanish):
    es, en = pair_books
    other = _make_book(spanish, "Otro", ["uno.", "dos."])
    service = Service(db.session)
    service.pair_books(es, en)

    service.pair_books(es, other)

    assert service.get_companion(es).book.id == other.id
    assert service.get_companion(en) is None


def test_unpair(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en)

    assert service.unpair(es) is True
    assert service.get_companion(es) is None
    assert service.get_companion(en) is None


def test_auto_anchors_from_sections(pair_books):
    "Chapter numbering lines the two books up despite an extra foreword."
    es, en = pair_books
    _add_sections(es, [("Capitulo I", 1), ("Capitulo II", 4), ("Capitulo III", 7)])
    _add_sections(
        en,
        [("Foreword", 1), ("Chapter I", 3), ("Chapter II", 9), ("Chapter III", 15)],
    )

    anchors = Service(db.session).auto_anchors(es, en)

    assert anchors == [(1, 3), (4, 9), (7, 15)]


def test_short_numbered_sections_are_still_anchorable(pair_books):
    """
    A dated-entry novel can end on a chapter of nine words.  A numbered
    heading is enough to anchor on, however short the chapter is.
    """
    es, en = pair_books
    _add_sections(es, [("Portada", 1)], token_count=9)
    _add_sections(es, [("14 de febrero", 2), ("15 de febrero", 3)], token_count=12)
    _add_sections(en, [("Cover", 1)], token_count=9)
    _add_sections(en, [("February 14", 4), ("February 15", 6)], token_count=13)

    service = Service(db.session)
    titles = [s.title for s in service.sections_for(es)]

    # The untitled cover goes; the tiny dated entries stay.
    assert titles == ["14 de febrero", "15 de febrero"]
    assert service.auto_anchors(es, en) == [(2, 4), (3, 6)]


def test_auto_anchors_empty_without_sections(pair_books):
    "No structure means no guesses."
    es, en = pair_books
    assert Service(db.session).auto_anchors(es, en) == []


def test_companion_falls_back_to_proportional_without_anchors(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en)

    companion = service.get_companion(es)
    assert companion.page_for(1) == 1
    assert companion.page_for(10) == 20


def test_anchor_state_reports_interpolation(pair_books):
    es, en = pair_books
    service = Service(db.session)
    service.pair_books(es, en, anchors=[(2, 3), (8, 16)])

    companion = service.get_companion(es)
    assert companion.anchor_state(2)["anchored"] is True
    state = companion.anchor_state(5)
    assert state["anchored"] is False
    assert state["before"] == (2, 3)
    assert state["after"] == (8, 16)
