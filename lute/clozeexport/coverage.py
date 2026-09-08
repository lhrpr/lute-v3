"""
Vocabulary coverage of a candidate span -- the "i+1" gate.

This is the cheap filter that runs before anything else and rejects the large
majority of candidates: a card is only useful if the reader can already read
everything on it except the thing being tested.

Statuses come straight off the rendered TextItems.  Note that unknown words are
*not* missing terms: lute.read.render.calculate_textitems manufactures a
status-0 Term for every word with no saved term, so every word TextItem has a
status.
"""

zws = "\u200B"  # zero-width space

STATUS_UNKNOWN = 0

# Defaults, overridable per run.  1-3 are the statuses a reader is actively
# drilling; 4 and 5 are all-but-learnt, 98 is "ignore this word", 99 is known.
DEFAULT_LEARNING_STATUSES = (1, 2, 3)
DEFAULT_KNOWN_STATUSES = (4, 5, 98, 99)


class Coverage:
    "Counts of what a reader would and wouldn't know in a span."

    def __init__(self, unknown, learning, known, total_words):
        self.unknown = unknown  # set of text_lc
        self.learning = learning  # set of text_lc
        self.known = known  # set of text_lc
        self.total_words = total_words

    @property
    def unknown_count(self):
        "Number of distinct unknown words."
        return len(self.unknown)

    @property
    def learning_count(self):
        "Number of distinct learning words, excluding the target."
        return len(self.learning)

    def __repr__(self):
        return (
            f"<Coverage {self.total_words}w "
            f"unknown={sorted(self.unknown)} learning={sorted(self.learning)}>"
        )


def _text_key(textitem):
    "Stable identity for a word, for de-duplicating repeated words in a span."
    return textitem.text_lc.replace(zws, " ").strip()


def summarize(
    textitems,
    exclude_term_ids=None,
    learning_statuses=DEFAULT_LEARNING_STATUSES,
    known_statuses=DEFAULT_KNOWN_STATUSES,
):
    """
    Bucket the words of a span by status.

    exclude_term_ids are the target term (and, when a card is built for a whole
    word family, its relatives): the point of the card is that the reader does
    *not* know them, so they mustn't count against the span's budget.

    Words are counted distinctly -- a sentence that repeats one unknown word
    four times is one unknown word's worth of difficulty, not four.  Any status
    that is neither known nor 0 counts as learning, so an unusual custom status
    makes a span harder rather than silently free.
    """
    exclude_term_ids = exclude_term_ids or set()
    learning_statuses = set(learning_statuses)
    known_statuses = set(known_statuses)

    unknown = set()
    learning = set()
    known = set()
    total_words = 0

    for ti in textitems:
        if not ti.is_word:
            continue
        total_words += 1
        term = ti.term
        if term is not None and term.id is not None and term.id in exclude_term_ids:
            continue
        status = ti.wo_status
        key = _text_key(ti)
        if status == STATUS_UNKNOWN or term is None:
            unknown.add(key)
        elif status in known_statuses:
            known.add(key)
        elif status in learning_statuses:
            learning.add(key)
        else:
            learning.add(key)

    return Coverage(unknown, learning, known, total_words)


def is_comprehensible(coverage, max_unknown=0, max_learning=1):
    "True if the span is within the reader's unknown/learning budget."
    return (
        coverage.unknown_count <= max_unknown
        and coverage.learning_count <= max_learning
    )
