-- parallel reading: book pairs and their structural sections

CREATE TABLE IF NOT EXISTS "booksections" (
       "BsID" INTEGER NOT NULL,
       "BsBkID" INTEGER NOT NULL,
       "BsOrder" INTEGER NOT NULL,
       "BsTitle" VARCHAR(200) NULL,
       "BsStartPage" INTEGER NOT NULL,
       "BsTokenCount" INTEGER NOT NULL DEFAULT '0',
       PRIMARY KEY ("BsID"),
       FOREIGN KEY("BsBkID") REFERENCES "books" ("BkID") ON UPDATE NO ACTION ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS "BsBkID" ON "booksections" ("BsBkID");

-- A book takes part in at most one pair, from either side: reading
-- either book shows the other, with the anchors inverted as needed.
CREATE TABLE IF NOT EXISTS "bookpairs" (
       "BpID" INTEGER NOT NULL,
       "BpPrimaryBkID" INTEGER NOT NULL UNIQUE,
       "BpCompanionBkID" INTEGER NOT NULL UNIQUE,
       "BpPageMap" TEXT NULL,
       PRIMARY KEY ("BpID"),
       FOREIGN KEY("BpPrimaryBkID") REFERENCES "books" ("BkID") ON UPDATE NO ACTION ON DELETE CASCADE,
       FOREIGN KEY("BpCompanionBkID") REFERENCES "books" ("BkID") ON UPDATE NO ACTION ON DELETE CASCADE
);
