"""
/parallel endpoints.

Pairing two books, reviewing the anchors proposed from their structure,
and the anchor/search calls the reading pane makes.
"""

from flask import Blueprint, flash, jsonify, redirect, render_template, request

from lute.db import db
from lute.models.book import Book
from lute.models.repositories import BookRepository
from lute.parallel.alignment import MIN_ANCHOR_CONFIDENCE
from lute.parallel.service import Service

bp = Blueprint("parallel", __name__, url_prefix="/parallel")

# Cap on search hits: the reader is picking a place to anchor, not
# reading the results, and a common word in a long book returns
# hundreds of pages.
MAX_SEARCH_HITS = 25


def _find_book(bookid):
    "Find book from db."
    return BookRepository(db.session).find(bookid)


def _candidate_books(book):
    """
    Books that could be paired with this one.

    Only other languages: two books in the same language are not a
    parallel text, and offering them just makes the list harder to use.
    """
    return (
        db.session.query(Book)
        .filter(Book.id != book.id)
        .filter(Book.language_id != book.language_id)
        .filter(Book.archived.is_(False))
        .order_by(Book.title)
        .all()
    )


@bp.route("/pair/<int:bookid>", methods=["GET", "POST"])
def pair(bookid):
    "Choose a book to read in parallel with this one."
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    service = Service(db.session)

    if request.method == "POST":
        companion_id = int(request.form.get("companion_id", 0))
        companion = _find_book(companion_id)
        if companion is None:
            flash("Please choose a book to pair with.", "error")
            return redirect(f"/parallel/pair/{bookid}", 302)
        service.pair_books(book, companion)
        return redirect(f"/parallel/review/{bookid}", 302)

    existing = service.get_companion(book)
    return render_template(
        "parallel/pair.html",
        book=book,
        candidates=_candidate_books(book),
        existing=existing,
    )


@bp.route("/unpair/<int:bookid>", methods=["POST"])
def unpair(bookid):
    "Remove a book's pair."
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)
    Service(db.session).unpair(book)
    flash(f'"{book.title}" is no longer a parallel text.')
    return redirect("/", 302)


@bp.route("/review/<int:bookid>", methods=["GET", "POST"])
def review(bookid):
    """
    Review the anchors proposed from the two books' structures.

    Nothing is committed until the reader confirms: an alignment built
    from a ropey epub is a suggestion, not a fact.
    """
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    service = Service(db.session)
    companion = service.get_companion(book)
    if companion is None:
        flash("Pair this book with another before reviewing anchors.")
        return redirect(f"/parallel/pair/{bookid}", 302)

    if request.method == "POST":
        anchors = []
        for value in request.form.getlist("anchor"):
            primary, sep, secondary = value.partition(":")
            if sep and primary.isdigit() and secondary.isdigit():
                anchors.append((int(primary), int(secondary)))
        service.pair_books(book, companion.book, anchors=anchors)
        flash(f"Saved {len(anchors)} anchors for “{book.title}”.")
        return redirect(f"/read/{bookid}", 302)

    alignment = service.propose_alignment(book, companion.book)
    # Tick exactly what the service would commit, rather than re-deriving
    # it from confidence in the template -- the plausibility check would
    # be invisible there, and a coincidental match would arrive ticked.
    proposed = set(service.auto_anchors(book, companion.book))
    return render_template(
        "parallel/review.html",
        book=book,
        companion=companion.book,
        alignment=alignment,
        proposed=proposed,
        # A book paired with one that has no stored sections aligns to
        # nothing but gaps; that is the "no structure" case too, not a
        # table of empty rows.
        has_structure=any(a and b for a, b, _ in alignment),
        min_confidence=MIN_ANCHOR_CONFIDENCE,
    )


@bp.route("/companion/<int:bookid>/<int:pagenum>", methods=["GET"])
def companion_page(bookid, pagenum):
    "The companion page to show alongside a page, and how it was derived."
    book = _find_book(bookid)
    if book is None:
        return jsonify({"error": "no such book"}), 404
    companion = Service(db.session).get_companion(book)
    if companion is None:
        return jsonify({"paired": False})

    page = companion.page_for(pagenum)
    state = companion.anchor_state(pagenum)
    return jsonify(
        {
            "paired": True,
            "book_id": companion.book.id,
            "title": companion.book.title,
            "page": page,
            "page_count": companion.page_count,
            "anchored": state["anchored"],
            "before": state["before"],
            "after": state["after"],
        }
    )


@bp.route("/text/<int:bookid>/<int:pagenum>", methods=["GET"])
def companion_text(bookid, pagenum):
    """
    A page of the companion book as plain text, for the reading pane.

    Deliberately not the reader: the companion is a translation to
    glance at, so it carries no term markup, no dictionary pane, and no
    click handlers that could change a term's status by accident.
    """
    book = _find_book(bookid)
    if book is None:
        return "", 404
    pagenum = book.page_in_range(pagenum)
    text = book.text_at_page(pagenum)
    paragraphs = [p for p in (text.text or "").split("\n") if p.strip()]
    return render_template(
        "parallel/companion_text.html",
        paragraphs=paragraphs,
        is_rtl=book.language.right_to_left,
    )


@bp.route("/anchor", methods=["POST"])
def set_anchor():
    "Anchor the page being read to the companion page on screen."
    data = request.json
    book = _find_book(int(data.get("bookid")))
    if book is None:
        return jsonify({"error": "no such book"}), 404
    service = Service(db.session)
    pair_record = service.set_anchor(
        book, int(data.get("pagenum")), int(data.get("companion_page"))
    )
    if pair_record is None:
        return jsonify({"error": "not paired"}), 400
    return jsonify("ok")


@bp.route("/unanchor", methods=["POST"])
def clear_anchor():
    "Remove the anchor on the page being read."
    data = request.json
    book = _find_book(int(data.get("bookid")))
    if book is None:
        return jsonify({"error": "no such book"}), 404
    service = Service(db.session)
    pair_record = service.clear_anchor(book, int(data.get("pagenum")))
    if pair_record is None:
        return jsonify({"error": "not paired"}), 400
    return jsonify("ok")


@bp.route("/search/<int:bookid>", methods=["GET"])
def search(bookid):
    """
    Find pages of a book containing the given words.

    Ranked by how many of the terms a page contains, so searching two
    character names puts the pages with both at the top -- which is how
    a reader finds their place without leaving the machine offline.
    """
    book = _find_book(bookid)
    if book is None:
        return jsonify({"error": "no such book"}), 404

    terms = [t for t in (request.args.get("q") or "").split() if t]
    if not terms:
        return jsonify({"hits": []})

    hits = []
    for text in book.texts:
        content = text.text.lower()
        matched = [t for t in terms if t.lower() in content]
        if matched:
            hits.append(
                {
                    "page": text.order,
                    "matched": len(matched),
                    "snippet": _snippet(text.text, matched[0]),
                }
            )

    hits.sort(key=lambda h: (-h["matched"], h["page"]))
    return jsonify({"hits": hits[:MAX_SEARCH_HITS], "total": len(hits)})


def _snippet(text, term, width=60):
    "A short window of text around the first hit, for the results list."
    index = text.lower().find(term.lower())
    if index < 0:
        return text[:width].strip()
    start = max(0, index - width // 2)
    end = min(len(text), index + len(term) + width // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"
