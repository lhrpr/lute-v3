"""
Splitting rendered TextItems into the units a cloze span is assembled from.

A "unit" is a contiguous run of TextItems that a card may include or exclude as
a whole -- either a whole sentence, or one clause of a sentence.  Working in
units (rather than raw character offsets) means every span we can produce is
by construction a verbatim slice of the book: there is no way to emit text the
author didn't write.  That property also applies to the LLM step, which picks
unit indices rather than writing text.

TextItems come from lute.read.render.service.Service.get_textitems, so all the
hard parts -- tokenizing, multi-word term overlaps, statuses -- are already
done.  Unknown words arrive as TextItems whose term has status 0.
"""

from lute.clozeexport.lexicons import CLAUSE_PUNCTUATION

zws = "\u200B"  # zero-width space
PARAGRAPH_MARKER = "¶"


def item_word_count(textitem):
    """
    Number of real words a TextItem puts on the card.

    Usually 1, but a displayed multi-word term ("a lot of") is one TextItem
    covering several word tokens, and the card's length budget is about words
    the reader sees, not about TextItems.
    """
    if not textitem.is_word:
        return 0
    parts = textitem.display_text.split(zws)
    return len([p for p in parts if p.strip() != ""])


def render_items(textitems, cloze_items=None, prefix="", suffix=""):
    """
    Join TextItems back into readable text.

    display_text (not text) is used because overlapping multi-word terms are
    resolved by the renderer into non-overlapping display slices; joining those
    reproduces the source exactly.  TextItems in cloze_items are wrapped in
    prefix/suffix, which is how the cloze deletion gets marked.
    """
    marked = {id(ti) for ti in (cloze_items or [])}
    out = []
    for ti in textitems:
        text = ti.display_text.replace(zws, "")
        if id(ti) in marked:
            text = f"{prefix}{text}{suffix}"
        out.append(text)
    return "".join(out).strip()


class Unit:
    """
    A candidate slice of text: one sentence, or one clause of one sentence.
    """

    def __init__(self, items, starts_sentence=True, ends_sentence=True):
        self.items = items
        # Whether this unit begins/ends at a real sentence boundary.  A span
        # that starts mid-sentence or stops before the full stop reads as a
        # fragment, which the scorer penalises.
        self.starts_sentence = starts_sentence
        self.ends_sentence = ends_sentence

    @property
    def word_items(self):
        "TextItems that are actual words (so, not spaces or punctuation)."
        return [ti for ti in self.items if ti.is_word]

    @property
    def word_count(self):
        "Number of words a reader would count in this unit."
        return sum(item_word_count(ti) for ti in self.items)

    @property
    def text(self):
        "Readable text of the unit."
        return render_items(self.items)

    def first_word_lc(self):
        "Lowercased first word of the unit, or '' if it has none."
        words = self.word_items
        if len(words) == 0:
            return ""
        return words[0].text_lc.replace(zws, " ").strip()

    def __repr__(self):
        return f'<Unit "{self.text[:40]}" ({self.word_count}w)>'


def paragraphs_of_sentences(textitems):
    """
    Group a page's TextItems into paragraphs of sentence Units.

    Paragraph breaks are hard boundaries for context expansion: a card should
    never straddle one, because the text on the other side is a different
    thought (and often a different speaker).
    """
    paragraphs = []
    current_paragraph = []
    current_sentence = []
    sentence_number = None

    def _flush_sentence():
        if len(current_sentence) > 0:
            current_paragraph.append(Unit(list(current_sentence)))
            current_sentence.clear()

    def _flush_paragraph():
        _flush_sentence()
        if len(current_paragraph) > 0:
            paragraphs.append(list(current_paragraph))
            current_paragraph.clear()

    for ti in textitems:
        if ti.text == PARAGRAPH_MARKER:
            _flush_paragraph()
            sentence_number = None
            continue
        if sentence_number is not None and ti.sentence_number != sentence_number:
            _flush_sentence()
        sentence_number = ti.sentence_number
        current_sentence.append(ti)

    _flush_paragraph()

    # Drop sentences with no words at all (stray whitespace runs).
    return [
        [u for u in para if len(u.word_items) > 0]
        for para in paragraphs
        if any(len(u.word_items) > 0 for u in para)
    ]


def _is_clause_break(textitem, conjunctions):
    "True if a clause boundary should be opened *before* this TextItem."
    if textitem.is_word:
        return textitem.text_lc.replace(zws, " ").strip() in conjunctions
    return textitem.text.strip() in CLAUSE_PUNCTUATION


def split_into_clauses(unit, conjunctions=None):
    """
    Split one sentence Unit into clause Units.

    Punctuation stays attached to the clause it closes ("I laughed," not
    "I laughed" + ","), so re-joining a run of clauses reproduces the original
    text.  Conjunctions instead open the next clause, since "but I laughed"
    reads better than "... but".

    Returns [unit] unchanged if there's nothing sensible to split on.
    """
    conjunctions = conjunctions or set()
    boundaries = []  # index at which a new clause starts
    for i, ti in enumerate(unit.items):
        if i == 0:
            continue
        if not _is_clause_break(ti, conjunctions):
            continue
        if ti.is_word:
            start = i
        else:
            # Punctuation closes the current clause: the next clause starts
            # after it (and after any following whitespace).
            start = i + 1
            while start < len(unit.items) and unit.items[start].text.strip() == "":
                start += 1
        if start < len(unit.items) and start not in boundaries:
            boundaries.append(start)

    if len(boundaries) == 0:
        return [unit]

    clauses = []
    starts = [0] + boundaries
    ends = boundaries + [len(unit.items)]
    for i, (s, e) in enumerate(zip(starts, ends)):
        items = unit.items[s:e]
        if len(items) == 0:
            continue
        clauses.append(
            Unit(
                items,
                starts_sentence=(i == 0 and unit.starts_sentence),
                ends_sentence=(e == len(unit.items) and unit.ends_sentence),
            )
        )

    # A split that leaves a wordless fragment isn't useful; keep the sentence.
    if any(len(c.word_items) == 0 for c in clauses):
        return [unit]
    return clauses
