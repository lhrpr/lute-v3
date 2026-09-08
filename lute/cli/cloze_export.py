"""
CLI plumbing for the cloze card export.

Kept separate from lute.clozeexport so the generation pipeline stays free of
click/CLI concerns and can be driven from tests or, later, a web route.
"""

from lute.clozeexport.options import ClozeOptions
from lute.clozeexport.service import Service, write_csv
from lute.db import db
from lute.models.book import Book
from lute.models.repositories import LanguageRepository


def _int_list(value, fallback):
    "Parse a comma-separated list of ints, e.g. '1,2,3'."
    if value is None or str(value).strip() == "":
        return fallback
    return tuple(int(p.strip()) for p in str(value).split(",") if p.strip() != "")


def _word_set(value):
    "Parse a comma-separated word list into a lowercased set."
    if value is None or value.strip() == "":
        return set()
    return {p.strip().lower() for p in value.split(",") if p.strip() != ""}


def find_books(session, language_name=None, book_ids=None):
    """
    Books to scan.  Raises ValueError if the selection matches nothing.

    A language name and explicit book ids can be combined, which is how you
    export from a subset of a language's library.
    """
    query = session.query(Book)
    if language_name:
        repo = LanguageRepository(session)
        language = repo.find_by_name(language_name)
        if language is None:
            raise ValueError(f'No language named "{language_name}".')
        query = query.filter(Book.language == language)
    if book_ids:
        query = query.filter(Book.id.in_(list(book_ids)))
    books = query.all()
    if len(books) == 0:
        raise ValueError("No books matched.")
    return books


def build_options(**kwargs):
    "Build ClozeOptions from raw CLI values."
    opts = ClozeOptions()
    opts.target_statuses = _int_list(kwargs.get("status"), opts.target_statuses)
    opts.learning_statuses = _int_list(
        kwargs.get("learning_statuses"), opts.learning_statuses
    )
    opts.known_statuses = _int_list(kwargs.get("known_statuses"), opts.known_statuses)
    for name in (
        "max_unknown",
        "max_learning",
        "min_words",
        "max_words",
        "long_sentence_words",
        "context_sentences",
        "context_clauses",
    ):
        value = kwargs.get(name)
        if value is not None:
            setattr(opts, name, value)
    opts.anaphora_words = _word_set(kwargs.get("anaphora"))
    opts.conjunction_words = _word_set(kwargs.get("conjunctions"))
    if kwargs.get("cloze_prefix"):
        opts.cloze_prefix = kwargs["cloze_prefix"]
    if kwargs.get("cloze_suffix"):
        opts.cloze_suffix = kwargs["cloze_suffix"]
    return opts


# pylint: disable=too-many-arguments,too-many-positional-arguments
def generate_cloze_file(
    output_path,
    language_name=None,
    book_ids=None,
    use_llm=False,
    max_per_term=1,
    group_by_family=False,
    limit=None,
    **option_kwargs,
):
    """
    Generate cloze cards and write them to output_path.  Returns the cards.

    The session is rolled back at the end: rendering manufactures unsaved
    status-0 Terms for unknown words, and an export has no business leaving
    anything behind.
    """
    books = find_books(db.session, language_name, book_ids)
    opts = build_options(**option_kwargs)
    service = Service(db.session)
    try:
        cards = service.generate(
            books,
            opts,
            use_llm=use_llm,
            max_per_term=max_per_term,
            group_by_family=group_by_family,
            verbose=True,
        )
        if limit is not None and limit > 0:
            cards = cards[:limit]
        write_csv(cards, output_path, opts)
        reviewed = len([c for c in cards if c.source == "llm"])
        clause_cut = len([c for c in cards if c.span.mode == "clause"])
        print(
            f"Wrote {len(cards)} cards to {output_path} "
            f"({clause_cut} cut to clauses, {reviewed} re-cut by the LLM)."
        )
        return cards
    finally:
        db.session.rollback()
