"""
Reading local changes out, and writing a peer's changes in.

Both ends of a sync use this module: a device (desktop, iPad) and the hub (the
hosted Lute they all sync with).  The wire format is described in schema.py:
every row is addressed by its natural key, never a local id.

A change on the wire:

    {"table": "words", "op": "upsert" | "delete",
     "row": {...wire fields...},           # just the key for a delete
     "changed_at": "2026-11-16 09:12:00"}  # UTC, origin device's clock

Conflicts are last-write-wins on `changed_at`, per row, with one refinement for
terms: a term's status is decided separately, by WoStatusChanged, so editing a
translation on one device doesn't undo a status change made on another.

What counts as "the local side's time" for a row differs by role:
  - a device compares against its *unpushed* edits only.  Anything it already
    pushed has been through the hub's own comparison, so whatever the hub sends
    back is the winner.
  - the hub compares against the newest change it has logged for that row,
    from anyone.

Unknown (status 0) terms are never sent.  Lute creates them for every new word
on a page as it renders it - two thirds of a real library - and they carry
nothing; worse, a fresh status-0 row has a fresh WoStatusChanged, which would
beat a real status from another device.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text as sqltext

from lute.sync.schema import SPECS, SYNC_ORDER, TableSpec, validate_batch

# Older than anything real: used for rows that have no change log entry, so a
# snapshot of existing data never beats an actual edit.
EPOCH = "1970-01-01 00:00:00"

# Synced by spec, but not carried yet.  wordsread rows are keyed by the
# device that recorded them, and nothing on the receiving side stores that
# device, so a hub couldn't pass them on.  They are reading statistics only.
NOT_CARRIED = ("wordsread",)

CARRIED: Tuple[TableSpec, ...] = tuple(s for s in SYNC_ORDER if s.name not in NOT_CARRIED)

_ORDER = {s.name: i for i, s in enumerate(SYNC_ORDER)}


def _q(session, sql, params=None):
    return session.execute(sqltext(sql), params or {})


# ---------------------------------------------------------------------------
# Sync state (key/value bookkeeping, never synced).
# ---------------------------------------------------------------------------


def get_state(session, key, default=None):
    "A syncstate value."
    row = _q(session, "SELECT SsValue FROM syncstate WHERE SsKey = :k", {"k": key}).first()
    return default if row is None or row[0] is None else row[0]


def set_state(session, key, value):
    "Set (or with None, clear) a syncstate value."
    if value is None:
        _q(session, "DELETE FROM syncstate WHERE SsKey = :k", {"k": key})
        return
    _q(
        session,
        "INSERT INTO syncstate (SsKey, SsValue) VALUES (:k, :v) "
        "ON CONFLICT(SsKey) DO UPDATE SET SsValue = excluded.SsValue",
        {"k": key, "v": str(value)},
    )


def device_id(session):
    "This database's device id."
    return get_state(session, "device_id")


def max_log_id(session) -> int:
    "The newest synclog id, or 0."
    return _q(session, "SELECT COALESCE(MAX(SlID), 0) FROM synclog").scalar()


# ---------------------------------------------------------------------------
# Keys.
# ---------------------------------------------------------------------------


def key_text(spec: TableSpec, row: Dict[str, Any]) -> str:
    """
    A natural key as the triggers write it into SlKey.

    json_object() writes keys in argument order (each trigger uses the spec's
    key order), without spaces, and leaves non-ASCII as is - so this produces
    the identical string, and the hub can look rows up by exact SlKey.
    """
    return json.dumps(
        {f: row[f] for f in spec.key}, separators=(",", ":"), ensure_ascii=False
    )


def _key_params(spec, row):
    return {f"key_{f}": row[f] for f in spec.key}


# ---------------------------------------------------------------------------
# Reading rows out.
# ---------------------------------------------------------------------------


def _cols_sql(spec):
    return ", ".join(f'{spec.columns[f]} AS "{f}"' for f in spec.wire_fields)


def read_row(session, spec: TableSpec, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    "The live wire row for a key, or None if it no longer exists."
    res = _q(session, spec.select_sql(spec.key_where()), _key_params(spec, key)).first()
    if res is None:
        return None
    return dict(zip(spec.wire_fields, res))


def _sendable(spec, row) -> bool:
    "False for rows that are never sent (see the module docstring)."
    if spec.name == "words" and row.get("status") == 0:
        return False
    return True


def _collapse(entries):
    """
    The newest log entry per (table, key), as (table, key_text, op, changed_at)
    in log order.  `entries` are (id, table, key, op, changed_at, origin) rows.
    """
    latest = {}
    for e in entries:
        latest[(e[1], e[2])] = e
    return sorted(latest.values(), key=lambda e: e[0])


def build_changes(session, entries) -> Tuple[List[Dict[str, Any]], int]:
    """
    Turn log entries into wire changes; returns (changes, skipped).

    Upserts re-read the live row, so it doesn't matter how many times a row
    was edited or what the log saw at the time.  A row that is gone has a
    later delete entry, which is what gets sent instead.
    """
    out = []
    skipped = 0
    for _id, table, key, op, changed_at, _origin in _collapse(entries):
        spec = SPECS.get(table)
        if spec is None or table in NOT_CARRIED:
            continue
        try:
            keyrow = json.loads(key)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(keyrow, dict) or any(keyrow.get(f) is None for f in spec.key):
            # A tombstone written during a cascade, after its parent had gone:
            # the parent's own delete covers it.
            skipped += 1
            continue
        if op == "delete":
            out.append({"table": table, "op": "delete", "row": keyrow, "changed_at": changed_at})
            continue
        row = read_row(session, spec, keyrow)
        if row is None or not _sendable(spec, row):
            skipped += 1
            continue
        out.append({"table": table, "op": "upsert", "row": row, "changed_at": changed_at})
    out.sort(key=lambda c: _ORDER[c["table"]])
    return out, skipped


def local_changes(session, after_id: int, upto_id: int):
    "Changes for local edits logged in (after_id, upto_id]."
    entries = _q(
        session,
        "SELECT SlID, SlTable, SlKey, SlOp, SlChangedAt, SlOrigin FROM synclog "
        "WHERE SlID > :a AND SlID <= :b AND SlOrigin IS NULL ORDER BY SlID",
        {"a": after_id, "b": upto_id},
    ).fetchall()
    return build_changes(session, entries)[0]


def changes_for_peer(session, since: int, peer: str, limit: int = 2000):
    """
    What a peer hasn't seen: (changes, cursor, more).

    Everything logged after `since` except what that peer itself sent.  The
    cursor covers the peer's own entries too, so it moves past them.
    """
    entries = _q(
        session,
        "SELECT SlID, SlTable, SlKey, SlOp, SlChangedAt, SlOrigin FROM synclog "
        "WHERE SlID > :s ORDER BY SlID LIMIT :n",
        {"s": since, "n": limit},
    ).fetchall()
    if not entries:
        return [], since, False
    cursor = entries[-1][0]
    # Collapse first, then drop the peer's own: if the newest change to a row
    # came from the peer, it already has that row as it is now.
    mine = [e for e in _collapse(entries) if e[5] != peer]
    changes, _ = build_changes(session, mine)
    return changes, cursor, len(entries) == limit


def _latest_times(session) -> Dict[Tuple[str, str], str]:
    rows = _q(
        session,
        "SELECT SlTable, SlKey, MAX(SlChangedAt) FROM synclog GROUP BY SlTable, SlKey",
    ).fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def snapshot_page(session, table: str, after_rowid: int, limit: int = 1000, times=None):
    """
    One page of a full copy of a table: (changes, last_rowid, more).

    Used when a device first joins.  Each row's changed_at is its newest log
    time, or EPOCH if it was never edited since sync was installed, so that
    a snapshot never beats a real edit.
    """
    spec = SPECS[table]
    sql = (
        f"SELECT {spec.name}.rowid AS _rowid, {_cols_sql(spec)} FROM {spec.source} "
        f"WHERE {spec.name}.rowid > :after ORDER BY {spec.name}.rowid LIMIT :n"
    )
    rows = _q(session, sql, {"after": after_rowid, "n": limit}).fetchall()
    if times is None:
        times = _latest_times(session)
    out = []
    last = after_rowid
    for r in rows:
        last = r[0]
        row = dict(zip(spec.wire_fields, r[1:]))
        if any(row.get(f) is None for f in spec.key) or not _sendable(spec, row):
            continue
        changed_at = times.get((table, key_text(spec, row)), EPOCH)
        out.append({"table": table, "op": "upsert", "row": row, "changed_at": changed_at})
    return out, last, len(rows) == limit


# ---------------------------------------------------------------------------
# Writing rows in.
#
# The read side (schema.py) maps columns to wire fields with SQL expressions
# that resolve foreign keys.  Those aren't reversible in general, so the write
# side is spelled out here: plain columns, and foreign keys with the wire
# fields that identify the referenced row.
# ---------------------------------------------------------------------------


def _lang(session, name):
    return _q(session, "SELECT LgID FROM languages WHERE LgName = :n", {"n": name}).scalar()


def _book(session, uuid):
    return _q(session, "SELECT BkID FROM books WHERE BkUUID = :u", {"u": uuid}).scalar()


def _word(session, lang, text_lc):
    return _q(
        session,
        "SELECT WoID FROM words JOIN languages ON LgID = WoLgID "
        "WHERE LgName = :l AND WoTextLC = :t",
        {"l": lang, "t": text_lc},
    ).scalar()


def _tag(session, t):
    return _q(session, "SELECT TgID FROM tags WHERE TgText = :t", {"t": t}).scalar()


def _tag2(session, t):
    return _q(session, "SELECT T2ID FROM tags2 WHERE T2Text = :t", {"t": t}).scalar()


def _text(session, uuid, order):
    return _q(
        session,
        "SELECT TxID FROM texts JOIN books ON BkID = TxBkID "
        "WHERE BkUUID = :u AND TxOrder = :o",
        {"u": uuid, "o": order},
    ).scalar()


@dataclass(frozen=True)
class Writer:
    "How to write one table's wire rows."

    cols: Dict[str, str]
    # column -> (resolver, wire fields passed to it)
    fks: Dict[str, Tuple[Any, Tuple[str, ...]]] = field(default_factory=dict)


WRITERS: Dict[str, Writer] = {
    "languages": Writer(
        cols={
            "name": "LgName",
            "character_substitutions": "LgCharacterSubstitutions",
            "regexp_split_sentences": "LgRegexpSplitSentences",
            "exceptions_split_sentences": "LgExceptionsSplitSentences",
            "regexp_word_characters": "LgRegexpWordCharacters",
            "right_to_left": "LgRightToLeft",
            "show_romanization": "LgShowRomanization",
            "parser_type": "LgParserType",
        }
    ),
    "languagedicts": Writer(
        cols={
            "use_for": "LdUseFor",
            "dict_uri": "LdDictURI",
            "dict_type": "LdType",
            "is_active": "LdIsActive",
            "sort_order": "LdSortOrder",
        },
        fks={"LdLgID": (_lang, ("lang",))},
    ),
    "tags": Writer(cols={"text": "TgText", "comment": "TgComment"}),
    "tags2": Writer(cols={"text": "T2Text", "comment": "T2Comment"}),
    "books": Writer(
        cols={
            "uuid": "BkUUID",
            "title": "BkTitle",
            "source_uri": "BkSourceURI",
            "archived": "BkArchived",
            "audio_filename": "BkAudioFilename",
            "audio_current_pos": "BkAudioCurrentPos",
            "audio_bookmarks": "BkAudioBookmarks",
        },
        fks={"BkLgID": (_lang, ("lang",))},
    ),
    "booksections": Writer(
        cols={
            "order": "BsOrder",
            "title": "BsTitle",
            "start_page": "BsStartPage",
            "token_count": "BsTokenCount",
            "level": "BsLevel",
        },
        fks={"BsBkID": (_book, ("book",))},
    ),
    "bookpairs": Writer(
        cols={"page_map": "BpPageMap"},
        fks={
            "BpPrimaryBkID": (_book, ("primary",)),
            "BpCompanionBkID": (_book, ("companion",)),
        },
    ),
    "texts": Writer(
        cols={
            "order": "TxOrder",
            "text": "TxText",
            "read_date": "TxReadDate",
            "word_count": "TxWordCount",
            "start_date": "TxStartDate",
        },
        fks={"TxBkID": (_book, ("book",))},
    ),
    "textbookmarks": Writer(
        cols={"title": "TbTitle"}, fks={"TbTxID": (_text, ("book", "order"))}
    ),
    "booktags": Writer(
        cols={}, fks={"BtBkID": (_book, ("book",)), "BtT2ID": (_tag2, ("tag",))}
    ),
    "words": Writer(
        cols={
            "text_lc": "WoTextLC",
            "text": "WoText",
            "status": "WoStatus",
            "translation": "WoTranslation",
            "romanization": "WoRomanization",
            "token_count": "WoTokenCount",
            "created": "WoCreated",
            "status_changed": "WoStatusChanged",
            "follow_parent": "WoSyncStatus",
        },
        fks={"WoLgID": (_lang, ("lang",))},
    ),
    "wordparents": Writer(
        cols={},
        fks={
            "WpWoID": (_word, ("lang", "child")),
            "WpParentWoID": (_word, ("lang", "parent")),
        },
    ),
    "wordtags": Writer(
        cols={}, fks={"WtWoID": (_word, ("lang", "text_lc")), "WtTgID": (_tag, ("tag",))}
    ),
    "wordimages": Writer(
        cols={"src": "WiSource"}, fks={"WiWoID": (_word, ("lang", "text_lc"))}
    ),
}


@dataclass
class ApplyResult:
    "What an apply did."

    applied: int = 0
    # Lost to a newer edit on this side.
    skipped: int = 0
    # Refer to a row (a language, a book) that doesn't exist here.
    orphaned: int = 0
    # The changes actually written, for the hub to log.
    written: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self):
        return {"applied": self.applied, "skipped": self.skipped, "orphaned": self.orphaned}

    def add(self, other: "ApplyResult"):
        self.applied += other.applied
        self.skipped += other.skipped
        self.orphaned += other.orphaned


def _rowid(session, spec, row):
    sql = f"SELECT {spec.name}.rowid FROM {spec.source} WHERE {spec.key_where()}"
    return _q(session, sql, _key_params(spec, row)).scalar()


def _column_values(session, table, row) -> Optional[Dict[str, Any]]:
    "Column -> value for a wire row, or None if a referenced row is missing."
    w = WRITERS[table]
    vals = {col: row[f] for f, col in w.cols.items() if f in row}
    for col, (resolve, fields) in w.fks.items():
        ref = resolve(session, *[row.get(f) for f in fields])
        if ref is None:
            return None
        vals[col] = ref
    return vals


def _write(session, spec, row, rowid):
    "Insert or update one row; returns its rowid, or None if orphaned."
    table = spec.name
    vals = _column_values(session, table, row)
    if vals is None:
        return None
    if table == "bookpairs":
        # Each book can be in one pair only (UNIQUE on both columns), and a
        # pair's key is both books - so re-pairing reads as a new key.  Clear
        # whatever pairing stands in the way.
        _q(
            session,
            "DELETE FROM bookpairs WHERE (BpPrimaryBkID = :p OR BpCompanionBkID = :c) "
            "AND rowid IS NOT :r",
            {"p": vals["BpPrimaryBkID"], "c": vals["BpCompanionBkID"], "r": rowid},
        )
    if rowid is None:
        cols = ", ".join(vals)
        binds = ", ".join(f":{c}" for c in vals)
        res = _q(session, f"INSERT INTO {table} ({cols}) VALUES ({binds})", vals)
        return res.lastrowid
    if vals:
        sets = ", ".join(f"{c} = :{c}" for c in vals)
        _q(session, f"UPDATE {table} SET {sets} WHERE rowid = :_rowid", {**vals, "_rowid": rowid})
    return rowid


def _word_status(session, rowid):
    return _q(
        session, "SELECT WoStatus, WoStatusChanged FROM words WHERE rowid = :r", {"r": rowid}
    ).first()


class Applier:
    """
    Writes validated changes into this database, with capture off.

    `local_time(table, key_text)` gives the time of this side's competing
    edit to a row, or None if there isn't one (see the module docstring).
    """

    def __init__(self, session, local_time):
        self.session = session
        self.local_time = local_time
        self.touched_books = set()
        self.touched_texts = set()
        self.words_changed = False

    def apply(self, changes) -> ApplyResult:
        "Apply one batch.  The caller commits."
        result = ApplyResult()
        triples = validate_batch(changes)
        stamps = [c.get("changed_at") or EPOCH for c in changes]
        order = sorted(range(len(triples)), key=lambda i: _ORDER[triples[i][0].name])
        for i in order:
            spec, op, row = triples[i]
            if spec.name in NOT_CARRIED:
                continue
            outcome = self._apply_one(spec, op, row, str(stamps[i]))
            if outcome == "applied":
                result.applied += 1
                result.written.append(
                    {"table": spec.name, "op": op, "row": row, "changed_at": str(stamps[i])}
                )
            elif outcome == "orphaned":
                result.orphaned += 1
            else:
                result.skipped += 1
        self._resolve_current_pages()
        return result

    def _apply_one(self, spec, op, row, changed_at):
        s = self.session
        rowid = _rowid(s, spec, row)
        mine = self.local_time(spec.name, key_text(spec, row))
        newer_here = mine is not None and mine > changed_at

        if spec.name == "words" and rowid is not None:
            current = _word_status(s, rowid)
            if current is not None and current[0] == 0:
                # An unknown term here is a placeholder, not an edit.
                newer_here = False
            if newer_here and op == "upsert":
                return self._merge_status_only(rowid, row, current)

        if newer_here:
            return "skipped"

        if op == "delete":
            if rowid is not None:
                self._note(spec.name, rowid, row)
                _q(s, f"DELETE FROM {spec.name} WHERE rowid = :r", {"r": rowid})
            return "applied"

        if spec.name == "words":
            if row.get("status") == 0:
                return "skipped"
            if rowid is not None:
                row = self._keep_newer_status(rowid, row)

        old_text = None
        if spec.name == "texts" and rowid is not None:
            old_text = _q(s, "SELECT TxText FROM texts WHERE rowid = :r", {"r": rowid}).scalar()

        newid = _write(s, spec, row, rowid)
        if newid is None:
            return "orphaned"
        if spec.name == "texts" and rowid is not None and old_text == row.get("text"):
            # Same words on the page (a read date, say): sentences still hold.
            self.touched_books.add(row["book"])
            return "applied"
        self._note(spec.name, newid, row)
        return "applied"

    def _keep_newer_status(self, rowid, row):
        "Take the incoming term, but keep this side's status if it's newer."
        current = _word_status(self.session, rowid)
        if current is None or current[0] == 0:
            return row
        incoming = row.get("status_changed") or EPOCH
        if current[1] is not None and str(current[1]) > str(incoming):
            row = dict(row)
            row["status"] = current[0]
            row["status_changed"] = current[1]
        return row

    def _merge_status_only(self, rowid, row, current):
        "This side's term edit wins, but the incoming status may be newer."
        incoming = row.get("status_changed")
        if incoming is None or row.get("status") in (None, 0):
            return "skipped"
        if current[1] is None or str(incoming) > str(current[1]):
            _q(
                self.session,
                "UPDATE words SET WoStatus = :s, WoStatusChanged = :c WHERE rowid = :r",
                {"s": row["status"], "c": incoming, "r": rowid},
            )
            self.words_changed = True
            return "applied"
        return "skipped"

    def _note(self, table, rowid, row):
        "Remember what derived data needs refreshing."
        if table == "books":
            self.touched_books.add(row["uuid"])
            if "current_page" in row:
                self._pending_page(row["uuid"], row["current_page"])
        elif table == "texts":
            self.touched_texts.add(rowid)
            self.touched_books.add(row["book"])
        elif table in ("words", "wordparents"):
            self.words_changed = True

    # A book's reading position travels as a page number, and can arrive
    # before the page itself does (pages come in later batches).  Unresolved
    # positions wait in syncstate until their page exists.

    def _pending_page(self, uuid, page):
        pending = json.loads(get_state(self.session, "pending_pages", "{}"))
        pending[uuid] = page
        set_state(self.session, "pending_pages", json.dumps(pending))

    def _resolve_current_pages(self):
        pending = json.loads(get_state(self.session, "pending_pages", "{}"))
        if not pending:
            return
        left = {}
        for uuid, page in pending.items():
            bkid = _book(self.session, uuid)
            if bkid is None:
                continue
            if page is None:
                txid = 0
            else:
                txid = _text(self.session, uuid, page)
                if txid is None:
                    left[uuid] = page
                    continue
            _q(
                self.session,
                "UPDATE books SET BkCurrentTxID = :t WHERE BkID = :b",
                {"t": txid, "b": bkid},
            )
        set_state(self.session, "pending_pages", json.dumps(left) if left else None)


def begin_apply(session):
    "Turn capture off for this transaction."
    set_state(session, "applying", "1")


def end_apply(session):
    "Turn capture back on.  Same transaction, so no other writer saw it off."
    set_state(session, "applying", "0")


def refresh_derived(session, applier: Applier):
    """
    Bring sentences and book stats up to date after an apply.  Call after the
    apply has been committed: this goes through the ORM.

    Lute keeps sentences for pages that have been opened (they are what the
    term form's "Sentences" tab searches), so they're rebuilt for opened pages
    whose text changed.  Book stats are dropped and recalculate on demand.
    """
    # pylint: disable=import-outside-toplevel
    from lute.models.book import Text

    for txid in applier.touched_texts:
        _q(session, "DELETE FROM sentences WHERE SeTxID = :t", {"t": txid})
        t = session.get(Text, txid)
        if t is not None and (t.read_date is not None or t.start_date is not None):
            try:
                t.load_sentences()
                session.add(t)
            except Exception:  # pylint: disable=broad-exception-caught
                # e.g. Japanese on a device without MeCab.  The page still
                # reads; only the sentence lookup misses it.
                pass
    if applier.words_changed:
        _q(session, "DELETE FROM bookstats")
    elif applier.touched_books:
        for uuid in applier.touched_books:
            _q(
                session,
                "DELETE FROM bookstats WHERE BkID = (SELECT BkID FROM books WHERE BkUUID = :u)",
                {"u": uuid},
            )
    session.commit()


def wipe_library(session):
    """
    Delete every book, term, language and tag, keeping settings.

    For a device (or hub) holding only Lute's demo data when it first
    syncs: the demo books would otherwise be merged into the real library.
    Unlike delete_all_data(), settings survive (a hub's AI key, for one).
    Capture is off, and the log is cleared, so none of it is sent anywhere.
    """
    begin_apply(session)
    for t in ("languages", "tags", "tags2"):
        _q(session, f"DELETE FROM {t}")
    _q(session, "DELETE FROM synclog")
    _q(session, "DELETE FROM settings WHERE StKey = 'IsDemoData'")
    set_state(session, "pending_pages", None)
    end_apply(session)
    session.commit()


def contains_demo_data(session) -> bool:
    "True if this database still holds only Lute's demo library."
    return (
        _q(session, "SELECT 1 FROM settings WHERE StKey = 'IsDemoData'").first() is not None
    )


def book_count(session) -> int:
    "How many books there are."
    return _q(session, "SELECT COUNT(*) FROM books").scalar()


def hub_local_time(session):
    "The hub's competing time for a row: its newest log entry, from anyone."

    def lookup(table, key):
        return _q(
            session,
            "SELECT MAX(SlChangedAt) FROM synclog WHERE SlTable = :t AND SlKey = :k",
            {"t": table, "k": key},
        ).scalar()

    return lookup


def device_local_time(session, after_id: int):
    "A device's competing time for a row: its newest unpushed edit."
    rows = _q(
        session,
        "SELECT SlTable, SlKey, MAX(SlChangedAt) FROM synclog "
        "WHERE SlID > :a AND SlOrigin IS NULL GROUP BY SlTable, SlKey",
        {"a": after_id},
    ).fetchall()
    pending = {(r[0], r[1]): r[2] for r in rows}

    def lookup(table, key):
        return pending.get((table, key))

    return lookup


def log_received(session, written: Iterable[Dict[str, Any]], origin: str):
    """
    On the hub: log what was applied, so other devices get it.

    Stamped with the sender's change time and id, so the sender isn't sent
    its own change back and later comparisons use the origin's clock.
    """
    for c in written:
        spec = SPECS[c["table"]]
        _q(
            session,
            "INSERT INTO synclog (SlTable, SlKey, SlOp, SlChangedAt, SlOrigin) "
            "VALUES (:t, :k, :o, :c, :d)",
            {
                "t": c["table"],
                "k": key_text(spec, c["row"]),
                "o": c["op"],
                "c": c["changed_at"],
                "d": origin,
            },
        )
