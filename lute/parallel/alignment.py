"""
Aligning the section structures of two parallel books.

Two editions of the same work rarely have the same section list: one may
carry a foreword, an appendix, or per-volume title pages that the other
lacks.  Sections are therefore aligned with Needleman-Wunsch, which is
order-preserving and treats gaps as first-class, so an extra section on
one side costs a single gap penalty instead of shifting every section
after it out of alignment.

Scoring is deliberately language-agnostic.  No word can be compared
directly across a Russian/English pair, but chapter ordinals are spelled
the same in both ("XVIII"), and relative section lengths survive the
systematic expansion or contraction of a translation.
"""

# Chapter numbers stay well under this in practice.  The cap also
# rejects incidental all-caps words that are valid numerals: "MIX"
# would otherwise read as 1009.
MAX_ORDINAL = 200

# An ordinal identifies a section outright, so agreement outweighs any
# amount of length evidence, and disagreement outweighs it just as
# hard.  A gap must stay cheaper than the mismatch it avoids -- that
# ratio is what makes a foreword on one side cost one gap rather than
# dragging the remaining chapters off by one.
ORDINAL_MATCH_BONUS = 3.0
ORDINAL_MISMATCH_PENALTY = -3.0
GAP_PENALTY = -1.0

# Same number, different numeral style: "III. The Sonora Desert" against
# "3 de enero".  Still a match -- an edition may renumber roman parts in
# arabic -- but it must lose to a same-style rival, or a part marker gets
# claimed by a date that happens to share its number.
ORDINAL_CROSS_STYLE_BONUS = 2.0

# Where a section sits in its own book.  Without this an aligner facing
# many sections on one side and few on the other can place the few
# almost anywhere, since the gap cost is the same wherever they land.
POSITION_WEIGHT = 1.5
POSITION_TOLERANCE = 0.10

# Nesting depth: a part should match a part, not one of its own
# chapters.  Weaker than an ordinal, because a depth only says what kind
# of thing a section is, never which one -- but it is often the only
# structural signal an unnumbered book has.
DEPTH_MATCH_BONUS = 1.0
DEPTH_MISMATCH_PENALTY = -1.0

# Sections are compared as a fraction of their own book's length, which
# absorbs the ~1.1-1.2x word count change of a Russian->English
# translation.  Length agreement falls to zero at this difference.
LENGTH_WEIGHT = 1.0
LENGTH_TOLERANCE = 0.02

# Below this, a proposed pair is shown to the reader but not committed.
MIN_ANCHOR_CONFIDENCE = 0.7

# An anchor landing further than this fraction of the companion book
# from where simple proportion would put it is a coincidence, not a
# correspondence.  Roman part numbers collide with day-of-month numbers
# -- "I. Mexicans Lost in Mexico" against "1 de diciembre" -- and such a
# pair otherwise arrives with full ordinal confidence.  Two editions of
# one work track each other far more closely than this, so the threshold
# can be generous and still catch the collisions.
MAX_ANCHOR_DEVIATION = 0.10

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

_ROMAN_NUMERALS = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]

_PUNCT = ".,:;!?-–—()[]\"'«»„“”‘’"


class Section:
    "A structural division of a book, e.g. a chapter."

    def __init__(self, order, title, token_count, start_page=None, level=None):
        self.order = order
        self.title = (title or "").strip()
        self.token_count = token_count
        self.start_page = start_page
        self.level = level
        # Set by assign_depths; absolute heading levels are not
        # comparable between books, their ranking is.
        self.depth = None
        self.ordinal, self.ordinal_style = parse_ordinal(self.title)

    def __repr__(self):
        return f"<Section {self.order} {self.title!r} ord={self.ordinal} d={self.depth}>"


def assign_depths(sections):
    """
    Turn heading levels into relative depths, in place.

    Books disagree on which tag means what: one uses h1 for parts and h2
    for chapters, another h2 and h3.  Only the ordering of the levels
    within a book carries meaning, so each book's levels are ranked from
    its own outermost heading.

    This is what lets two books align on the shape of their contents --
    a list of planets with moons nested under them matches the same list
    in another language, with no numbering needed anywhere.

    A book using a single heading level throughout has no hierarchy, so
    its sections are left without a depth rather than all ranked 0.
    Otherwise every one of them would appear to disagree with the nested
    book it is being matched against, and be penalised for a difference
    that says nothing.
    """
    levels = sorted({s.level for s in sections if s.level is not None})
    ranks = {level: rank for rank, level in enumerate(levels)} if len(levels) > 1 else {}
    for section in sections:
        section.depth = ranks.get(section.level)
    return sections


def _int_to_roman(n):
    "Render an int as a roman numeral."
    out = []
    for value, numeral in _ROMAN_NUMERALS:
        while n >= value:
            out.append(numeral)
            n -= value
    return "".join(out)


def _roman_to_int(word):
    "Convert a roman numeral to an int, or None if not a well-formed numeral."
    if not word or any(c not in _ROMAN_VALUES for c in word):
        return None
    total = 0
    prev = 0
    for char in reversed(word):
        value = _ROMAN_VALUES[char]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    if total <= 0 or total > MAX_ORDINAL:
        return None
    # Round-tripping rejects malformed numerals ("IIII", "IC") and, more
    # usefully, ordinary words that happen to use only roman letters
    # ("CIVIL", "DID").
    if _int_to_roman(total) != word:
        return None
    return total


def parse_ordinal(title):
    """
    Pull a chapter number and its numeral style out of a heading.

    Returns (number, style) with style "roman" or "arabic", or
    (None, None).  Handles arabic ("Chapter 18", "18.") and roman
    ("Глава XVIII") numerals.  Roman numerals are the strongest
    cross-language signal available, since they are identical in both
    books.  Number words ("Chapter Eighteen") are language-specific and
    not attempted.

    The style matters as much as the number.  Editions overwhelmingly
    use roman for parts and arabic for chapters and dates, so "III. The
    Sonora Desert" and "3 de enero" both read as 3 but are not the same
    kind of thing.
    """
    for raw in (title or "").split():
        word = raw.strip(_PUNCT)
        if not word:
            continue
        if word.isdigit():
            number = int(word)
            if 0 < number <= MAX_ORDINAL:
                return number, "arabic"
            continue
        number = _roman_to_int(word.upper())
        if number is not None:
            return number, "roman"
    return None, None


def extract_ordinal(title):
    "The chapter number in a heading, or None."
    return parse_ordinal(title)[0]


def _length_score(a, b, a_total, b_total):
    "Agreement of two sections' relative sizes, 0..1."
    if a_total <= 0 or b_total <= 0:
        return 0.0
    diff = abs(a.token_count / a_total - b.token_count / b_total)
    return max(0.0, 1.0 - diff / LENGTH_TOLERANCE)


def _position_score(a, b, a_page_count, b_page_count):
    "Agreement of two sections' positions in their books, 0..1."
    if not a_page_count or not b_page_count:
        return None
    if a.start_page is None or b.start_page is None:
        return None
    diff = abs(a.start_page / a_page_count - b.start_page / b_page_count)
    return max(0.0, 1.0 - diff / POSITION_TOLERANCE)


def _pair_score(a, b, a_total, b_total, a_page_count=None, b_page_count=None):
    "Alignment score for pairing two sections."
    score = LENGTH_WEIGHT * _length_score(a, b, a_total, b_total)

    position = _position_score(a, b, a_page_count, b_page_count)
    if position is not None:
        # Signed: agreeing on position earns, disagreeing costs.  A bonus
        # that could only ever be withheld is not enough, because a
        # coincidental ordinal match elsewhere in the book still profits
        # from being matched at all.
        score += POSITION_WEIGHT * (2 * position - 1)

    if a.depth is not None and b.depth is not None:
        score += DEPTH_MATCH_BONUS if a.depth == b.depth else DEPTH_MISMATCH_PENALTY

    if a.ordinal is not None and b.ordinal is not None:
        if a.ordinal != b.ordinal:
            score += ORDINAL_MISMATCH_PENALTY
        elif position == 0:
            # Same number, opposite ends of the book.  That is a
            # coincidence, and paying for it lets a date claim a part.
            pass
        elif a.ordinal_style == b.ordinal_style:
            score += ORDINAL_MATCH_BONUS
        else:
            score += ORDINAL_CROSS_STYLE_BONUS
    return score


def pair_confidence(a, b, a_total, b_total, a_page_count=None, b_page_count=None):
    """
    Confidence in a proposed pair, 0..1.

    Ordinal agreement dominates, being the only signal that identifies a
    section outright.  Failing that, an unnumbered book can still earn
    confidence from its shape: agreeing nesting depth, comparable size,
    and a comparable place in the book.  All three have to hold, because
    a depth says only what kind of section this is, never which one.

    With no structural evidence at all the result stays below
    MIN_ANCHOR_CONFIDENCE, so such a pair falls back to manual anchoring
    rather than inventing plausible-looking noise.
    """
    length = _length_score(a, b, a_total, b_total)

    if a.ordinal is not None and b.ordinal is not None:
        if a.ordinal != b.ordinal:
            return 0.1 * length
        if a.ordinal_style == b.ordinal_style:
            return 0.8 + 0.2 * length
        # Same number, different numeral style: believable only if the
        # sizes back it up.
        return 0.6 + 0.2 * length

    score = 0.4 * length
    if a.depth is not None and b.depth is not None:
        score += 0.35 if a.depth == b.depth else -0.2

    position = _position_score(a, b, a_page_count, b_page_count)
    if position is not None:
        score *= 0.4 + 0.6 * position

    return max(0.0, min(score, 0.95))


def align_sections(a_sections, b_sections, a_page_count=None, b_page_count=None):
    """
    Align two section lists.

    Returns a list of (a_section, b_section, confidence) in reading
    order.  Either side may be None, marking a section present in one
    book only; confidence is 0.0 for those gaps.

    Page counts are optional, and enable the position term: without them
    sections are matched on their headings and sizes alone.
    """
    n = len(a_sections)
    m = len(b_sections)
    if n == 0 or m == 0:
        return [(s, None, 0.0) for s in a_sections] + [
            (None, s, 0.0) for s in b_sections
        ]

    a_total = sum(s.token_count for s in a_sections)
    b_total = sum(s.token_count for s in b_sections)

    # dp[i][j]: best score aligning the first i of a with the first j of b.
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + GAP_PENALTY
        back[i][0] = "up"
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + GAP_PENALTY
        back[0][j] = "left"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = dp[i - 1][j - 1] + _pair_score(
                a_sections[i - 1],
                b_sections[j - 1],
                a_total,
                b_total,
                a_page_count,
                b_page_count,
            )
            up = dp[i - 1][j] + GAP_PENALTY
            left = dp[i][j - 1] + GAP_PENALTY
            best = max(diag, up, left)
            dp[i][j] = best
            if best == diag:
                back[i][j] = "diag"
            elif best == up:
                back[i][j] = "up"
            else:
                back[i][j] = "left"

    result = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == "diag":
            a_sec = a_sections[i - 1]
            b_sec = b_sections[j - 1]
            result.append(
                (
                    a_sec,
                    b_sec,
                    pair_confidence(
                        a_sec, b_sec, a_total, b_total, a_page_count, b_page_count
                    ),
                )
            )
            i -= 1
            j -= 1
        elif move == "up":
            result.append((a_sections[i - 1], None, 0.0))
            i -= 1
        else:
            result.append((None, b_sections[j - 1], 0.0))
            j -= 1
    result.reverse()
    return result


def is_plausible_anchor(anchor, primary_page_count, companion_page_count):
    """
    True if an anchor lands near where simple proportion would put it.

    Catches ordinal matches that are pure coincidence.  Deliberately
    loose: it is only meant to reject anchors that are wrong by a
    sizeable fraction of the whole book.
    """
    if not primary_page_count or not companion_page_count:
        return True
    primary, companion = anchor
    expected = primary / primary_page_count * companion_page_count
    return abs(companion - expected) / companion_page_count <= MAX_ANCHOR_DEVIATION


def anchors_from_alignment(
    alignment,
    min_confidence=MIN_ANCHOR_CONFIDENCE,
    primary_page_count=None,
    companion_page_count=None,
):
    """
    Reduce an alignment to sorted (primary_page, companion_page) anchors.

    Gaps, low-confidence pairs and implausibly placed pairs are dropped
    rather than guessed at; the stretches they leave behind are covered
    by proportional interpolation, and the reader can anchor them by
    hand.  A wrong anchor is worse than a missing one, because it drags
    everything around it out of place.

    Page counts are optional; without them the plausibility check is
    skipped, since there is nothing to judge position against.
    """
    anchors = []
    for a_sec, b_sec, confidence in alignment:
        if a_sec is None or b_sec is None:
            continue
        if confidence < min_confidence:
            continue
        if a_sec.start_page is None or b_sec.start_page is None:
            continue
        anchors.append((a_sec.start_page, b_sec.start_page))

    return sorted(
        {
            anchor
            for anchor in anchors
            if is_plausible_anchor(anchor, primary_page_count, companion_page_count)
        }
    )
