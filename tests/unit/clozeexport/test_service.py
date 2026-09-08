"""
End-to-end generation, from books to CSV.
"""

import csv

from lute.cli.cloze_export import generate_cloze_file
from lute.clozeexport.service import CSV_HEADINGS, Service, write_csv
from lute.db import db
from lute.models.term import Term
from tests.dbasserts import assert_sql_result
from tests.utils import make_book


CONTENT = (
    "Last night I watched a comedy film with my children. I laughed a lot.\n"
    "The dog barked loudly at the postman this morning."
)


def _setup(english, statuses):
    "A one-book library where everything is known bar the given statuses."
    book = make_book("Diary", CONTENT, english)
    db.session.add(book)
    words = {
        english.get_lowercase(t.token)
        for t in english.get_parsed_tokens(CONTENT)
        if t.is_word
    }
    for word in sorted(words):
        status = statuses.get(word, 99)
        if status == 0:
            continue
        term = Term(english, word)
        term.status = status
        db.session.add(term)
    db.session.commit()
    return book


def test_generates_one_card_per_studied_term(empty_db, english):
    "Only terms with a studied status get cards, one each."
    book = _setup(english, {"laughed": 2, "barked": 1})
    cards = Service(db.session).generate([book])
    by_term = {c.term.text_lc: c for c in cards}
    assert sorted(by_term) == ["barked", "laughed"]
    assert by_term["laughed"].span.text == (
        "Last night I watched a comedy film with my children. I laughed a lot."
    )
    assert by_term["barked"].span.text == (
        "The dog barked loudly at the postman this morning."
    )


def test_cards_do_not_cross_a_paragraph_break(empty_db, english):
    "The second paragraph is a different thought; it never gets pulled in."
    book = _setup(english, {"barked": 1})
    card = Service(db.session).generate([book])[0]
    assert "children" not in card.span.text


def test_no_card_when_the_reader_could_not_read_it(empty_db, english):
    "An unknown word alongside the target means no card at all."
    book = _setup(english, {"laughed": 2, "lot": 0})
    cards = Service(db.session).generate([book])
    assert [c.term.text_lc for c in cards] == []


def test_an_unknown_word_nearby_only_blocks_expansion(empty_db, english):
    "The target's own sentence is still usable; the context just isn't."
    book = _setup(english, {"laughed": 2, "comedy": 0})
    card = Service(db.session).generate([book])[0]
    assert card.span.text == "I laughed a lot."


def test_max_per_term(empty_db, english):
    "More than one card per term can be kept."
    content = "The dog barked at me. Then the dog barked at the postman again."
    book = make_book("Diary", content, english)
    db.session.add(book)
    for word in ["the", "dog", "at", "me", "then", "postman", "again"]:
        term = Term(english, word)
        term.status = 99
        db.session.add(term)
    barked = Term(english, "barked")
    barked.status = 1
    db.session.add(barked)
    db.session.commit()

    service = Service(db.session)
    assert len(service.generate([book], max_per_term=1)) == 1
    assert len(service.generate([book], max_per_term=3)) == 2, "only 2 occurrences"


def test_group_by_family(empty_db, english):
    "Cards can be deduplicated to one per word family."
    content = "The dog barked at me. The other dog barks at the postman."
    book = make_book("Diary", content, english)
    db.session.add(book)
    for word in ["the", "dog", "at", "me", "other", "postman"]:
        term = Term(english, word)
        term.status = 99
        db.session.add(term)
    parent = Term(english, "bark")
    parent.status = 1
    db.session.add(parent)
    for word in ["barked", "barks"]:
        child = Term(english, word)
        child.status = 1
        child.add_parent(parent)
        db.session.add(child)
    db.session.commit()

    service = Service(db.session)
    assert len(service.generate([book], group_by_family=False)) == 2
    assert len(service.generate([book], group_by_family=True)) == 1


def test_export_writes_nothing_to_the_database(empty_db, english, tmp_path):
    "Rendering invents status-0 terms for unknown words; none may be saved."
    _setup(english, {"laughed": 2, "postman": 0})
    assert_sql_result(
        "select WoTextLC from words where WoStatus = 0", [], "none before"
    )
    generate_cloze_file(tmp_path / "cards.csv", language_name="English")
    assert_sql_result("select WoTextLC from words where WoStatus = 0", [], "none after")


def test_csv_output(empty_db, english, tmp_path):
    "The CSV carries both a cloze-marked and a plain version of the text."
    book = _setup(english, {"laughed": 2})
    cards = Service(db.session).generate([book])
    outfile = tmp_path / "cards.csv"
    write_csv(cards, outfile)

    with open(outfile, "r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0].keys()) == CSV_HEADINGS
    assert len(rows) == 1
    row = rows[0]
    assert row["term"] == "laughed"
    assert row["book"] == "Diary"
    assert row["language"] == "English"
    assert row["page"] == "1"
    assert row["source"] == "heuristic"
    assert row["cloze"].endswith("I {{c1::laughed}} a lot.")
    assert row["sentence"].endswith("I laughed a lot.")


def test_cli_reports_an_unknown_language(empty_db, tmp_path):
    "A typo'd language name fails loudly rather than exporting nothing."
    try:
        generate_cloze_file(tmp_path / "cards.csv", language_name="Klingon")
        assert False, "should have raised"
    except ValueError as ex:
        assert "Klingon" in str(ex)
