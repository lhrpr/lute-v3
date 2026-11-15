"""
Sync engine tests: capture, conflict rules, and the hub's logging.

Two devices can't be simulated in one test database, so these apply wire
changes "from a peer" directly and check what lands.  The full device -> hub ->
device round trip was checked by running three Lute servers against each other.
"""

import json

import pytest
from sqlalchemy import text

from lute.db import db
from lute.sync import engine, hub
from lute.sync.schema import WORDS

PEER = "peer-device"


def ex(sql, params=None):
    return db.session.execute(text(sql), params or {})


def _word(lgid, textlc, status=1, translation=None):
    ex(
        "insert into words (WoLgID, WoText, WoTextLC, WoStatus, WoTokenCount, WoTranslation) "
        "values (:l, :t, :t, :s, 1, :tr)",
        {"l": lgid, "t": textlc, "s": status, "tr": translation},
    )


def _row(textlc):
    return ex(
        "select WoStatus, WoTranslation, WoStatusChanged from words where WoTextLC = :t",
        {"t": textlc},
    ).first()


def _incoming(lang, textlc, status, translation, status_changed, changed_at, op="upsert"):
    row = {"lang": lang, "text_lc": textlc}
    if op == "upsert":
        row.update(
            {
                "text": textlc,
                "status": status,
                "translation": translation,
                "token_count": 1,
                "status_changed": status_changed,
                "follow_parent": 0,
            }
        )
    return {"table": "words", "op": op, "row": row, "changed_at": changed_at}


def _device_apply(changes):
    "Apply as a device: competing times are this side's unpushed edits."
    applier = engine.Applier(db.session, engine.device_local_time(db.session, 0))
    engine.begin_apply(db.session)
    result = applier.apply(changes)
    engine.end_apply(db.session)
    db.session.commit()
    return result


@pytest.fixture(name="clean_log")
def fixture_clean_log(app_context):
    "Start each test with an empty change log."
    ex("delete from synclog")
    db.session.commit()


def test_key_text_matches_trigger_key(english, clean_log):
    "The hub finds rows by exact SlKey, so Python must write what SQLite writes."
    _word(english.id, "café")
    db.session.commit()
    logged = ex("select SlKey from synclog where SlTable = 'words'").scalar()
    assert logged == engine.key_text(WORDS, {"lang": "English", "text_lc": "café"})


def test_deleting_a_term_doesnt_log_the_rest(english, clean_log):
    """
    Lute's delete trigger rewrites WoSyncStatus on every parentless term.
    Those non-changes must not be logged as edits, or they would overwrite
    real edits from other devices.
    """
    for t in ("a", "b", "c", "d"):
        _word(english.id, t)
    db.session.commit()
    ex("delete from synclog")
    ex("delete from words where WoTextLC = 'a'")
    db.session.commit()
    logged = ex("select SlKey, SlOp from synclog where SlTable = 'words'").fetchall()
    assert [json.loads(k)["text_lc"] for k, _ in logged] == ["a"]


def test_applying_doesnt_log(english, clean_log):
    "Applied changes are the peer's, not local edits: they mustn't echo back."
    _device_apply([_incoming("English", "hi", 2, "x", "2026-01-01 00:00:00", "2026-01-01 00:00:00")])
    assert _row("hi")[0] == 2
    assert ex("select count(*) from synclog").scalar() == 0
    assert engine.get_state(db.session, "applying") == "0"


def test_newer_incoming_edit_wins(english, clean_log):
    _word(english.id, "hi", translation="mine")
    db.session.commit()
    ex("update synclog set SlChangedAt = '2026-01-01 00:00:00'")
    db.session.commit()
    _device_apply([_incoming("English", "hi", 1, "theirs", "2000-01-01 00:00:00", "2026-01-02 00:00:00")])
    assert _row("hi")[1] == "theirs"


def test_newer_local_edit_wins(english, clean_log):
    _word(english.id, "hi", translation="mine")
    db.session.commit()
    ex("update synclog set SlChangedAt = '2026-01-03 00:00:00'")
    db.session.commit()
    result = _device_apply(
        [_incoming("English", "hi", 1, "theirs", "2000-01-01 00:00:00", "2026-01-02 00:00:00")]
    )
    assert _row("hi")[1] == "mine"
    assert result.skipped == 1


def test_status_is_decided_by_status_changed(english, clean_log):
    """
    A translation edit on one device and a status change on another both
    survive: the term is last-write-wins, its status goes by WoStatusChanged.
    """
    _word(english.id, "hi", status=1, translation="old")
    db.session.commit()
    # Local: status changed to 5 recently.
    ex("update words set WoStatus = 5 where WoTextLC = 'hi'")
    ex("update words set WoStatusChanged = '2026-01-05 00:00:00' where WoTextLC = 'hi'")
    ex("update synclog set SlChangedAt = '2026-01-05 00:00:00'")
    db.session.commit()
    # Peer: translation edited later, status untouched since long ago.
    _device_apply([_incoming("English", "hi", 1, "new", "2025-01-01 00:00:00", "2026-01-06 00:00:00")])
    status, translation, _ = _row("hi")
    assert (status, translation) == (5, "new")


def test_newer_status_from_losing_edit_still_applies(english, clean_log):
    "The term edit here wins, but the peer's status change is newer."
    _word(english.id, "hi", status=1, translation="mine")
    db.session.commit()
    ex("update words set WoStatusChanged = '2026-01-01 00:00:00' where WoTextLC = 'hi'")
    ex("update synclog set SlChangedAt = '2026-01-09 00:00:00'")
    db.session.commit()
    _device_apply([_incoming("English", "hi", 4, "theirs", "2026-01-08 00:00:00", "2026-01-08 00:00:00")])
    status, translation, _ = _row("hi")
    assert (status, translation) == (4, "mine")


def test_unknown_terms_are_not_sent(english, clean_log):
    "Status 0 terms are render placeholders; they never leave the device."
    _word(english.id, "seen", status=0)
    _word(english.id, "learning", status=1)
    db.session.commit()
    changes = engine.local_changes(db.session, 0, engine.max_log_id(db.session))
    assert [c["row"]["text_lc"] for c in changes] == ["learning"]


def test_local_placeholder_takes_incoming_term(english, clean_log):
    "A status 0 term here, created just now, doesn't beat a real one from a peer."
    _word(english.id, "hi", status=0)
    db.session.commit()
    _device_apply([_incoming("English", "hi", 3, "x", "2026-01-01 00:00:00", "2020-01-01 00:00:00")])
    assert _row("hi")[0] == 3


def test_incoming_delete(english, clean_log):
    _word(english.id, "hi")
    db.session.commit()
    ex("delete from synclog")
    db.session.commit()
    _device_apply([_incoming("English", "hi", None, None, None, "2026-01-01 00:00:00", op="delete")])
    assert _row("hi") is None


def test_change_for_missing_language_is_orphaned(app_context, clean_log):
    result = _device_apply([_incoming("Klingon", "qapla", 1, "x", "2026-01-01 00:00:00", "2026-01-01 00:00:00")])
    assert result.orphaned == 1


def test_reading_position_waits_for_its_page(english, clean_log):
    "A book can arrive before the page its position points at."
    book = {
        "uuid": "u1",
        "lang": "English",
        "title": "B",
        "archived": 0,
        "current_page": 2,
    }
    _device_apply([{"table": "books", "op": "upsert", "row": book, "changed_at": engine.EPOCH}])
    assert ex("select BkCurrentTxID from books where BkUUID = 'u1'").scalar() == 0
    pages = [
        {"table": "texts", "op": "upsert", "row": {"book": "u1", "order": n, "text": f"Page {n}."},
         "changed_at": engine.EPOCH}
        for n in (1, 2)
    ]
    _device_apply(pages)
    txid = ex("select TxID from texts join books on BkID = TxBkID where BkUUID = 'u1' and TxOrder = 2").scalar()
    assert ex("select BkCurrentTxID from books where BkUUID = 'u1'").scalar() == txid
    assert engine.get_state(db.session, "pending_pages") is None


def test_hub_logs_received_changes_and_skips_sender(english, clean_log):
    "The hub passes a device's change on to others, but not back to the sender."
    result = hub.receive(
        db.session,
        PEER,
        [_incoming("English", "hi", 2, "x", "2026-01-01 00:00:00", "2026-01-01 00:00:00")],
    )
    assert result.applied == 1
    origin = ex("select SlOrigin, SlChangedAt from synclog").first()
    assert tuple(origin) == (PEER, "2026-01-01 00:00:00")
    assert hub.pull(db.session, 0, PEER)["changes"] == []
    other = hub.pull(db.session, 0, "another-device")["changes"]
    assert [c["row"]["text_lc"] for c in other] == ["hi"]


def test_hub_rejects_older_change(english, clean_log):
    "The hub compares against the newest change it holds, from anyone."
    hub.receive(db.session, "a", [_incoming("English", "hi", 2, "from a", "2026-01-02 00:00:00", "2026-01-02 00:00:00")])
    result = hub.receive(
        db.session, "b", [_incoming("English", "hi", 1, "from b", "2026-01-01 00:00:00", "2026-01-01 00:00:00")]
    )
    assert result.skipped == 1
    assert _row("hi")[1] == "from a"


def test_snapshot_round_trip(english, clean_log):
    "A snapshot page, applied, reproduces the rows."
    _word(english.id, "hi", status=3, translation="x")
    db.session.commit()
    changes, _, more = engine.snapshot_page(db.session, "words", 0)
    assert not more
    ex("delete from words")
    db.session.commit()
    _device_apply(changes)
    assert _row("hi")[:2] == (3, "x")
