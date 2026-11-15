"""
The read-only vocab senses shown for linked terms.
"""

import json

from sqlalchemy import text as sqltext

from lute.db import db
from lute.vocablink.shadow import senses_json, vocab_senses


def test_only_entries_with_several_senses_are_kept():
    one = {"senses": [{"cue": "la casa", "translation": "house"}]}
    assert senses_json(one) == ""
    two = {
        "senses": [
            {"cue": "el capital", "pos": None, "translation": "money", "status": "3", "id": "x"},
            {"cue": "la capital", "pos": "noun", "translation": "city", "status": None, "id": "y"},
        ]
    }
    kept = json.loads(senses_json(two))
    assert [s["cue"] for s in kept] == ["el capital", "la capital"]
    assert "id" not in kept[0]


def test_shown_for_linked_terms(app_context, empty_db, spanish):
    "Stored senses come back for a linked term only."
    db.session.execute(
        sqltext(
            "INSERT INTO words (WoLgID, WoText, WoTextLC, WoStatus) "
            "VALUES (:lg, 'capital', 'capital', 1)"
        ),
        {"lg": spanish.id},
    )
    woid = db.session.execute(sqltext("SELECT WoID FROM words")).scalar()
    senses = senses_json(
        {"senses": [{"cue": "el capital"}, {"cue": "la capital", "translation": "city"}]}
    )
    db.session.execute(
        sqltext(
            "INSERT INTO vocablink (VlWoID, VlVocabID, VlVocabModified, VlBase, VlSenses) "
            "VALUES (:w, 'v1', 0, '{}', :s)"
        ),
        {"w": woid, "s": senses},
    )
    assert [s["cue"] for s in vocab_senses(db.session, woid)] == ["el capital", "la capital"]
    db.session.execute(sqltext("UPDATE vocablink SET VlState = 'deleted'"))
    assert vocab_senses(db.session, woid) == []
    assert vocab_senses(db.session, None) == []
