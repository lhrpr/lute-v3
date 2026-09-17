"""
Tests for the /term routes backing the touch term popover.

See lute/static/js/term-popover.js: the popover saves with a translation and a
status and nothing else, keyed on (langid, text) because a word that isn't a
term yet has no id.
"""

from lute.db import db
from lute.models.term import Status
from lute.term.model import Repository


def test_popover_info_unknown_word_returns_nulls(client, spanish):
    "A word that isn't a term yet has nothing to report."
    langid = spanish.id
    resp = client.get(f"/term/popover_info/{langid}/nonexistentword")
    assert resp.status_code == 200
    assert resp.json == {"term_id": None, "translation": None, "status": None}


def test_quick_save_creates_term(client, spanish):
    "Saving a brand-new word creates it with the translation and status."
    langid = spanish.id
    resp = client.post(
        "/term/quick_save",
        json={
            "langid": langid,
            "text": "gato",
            "translation": "cat",
            "status": 3,
        },
    )
    assert resp.status_code == 200
    assert resp.json["status"] == 3
    assert resp.json["translation"] == "cat"
    assert resp.json["term_id"] is not None

    term = Repository(db.session).find(langid, "gato")
    assert term.translation == "cat"
    assert term.status == 3


def test_quick_save_then_popover_info_round_trip(client, spanish):
    "What was saved is what the popover reads back on the next tap."
    langid = spanish.id
    client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "perro", "translation": "dog", "status": 2},
    )
    resp = client.get(f"/term/popover_info/{langid}/perro")
    assert resp.json["translation"] == "dog"
    assert resp.json["status"] == 2


def test_quick_save_empty_translation_keeps_existing(client, spanish):
    """
    Status-only saves must not wipe a translation.

    Tapping Well Known or Ignore sends an empty translation, and that should
    change the status only -- otherwise marking a word known would silently
    discard the meaning the user saved earlier.
    """
    langid = spanish.id
    client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "casa", "translation": "house", "status": 1},
    )
    resp = client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "casa", "translation": "", "status": 5},
    )
    assert resp.json["status"] == 5
    assert resp.json["translation"] == "house"


def test_quick_save_updates_translation_when_given(client, spanish):
    "A new meaning replaces the old one."
    langid = spanish.id
    client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "banco", "translation": "bench", "status": 1},
    )
    resp = client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "banco", "translation": "bank", "status": 1},
    )
    assert resp.json["translation"] == "bank"


def test_quick_save_accepts_well_known_and_ignored(client, spanish):
    "The two terminal statuses save straight from the status row."
    langid = spanish.id
    for text, status in [("libro", Status.WELLKNOWN), ("mesa", Status.IGNORED)]:
        resp = client.post(
            "/term/quick_save",
            json={"langid": langid, "text": text, "status": status},
        )
        assert resp.status_code == 200, resp.json
        assert resp.json["status"] == status


def test_quick_save_rejects_bad_status(client, spanish):
    "Statuses outside the allowed set are refused rather than stored."
    langid = spanish.id
    resp = client.post(
        "/term/quick_save",
        json={"langid": langid, "text": "silla", "status": 42},
    )
    assert resp.status_code == 400
    assert "Invalid status" in resp.json["error"]


def test_quick_save_requires_langid_and_text(client, spanish):
    "Both are needed to identify the term."
    langid = spanish.id
    assert client.post("/term/quick_save", json={"text": "hola"}).status_code == 400
    assert (
        client.post(
            "/term/quick_save", json={"langid": langid, "text": "   "}
        ).status_code
        == 400
    )
