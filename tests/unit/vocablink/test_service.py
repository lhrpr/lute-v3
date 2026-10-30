"""
Vocab link runs, against an in-memory stand-in for the vocab server.

The real server's behaviour is tested in anki-alternative
(crates/sync-server/tests/ext_test.rs); FakeVocab copies only what the link
relies on: matching on (language, lowercased term), fields left out are left
alone, expect_modified_ms conflicts, sentences not added twice, and a change
feed.  modified_ms doubles as the server's sequence number.
"""

import copy
import json
from datetime import datetime

from sqlalchemy import text as sqltext

from lute.db import db
from lute.vocablink.config import VocabConfig
from lute.vocablink.service import Service
from tests.utils import add_terms, make_book


class FakeVocab:
    "Just enough of /v1/ext."

    def __init__(self):
        self.words = {}
        self.clock = 4_000_000_000_000  # later than any Lute timestamp in these tests
        self.upserts = []
        self.graves = []  # (id, sequence number)
        self.next_id = 0

    def _key(self, language, term):
        return (language, term.strip().lower())

    def _tick(self):
        self.clock += 1
        return self.clock

    def add(self, language, term, **fields):
        "A word that already exists in vocab."
        self.next_id += 1
        word = {"id": f"v{self.next_id}", "language": language, "term": term,
                "reading": "", "translation": "", "status": None, "contexts": [],
                "modified_ms": self._tick()}
        word.update(fields)
        self.words[self._key(language, term)] = word
        return word

    def edit(self, term, language="Spanish", **fields):
        "An edit or review made on a device."
        word = self.get(term, language)
        word.update(fields)
        word["modified_ms"] = self._tick()

    def delete(self, term, language="Spanish"):
        word = self.words.pop(self._key(language, term))
        self.graves.append((word["id"], self._tick()))

    def changes(self, since):
        return {
            "usn": self.clock,
            "words": [copy.deepcopy(w) for w in self.words.values() if w["modified_ms"] > since],
            "deleted": [i for i, usn in self.graves if usn > since],
        }

    def lookup(self, keys):
        return [copy.deepcopy(self.words.get(self._key(k["language"], k["term"]))) for k in keys]

    def upsert(self, items):
        self.upserts.append(copy.deepcopy(items))
        results = []
        for item in items:
            key = self._key(item["language"], item["term"])
            word = self.words.get(key)
            outcome = "unchanged"
            if word is None:
                word = self.add(item["language"], item["term"], deck=item.get("deck", ""))
                outcome = "created"
            elif "expect_modified_ms" in item and item["expect_modified_ms"] != word["modified_ms"]:
                results.append({"outcome": "conflict", "word": copy.deepcopy(word)})
                continue
            for f in ("reading", "translation", "status"):
                if f in item and item[f] != word[f]:
                    word[f] = item[f]
                    word["modified_ms"] = self._tick()
                    outcome = "updated" if outcome == "unchanged" else outcome
            ctx = item.get("context")
            if ctx:
                entry = (ctx["sentence"], ctx["source"]["external_ref"], ctx["source"]["title"], ctx["location"])
                if entry not in word["contexts"]:
                    word["contexts"].append(entry)
                    outcome = "updated" if outcome == "unchanged" else outcome
            results.append({"outcome": outcome, "word": copy.deepcopy(word)})
        return results

    def get(self, term, language="Spanish"):
        return self.words.get(self._key(language, term))


CONFIG = VocabConfig(url="http://vocab.test", token="t", languages={"Spanish": "Spanish"})


def run(fake, config=CONFIG):
    return Service(db.session, config, fake).run()


def set_term(term, **attrs):
    for k, v in attrs.items():
        setattr(term, k, v)
    db.session.add(term)
    db.session.commit()


def links():
    rows = db.session.execute(sqltext("SELECT VlWoID, VlVocabID, VlBase, VlState FROM vocablink")).fetchall()
    return {woid: (vid, json.loads(base), state) for woid, vid, base, state in rows}


def test_learning_terms_are_created_and_others_are_not(empty_db, spanish):
    gato, perro, casa, sol = add_terms(spanish, ["gato", "perro", "casa", "sol"])
    set_term(gato, status=1, translation="cat")
    set_term(perro, status=5)
    set_term(casa, status=99)
    set_term(sol, status=0)

    fake = FakeVocab()
    summary = run(fake, VocabConfig(**{**CONFIG.__dict__, "deck": "Lute::{language}"}))
    assert summary.created == 2
    assert fake.get("gato")["translation"] == "cat"
    assert fake.get("gato")["status"] == "1"
    assert fake.get("gato")["deck"] == "Lute::Spanish"
    assert fake.get("perro")["status"] == "5"
    assert fake.get("casa") is None and fake.get("sol") is None
    assert set(links()) == {gato.id, perro.id}

    # Nothing changed: nothing sent.
    assert run(fake).created == 0
    assert len(fake.upserts) == 1


def test_unlinked_languages_are_left_alone(empty_db, spanish, english):
    [hello] = add_terms(english, ["hello"])
    set_term(hello, status=2)
    fake = FakeVocab()
    run(fake)
    assert fake.words == {}


def test_status_changes_reach_vocab_including_known(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=2)
    fake = FakeVocab()
    run(fake)

    set_term(gato, status=99)
    assert run(fake).updated == 1
    assert fake.get("gato")["status"] == "K"


def test_existing_vocab_words_are_linked_without_clobbering(empty_db, spanish):
    [manzana, pera] = add_terms(spanish, ["manzana", "pera"])
    set_term(manzana, status=1, translation="apple (lute)")
    set_term(pera, status=3, translation="pear")
    fake = FakeVocab()
    # Reviewed in vocab after Lute's status was set: vocab's status is newer.
    fake.add("Spanish", "Manzana", translation="apple", status="4")
    fake.add("Spanish", "pera", translation="", status="3")

    summary = run(fake)
    assert fake.get("manzana")["translation"] == "apple", "vocab's text wins"
    assert fake.get("manzana")["status"] == "4", "the later status wins"
    assert fake.get("pera")["translation"] == "pear", "empty in vocab: Lute's is sent"
    # Both are linked by the pull; manzana takes vocab's values, pera sends its translation.
    assert (summary.created, summary.lute_updated, summary.updated) == (0, 1, 1)

    # Vocab's values are brought into Lute.
    db.session.refresh(manzana)
    assert (manzana.translation, manzana.status) == ("apple", 4)
    before = len(fake.upserts)
    run(fake)
    assert len(fake.upserts) == before


def test_vocab_edits_since_the_lookup_conflict_and_are_retried(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=2)
    fake = FakeVocab()
    run(fake)
    set_term(gato, status=3)

    real_lookup = fake.lookup

    def lookup_then_review(keys):
        found = real_lookup(keys)
        fake.get("gato")["modified_ms"] = fake._tick()  # pylint: disable=protected-access
        return found

    fake.lookup = lookup_then_review
    assert run(fake).conflicts == 1
    fake.lookup = real_lookup
    assert run(fake).updated == 1
    assert fake.get("gato")["status"] == "3"


def test_words_deleted_in_vocab_are_not_recreated(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=2)
    fake = FakeVocab()
    run(fake)
    fake.delete("gato")
    set_term(gato, status=3)

    assert run(fake).deleted == 1
    assert links()[gato.id][2] == "deleted"
    set_term(gato, status=4)
    run(fake)
    assert fake.get("gato") is None


def read_book(spanish, title, pages):
    book = make_book(title, pages, spanish)
    db.session.add(book)
    db.session.commit()
    for t in book.texts:
        t.read_date = datetime.now()
        db.session.add(t)
    db.session.commit()
    return book


def test_read_pages_send_one_sentence_per_term_per_book(empty_db, spanish):
    gato, perro, de_nuevo = add_terms(spanish, ["gato", "perro", "de nuevo"])
    set_term(gato, status=1)
    set_term(perro, status=99)
    set_term(de_nuevo, status=2)
    read_book(spanish, "Cuentos", ["El gato duerme. Otro gato.", "El perro ve al gato de nuevo."])
    fake = FakeVocab()

    summary = run(fake)
    assert summary.pages == 2
    uuid = db.session.execute(sqltext("SELECT BkUUID FROM books WHERE BkTitle = 'Cuentos'")).scalar()
    assert fake.get("gato")["contexts"] == [("El gato duerme.", f"lute:{uuid}", "Cuentos", "p. 1")]
    assert fake.get("de nuevo")["contexts"] == [("El perro ve al gato de nuevo.", f"lute:{uuid}", "Cuentos", "p. 2")]
    assert fake.get("perro") is None, "known words aren't learning"

    # Pages are handled once; a second book gives a second sentence.
    run(fake)
    assert len(fake.get("gato")["contexts"]) == 1
    read_book(spanish, "Más cuentos", ["Un gato negro."])
    assert run(fake).sentences == 1
    assert len(fake.get("gato")["contexts"]) == 2


def test_unread_pages_send_nothing(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=1)
    book = make_book("Sin leer", ["El gato."], spanish)
    db.session.add(book)
    db.session.commit()
    fake = FakeVocab()
    assert run(fake).pages == 0
    assert fake.get("gato")["contexts"] == []


def lute_term(text, language_name="Spanish"):
    "(WoText without zero-width spaces, WoStatus, WoTranslation, WoTokenCount), or None."
    row = db.session.execute(
        sqltext(
            "SELECT WoText, WoStatus, WoTranslation, WoTokenCount FROM words "
            "JOIN languages ON LgID = WoLgID WHERE LgName = :l AND WoTextLC = :t"
        ),
        {"l": language_name, "t": text.lower().replace(" ", "\u200b \u200b")},
    ).fetchone()
    return None if row is None else (row[0].replace("\u200b", ""), row[1], row[2], row[3])


def test_vocab_learning_words_become_lute_terms(empty_db, spanish):
    fake = FakeVocab()
    fake.add("Spanish", "Perro", translation="dog", status="3")
    fake.add("Spanish", "la manzana", translation="apple", status="1")
    fake.add("Spanish", "nuevo", translation="new")  # unrated
    fake.add("Spanish", "sol", translation="sun", status="K")
    fake.add("German", "Hund", translation="dog", status="2")

    summary = run(fake)
    assert summary.lute_created == 2
    assert lute_term("perro") == ("Perro", 3, "dog", 1)
    assert lute_term("la manzana") == ("la manzana", 1, "apple", 3), "multiword terms are parsed"
    assert lute_term("nuevo") is None and lute_term("sol") is None
    assert len(links()) == 2

    # Nothing new: nothing changes on either side.
    before = len(fake.upserts)
    summary = run(fake)
    assert summary.lute_created == summary.lute_updated == 0
    assert len(fake.upserts) == before


def test_reviews_and_edits_in_vocab_reach_lute(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=1, translation="cat")
    fake = FakeVocab()
    run(fake)

    fake.edit("gato", status="4", translation="cat (feline)")
    assert run(fake).lute_updated == 1
    assert lute_term("gato")[1:3] == (4, "cat (feline)")
    # Reviewed to known in vocab, then to unrated.
    fake.edit("gato", status="K")
    run(fake)
    assert lute_term("gato")[1] == 99
    fake.edit("gato", status=None)
    run(fake)
    assert lute_term("gato")[1] == 0


def test_when_both_change_status_goes_to_the_later_and_text_to_vocab(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=1, translation="cat")
    fake = FakeVocab()
    run(fake)

    # Vocab's edit is the earlier one: date it before Lute's, and rewind the
    # pull cursor so the backdated word is still in the next change feed.
    fake.edit("gato", translation="kitty", status="2")
    fake.get("gato")["modified_ms"] = 1_000
    set_term(gato, status=3, translation="cat!")
    db.session.execute(sqltext("UPDATE vocablinkstate SET VkValue = '0' WHERE VkKey = 'pull_usn'"))
    db.session.commit()
    run(fake)
    assert lute_term("gato")[1:3] == (3, "kitty")
    assert fake.get("gato")["status"] == "3"
    assert fake.get("gato")["translation"] == "kitty"


def test_terms_deleted_in_lute_are_not_recreated(empty_db, spanish):
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=1)
    fake = FakeVocab()
    run(fake)
    db.session.delete(gato)
    db.session.commit()

    fake.edit("gato", status="2")
    run(fake)
    assert lute_term("gato") is None

    # Added again in Lute: linked again, and pulled from then on.
    [gato] = add_terms(spanish, ["gato"])
    set_term(gato, status=1)
    run(fake)
    assert gato.id in links()
    fake.edit("gato", status="5")
    run(fake)
    assert lute_term("gato")[1] == 5


def test_vocab_values_too_long_for_lute_are_not_pulled(empty_db, spanish):
    fake = FakeVocab()
    fake.add("Spanish", "gato", translation="x" * 501, reading="ga-to", status="2")
    run(fake)
    assert lute_term("gato")[1:3] == (2, None)
    assert fake.get("gato")["translation"] == "x" * 501, "and not overwritten either"


def test_linking_to_an_unrated_vocab_word_settles_in_one_run(empty_db, spanish):
    [tragar] = add_terms(spanish, ["tragar"])
    set_term(tragar, status=3, translation="Swallow; stand")
    fake = FakeVocab()
    fake.add("Spanish", "tragar", translation="to swallow")  # unrated, edited later than Lute

    summary = run(fake)
    assert fake.get("tragar")["status"] == "3", "unrated never wins"
    assert lute_term("tragar")[1:3] == (3, "to swallow"), "vocab's text wins, now"
    assert summary.lute_updated == 1

    summary = run(fake)
    assert (summary.lute_updated, summary.updated) == (0, 0)
