# Lute ↔ anki-alternative integration plan

Learning words in Lute are kept in sync with the anki-alternative ("vocab")
spaced-repetition collection, in both directions, with the book each word was
met in recorded as a source.

## Topology

```
Lute desktop/iPad ──(Lute device sync)──► Lute cloud ──(vocablink job)──► vocab sync server ◄──(vocab sync)── vocab apps
```

The two **servers** are linked, not the desktop apps:

- Each app already converges its own devices on its server. Linking desktops
  pairwise would let one change arrive by two routes with different timestamps
  (echoes, spurious conflicts), and would need both apps open on one machine.
- The compute saved by linking desktops is negligible (a few hundred words a
  day; a full scan of ~15k Lute terms takes milliseconds).

**Lute is the client.** The mapping (statuses, parents, books, sentences)
needs Lute's schema and parser, so it lives in Python in Lute
(`lute/vocablink/`, run only on the cloud instance). The vocab server exposes a
generic external API (`/v1/ext/...`) that Voracious can reuse.

Dependency: changes made on desktop Lute reach vocab only once Lute device sync
to the cloud works (`lute/sync`, in progress). Reading on the cloud instance
works without it.

## Vocab server API (`/v1/ext`)

- Auth: a per-integration bearer token (`EXT_TOKEN1=<user>:<token>`), separate
  from the sync password, so it can be revoked without signing devices out.
- `GET /v1/ext/words?since=<usn>&limit=N`: words changed since a cursor, with
  their sources. Returns the next cursor. Unsynced device changes appear once
  the device syncs.
- `POST /v1/ext/words`: batch upsert, matched by `(language, term)` (unique,
  case-insensitive). Status is set via `set_word_status` (schedule untouched).
  Optional `source` (`external_ref` + title) and `sentence` add a context.
- Writes go through `Collection` and stamp the server's usn, so devices pull
  them on their next sync.

## Lute side (`lute/vocablink`)

- Link table `vocablink(WoID, vocab_id, snapshot_json, state)`, **not** synced
  between Lute devices. `state` includes `tombstone` (deleted on one side;
  never recreated).
- A job every few minutes plus a "Sync now" button: diff Lute terms against
  snapshots → pull vocab changes since the cursor → three-way merge → push
  both ways → save snapshots. Snapshot diffing, not the device-sync synclog,
  keeps this independent of `lute/sync`.
- Lute terms are created through `Term`/repository code with the language's
  parser (multi-word terms need zero-width joiners and a token count; Japanese
  needs MeCab). Raw inserts would never match while reading.

## Which words sync

- Status **1–5** on either side → created on the other. Linked words then sync
  every status change, including I/K/98/99.
- **Unrated (vocab) ↔ status 0 (Lute)** never create a word on the other side;
  a vocab word is created in Lute after its first review. A first-ever rating
  of I/K (or Lute 98/99) does not create either.
- Vocab words with no language, or a language missing from the language map
  (Lute setting, e.g. `Spanish → es`), are skipped and listed in the log.
- Deletes don't propagate; the link becomes a tombstone.

## Field mapping

| Lute | vocab | Notes |
|---|---|---|
| `WoText` + `LgName` | `term` + `language` | language map |
| `WoStatus` 0 / 1–5 / 98 / 99 | `status` none / 1–5 / I / K | `Rating::{to,from}_lute_status` |
| `WoTranslation` (500) | `translation` | |
| `WoRomanization` (100) | `reading` | |
| book (`BkUUID`) | source `external_ref = "lute:<BkUUID>"` + context sentence | added on page read |
| `TermTag` (20) | `tags` | optional, later |
| images | `image` | later |
| parents | `word_parents` (new) | later |
| — | `notes`, `level`, decks, cards | vocab only |

- Lute's length limits: an over-long vocab value is not truncated; that field is
  skipped and logged.
- Renames: Lute term text can't change, so a vocab rename cuts the link.
- The mapping is one table-driven spec; requests carry a version, unknown
  fields are ignored.

## Book sources

Added when a page is **marked as read**: Lute has already parsed the page, so
the learning terms on it and their sentences are free. Books imported but
never read don't tag anything.

## Conflicts

- Three-way merge per field against the snapshot. One side changed → it wins.
  This also suppresses echoes (the job's own writes match the snapshot).
- Both changed status → later wins (`WoStatusChanged` vs the vocab word's
  last change).
- Both changed text → vocab wins; the other value goes in a conflict log.
- Lute's parent-status triggers are wanted here: children that follow a parent
  appear in the next diff and are pushed. No apply-guard needed.

## Parents (later)

Vocab gets a many-to-many `word_parents` table (synced, with graves), MCP
tools (`set_word_parents`, `parent:` search) and the parent on the card back.
Pairs sync both ways, so MCP cleanup flows back to Lute's `wordparents`.
`WoSyncStatus` stays Lute-only.

## Phases

1. ✅ Vocab: `/v1/ext` endpoints (changes, upsert, read-only lookup), token
   auth, tests. anki-alternative branch `lute-ext-api`.
2. ✅ Lute: `lute/vocablink` — link tables, mapping spec, push of learning
   terms and status changes, one sentence per term per book from read pages.
   Runs in the background (`LUTE_VOCAB_INTERVAL`), via
   `POST /vocablink/sync`, or `flask cli vocab_sync`; `GET /vocablink/status`
   shows the last run.
3. ✅ Pull from vocab: each run first applies words changed in vocab since
   the cursor (`vocablinkstate.pull_usn`): linked terms take the fields
   vocab wins, and vocab learning words become Lute terms (through the term
   Repository, so they're parsed like any term) or link to existing ones.
   Terms deleted in Lute are remembered (`vocablinkdeleted`, filled by a
   trigger) so a pull doesn't recreate them. Not done: a conflict log page;
   counts and errors are in `GET /vocablink/status` for now.
4. Parents on both sides, MCP tools.
5. Images and tags.

## How a run works

1. **Pull**: words changed in vocab since the cursor are merged three-way
   against `VlBase`; what vocab wins is written into Lute. Unlinked vocab
   learning words are linked to the Lute term with the same text, or
   create one. Fields Lute wins keep vocab's value as base, so step 2 sees
   them as Lute's changes.
2. **Push**: terms at status 1–5, plus linked terms whose fields differ from
   `VlBase`, are looked up in vocab (`/v1/ext/lookup`) and merged; fields
   Lute wins are sent with `expect_modified_ms` (a word edited in vocab
   meanwhile comes back as a conflict and is retried next run), and fields
   vocab wins are written into Lute straight away.
3. **Sentences** from newly read pages.

Merge rules: a side that changed since base wins. If both changed, or the
word is being linked for the first time, a status goes to the later change,
except that at first link a missing status (unrated / 0) never beats a set
one; text goes to vocab unless vocab's is empty. Values too long for Lute's
columns don't cross.

Sentences: read pages not yet in `vocablinkpages` (200 per run) are matched
against linked learning terms using `sentences.SeTextLC` (`'*'` means
`LOWER(SeText)`), no re-parsing. One sentence per (term, book), tracked in
`vocablinksources`; the source is `lute:<BkUUID>`, the location `p. <n>`.

Tried against copies of the real collections (2026-10-31), both directions:
the first run created 935 vocab words and 547 Lute terms, updated 36 Lute
terms and 15 vocab words, and sent 842 sentences from 59 pages in 9 s; the
second run changed nothing on either side.

At first link, a vocab status set more recently than Lute's wins, which
demoted 9 of Lute's well-known (99) terms to 3–4 in that trial (e.g.
"buscar", "aunque") because vocab was still reviewing them.

## Known data mismatches

- Most vocab Spanish terms include the article ("la manzana"); Lute terms
  don't ("manzana"). They don't match, so both exist in vocab. Parent links
  (Phase 4) or an article-stripping match rule could join them.
- Vocab has one word with language `es` beside 1,299 `Spanish`.
- Many Lute learning terms have no translation, so their new vocab cards
  have an empty meaning side.
- Pulling creates multiword Lute terms for article-plus-noun vocab words
  ("la manzana"); they highlight as phrases while reading, and the bare
  noun stays unknown.
