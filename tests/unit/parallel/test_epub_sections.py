"""
Epub spine extraction tests.

Builds real (minimal) epub files, since the point of the feature is
surviving the structure that actual files carry.
"""

import zipfile
from io import BytesIO

import pytest

from lute.book.model import Book, Repository
from lute.book.service import FileTextExtraction, MIN_SECTION_TOKENS
from lute.db import db


def _chapter_html(heading, word, count=200):
    body = " ".join([word] * count)
    return (
        "<html><head><title>x</title></head><body>"
        f"<h1>{heading}</h1><p>{body}</p>"
        "</body></html>"
    )


def _make_epub(chapters):
    """
    Build an epub from [(filename, html)] in spine order.
    """
    manifest = "".join(
        f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _) in enumerate(chapters)
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        "<metadata/>"
        f"<manifest>{manifest}</manifest>"
        f"<spine>{spine}</spine>"
        "</package>"
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        for name, html in chapters:
            z.writestr(name, html)
    buf.seek(0)
    return buf


def test_extracts_spine_sections_with_headings():
    epub = _make_epub(
        [
            ("c0.xhtml", _chapter_html("Chapter I", "alpha")),
            ("c1.xhtml", _chapter_html("Chapter II", "beta")),
            ("c2.xhtml", _chapter_html("Chapter III", "gamma")),
        ]
    )
    fte = FileTextExtraction()

    content = fte.get_file_content("t.epub", epub)

    assert [s.title for s in fte.sections] == ["Chapter I", "Chapter II", "Chapter III"]
    # Sections are separated by the page break marker.
    assert content.count("\n---\n") == 2
    assert "alpha" in content and "gamma" in content


def test_short_sections_are_kept_in_the_text():
    """
    Short sections must never be dropped from the book.

    Novels built from brief dated entries are made of them, and dropping
    one silently removes its text -- which is exactly what chopped the
    start and end off a real import.
    """
    epub = _make_epub(
        [
            ("e0.xhtml", _chapter_html("2 de noviembre", "primero", 20)),
            ("e1.xhtml", _chapter_html("3 de noviembre", "segundo", 20)),
            ("c0.xhtml", _chapter_html("Los detectives", "cuerpo")),
            ("c1.xhtml", _chapter_html("Desiertos", "mas")),
            ("e2.xhtml", _chapter_html("15 de febrero", "penultimo", 20)),
            ("e3.xhtml", _chapter_html("16 de febrero", "ultimo", 20)),
        ]
    )
    fte = FileTextExtraction()

    content = fte.get_file_content("t.epub", epub)

    for word in ["primero", "segundo", "cuerpo", "mas", "penultimo", "ultimo"]:
        assert word in content, f"lost the section containing {word}"
    assert len(fte.sections) == 6


def test_short_front_matter_is_kept_but_does_not_create_structure():
    """
    A cover page is not a chapter, but its text still belongs to the book.
    """
    epub = _make_epub(
        [
            ("cover.xhtml", "<html><body><p>Cubierta</p></body></html>"),
            ("c0.xhtml", _chapter_html("Chapter I", "alpha")),
            ("c1.xhtml", _chapter_html("Chapter II", "beta")),
        ]
    )
    fte = FileTextExtraction()

    content = fte.get_file_content("t.epub", epub)

    assert "Cubierta" in content
    assert [s.title for s in fte.sections] == [None, "Chapter I", "Chapter II"]


def test_pile_of_short_sections_is_not_mistaken_for_structure():
    "Front matter alone is not chapter structure."
    epub = _make_epub(
        [
            ("f0.xhtml", "<html><body><p>Cubierta</p></body></html>"),
            ("f1.xhtml", "<html><body><p>Portada</p></body></html>"),
            ("f2.xhtml", "<html><body><p>Creditos</p></body></html>"),
        ]
    )
    fte = FileTextExtraction()

    content = fte.get_file_content("t.epub", epub)

    assert fte.sections == []
    assert "Cubierta" in content and "Creditos" in content


def test_single_section_epub_yields_no_structure():
    """
    A whole-novel-in-one-file epub (common from Gutenberg) has nothing to
    align against, and must not pretend otherwise.
    """
    epub = _make_epub([("all.xhtml", _chapter_html("War and Peace", "word", 5000))])
    fte = FileTextExtraction()

    content = fte.get_file_content("t.epub", epub)

    assert fte.sections == []
    assert "---" not in content
    assert "word" in content


def test_split_at_sections_off_reads_the_epub_as_one_run():
    "Opting out restores the previous behaviour exactly."
    epub = _make_epub(
        [
            ("c0.xhtml", _chapter_html("Chapter I", "alpha")),
            ("c1.xhtml", _chapter_html("Chapter II", "beta")),
        ]
    )
    fte = FileTextExtraction(split_at_sections=False)

    content = fte.get_file_content("t.epub", epub)

    assert fte.sections == []
    assert "---" not in content
    assert "alpha" in content and "beta" in content


def test_section_without_heading_still_extracted():
    epub = _make_epub(
        [
            ("c0.xhtml", f"<html><body><p>{' '.join(['x'] * 200)}</p></body></html>"),
            ("c1.xhtml", _chapter_html("Chapter II", "beta")),
        ]
    )
    fte = FileTextExtraction()

    fte.get_file_content("t.epub", epub)

    assert [s.title for s in fte.sections] == [None, "Chapter II"]


def test_min_section_tokens_boundary():
    "A section right at the threshold is kept."
    epub = _make_epub(
        [
            ("c0.xhtml", _chapter_html("Chapter I", "a", MIN_SECTION_TOKENS)),
            ("c1.xhtml", _chapter_html("Chapter II", "b", MIN_SECTION_TOKENS)),
        ]
    )
    fte = FileTextExtraction()

    fte.get_file_content("t.epub", epub)

    assert len(fte.sections) == 2


@pytest.mark.parametrize("split_by", ["paragraphs", "sentences"])
def test_sections_start_on_exact_page_boundaries(app_context, english, split_by):
    """
    The whole anchoring scheme rests on this: a section's recorded start
    page must be the page its text actually begins on.
    """
    epub = _make_epub(
        [
            ("c0.xhtml", _chapter_html("Chapter I", "alpha")),
            ("c1.xhtml", _chapter_html("Chapter II", "beta")),
            ("c2.xhtml", _chapter_html("Chapter III", "gamma")),
        ]
    )
    fte = FileTextExtraction()

    book = Book()
    book.title = "Test"
    book.language_id = english.id
    book.text = fte.get_file_content("t.epub", epub)
    book.sections = fte.sections
    book.split_by = split_by
    book.threshold_page_tokens = 50

    repo = Repository(db.session)
    dbbook = repo.add(book)
    repo.commit()

    assert len(dbbook.sections) == 3
    for section, word in zip(dbbook.sections, ["alpha", "beta", "gamma"]):
        page = dbbook.text_at_page(section.start_page)
        assert word in page.text, f"{section.title} does not start on its page"
