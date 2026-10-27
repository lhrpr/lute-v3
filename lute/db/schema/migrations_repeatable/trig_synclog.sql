-- Change capture for multi-device sync.
--
-- Triggers rather than SQLAlchemy events, for three reasons:
--   * Nearly every foreign key here is ON DELETE CASCADE.  Deleting a
--     language removes its books and words inside SQLite, where ORM hooks
--     never see it.  A missed delete is the bug that resurrects data on the
--     next pull.  (Verified: cascade deletes do fire these triggers, at any
--     depth, regardless of the recursive_triggers pragma.)
--   * ~14 sites write raw SQL and bypass the ORM entirely.
--   * The capture then holds for anything that opens the file, including
--     migrations and the CLI.
--
-- Every trigger is guarded by syncstate.applying.  See the comment in
-- migrations/20260918_add_sync_support.sql for why that guard is required
-- and not merely an optimisation.
--
-- SlKey is the natural key as JSON, resolved here because a tombstone has to
-- survive the row it describes.  Subqueries return NULL when the parent is
-- already gone (a cascade deletes the parent first); the Python layer drops
-- such tombstones, because the parent's own tombstone already implies them.
--
-- NOT captured, deliberately:
--   sentences, bookstats  - derived from texts; recomputed locally.  Syncing
--                           them would invent conflicts out of nothing.
--   settings              - device-local (mecab_path, backup_dir) and secret
--                           (ai_api_key, deepl_api_key).  Default-deny.
--   synclog, syncstate    - bookkeeping.
--
-- Known limitation: renaming a language or a tag changes its natural key, so
-- a rename reads as delete-then-create on the peer.  Terms survive (they are
-- re-linked by name), but the rename is not carried across as a rename.


-- ---------------------------------------------------------------------
-- Book identity for new rows.
--
-- Not a column default: SQLite forbids a non-constant default on ADD COLUMN.
-- Unguarded by `applying` on purpose - a book arriving from a peer already
-- carries its uuid, and this only fills the gap when one is absent.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_books_after_insert_set_BkUUID;

CREATE TRIGGER trig_books_after_insert_set_BkUUID
-- created by db/schema/migrations_repeatable/trig_synclog.sql
AFTER INSERT ON books
FOR EACH ROW
WHEN new.BkUUID IS NULL
BEGIN
    UPDATE books SET BkUUID = lower(hex(randomblob(16))) WHERE BkID = new.BkID;
END;


-- ---------------------------------------------------------------------
-- languages
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_languages_synclog_upsert;

CREATE TRIGGER trig_languages_synclog_upsert
AFTER INSERT ON languages FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('languages', json_object('name', new.LgName), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_languages_synclog_update;

CREATE TRIGGER trig_languages_synclog_update
AFTER UPDATE ON languages FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('languages', json_object('name', new.LgName), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_languages_synclog_delete;

CREATE TRIGGER trig_languages_synclog_delete
AFTER DELETE ON languages FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('languages', json_object('name', old.LgName), 'delete');
END;


-- ---------------------------------------------------------------------
-- books
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_books_synclog_insert;

CREATE TRIGGER trig_books_synclog_insert
AFTER INSERT ON books FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('books', json_object('uuid',
        (SELECT BkUUID FROM books WHERE BkID = new.BkID)), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_books_synclog_update;

CREATE TRIGGER trig_books_synclog_update
AFTER UPDATE ON books FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('books', json_object('uuid', new.BkUUID), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_books_synclog_delete;

CREATE TRIGGER trig_books_synclog_delete
AFTER DELETE ON books FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('books', json_object('uuid', old.BkUUID), 'delete');
END;


-- ---------------------------------------------------------------------
-- texts: identified by (book uuid, TxOrder).
-- Verified unique over real data; no index enforces it, so treat a
-- duplicate as a bug rather than a merge case.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_texts_synclog_insert;

CREATE TRIGGER trig_texts_synclog_insert
AFTER INSERT ON texts FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('texts', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID = new.TxBkID),
        'order', new.TxOrder), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_texts_synclog_update;

CREATE TRIGGER trig_texts_synclog_update
AFTER UPDATE ON texts FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('texts', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID = new.TxBkID),
        'order', new.TxOrder), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_texts_synclog_delete;

CREATE TRIGGER trig_texts_synclog_delete
AFTER DELETE ON texts FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('texts', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID = old.TxBkID),
        'order', old.TxOrder), 'delete');
END;


-- ---------------------------------------------------------------------
-- words: identified by (language name, WoTextLC), per UNIQUE INDEX
-- WoTextLCLgID with the language id mapped to its portable name.
--
-- AFTER UPDATE is unfiltered by column on purpose: WoSyncStatus and
-- WoStatusChanged are rewritten by Lute's own triggers in trig_words.sql,
-- and those edits matter to a peer too.  During apply they are suppressed
-- by the guard, which is what stops the parent/child status cascade from
-- echoing back and forth between devices.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_words_synclog_insert;

CREATE TRIGGER trig_words_synclog_insert
AFTER INSERT ON words FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('words', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID = new.WoLgID),
        'text_lc', new.WoTextLC), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_words_synclog_update;

CREATE TRIGGER trig_words_synclog_update
AFTER UPDATE ON words FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('words', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID = new.WoLgID),
        'text_lc', new.WoTextLC), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_words_synclog_delete;

CREATE TRIGGER trig_words_synclog_delete
AFTER DELETE ON words FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('words', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID = old.WoLgID),
        'text_lc', old.WoTextLC), 'delete');
END;


-- ---------------------------------------------------------------------
-- wordparents: insert/delete only - a pure mapping table with no columns
-- of its own to update.  Both terms are always in the same language.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_wordparents_synclog_insert;

CREATE TRIGGER trig_wordparents_synclog_insert
AFTER INSERT ON wordparents FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordparents', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = new.WpWoID)),
        'child', (SELECT WoTextLC FROM words WHERE WoID = new.WpWoID),
        'parent', (SELECT WoTextLC FROM words WHERE WoID = new.WpParentWoID)
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_wordparents_synclog_delete;

CREATE TRIGGER trig_wordparents_synclog_delete
AFTER DELETE ON wordparents FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordparents', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = old.WpWoID)),
        'child', (SELECT WoTextLC FROM words WHERE WoID = old.WpWoID),
        'parent', (SELECT WoTextLC FROM words WHERE WoID = old.WpParentWoID)
    ), 'delete');
END;


-- ---------------------------------------------------------------------
-- tags (term tags) and tags2 (book tags)
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_tags_synclog_insert;

CREATE TRIGGER trig_tags_synclog_insert
AFTER INSERT ON tags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags', json_object('text', new.TgText), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_tags_synclog_update;

CREATE TRIGGER trig_tags_synclog_update
AFTER UPDATE ON tags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags', json_object('text', new.TgText), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_tags_synclog_delete;

CREATE TRIGGER trig_tags_synclog_delete
AFTER DELETE ON tags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags', json_object('text', old.TgText), 'delete');
END;

DROP TRIGGER IF EXISTS trig_tags2_synclog_insert;

CREATE TRIGGER trig_tags2_synclog_insert
AFTER INSERT ON tags2 FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags2', json_object('text', new.T2Text), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_tags2_synclog_update;

CREATE TRIGGER trig_tags2_synclog_update
AFTER UPDATE ON tags2 FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags2', json_object('text', new.T2Text), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_tags2_synclog_delete;

CREATE TRIGGER trig_tags2_synclog_delete
AFTER DELETE ON tags2 FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('tags2', json_object('text', old.T2Text), 'delete');
END;


-- ---------------------------------------------------------------------
-- wordtags, booktags: mapping tables, insert/delete only.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_wordtags_synclog_insert;

CREATE TRIGGER trig_wordtags_synclog_insert
AFTER INSERT ON wordtags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordtags', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = new.WtWoID)),
        'text_lc', (SELECT WoTextLC FROM words WHERE WoID = new.WtWoID),
        'tag', (SELECT TgText FROM tags WHERE TgID = new.WtTgID)
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_wordtags_synclog_delete;

CREATE TRIGGER trig_wordtags_synclog_delete
AFTER DELETE ON wordtags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordtags', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = old.WtWoID)),
        'text_lc', (SELECT WoTextLC FROM words WHERE WoID = old.WtWoID),
        'tag', (SELECT TgText FROM tags WHERE TgID = old.WtTgID)
    ), 'delete');
END;

DROP TRIGGER IF EXISTS trig_booktags_synclog_insert;

CREATE TRIGGER trig_booktags_synclog_insert
AFTER INSERT ON booktags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('booktags', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID = new.BtBkID),
        'tag', (SELECT T2Text FROM tags2 WHERE T2ID = new.BtT2ID)
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_booktags_synclog_delete;

CREATE TRIGGER trig_booktags_synclog_delete
AFTER DELETE ON booktags FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('booktags', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID = old.BtBkID),
        'tag', (SELECT T2Text FROM tags2 WHERE T2ID = old.BtT2ID)
    ), 'delete');
END;


-- ---------------------------------------------------------------------
-- wordimages: the row syncs here, the file itself is deferred to the R2
-- work.  A row whose file has not arrived degrades to a missing image.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_wordimages_synclog_insert;

CREATE TRIGGER trig_wordimages_synclog_insert
AFTER INSERT ON wordimages FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordimages', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = new.WiWoID)),
        'text_lc', (SELECT WoTextLC FROM words WHERE WoID = new.WiWoID),
        'src', new.WiSource
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_wordimages_synclog_delete;

CREATE TRIGGER trig_wordimages_synclog_delete
AFTER DELETE ON wordimages FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordimages', json_object(
        'lang', (SELECT LgName FROM languages WHERE LgID =
                    (SELECT WoLgID FROM words WHERE WoID = old.WiWoID)),
        'text_lc', (SELECT WoTextLC FROM words WHERE WoID = old.WiWoID),
        'src', old.WiSource
    ), 'delete');
END;


-- ---------------------------------------------------------------------
-- textbookmarks
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_textbookmarks_synclog_insert;

CREATE TRIGGER trig_textbookmarks_synclog_insert
AFTER INSERT ON textbookmarks FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('textbookmarks', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID =
                    (SELECT TxBkID FROM texts WHERE TxID = new.TbTxID)),
        'order', (SELECT TxOrder FROM texts WHERE TxID = new.TbTxID),
        'title', new.TbTitle
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_textbookmarks_synclog_update;

CREATE TRIGGER trig_textbookmarks_synclog_update
AFTER UPDATE ON textbookmarks FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('textbookmarks', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID =
                    (SELECT TxBkID FROM texts WHERE TxID = new.TbTxID)),
        'order', (SELECT TxOrder FROM texts WHERE TxID = new.TbTxID),
        'title', new.TbTitle
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_textbookmarks_synclog_delete;

CREATE TRIGGER trig_textbookmarks_synclog_delete
AFTER DELETE ON textbookmarks FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('textbookmarks', json_object(
        'book', (SELECT BkUUID FROM books WHERE BkID =
                    (SELECT TxBkID FROM texts WHERE TxID = old.TbTxID)),
        'order', (SELECT TxOrder FROM texts WHERE TxID = old.TbTxID),
        'title', old.TbTitle
    ), 'delete');
END;


-- ---------------------------------------------------------------------
-- wordsread: an append-only reading-statistics log, never updated.  Rows
-- are scoped by the originating device so two devices reading on the same
-- day produce two facts rather than one contested one - the union is the
-- correct merge, and there is no conflict to resolve.
-- ---------------------------------------------------------------------

DROP TRIGGER IF EXISTS trig_wordsread_synclog_insert;

CREATE TRIGGER trig_wordsread_synclog_insert
AFTER INSERT ON wordsread FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordsread', json_object(
        'device', (SELECT SsValue FROM syncstate WHERE SsKey = 'device_id'),
        'id', new.WrID
    ), 'upsert');
END;

DROP TRIGGER IF EXISTS trig_wordsread_synclog_delete;

CREATE TRIGGER trig_wordsread_synclog_delete
AFTER DELETE ON wordsread FOR EACH ROW
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    INSERT INTO synclog (SlTable, SlKey, SlOp)
    VALUES ('wordsread', json_object(
        'device', (SELECT SsValue FROM syncstate WHERE SsKey = 'device_id'),
        'id', old.WrID
    ), 'delete');
END;
