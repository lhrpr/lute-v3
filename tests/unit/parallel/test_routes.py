"""
/parallel route tests.
"""

import json

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


@pytest.fixture(name="books")
def fixture_books(app_context, spanish, english):
    "A Spanish and an English book."
    es = _make_book(spanish, "Libro", [f"pagina {i} de Pedro." for i in range(1, 11)])
    en = _make_book(
        english,
        "Book",
        [f"page {i} of Peter and Mary." for i in range(1, 21)],
    )
    return es, en


def test_pair_page_lists_other_language_books(client, books):
    es, en = books
    response = client.get(f"/parallel/pair/{es.id}")
    assert response.status_code == 200
    assert b"Book" in response.data


def test_pairing_redirects_to_review(client, books):
    es, en = books
    response = client.post(
        f"/parallel/pair/{es.id}", data={"companion_id": en.id}
    )
    assert response.status_code == 302
    assert f"/parallel/review/{es.id}" in response.headers["Location"]
    assert Service(db.session).get_companion(es).book.id == en.id


def test_review_without_structure_explains_itself(client, books):
    es, en = books
    Service(db.session).pair_books(es, en)

    response = client.get(f"/parallel/review/{es.id}")

    assert response.status_code == 200
    assert b"nothing to line" in response.data


def test_review_with_structure_on_one_side_only(client, books):
    """
    Pairing a newly imported book with one imported before sections were
    recorded aligns to nothing but gaps -- that is the "no structure"
    case, not a table of empty rows.
    """
    es, en = books
    for order, (title, page) in enumerate([("Capitulo I", 1), ("Capitulo II", 5)]):
        section = BookSection()
        section.bk_id = es.id
        section.order = order
        section.title = title
        section.start_page = page
        section.token_count = 500
        db.session.add(section)
    db.session.commit()
    Service(db.session).pair_books(es, en)

    response = client.get(f"/parallel/review/{es.id}")

    assert b"nothing to line" in response.data
    assert b"not in this book" not in response.data


def test_review_post_saves_anchors(client, books):
    es, en = books
    Service(db.session).pair_books(es, en)

    response = client.post(
        f"/parallel/review/{es.id}", data={"anchor": ["3:7", "5:11"]}
    )

    assert response.status_code == 302
    assert Service(db.session).get_companion(es).anchors == [(3, 7), (5, 11)]


def test_review_post_ignores_malformed_anchors(client, books):
    es, en = books
    Service(db.session).pair_books(es, en)

    client.post(f"/parallel/review/{es.id}", data={"anchor": ["3:7", "junk", "x:y"]})

    assert Service(db.session).get_companion(es).anchors == [(3, 7)]


def test_companion_endpoint_reports_unpaired(client, books):
    es, _ = books
    response = client.get(f"/parallel/companion/{es.id}/1")
    assert response.json == {"paired": False}


def test_companion_endpoint_reports_page_and_state(client, books):
    es, en = books
    Service(db.session).pair_books(es, en, anchors=[(3, 7)])

    data = client.get(f"/parallel/companion/{es.id}/3").json

    assert data["paired"] is True
    assert data["page"] == 7
    assert data["anchored"] is True
    assert data["book_id"] == en.id


def test_set_and_clear_anchor(client, books):
    es, en = books
    Service(db.session).pair_books(es, en)

    client.post(
        "/parallel/anchor",
        data=json.dumps({"bookid": es.id, "pagenum": 4, "companion_page": 9}),
        content_type="application/json",
    )
    assert Service(db.session).get_companion(es).anchors == [(4, 9)]

    client.post(
        "/parallel/unanchor",
        data=json.dumps({"bookid": es.id, "pagenum": 4}),
        content_type="application/json",
    )
    assert Service(db.session).get_companion(es).anchors == []


def test_anchor_on_unpaired_book_is_rejected(client, books):
    es, _ = books
    response = client.post(
        "/parallel/anchor",
        data=json.dumps({"bookid": es.id, "pagenum": 1, "companion_page": 1}),
        content_type="application/json",
    )
    assert response.status_code == 400


def test_search_ranks_pages_by_how_many_terms_match(client, books):
    "Two character names should beat one."
    _, en = books
    data = client.get(f"/parallel/search/{en.id}?q=Peter+Mary").json

    assert len(data["hits"]) > 0
    assert data["hits"][0]["matched"] == 2
    assert "snippet" in data["hits"][0]


def test_search_with_no_terms_returns_nothing(client, books):
    _, en = books
    assert client.get(f"/parallel/search/{en.id}?q=").json == {"hits": []}


def test_search_miss_returns_no_hits(client, books):
    _, en = books
    assert client.get(f"/parallel/search/{en.id}?q=zzzznotfound").json["hits"] == []


def test_companion_text_renders_the_page(client, books):
    _, en = books
    response = client.get(f"/parallel/text/{en.id}/2")
    assert response.status_code == 200
    assert b"page 2 of Peter" in response.data


def test_companion_text_clamps_out_of_range_pages(client, books):
    _, en = books
    response = client.get(f"/parallel/text/{en.id}/9999")
    assert response.status_code == 200
    assert b"page 20" in response.data


def test_unpair(client, books):
    es, en = books
    Service(db.session).pair_books(es, en)

    response = client.post(f"/parallel/unpair/{es.id}")

    assert response.status_code == 302
    assert Service(db.session).get_companion(es) is None


def test_reading_page_shows_companion_pane(client, books):
    es, en = books
    Service(db.session).pair_books(es, en, anchors=[(2, 5)])

    response = client.get(f"/read/{es.id}/page/2")

    assert response.status_code == 200
    assert b"read_pane_companion" in response.data
    assert b"/parallel/text/%d/5" % en.id in response.data


def test_reading_page_without_pair_has_no_companion_pane(client, books):
    es, _ = books
    response = client.get(f"/read/{es.id}/page/1")
    assert response.status_code == 200
    assert b"read_pane_companion" not in response.data
