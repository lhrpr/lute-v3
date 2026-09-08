"""
Simple CLI commands.
"""

import click
from flask import Blueprint

from lute.cli.cloze_export import generate_cloze_file
from lute.cli.language_term_export import generate_language_file, generate_book_file
from lute.cli.import_books import import_books_from_csv

bp = Blueprint("cli", __name__)


@bp.cli.command("hello")
def hello():
    "Say hello -- proof-of-concept CLI command only."
    msg = """
    Hello there!

    This is the Lute cli.

    There may be some experimental scripts here ...
    nothing that will change or damage your Lute data,
    but the CLI may change.

    Thanks for looking.
    """
    print(msg)


@bp.cli.command("language_export")
@click.argument("language")
@click.argument("output_path")
def language_export(language, output_path):
    """
    Get all terms from all books in the language, and write a
    data file of term frequencies and children.
    """
    generate_language_file(language, output_path)


@bp.cli.command("book_term_export")
@click.argument("bookid")
@click.argument("output_path")
def book_term_export(bookid, output_path):
    """
    Get all terms for the given book, and write a
    data file of term frequencies and children.
    """
    generate_book_file(bookid, output_path)


@bp.cli.command("import_books_from_csv")
@click.option(
    "--commit",
    is_flag=True,
    help="""
    Commit the changes to the database. If not set, import in dry-run mode. A
    list of changes will be printed out but not applied.
""",
)
@click.option(
    "--tags",
    default="",
    help="""
    A comma-separated list of tags to apply to all books.
""",
)
@click.option(
    "--language",
    default="",
    help="""
    The name of the default language to apply to each book, as it appears in
    your language settings. If unset, the language must be indicated in the
    "language" column of the CSV file.
""",
)
@click.argument("file")
def import_books_from_csv_cmd(language, file, tags, commit):
    """
    Import books from a CSV file.

    The CSV file must have a header row with the following, case-sensitive,
    column names. The order of the columns does not matter. The CSV file may
    include additional columns, which will be ignored.

      - title: the title of the book

      - text: the text of the book

      - language: [optional] the name of the language of book, as it appears in
      your language settings. If unspecified, the language specified on the
      command line (using the --language option) will be used.

      - url: [optional] the source URL for the book

      - tags: [optional] a comma-separated list of tags to apply to the book
      (e.g., "audiobook,beginner")

      - audio: [optional] the path to the audio file of the book. This should
      either be an absolute path, or a path relative to the CSV file.

      - bookmarks: [optional] a semicolon-separated list of audio bookmark
      positions, in seconds (decimals permitted; e.g., "12.34;42.89;89.00").
    """
    tags = list(tags.split(",")) if tags else []
    import_books_from_csv(file, language, tags, commit)


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
@bp.cli.command("cloze_export")
@click.option("--language", default=None, help="Language name, e.g. 'Spanish'.")
@click.option(
    "--book",
    "book_ids",
    multiple=True,
    type=int,
    help="Book id to export from.  Repeatable.  Defaults to every book.",
)
@click.option(
    "--status",
    default="1,2,3",
    help="Statuses of the terms to make cards for (comma-separated).",
)
@click.option(
    "--max-learning",
    default=1,
    show_default=True,
    help="Most 'learning' words allowed on a card, besides the target.",
)
@click.option(
    "--max-unknown",
    default=0,
    show_default=True,
    help="Most unknown (status 0) words allowed on a card.",
)
@click.option("--min-words", default=10, show_default=True, help="Comfortable minimum.")
@click.option("--max-words", default=20, show_default=True, help="Comfortable maximum.")
@click.option(
    "--long-sentence",
    "long_sentence_words",
    default=25,
    show_default=True,
    help="Sentences longer than this may be cut down to clauses.",
)
@click.option(
    "--context",
    "context_sentences",
    default=2,
    show_default=True,
    help="Neighbouring sentences that may be pulled in for context.",
)
@click.option(
    "--max-per-term",
    default=1,
    show_default=True,
    help="Cards to keep per term.",
)
@click.option(
    "--family",
    is_flag=True,
    help="One card per word family rather than per term (uses the parent term).",
)
@click.option("--limit", default=None, type=int, help="Cap the number of cards.")
@click.option(
    "--use-llm",
    is_flag=True,
    help="""
    Ask the configured AI provider to re-cut the minority of cards the
    heuristics aren't confident about (long sentences cut to clauses, and
    borderline spans).  Requires AI to be enabled in Settings.  The model
    chooses boundaries only -- it never writes card text.
""",
)
@click.option(
    "--anaphora",
    default=None,
    help="""
    Comma-separated words that, at the start of a sentence, mean it depends on
    the previous one ("but", "he", "then").  Overrides the built-in list for
    the language.
""",
)
@click.option(
    "--conjunctions",
    default=None,
    help="""
    Comma-separated words to treat as clause boundaries when cutting a long
    sentence down.  Overrides the built-in list for the language.
""",
)
@click.argument("output_path")
def cloze_export(output_path, language, book_ids, family, use_llm, limit, **kwargs):
    """
    Export cloze (fill-in-the-blank) cards from your books to a CSV.

    Picks, for each term you're studying, a span of text that you can actually
    read: no unknown words, at most one other word you're still learning.  Very
    short sentences pull in their neighbours for context; very long ones are cut
    down to the clauses around the target.  The word counts are a comfortable
    band, not a hard rule -- a natural, complete thought wins over hitting the
    range exactly.

    The 'cloze' column holds Anki-style {{c1::...}} deletions; 'sentence' holds
    the same text unmarked.

    Nothing is written to your database.
    """
    generate_cloze_file(
        output_path,
        language_name=language,
        book_ids=list(book_ids),
        use_llm=use_llm,
        max_per_term=kwargs.pop("max_per_term"),
        group_by_family=family,
        limit=limit,
        **kwargs,
    )
