"""
Parallel reading service.

Wraps the pure alignment and mapping logic with the book pair records,
and handles orientation: a pair is stored once, but either of its books
can be the one being read, so the anchors are inverted when the reader
opens the companion side.
"""

from lute.book.service import MIN_SECTION_TOKENS
from lute.models.book import BookPair, BookSection
from lute.models.repositories import BookPairRepository, BookRepository
from lute.parallel import mapping
from lute.parallel.alignment import (
    Section,
    align_sections,
    anchors_from_alignment,
    assign_depths,
)


class Companion:
    "The other half of a pair, oriented for the book being read."

    def __init__(self, book, anchors, reading_book):
        self.book = book
        self.anchors = anchors
        self.reading_book = reading_book

    @property
    def page_count(self):
        return self.book.page_count

    def page_for(self, page):
        "The companion page to show alongside the given page."
        return mapping.companion_page(
            page, self.anchors, self.reading_book.page_count, self.book.page_count
        )

    def anchor_state(self, page):
        """
        How the companion page for this page was arrived at.

        Lets the reader see whether they are on solid ground or drifting
        between distant anchors.
        """
        if mapping.is_anchored(page, self.anchors):
            return {"anchored": True, "before": None, "after": None}
        before, after = mapping.surrounding_anchors(page, self.anchors)
        return {"anchored": False, "before": before, "after": after}


class Service:
    "Parallel reading service."

    def __init__(self, session):
        self.session = session
        self.pair_repo = BookPairRepository(session)
        self.book_repo = BookRepository(session)

    def get_companion(self, book):
        "The Companion for a book being read, or None if it has no pair."
        pair = self.pair_repo.find_for_book(book.id)
        if pair is None:
            return None
        other_id = pair.other_book_id(book.id)
        other = self.book_repo.find(other_id)
        if other is None:
            return None

        anchors = mapping.parse_anchors(pair.page_map)
        if book.id == pair.companion_bk_id:
            # Stored anchors run primary -> companion; reading from the
            # far side means reading them backwards.
            anchors = sorted((c, p) for p, c in anchors)
        return Companion(other, anchors, book)

    def set_anchor(self, book, page, companion_page):
        "Anchor a page of the book being read to a companion page."
        pair = self.pair_repo.find_for_book(book.id)
        if pair is None:
            return None
        anchors = mapping.parse_anchors(pair.page_map)
        if book.id == pair.companion_bk_id:
            anchors = mapping.add_anchor(anchors, companion_page, page)
        else:
            anchors = mapping.add_anchor(anchors, page, companion_page)
        pair.page_map = mapping.dump_anchors(anchors)
        self.session.add(pair)
        self.session.commit()
        return pair

    def clear_anchor(self, book, page):
        "Remove the anchor on a page of the book being read."
        pair = self.pair_repo.find_for_book(book.id)
        if pair is None:
            return None
        anchors = mapping.parse_anchors(pair.page_map)
        if book.id == pair.companion_bk_id:
            anchors = sorted((c, p) for p, c in anchors)
            anchors = mapping.remove_anchor(anchors, page)
            anchors = sorted((c, p) for p, c in anchors)
        else:
            anchors = mapping.remove_anchor(anchors, page)
        pair.page_map = mapping.dump_anchors(anchors)
        self.session.add(pair)
        self.session.commit()
        return pair

    def sections_for(self, book):
        """
        The book's stored sections as alignment Sections.

        Covers, nav documents and half-titles are dropped; everything
        else is kept.  A numbered heading is enough on its own, because
        plenty of real chapters are shorter than the front matter around
        them -- a novel of dated entries can end on a chapter of nine
        words, and that chapter is still worth anchoring.

        The filtering happens here rather than at import, where dropping
        a section would drop its text from the book.
        """
        rows = (
            self.session.query(BookSection)
            .filter(BookSection.bk_id == book.id)
            .order_by(BookSection.order)
            .all()
        )
        sections = [
            Section(r.order, r.title, r.token_count, r.start_page, r.level)
            for r in rows
        ]
        kept = [
            s
            for s in sections
            if s.ordinal is not None or s.token_count >= MIN_SECTION_TOKENS
        ]
        # Rank depths over the sections actually being aligned, so that
        # discarded front matter cannot shift everything else down a level.
        return assign_depths(kept)

    def propose_alignment(self, primary, companion):
        """
        Align two books by their sections, for the reader to confirm.

        Returns the raw alignment, gaps included, so the review screen
        can show what was skipped and why.
        """
        return align_sections(
            self.sections_for(primary),
            self.sections_for(companion),
            a_page_count=primary.page_count,
            b_page_count=companion.page_count,
        )

    def pair_books(self, primary, companion, anchors=None):
        """
        Pair two books, replacing any pair either book already has.

        With no anchors given the pair still works -- the mapping falls
        back to proportional -- so pairing never blocks on alignment.
        """
        self.pair_repo.delete_for_book(primary.id)
        self.pair_repo.delete_for_book(companion.id)
        self.session.flush()

        pair = BookPair()
        pair.primary_bk_id = primary.id
        pair.companion_bk_id = companion.id
        pair.page_map = mapping.dump_anchors(anchors or [])
        self.session.add(pair)
        self.session.commit()
        return pair

    def auto_anchors(self, primary, companion):
        "Anchors proposed from the two books' section structures."
        return anchors_from_alignment(
            self.propose_alignment(primary, companion),
            primary_page_count=primary.page_count,
            companion_page_count=companion.page_count,
        )

    def unpair(self, book):
        "Remove the book's pair, if any."
        removed = self.pair_repo.delete_for_book(book.id)
        if removed:
            self.session.commit()
        return removed
