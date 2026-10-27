"""
Sync domain mapping tests.

The point of lute/sync/schema.py is that every synced row can be addressed by
portable values instead of local integer ids.  These tests check that claim two
ways: the generated SQL runs and yields keys that are unique and non-null, and
a peer's payload is validated rather than trusted.

Five mapping tables (tags, tags2, wordtags, booktags, wordimages,
textbookmarks) are empty in real use so far, so they are populated here
explicitly - otherwise their specs would only ever be "tested" against no rows.
"""

from collections import Counter

import pytest
from sqlalchemy import text

from lute.db import db
from lute.sync.schema import (
    SPECS,
    SYNC_ORDER,
    SyncSchemaError,
    WORDS,
    WORDSREAD,
    spec_for,
    validate_batch,
)


def _rows(spec):
    "Wire rows for a spec, as dicts."
    result = db.session.execute(text(spec.select_sql()))
    return [dict(zip(spec.wire_fields, r)) for r in result]


@pytest.fixture(name="populated")
def fixture_populated(app_context, english):
    """
    Give every spec at least one row, including the mapping tables that are
    empty in the real database.
    """
    ex = lambda sql: db.session.execute(text(sql))  # noqa: E731
    lgid = english.id
    ex(
        "insert into words (WoLgID, WoText, WoTextLC, WoStatus, WoTokenCount) "
        f"values ({lgid}, 'Hello', 'hello', 1, 1)"
    )
    ex(
        "insert into words (WoLgID, WoText, WoTextLC, WoStatus, WoTokenCount) "
        f"values ({lgid}, 'Hell', 'hell', 1, 1)"
    )
    ex(
        "insert into wordparents (WpWoID, WpParentWoID) values "
        "((select WoID from words where WoTextLC='hello'), "
        " (select WoID from words where WoTextLC='hell'))"
    )
    ex("insert into tags (TgText, TgComment) values ('greeting', 'hi there')")
    ex("insert into tags2 (T2Text, T2Comment) values ('fiction', 'made up')")
    ex(
        "insert into wordtags (WtWoID, WtTgID) values "
        "((select WoID from words where WoTextLC='hello'), "
        " (select TgID from tags where TgText='greeting'))"
    )
    ex(
        "insert into wordimages (WiWoID, WiSource) values "
        "((select WoID from words where WoTextLC='hello'), 'hello.png')"
    )
    ex(
        f"insert into books (BkLgID, BkTitle, BkSourceURI) "
        f"values ({lgid}, 'Greetings', 'http://example.com')"
    )
    ex(
        "insert into texts (TxBkID, TxOrder, TxText) values "
        "((select BkID from books where BkTitle='Greetings'), 1, 'Hello there.')"
    )
    ex(
        "insert into booktags (BtBkID, BtT2ID) values "
        "((select BkID from books where BkTitle='Greetings'), "
        " (select T2ID from tags2 where T2Text='fiction'))"
    )
    ex(
        "insert into textbookmarks (TbTxID, TbTitle) values "
        "((select TxID from texts where TxText='Hello there.'), 'the start')"
    )
    ex(
        "insert into booksections (BsBkID, BsOrder, BsTitle, BsStartPage) values "
        "((select BkID from books where BkTitle='Greetings'), 0, 'Part One', 1)"
    )
    # A second book, so there is a pair to sync (parallel reading).
    ex(
        f"insert into books (BkLgID, BkTitle) values ({lgid}, 'Greetings (translated)')"
    )
    ex(
        "insert into bookpairs (BpPrimaryBkID, BpCompanionBkID, BpPageMap) values "
        "((select BkID from books where BkTitle='Greetings'), "
        " (select BkID from books where BkTitle='Greetings (translated)'), '[[1,1]]')"
    )
    ex(
        f"insert into wordsread (WrLgID, WrTxID, WrReadDate, WrWordCount) values "
        f"({lgid}, (select TxID from texts where TxText='Hello there.'), "
        "'2026-09-18 10:00:00', 3)"
    )
    db.session.commit()


# ---------------------------------------------------------------------------
# The core claim: portable keys.
# ---------------------------------------------------------------------------


def test_every_spec_produces_valid_sql(populated):
    "Each spec's SELECT runs and returns its declared wire fields."
    for spec in SYNC_ORDER:
        rows = _rows(spec)
        assert rows, f"{spec.name}: no rows, so the spec is untested"
        assert set(rows[0]) == set(spec.wire_fields), spec.name


def test_natural_keys_are_unique(populated):
    "Two rows must never share a key, or they would merge into each other."
    for spec in SYNC_ORDER:
        keys = [spec.key_of(r) for r in _rows(spec)]
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        assert not dupes, f"{spec.name}: duplicate keys {dupes[:3]}"


def test_natural_keys_are_never_null(populated):
    "A null key field cannot address a row, so it must not occur."
    for spec in SYNC_ORDER:
        for row in _rows(spec):
            spec.key_of(row)  # raises if any key field is None


def test_no_local_ids_on_the_wire(populated):
    """
    No wire field may carry a local integer primary key.

    This is the whole design: WoID/BkID/TxID differ between devices, so any
    that leaked into a payload would address the wrong row on the peer.
    """
    local_id_columns = ("WoID", "BkID", "TxID", "TgID", "T2ID", "LgID", "WrID")
    for spec in SYNC_ORDER:
        for wire_field, expr in spec.columns.items():
            if spec is WORDSREAD and wire_field == "id":
                # Scoped by originating device, so it is portable as a pair.
                continue
            bare = expr.strip()
            for col in local_id_columns:
                assert not bare.endswith(f".{col}"), (
                    f"{spec.name}.{wire_field} exposes local id {col}"
                )


def test_book_reading_position_travels_as_a_page_number(populated):
    "BkCurrentTxID is a local id; the peer needs the page it refers to."
    assert "current_page" in SPECS["books"].wire_fields
    assert "BkCurrentTxID" not in SPECS["books"].columns["current_page"].split(".")[-1:]


# ---------------------------------------------------------------------------
# Validation of what a peer sends.
# ---------------------------------------------------------------------------


def test_unknown_table_is_refused():
    """
    Refused, not ignored: it means the two sides disagree about what sync
    covers, and dropping the rows would look like successful convergence.
    """
    with pytest.raises(SyncSchemaError) as exc:
        spec_for("settings")
    assert "not a synced table" in str(exc.value)


def test_unknown_fields_are_dropped_not_refused():
    "A newer peer may send fields this version has never heard of."
    payload = {"lang": "English", "text_lc": "hello", "status": 3, "future": "?"}
    out = WORDS.validate(payload)
    assert "future" not in out
    assert out["status"] == 3


def test_missing_key_field_is_fatal():
    "Without a key the row cannot be addressed at all."
    with pytest.raises(SyncSchemaError) as exc:
        WORDS.validate({"text_lc": "hello", "status": 1})
    assert "lang" in str(exc.value)


def test_null_key_field_is_fatal():
    "An explicit null is no more addressable than an absent field."
    with pytest.raises(SyncSchemaError):
        WORDS.validate({"lang": None, "text_lc": "hello"})


def test_delete_is_narrowed_to_its_key():
    "A delete names a row that is already gone; its other fields are noise."
    batch = [
        {
            "table": "words",
            "op": "delete",
            "row": {"lang": "English", "text_lc": "hello", "status": 5},
        }
    ]
    (spec, op, payload), = validate_batch(batch)
    assert (spec.name, op) == ("words", "delete")
    assert payload == {"lang": "English", "text_lc": "hello"}


def test_bad_op_is_refused():
    with pytest.raises(SyncSchemaError) as exc:
        validate_batch([{"table": "words", "op": "update", "row": {}}])
    assert "upsert" in str(exc.value)


def test_malformed_changes_are_refused():
    for bad in (
        [{"op": "upsert", "row": {}}],
        [{"table": "words", "row": {}}],
        [{"table": "words", "op": "upsert"}],
        ["not an object"],
    ):
        with pytest.raises(SyncSchemaError):
            validate_batch(bad)


def test_derived_and_secret_tables_are_not_synced():
    """
    sentences/bookstats are recomputed locally; settings holds device paths
    and API keys.  None may be addressed by a peer.
    """
    for table in ("sentences", "bookstats", "settings", "synclog", "syncstate"):
        with pytest.raises(SyncSchemaError):
            spec_for(table)
