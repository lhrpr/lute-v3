"""
The sync domain mapping: what travels between devices, and how it is named.

Local integer primary keys (WoID, BkID, TxID) are meaningless across devices -
two devices that each create a term offline both call it WoID 500.  So nothing
on the wire is ever a local id.  Every row is addressed by a *natural key*
made of portable values, which the schema already enforces as unique:

    words         UNIQUE (WoTextLC, WoLgID)   -> (language name, text_lc)
    languages     UNIQUE (LgName)             -> (name,)
    tags / tags2  UNIQUE (TgText) / (T2Text)  -> (text,)
    wordparents   UNIQUE (WpWoID, WpParentWoID)

Two devices that independently create the same term therefore produce the same
key and merge, rather than colliding.  `books` is the one entity with no such
key - BkTitle is not unique, and duplicate (BkTitle, BkLgID) pairs occur in
real databases - so books carry an explicit BkUUID, and texts hang off it as
(book uuid, TxOrder).

Deliberately absent:
  sentences, bookstats  derived from texts; recomputed locally.  Syncing them
                        would manufacture conflicts between devices that agree.
  settings              device-local (mecab_path, backup_dir) and secret
                        (ai_api_key, deepl_api_key).  Default-deny.

No pydantic, despite it being the obvious choice for payload validation: the
iOS build installs every dependency as a `py3-none-any` wheel, and pydantic v2
is built on a compiled Rust core with no pure-Python fallback.  Validation here
is explicit instead.  See ios/build_pure_wheels.sh.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple


class SyncSchemaError(ValueError):
    """A payload did not match what this schema allows."""


@dataclass(frozen=True)
class TableSpec:
    """
    How one table is addressed and carried on the wire.

    key      wire field names forming the natural key, in a stable order.
    fields   the remaining wire fields.  Key fields are not repeated here.
    columns  wire field name -> SQL expression producing it, evaluated
             against `source`.  Expressions resolve foreign keys to portable
             values (a language name, a book uuid) rather than emitting ids.
    source   FROM clause, including whatever joins `columns` needs.
    """

    name: str
    key: Tuple[str, ...]
    fields: Tuple[str, ...]
    columns: Dict[str, str]
    source: str
    # Tables holding a user-visible record that a peer could have edited
    # concurrently.  Pure mapping tables (wordtags, wordparents) have no
    # attributes of their own, so they only ever exist or not - there is
    # nothing to reconcile and last-write-wins does not apply to them.
    mergeable: bool = True

    @property
    def wire_fields(self) -> Tuple[str, ...]:
        "Every field carried for this table, key first."
        return self.key + self.fields

    def select_sql(self, where: Optional[str] = None) -> str:
        "SELECT returning wire rows, optionally filtered."
        # Aliases are quoted: several wire names ("order", "primary") are SQL
        # reserved words, and an unquoted alias is a syntax error.
        cols = ", ".join(f'{self.columns[f]} AS "{f}"' for f in self.wire_fields)
        sql = f"SELECT {cols} FROM {self.source}"
        if where:
            sql = f"{sql} WHERE {where}"
        return sql

    def key_where(self) -> str:
        "WHERE matching one natural key, with :key_<field> bind params."
        return " AND ".join(f"{self.columns[f]} = :key_{f}" for f in self.key)

    def key_of(self, payload: Dict[str, Any]) -> Tuple[Any, ...]:
        "The natural key tuple of a wire payload."
        missing = [f for f in self.key if payload.get(f) is None]
        if missing:
            raise SyncSchemaError(
                f"{self.name}: key field(s) {', '.join(missing)} missing or null"
            )
        return tuple(payload[f] for f in self.key)

    def validate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check a payload from a peer and return it narrowed to known fields.

        Unknown fields are dropped rather than rejected, so a device running a
        newer Lute can sync with an older one instead of failing outright.  A
        missing key field is fatal: without it the row cannot be addressed.
        """
        if not isinstance(payload, dict):
            raise SyncSchemaError(f"{self.name}: payload must be an object")
        self.key_of(payload)
        return {f: payload.get(f) for f in self.wire_fields if f in payload}


# ---------------------------------------------------------------------------
# Table specifications.
#
# Column expressions resolve every foreign key to a portable value.  The joins
# are LEFT where the parent may legitimately be absent mid-apply, so that a row
# read during a partial sync yields NULL rather than vanishing from the result.
# ---------------------------------------------------------------------------

LANGUAGES = TableSpec(
    name="languages",
    key=("name",),
    fields=(
        "character_substitutions",
        "regexp_split_sentences",
        "exceptions_split_sentences",
        "regexp_word_characters",
        "right_to_left",
        "show_romanization",
        "parser_type",
    ),
    columns={
        "name": "languages.LgName",
        "character_substitutions": "languages.LgCharacterSubstitutions",
        "regexp_split_sentences": "languages.LgRegexpSplitSentences",
        "exceptions_split_sentences": "languages.LgExceptionsSplitSentences",
        "regexp_word_characters": "languages.LgRegexpWordCharacters",
        "right_to_left": "languages.LgRightToLeft",
        "show_romanization": "languages.LgShowRomanization",
        "parser_type": "languages.LgParserType",
    },
    source="languages",
)

LANGUAGEDICTS = TableSpec(
    name="languagedicts",
    key=("lang", "use_for", "dict_uri"),
    fields=("dict_type", "is_active", "sort_order"),
    columns={
        "lang": "lang.LgName",
        "use_for": "languagedicts.LdUseFor",
        "dict_uri": "languagedicts.LdDictURI",
        "dict_type": "languagedicts.LdType",
        "is_active": "languagedicts.LdIsActive",
        "sort_order": "languagedicts.LdSortOrder",
    },
    source=(
        "languagedicts "
        "LEFT JOIN languages AS lang ON lang.LgID = languagedicts.LdLgID"
    ),
)

BOOKS = TableSpec(
    name="books",
    key=("uuid",),
    fields=(
        "lang",
        "title",
        "source_uri",
        "archived",
        "current_page",
        "audio_filename",
        "audio_current_pos",
        "audio_bookmarks",
    ),
    columns={
        "uuid": "books.BkUUID",
        "lang": "lang.LgName",
        "title": "books.BkTitle",
        "source_uri": "books.BkSourceURI",
        "archived": "books.BkArchived",
        # The reading position travels as a page number, not a TxID, because
        # the peer's TxIDs differ.  0 means "not started" and has no text.
        "current_page": (
            "(SELECT TxOrder FROM texts WHERE texts.TxID = books.BkCurrentTxID)"
        ),
        # Audio rows sync; the audio files themselves are deliberately not
        # stored remotely (they are the only asset large enough to matter).
        "audio_filename": "books.BkAudioFilename",
        "audio_current_pos": "books.BkAudioCurrentPos",
        "audio_bookmarks": "books.BkAudioBookmarks",
    },
    source="books LEFT JOIN languages AS lang ON lang.LgID = books.BkLgID",
)

TEXTS = TableSpec(
    name="texts",
    key=("book", "order"),
    fields=("text", "read_date", "word_count", "start_date"),
    columns={
        "book": "bk.BkUUID",
        "order": "texts.TxOrder",
        "text": "texts.TxText",
        "read_date": "texts.TxReadDate",
        "word_count": "texts.TxWordCount",
        "start_date": "texts.TxStartDate",
    },
    source="texts LEFT JOIN books AS bk ON bk.BkID = texts.TxBkID",
)

WORDS = TableSpec(
    name="words",
    key=("lang", "text_lc"),
    fields=(
        "text",
        "status",
        "translation",
        "romanization",
        "token_count",
        "created",
        "status_changed",
        "follow_parent",
    ),
    columns={
        "lang": "lang.LgName",
        "text_lc": "words.WoTextLC",
        "text": "words.WoText",
        "status": "words.WoStatus",
        "translation": "words.WoTranslation",
        "romanization": "words.WoRomanization",
        "token_count": "words.WoTokenCount",
        "created": "words.WoCreated",
        # The timestamp that decides which device's status edit wins.  Lute
        # maintains it by trigger; that trigger is suppressed during apply so
        # the origin value survives.  See trig_words.sql.
        "status_changed": "words.WoStatusChanged",
        # WoSyncStatus: "follow my parent's status".  Renamed on the wire
        # because "sync" here would read as device sync, which it is not.
        "follow_parent": "words.WoSyncStatus",
    },
    source="words LEFT JOIN languages AS lang ON lang.LgID = words.WoLgID",
)

WORDPARENTS = TableSpec(
    name="wordparents",
    key=("lang", "child", "parent"),
    fields=(),
    columns={
        "lang": "lang.LgName",
        "child": "child.WoTextLC",
        "parent": "parent.WoTextLC",
    },
    source=(
        "wordparents "
        "LEFT JOIN words AS child ON child.WoID = wordparents.WpWoID "
        "LEFT JOIN words AS parent ON parent.WoID = wordparents.WpParentWoID "
        "LEFT JOIN languages AS lang ON lang.LgID = child.WoLgID"
    ),
    mergeable=False,
)

TAGS = TableSpec(
    name="tags",
    key=("text",),
    fields=("comment",),
    columns={"text": "tags.TgText", "comment": "tags.TgComment"},
    source="tags",
)

TAGS2 = TableSpec(
    name="tags2",
    key=("text",),
    fields=("comment",),
    columns={"text": "tags2.T2Text", "comment": "tags2.T2Comment"},
    source="tags2",
)

WORDTAGS = TableSpec(
    name="wordtags",
    key=("lang", "text_lc", "tag"),
    fields=(),
    columns={
        "lang": "lang.LgName",
        "text_lc": "w.WoTextLC",
        "tag": "t.TgText",
    },
    source=(
        "wordtags "
        "LEFT JOIN words AS w ON w.WoID = wordtags.WtWoID "
        "LEFT JOIN languages AS lang ON lang.LgID = w.WoLgID "
        "LEFT JOIN tags AS t ON t.TgID = wordtags.WtTgID"
    ),
    mergeable=False,
)

BOOKTAGS = TableSpec(
    name="booktags",
    key=("book", "tag"),
    fields=(),
    columns={"book": "bk.BkUUID", "tag": "t.T2Text"},
    source=(
        "booktags "
        "LEFT JOIN books AS bk ON bk.BkID = booktags.BtBkID "
        "LEFT JOIN tags2 AS t ON t.T2ID = booktags.BtT2ID"
    ),
    mergeable=False,
)

WORDIMAGES = TableSpec(
    name="wordimages",
    key=("lang", "text_lc", "src"),
    fields=(),
    columns={
        "lang": "lang.LgName",
        "text_lc": "w.WoTextLC",
        "src": "wordimages.WiSource",
    },
    source=(
        "wordimages "
        "LEFT JOIN words AS w ON w.WoID = wordimages.WiWoID "
        "LEFT JOIN languages AS lang ON lang.LgID = w.WoLgID"
    ),
    mergeable=False,
)

TEXTBOOKMARKS = TableSpec(
    name="textbookmarks",
    key=("book", "order", "title"),
    fields=(),
    columns={
        "book": "bk.BkUUID",
        "order": "tx.TxOrder",
        "title": "textbookmarks.TbTitle",
    },
    source=(
        "textbookmarks "
        "LEFT JOIN texts AS tx ON tx.TxID = textbookmarks.TbTxID "
        "LEFT JOIN books AS bk ON bk.BkID = tx.TxBkID"
    ),
    mergeable=False,
)

BOOKSECTIONS = TableSpec(
    name="booksections",
    key=("book", "order"),
    fields=("title", "start_page", "token_count", "level"),
    columns={
        "book": "bk.BkUUID",
        "order": "booksections.BsOrder",
        "title": "booksections.BsTitle",
        "start_page": "booksections.BsStartPage",
        "token_count": "booksections.BsTokenCount",
        "level": "booksections.BsLevel",
    },
    source="booksections LEFT JOIN books AS bk ON bk.BkID = booksections.BsBkID",
)

BOOKPAIRS = TableSpec(
    name="bookpairs",
    key=("primary", "companion"),
    fields=("page_map",),
    columns={
        "primary": "pb.BkUUID",
        "companion": "cb.BkUUID",
        "page_map": "bookpairs.BpPageMap",
    },
    source=(
        "bookpairs "
        "LEFT JOIN books AS pb ON pb.BkID = bookpairs.BpPrimaryBkID "
        "LEFT JOIN books AS cb ON cb.BkID = bookpairs.BpCompanionBkID"
    ),
)

WORDSREAD = TableSpec(
    name="wordsread",
    # An append-only statistics log, scoped to the device that recorded it:
    # two devices reading on the same day produce two facts, not one contested
    # one, so the union is the correct merge and no conflict can arise.
    key=("device", "id"),
    fields=("lang", "book", "order", "read_date", "word_count"),
    columns={
        "device": "(SELECT SsValue FROM syncstate WHERE SsKey = 'device_id')",
        "id": "wordsread.WrID",
        "lang": "lang.LgName",
        "book": "bk.BkUUID",
        "order": "tx.TxOrder",
        "read_date": "wordsread.WrReadDate",
        "word_count": "wordsread.WrWordCount",
    },
    source=(
        "wordsread "
        "LEFT JOIN languages AS lang ON lang.LgID = wordsread.WrLgID "
        "LEFT JOIN texts AS tx ON tx.TxID = wordsread.WrTxID "
        "LEFT JOIN books AS bk ON bk.BkID = tx.TxBkID"
    ),
    mergeable=False,
)


# ---------------------------------------------------------------------------
# Registry.
#
# Order matters on apply: a row cannot be written before the rows its natural
# key refers to exist.  Languages precede everything that names one; books
# precede their texts and sections; tags precede their mappings.
#
# wordparents is the exception that ordering alone cannot solve - a parent term
# may arrive in the same batch as its child - so it is applied in a second pass
# once every word in the batch is present.
# ---------------------------------------------------------------------------

SYNC_ORDER: Tuple[TableSpec, ...] = (
    LANGUAGES,
    LANGUAGEDICTS,
    TAGS,
    TAGS2,
    BOOKS,
    BOOKSECTIONS,
    BOOKPAIRS,
    TEXTS,
    TEXTBOOKMARKS,
    BOOKTAGS,
    WORDS,
    WORDPARENTS,
    WORDTAGS,
    WORDIMAGES,
    WORDSREAD,
)

SPECS: Dict[str, TableSpec] = {s.name: s for s in SYNC_ORDER}

# Applied only after every word in a batch has been written.
SECOND_PASS = (WORDPARENTS.name,)

# Recomputed locally after an apply, never carried on the wire.
DERIVED_TABLES = ("sentences", "bookstats")


def spec_for(table: str) -> TableSpec:
    "The spec for a table name, or an error naming what is allowed."
    try:
        return SPECS[table]
    except KeyError:
        # An unknown table from a peer is refused rather than ignored: it
        # means the two sides disagree about what sync covers, and silently
        # dropping the rows would look like successful convergence.
        known = ", ".join(sorted(SPECS))
        raise SyncSchemaError(
            f"'{table}' is not a synced table (known: {known})"
        ) from None


def validate_batch(
    changes: Sequence[Dict[str, Any]]
) -> list:
    """
    Check a peer's change list, returning (spec, op, payload) triples.

    Each change is {"table":..., "op": "upsert"|"delete", "row": {...}}.  A
    delete carries only the natural key, since the row it names is already
    gone on the sending side.
    """
    out = []
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise SyncSchemaError(f"change {i}: must be an object")
        table = change.get("table")
        if not isinstance(table, str):
            raise SyncSchemaError(f"change {i}: 'table' missing")
        spec = spec_for(table)
        op = change.get("op")
        if op not in ("upsert", "delete"):
            raise SyncSchemaError(
                f"change {i} ({table}): op must be 'upsert' or 'delete', got {op!r}"
            )
        row = change.get("row")
        if not isinstance(row, dict):
            raise SyncSchemaError(f"change {i} ({table}): 'row' missing")
        payload = spec.validate(row)
        if op == "delete":
            payload = {f: payload[f] for f in spec.key}
        out.append((spec, op, payload))
    return out
