-- The vocab senses of a linked term, as last seen (JSON list of {cue, pos,
-- translation, status}), when the vocab entry has several.  Their
-- translations don't sync with the term's; Lute shows them read-only so
-- it's clear why the two differ.  Empty when the entry has one sense.
ALTER TABLE vocablink ADD COLUMN VlSenses TEXT NOT NULL DEFAULT '';
