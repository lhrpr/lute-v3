-- Multi-device sync: stable book identity, a change log, and sync state.
--
-- Terminology note: Lute already uses "sync" for words.WoSyncStatus, which
-- means "this term follows its parent's status".  That is unrelated to
-- device sync.  Everything added here is prefixed Sl* (synclog) or Ss*
-- (syncstate) to keep the two apart.


-- ---------------------------------------------------------------------
-- Book identity.
--
-- Every other synced table has a natural key already enforced by a unique
-- index (words.WoTextLC+WoLgID, languages.LgName, tags.TgText, and so on),
-- so rows created independently on two devices can be matched on content.
-- Books have no such key: BkTitle is not unique, and duplicate
-- (BkTitle, BkLgID) pairs exist in real databases.  They get an explicit
-- opaque id instead.  Texts are then identified by (BkUUID, TxOrder).
--
-- Added NULL and backfilled because SQLite forbids ALTER TABLE ADD COLUMN
-- with a non-constant default.  New rows are populated by a trigger in
-- migrations_repeatable/trig_synclog.sql, and the unique index below keeps
-- collisions loud rather than silent.
-- ---------------------------------------------------------------------

ALTER TABLE "books" ADD COLUMN "BkUUID" TEXT NULL;

UPDATE "books"
SET "BkUUID" = lower(hex(randomblob(16)))
WHERE "BkUUID" IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS "BkUUID" ON "books" ("BkUUID");


-- ---------------------------------------------------------------------
-- Change log.
--
-- One row per local write to a synced table, appended by triggers.  SlID is
-- AUTOINCREMENT so it is strictly monotonic and never reused after a delete:
-- it doubles as the sync cursor, which means peers exchange "everything
-- after N" rather than comparing wall-clock times.  Clocks on two devices
-- disagree; a local counter cannot.
--
-- SlKey holds the natural key as JSON where the trigger can resolve it
-- without joining to a row that may already be gone, and the local integer
-- id otherwise.  The Python layer translates to the wire format at push
-- time; for upserts it re-reads the live row, so a stale SlKey is harmless.
--
-- SlChangedAt is for debugging and conflict tie-breaks only, never ordering.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS "synclog" (
       "SlID" INTEGER PRIMARY KEY AUTOINCREMENT,
       "SlTable" TEXT NOT NULL,
       "SlKey" TEXT NOT NULL,
       "SlOp" TEXT NOT NULL,
       "SlChangedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "SlTableKey" ON "synclog" ("SlTable", "SlKey");


-- ---------------------------------------------------------------------
-- Sync state.
--
-- Key/value, deliberately separate from `settings`: settings is user-facing
-- and partly device-local (mecab_path, backup_dir) and partly secret
-- (ai_api_key, deepl_api_key).  None of it should be confused with sync
-- bookkeeping, and sync bookkeeping must never be exported with settings.
--
-- Keys:
--   applying   '0' or '1'.  Set to '1' while remote changes are being
--              written locally.  Every synclog trigger checks it.
--
--              This is load-bearing, for two reasons.  Lute's existing
--              triggers mutate data: updating a parent term's status
--              rewrites its children's status, so applying one remote
--              change would generate fresh local changes, which would be
--              pushed back to the peer, which would apply and re-log them
--              again.  And trig_words_update_WoStatusChanged overwrites
--              WoStatusChanged with CURRENT_TIMESTAMP on any status update,
--              which would destroy the origin timestamp that last-write-
--              wins depends on.  Suppressing capture during apply breaks
--              the loop, and preserves convergence because the derived
--              updates are deterministic: the peer recomputes the same
--              values from the same trigger.
--
--   device_id  opaque id for this database, so a server can tell peers
--              apart and so a device never applies its own echo.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS "syncstate" (
       "SsKey" TEXT NOT NULL,
       "SsValue" TEXT NULL,
       PRIMARY KEY ("SsKey")
);

INSERT OR IGNORE INTO "syncstate" ("SsKey", "SsValue") VALUES ('applying', '0');

INSERT OR IGNORE INTO "syncstate" ("SsKey", "SsValue")
VALUES ('device_id', lower(hex(randomblob(16))));
