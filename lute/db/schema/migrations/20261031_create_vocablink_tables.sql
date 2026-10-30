-- Link to a vocab (anki-alternative) server: see lute/vocablink.
--
-- Local bookkeeping for the instance that runs the link (the cloud Lute).
-- None of these tables are carried by device sync (lute/sync/schema.py),
-- and they have no synclog triggers.

-- One row per term known to the vocab server.  VlBase holds the field
-- values both sides last agreed on (JSON), so each run can tell which side
-- changed a field.  VlVocabModified is the vocab word's modified_ms when it
-- was last seen.  VlState is 'linked', or 'deleted' once the word is gone
-- from vocab: such terms are never sent again.
CREATE TABLE IF NOT EXISTS "vocablink" (
       "VlWoID" INTEGER PRIMARY KEY REFERENCES "words" ("WoID") ON DELETE CASCADE,
       "VlVocabID" TEXT NOT NULL,
       "VlVocabModified" INTEGER NOT NULL,
       "VlBase" TEXT NOT NULL,
       "VlState" TEXT NOT NULL DEFAULT 'linked'
);

-- Read pages whose sentences have been sent as examples.
CREATE TABLE IF NOT EXISTS "vocablinkpages" (
       "VpTxID" INTEGER PRIMARY KEY REFERENCES "texts" ("TxID") ON DELETE CASCADE
);

-- (term, book) pairs that already have an example sentence in vocab, so a
-- word gets one sentence per book rather than one per occurrence.
CREATE TABLE IF NOT EXISTS "vocablinksources" (
       "VsWoID" INTEGER NOT NULL REFERENCES "words" ("WoID") ON DELETE CASCADE,
       "VsBkID" INTEGER NOT NULL REFERENCES "books" ("BkID") ON DELETE CASCADE,
       PRIMARY KEY ("VsWoID", "VsBkID")
);
