-- Vocab link, pulling: see lute/vocablink.  Local to the instance that runs
-- the link, like the tables in 20261031_create_vocablink_tables.sql.

-- Key/value state; holds the pull cursor (the vocab server's sequence number).
CREATE TABLE IF NOT EXISTS "vocablinkstate" (
       "VkKey" TEXT PRIMARY KEY,
       "VkValue" TEXT NOT NULL
);

-- Linked terms deleted in Lute.  Their link row goes with the term (ON DELETE
-- CASCADE), so the deletion is recorded here instead: without it the next
-- pull would recreate the term from its vocab word.
CREATE TABLE IF NOT EXISTS "vocablinkdeleted" (
       "VdLgID" INTEGER NOT NULL,
       "VdTextLC" TEXT NOT NULL,
       PRIMARY KEY ("VdLgID", "VdTextLC")
);

CREATE TRIGGER IF NOT EXISTS "trig_vocablink_term_deleted"
BEFORE DELETE ON "words"
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM "vocablink" WHERE "VlWoID" = OLD."WoID" AND "VlState" = 'linked')
BEGIN
    INSERT OR IGNORE INTO "vocablinkdeleted" ("VdLgID", "VdTextLC") VALUES (OLD."WoLgID", OLD."WoTextLC");
END;

CREATE INDEX IF NOT EXISTS "VlVocabID" ON "vocablink" ("VlVocabID");
