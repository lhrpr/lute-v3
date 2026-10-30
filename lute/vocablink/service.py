"""
One run of the link: send Lute's learning terms to vocab, then the sentences
they were met in on pages that have been read.

Only pushes for now.  Changes made in vocab are detected (the merge knows
when vocab's value should win) but not yet written into Lute; see
docs/vocab-integration-plan.md.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sqltext

from lute.models.book import Text
from lute.vocablink.client import VocabClient
from lute.vocablink.config import VocabConfig
from lute.vocablink.mapping import (
    LEARNING,
    ZWS,
    lute_timestamp_ms,
    lute_values,
    merge,
    split_base,
    term_text,
    vocab_values,
)

# Read pages handled per run, so the first run over a long reading history
# is spread out rather than one long transaction.
PAGES_PER_RUN = 200


@dataclass
class RunSummary:
    "What a run did."

    created: int = 0
    updated: int = 0
    linked: int = 0  # matched an existing vocab word, nothing to send
    conflicts: int = 0  # changed in vocab meanwhile; retried next run
    deleted: int = 0  # gone from vocab; not sent again
    sentences: int = 0
    pages: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self):
        "For JSON."
        return dict(self.__dict__)


@dataclass
class _Pending:
    "A term to reconcile."

    woid: int
    language: str  # vocab's
    term: str
    lute: Dict[str, Any]
    base: Optional[Dict[str, Any]]
    status_ms: int


class Service:
    "Runs the link against one vocab server."

    def __init__(self, session, config: VocabConfig, client: VocabClient = None):
        self.session = session
        self.config = config
        self.client = client or VocabClient(config.url, config.token)

    def _exec(self, sql, params=None):
        return self.session.execute(sqltext(sql), params or {})

    def _languages(self) -> Dict[int, str]:
        "LgID -> vocab language, for the linked languages that exist here."
        rows = self._exec("SELECT LgID, LgName FROM languages").fetchall()
        return {
            lgid: self.config.languages[name]
            for lgid, name in rows
            if name in self.config.languages
        }

    def run(self) -> RunSummary:
        "Push terms, then sentences."
        summary = RunSummary()
        languages = self._languages()
        if languages:
            self.push_terms(languages, summary)
            self.push_sentences(languages, summary)
        return summary

    # ---- Terms ----

    def _pending_terms(self, languages) -> List[_Pending]:
        "Learning terms, and linked terms that changed since the last run."
        ids = ",".join(str(i) for i in languages)
        rows = self._exec(
            f"""
            SELECT WoID, WoLgID, WoText, WoRomanization, WoTranslation, WoStatus,
                   WoStatusChanged, VlBase, VlState
            FROM words LEFT JOIN vocablink ON VlWoID = WoID
            WHERE WoLgID IN ({ids})
              AND (VlWoID IS NOT NULL OR WoStatus BETWEEN {LEARNING[0]} AND {LEARNING[-1]})
            """
        ).fetchall()
        ret = []
        for woid, lgid, wotext, reading, translation, status, changed, base, state in rows:
            if state == "deleted":
                continue
            lute = lute_values(reading, translation, status)
            base = None if base is None else json.loads(base)
            if base == lute:
                continue
            ret.append(
                _Pending(
                    woid,
                    languages[lgid],
                    term_text(wotext),
                    lute,
                    base,
                    lute_timestamp_ms(changed),
                )
            )
        return ret

    def _save_link(self, woid, word, base, state="linked"):
        self._exec(
            """
            INSERT OR REPLACE INTO vocablink (VlWoID, VlVocabID, VlVocabModified, VlBase, VlState)
            VALUES (:woid, :vid, :vmod, :base, :state)
            """,
            {
                "woid": woid,
                "vid": word["id"],
                "vmod": word["modified_ms"],
                "base": json.dumps(base, sort_keys=True),
                "state": state,
            },
        )

    def push_terms(self, languages, summary: RunSummary):
        "Create or update vocab words for terms that changed in Lute."
        pending = self._pending_terms(languages)
        if not pending:
            return
        found = self.client.lookup(
            [{"language": p.language, "term": p.term} for p in pending]
        )

        sends: List[Tuple[_Pending, Dict[str, Any], Dict[str, Any]]] = []
        for p, word in zip(pending, found):
            if word is None and p.base is not None:
                # Deleted in vocab: keep it deleted.
                self._exec(
                    "UPDATE vocablink SET VlState = 'deleted' WHERE VlWoID = :woid",
                    {"woid": p.woid},
                )
                summary.deleted += 1
                continue
            vocab = None if word is None else vocab_values(word)
            vocab_ms = 0 if word is None else word["modified_ms"]
            m = merge(p.base, p.lute, vocab, p.status_ms, vocab_ms)
            base = split_base(m, p.lute, apply_to_lute=False)
            item = {"language": p.language, "term": p.term, **m.to_vocab}
            if word is None:
                item["deck"] = self.config.deck_for(p.language)
            elif m.to_vocab:
                item["expect_modified_ms"] = vocab_ms
            else:
                self._save_link(p.woid, word, base)
                summary.linked += 1
                continue
            sends.append((p, item, base))

        results = self.client.upsert([item for _, item, _ in sends]) if sends else []
        for (p, _, base), res in zip(sends, results):
            outcome = res["outcome"]
            if outcome in ("created", "updated", "unchanged"):
                self._save_link(p.woid, res["word"], base)
                if outcome == "created":
                    summary.created += 1
                else:
                    summary.updated += 1
            elif outcome == "conflict":
                summary.conflicts += 1
            else:
                summary.errors.append(f"{p.term}: {res.get('error')}")
        self.session.commit()

    # ---- Sentences ----

    def _unread_pages(self, languages):
        ids = ",".join(str(i) for i in languages)
        return self._exec(
            f"""
            SELECT TxID, TxOrder, BkID, BkTitle, BkUUID, BkLgID
            FROM texts
            INNER JOIN books ON BkID = TxBkID
            LEFT JOIN vocablinkpages ON VpTxID = TxID
            WHERE TxReadDate IS NOT NULL AND VpTxID IS NULL AND BkLgID IN ({ids})
            ORDER BY TxReadDate
            LIMIT {PAGES_PER_RUN}
            """
        ).fetchall()

    def _learning_terms(self, lgid):
        "(single-token terms by text_lc, multi-token terms) linked and being learned."
        rows = self._exec(
            f"""
            SELECT WoID, WoText, WoTextLC, WoTokenCount
            FROM words INNER JOIN vocablink ON VlWoID = WoID
            WHERE VlState = 'linked' AND WoLgID = :lgid
              AND WoStatus BETWEEN {LEARNING[0]} AND {LEARNING[-1]}
            """,
            {"lgid": lgid},
        ).fetchall()
        single = {lc: (woid, term_text(t)) for woid, t, lc, n in rows if n == 1}
        multi = [(woid, term_text(t), lc) for woid, t, lc, n in rows if n > 1]
        return single, multi

    def _sentences(self, txid):
        "SeText and its lowercase form for a page, parsing it first if it never was."
        # SeTextLC is '*' when SQLite's LOWER() gives the parser's result; see
        # Sentence.set_lowercase_text.
        sql = """
            SELECT SeText,
                   CASE WHEN SeTextLC IS NULL OR SeTextLC = '*' THEN LOWER(SeText) ELSE SeTextLC END
            FROM sentences WHERE SeTxID = :t ORDER BY SeOrder
        """
        rows = self._exec(sql, {"t": txid}).fetchall()
        if not rows:
            page = self.session.get(Text, txid)
            page.load_sentences()
            self.session.add(page)
            self.session.commit()
            rows = self._exec(sql, {"t": txid}).fetchall()
        return rows

    def push_sentences(self, languages, summary: RunSummary):
        """
        For each newly read page, send the first sentence each learning term
        appears in, with the book as its source: one sentence per term per book.
        """
        pages = self._unread_pages(languages)
        if not pages:
            return
        done = {
            (w, b)
            for w, b in self._exec("SELECT VsWoID, VsBkID FROM vocablinksources")
        }
        terms_by_lang = {}
        items, keys = [], []
        for txid, order, bkid, title, uuid, lgid in pages:
            if lgid not in terms_by_lang:
                terms_by_lang[lgid] = self._learning_terms(lgid)
            single, multi = terms_by_lang[lgid]
            for setext, setextlc in self._sentences(txid):
                sentence = (setext or "").replace(ZWS, "").replace("¶", "").strip()
                if sentence == "":
                    continue
                lc = setextlc or ""
                hits = [single[tok] for tok in lc.split(ZWS) if tok in single]
                hits += [(w, t) for w, t, tlc in multi if f"{ZWS}{tlc}{ZWS}" in lc]
                for woid, term in hits:
                    if (woid, bkid) in done:
                        continue
                    done.add((woid, bkid))
                    keys.append((woid, bkid))
                    items.append(
                        {
                            "language": languages[lgid],
                            "term": term,
                            "context": {
                                "sentence": sentence,
                                "location": f"p. {order}",
                                "source": {
                                    "external_ref": f"lute:{uuid}",
                                    "title": title,
                                    "kind": "book",
                                },
                            },
                        }
                    )

        results = self.client.upsert(items) if items else []
        for (woid, bkid), res in zip(keys, results):
            if res["outcome"] == "error":
                summary.errors.append(f"sentence for word {woid}: {res.get('error')}")
                continue
            self._exec(
                "INSERT OR IGNORE INTO vocablinksources (VsWoID, VsBkID) VALUES (:w, :b)",
                {"w": woid, "b": bkid},
            )
            summary.sentences += 1
        for page in pages:
            self._exec(
                "INSERT OR IGNORE INTO vocablinkpages (VpTxID) VALUES (:t)", {"t": page[0]}
            )
        summary.pages += len(pages)
        self.session.commit()
