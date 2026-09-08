"""
Cloze card generation: the pipeline that ties the pieces together.

Ordering is the whole point -- cheap, certain work first, expensive uncertain
work last:

1. Render each page once, which gives tokens already tagged with their term and
   status, with multi-word terms resolved (lute.read.render).
2. Split into paragraphs of sentences (units.py).
3. For every occurrence of a term worth studying, reject anything the reader
   couldn't read (coverage.py).  This throws away the large majority.
4. Score the survivors' possible spans and keep the best (scoring.py).
5. Keep only the best card(s) per term.
6. Optionally, ask an LLM to re-cut just the ones step 4 wasn't confident about
   (llm.py).

Nothing here writes to the database.  Rendering manufactures status-0 Term
objects for unknown words, so the caller runs inside no_autoflush and rolls
back afterwards -- see generate().
"""

import csv

from lute.clozeexport.lexicons import anaphora_words, conjunction_words
from lute.clozeexport.llm import ClozeRefiner, ReviewItem, is_available
from lute.clozeexport.options import ClozeOptions
from lute.clozeexport.scoring import select_span
from lute.clozeexport.units import paragraphs_of_sentences
from lute.read.render.service import Service as RenderService

zws = "\u200B"  # zero-width space

CSV_HEADINGS = [
    "term",
    "parent",
    "translation",
    "status",
    "language",
    "book",
    "page",
    "words",
    "cloze",
    "sentence",
    "mode",
    "score",
    "source",
]


class Card:
    "One generated cloze card."

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(self, term, book, page, span, target_items, needs_review):
        self.term = term
        self.book = book
        self.page = page
        self.span = span
        self.target_items = target_items
        self.needs_review = needs_review
        self.source = "heuristic"

    @property
    def cost(self):
        "Score of the chosen span (lower is better)."
        return self.span.cost

    def cloze(self, opts):
        "Span text with the target hidden."
        return self.span.cloze_text(self.target_items, opts)

    def row(self, opts):
        "CSV row for this card."
        parents = sorted(p.text.replace(zws, "") for p in self.term.parents)
        return [
            self.term.text.replace(zws, ""),
            "; ".join(parents),
            self.term.translation or "",
            self.term.status,
            self.book.language.name,
            self.book.title,
            self.page,
            self.span.word_count,
            self.cloze(opts),
            self.span.text,
            self.span.mode,
            f"{self.span.cost:.1f}",
            self.source,
        ]


class Service:
    "Generates cloze cards from books."

    def __init__(self, session):
        self.session = session
        self.render_service = RenderService(session)

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def generate(
        self,
        books,
        opts=None,
        use_llm=False,
        max_per_term=1,
        group_by_family=False,
        verbose=False,
    ):
        """
        Return the cards for the given books, best first.

        Rendering creates unsaved status-0 Terms for unknown words (that's how
        the reading screen works too), so this runs with autoflush off and the
        caller should roll back afterwards.  Export is read-only; nothing here
        is meant to survive.
        """
        opts = opts or ClozeOptions()
        by_key = {}
        with self.session.no_autoflush:
            for book in books:
                if verbose:
                    print(f"Scanning {book.title} ...")
                self._scan_book(
                    book, opts, max_per_term, group_by_family, by_key, verbose
                )

        cards = [c for cards in by_key.values() for c in cards]

        if use_llm:
            self._refine(cards, opts, verbose)

        cards.sort(key=lambda c: (c.cost, c.term.text_lc))
        return cards

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _scan_book(self, book, opts, max_per_term, group_by_family, by_key, verbose):
        "Walk a book's pages, collecting candidate cards."
        book_opts = self._language_options(opts, book.language)
        indexer = self.render_service.get_multiword_indexer(book.language)
        for i, text in enumerate(book.texts, start=1):
            if verbose and i % 10 == 0:
                print(f"  page {i} of {book.page_count}", end="\r")
            textitems = self.render_service.get_textitems(
                text.text, book.language, indexer
            )
            for paragraph in paragraphs_of_sentences(textitems):
                self._scan_paragraph(
                    paragraph,
                    book,
                    text.order,
                    book_opts,
                    max_per_term,
                    group_by_family,
                    by_key,
                )
        if verbose:
            print(f"  {book.page_count} pages done.")

    @staticmethod
    def _language_options(opts, language):
        """
        Fill in the language's word lists, unless the caller supplied their own.

        Done per book rather than per run so a mixed-language export still gets
        the right hints for each language.
        """
        if len(opts.anaphora_words) > 0 and len(opts.conjunction_words) > 0:
            return opts
        book_opts = ClozeOptions(**vars(opts))
        if len(book_opts.anaphora_words) == 0:
            book_opts.anaphora_words = anaphora_words(language.name)
        if len(book_opts.conjunction_words) == 0:
            book_opts.conjunction_words = conjunction_words(language.name)
        return book_opts

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _scan_paragraph(
        self, sentences, book, page, opts, max_per_term, group_by_family, by_key
    ):
        "Build candidate cards for every studiable term in a paragraph."
        for sentence_index, sentence in enumerate(sentences):
            for item in sentence.word_items:
                term = item.term
                if term is None or term.id is None:
                    continue
                if term.status not in opts.target_statuses:
                    continue
                card = self._make_card(
                    sentences, sentence_index, item, term, book, page, opts
                )
                if card is not None:
                    key = self._key(term, group_by_family)
                    self._keep(by_key, key, card, max_per_term)

    @staticmethod
    def _key(term, group_by_family):
        """
        De-duplication key: one set of cards per term, or per word family.

        Grouping by family avoids drilling "went", "goes" and "going" as three
        separate cards.  Only applied when the term has exactly one parent --
        with several, there's no single family it belongs to.
        """
        if group_by_family and len(term.parents) == 1:
            return term.parents[0].text_lc
        return term.text_lc

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _make_card(self, sentences, sentence_index, item, term, book, page, opts):
        "Pick a span for one occurrence, or None if no readable span exists."
        exclude = {term.id}
        span, needs_review = select_span(
            sentences, sentence_index, [item], exclude, opts
        )
        if span is None:
            return None
        # Hide *every* occurrence of the term in the span: leaving a second one
        # visible would give the answer away.
        target_items = [
            ti
            for ti in span.items
            if ti.is_word and ti.term is not None and ti.term.id in exclude
        ]
        return Card(term, book, page, span, target_items, needs_review)

    @staticmethod
    def _keep(by_key, key, card, max_per_term):
        "Keep the best max_per_term cards for a term."
        cards = by_key.setdefault(key, [])
        cards.append(card)
        cards.sort(key=lambda c: c.cost)
        del cards[max_per_term:]

    def _refine(self, cards, opts, verbose):
        "Send the low-confidence cards to the LLM and take what it improves."
        review = [c for c in cards if c.needs_review]
        if len(review) == 0:
            if verbose:
                print("No cards needed an LLM opinion.")
            return
        if not is_available(self.session):
            print(
                f"Skipping LLM refinement of {len(review)} card(s): "
                "AI is not enabled/configured in Settings."
            )
            return

        if verbose:
            print(f"Asking the LLM about {len(review)} of {len(cards)} cards ...")
        refiner = ClozeRefiner(self.session, opts)
        items = [
            ReviewItem(
                id(card),
                card.span,
                card.target_items,
                {card.term.id},
                card.book.language.name,
            )
            for card in review
        ]
        spans = refiner.refine(items)
        for card in review:
            span = spans.get(id(card))
            if span is None:
                continue
            card.span = span
            card.target_items = [
                ti
                for ti in span.items
                if ti.is_word and ti.term is not None and ti.term.id == card.term.id
            ]
            card.source = "llm"
        if verbose:
            print(
                f"  {refiner.calls} call(s); {refiner.refined} card(s) re-cut, "
                f"{refiner.rejected} suggestion(s) rejected."
            )


def write_csv(cards, output_path, opts=None):
    "Write cards to a CSV that Anki (or anything else) can import."
    opts = opts or ClozeOptions()
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADINGS)
        for card in cards:
            writer.writerow(card.row(opts))
