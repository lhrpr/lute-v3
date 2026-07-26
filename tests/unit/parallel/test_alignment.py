"""
Section alignment tests.
"""

from lute.parallel.alignment import (
    Section,
    align_sections,
    anchors_from_alignment,
    assign_depths,
    extract_ordinal,
)


def _chapters(prefix, count, tokens=1000, start=1, first_page=1):
    """
    Build a run of numbered chapters, one page each for simplicity.
    """
    return [
        Section(i, f"{prefix} {_roman(start + i)}", tokens, first_page + i)
        for i in range(count)
    ]


def _roman(n):
    "Small roman numeral helper for building test headings."
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, numeral in vals:
        while n >= value:
            out += numeral
            n -= value
    return out


def test_extract_ordinal_arabic():
    assert extract_ordinal("Chapter 18") == 18
    assert extract_ordinal("18.") == 18


def test_extract_ordinal_roman_is_language_independent():
    "The whole point: the numeral is identical in both books."
    assert extract_ordinal("Chapter XVIII") == 18
    assert extract_ordinal("Глава XVIII") == 18


def test_extract_ordinal_ignores_non_numerals():
    assert extract_ordinal("Foreword") is None
    assert extract_ordinal("Introduction") is None


def test_extract_ordinal_rejects_words_made_of_roman_letters():
    "Round-tripping keeps ordinary words from reading as numerals."
    assert extract_ordinal("CIVIL") is None
    assert extract_ordinal("DID") is None
    assert extract_ordinal("MIX") is None


def test_extract_ordinal_ignores_large_numbers():
    "A year in a heading is not a chapter number."
    assert extract_ordinal("The 1812 Campaign") is None


def test_aligns_identical_structures():
    a = _chapters("Chapter", 10)
    b = _chapters("Глава", 10)
    result = align_sections(a, b)
    assert len(result) == 10
    assert all(x is not None and y is not None for x, y, _ in result)
    assert all(x.ordinal == y.ordinal for x, y, _ in result)


def test_foreword_on_one_side_becomes_a_gap():
    """
    The motivating case: an edition with a foreword must not shift every
    chapter after it by one.
    """
    a = [Section(0, "Foreword", 800, 1)] + _chapters("Chapter", 10, first_page=2)
    b = _chapters("Глава", 10, first_page=1)

    result = align_sections(a, b)

    assert result[0] == (a[0], None, 0.0)
    matched = [(x, y) for x, y, _ in result if x and y]
    assert len(matched) == 10
    assert all(x.ordinal == y.ordinal for x, y in matched)


def test_appendix_on_one_side_becomes_a_gap():
    a = _chapters("Chapter", 10)
    b = _chapters("Глава", 10) + [Section(11, "Приложение", 500, 11)]

    result = align_sections(b, a)

    assert result[-1] == (b[-1], None, 0.0)
    assert len([1 for x, y, _ in result if x and y]) == 10


def test_extra_sections_on_both_sides():
    a = [Section(0, "Foreword", 800, 1)] + _chapters("Chapter", 8, first_page=2)
    b = _chapters("Глава", 8, first_page=1) + [Section(9, "Notes", 400, 9)]

    result = align_sections(a, b)

    matched = [(x, y) for x, y, _ in result if x and y]
    assert len(matched) == 8
    assert all(x.ordinal == y.ordinal for x, y in matched)


def test_unnumbered_sections_never_reach_anchor_confidence():
    """
    With no ordinals there is not enough evidence to commit anchors, even
    when the lengths line up perfectly.
    """
    a = [Section(i, "", 1000, i + 1) for i in range(6)]
    b = [Section(i, "", 1000, i + 1) for i in range(6)]

    result = align_sections(a, b)

    assert all(conf < 0.7 for _, _, conf in result)
    assert anchors_from_alignment(result) == []


def test_anchors_from_alignment_skips_gaps():
    a = [Section(0, "Foreword", 800, 1)] + _chapters("Chapter", 5, first_page=2)
    b = _chapters("Глава", 5, first_page=1)

    anchors = anchors_from_alignment(align_sections(a, b))

    # Chapter I is page 2 in a, page 1 in b, and the offset holds.
    assert anchors == [(2, 1), (3, 2), (4, 3), (5, 4), (6, 5)]


def test_section_with_a_corrupt_heading_is_matched_but_not_committed():
    """
    A real epub had a page number ("253") leak into a chapter heading, and
    the same section also swallowed the tail of the previous chapter.

    The ordinal must NOT be inferred from the neighbours (8 _ 10 -> 9).
    The section starts where the *previous* chapter's tail begins, so an
    inferred anchor would pin the chapter pages before it actually
    starts.  Leaving it uncommitted lets the neighbouring anchors
    interpolate across it, which lands far closer.
    """
    a = [
        Section(0, "8", 5000, 10),
        Section(1, "253", 13500, 20),  # ch8 tail + ch9, heading corrupt
        Section(2, "10", 5000, 50),
    ]
    b = [
        Section(0, "8", 8000, 10),
        Section(1, "9", 11000, 25),
        Section(2, "10", 5200, 55),
    ]

    result = align_sections(a, b)
    broken = [(x, y, c) for x, y, c in result if x and x.title == "253"]

    assert len(broken) == 1, "the corrupt section should still be shown"
    assert broken[0][1].title == "9", "matched, so the reader can see it"
    assert broken[0][2] < 0.7, "but never committed as an anchor"

    anchors = anchors_from_alignment(result)
    assert anchors == [(10, 10), (50, 55)], "neighbours anchor, the broken one does not"


def test_coincidental_ordinal_match_in_the_wrong_place_is_rejected():
    """
    Real case: a Spanish edition splits Part I into dated entries while
    the English keeps it whole, so "1 de diciembre" (p.143 of 1036) meets
    "I. Mexicans Lost in Mexico" (p.6 of 1019).  Both read as ordinal 1,
    so the pair arrives fully confident and 13% of a book out of place.
    """
    a = [Section(0, "1 de diciembre", 17, 143)]
    b = [Section(0, "I. Mexicans Lost in Mexico", 33377, 6)]

    alignment = align_sections(a, b)

    # Confident on the ordinal alone...
    assert alignment[0][2] >= 0.7
    # ...but rejected once its position is checked.
    assert anchors_from_alignment(alignment) == [(143, 6)]
    assert (
        anchors_from_alignment(
            alignment, primary_page_count=1036, companion_page_count=1019
        )
        == []
    )


def test_plausible_anchors_survive_the_position_check():
    "A correct anchor sits near its proportional position and is kept."
    a = [Section(0, "II", 5, 214)]
    b = [Section(0, "II", 9084, 201)]

    anchors = anchors_from_alignment(
        align_sections(a, b), primary_page_count=1036, companion_page_count=1019
    )

    assert anchors == [(214, 201)]


def test_roman_part_marker_beats_a_date_with_the_same_number():
    """
    Real case.  A Spanish edition splits Part I into dated entries while
    the English keeps it whole, so the part marker "I. Mexicans Lost in
    Mexico" has two rivals reading as ordinal 1: the matching Spanish
    part marker at the front of the book, and "1 de diciembre" a fifth of
    the way in.  Numeral style and position have to pick the right one.
    """
    es = [
        Section(0, "I. Mexicanos perdidos en México (1975)", 6, 5),
        Section(1, "2 de noviembre", 24, 6),
        Section(2, "1 de diciembre", 17, 123),
        Section(3, "II. Los detectives salvajes", 5, 187),
    ]
    en = [
        Section(0, "", 114, 1),
        Section(1, "I. Mexicans Lost in Mexico", 33377, 6),
        Section(2, "II. The Savage Detectives", 9084, 162),
    ]

    alignment = align_sections(es, en, a_page_count=632, b_page_count=584)
    pairs = {a.title: b.title for a, b, _ in alignment if a and b}

    assert pairs["I. Mexicanos perdidos en México (1975)"] == "I. Mexicans Lost in Mexico"
    assert pairs.get("1 de diciembre") != "I. Mexicans Lost in Mexico"
    assert pairs["II. Los detectives salvajes"] == "II. The Savage Detectives"


def test_numeral_style_is_a_tiebreak_not_a_requirement():
    """
    An edition may renumber roman parts in arabic.  A cross-style match
    still stands when there is no same-style rival.
    """
    a = [Section(0, "Part IV", 5000, 40)]
    b = [Section(0, "Part 4", 5200, 41)]

    alignment = align_sections(a, b, a_page_count=100, b_page_count=100)

    assert alignment[0][0] is not None and alignment[0][1] is not None
    assert alignment[0][2] >= 0.7


def test_aligns_on_nesting_alone_with_no_numbers_anywhere():
    """
    Two contents lists of the same shape should match on that shape:

        Planets          Planetas
        - Venus          - Venus
        - Earth          - Tierra
        - Mars           - Marte
        -- Phobos        -- Fobos
        -- Deimos        -- Deimos
    """
    en = assign_depths(
        [
            Section(0, "Planets", 400, 1, level=1),
            Section(1, "Venus", 1000, 2, level=2),
            Section(2, "Earth", 1200, 6, level=2),
            Section(3, "Mars", 1100, 11, level=2),
            Section(4, "Phobos", 300, 16, level=3),
            Section(5, "Deimos", 300, 18, level=3),
        ]
    )
    es = assign_depths(
        [
            Section(0, "Planetas", 420, 1, level=2),  # different tags, same shape
            Section(1, "Venus", 1050, 2, level=3),
            Section(2, "Tierra", 1260, 6, level=3),
            Section(3, "Marte", 1155, 11, level=3),
            Section(4, "Fobos", 315, 16, level=4),
            Section(5, "Deimos", 315, 18, level=4),
        ]
    )

    alignment = align_sections(en, es, a_page_count=20, b_page_count=20)
    pairs = [(a.title, b.title) for a, b, _ in alignment if a and b]

    assert pairs == [
        ("Planets", "Planetas"),
        ("Venus", "Venus"),
        ("Earth", "Tierra"),
        ("Mars", "Marte"),
        ("Phobos", "Fobos"),
        ("Deimos", "Deimos"),
    ]
    anchors = anchors_from_alignment(
        alignment, primary_page_count=20, companion_page_count=20
    )
    assert len(anchors) == 6


def test_depth_keeps_a_part_from_matching_a_chapter():
    "A nested item must not be taken for its own parent."
    a = assign_depths(
        [Section(0, "Mars", 1100, 1, level=2), Section(1, "Phobos", 300, 6, level=3)]
    )
    b = assign_depths(
        [Section(0, "Marte", 1150, 1, level=2), Section(1, "Fobos", 310, 6, level=3)]
    )

    pairs = [(x.title, y.title) for x, y, _ in align_sections(a, b, 10, 10) if x and y]

    assert pairs == [("Mars", "Marte"), ("Phobos", "Fobos")]


def test_a_flat_book_gets_no_depths():
    """
    One heading level throughout is not a hierarchy.  Ranking it all as
    depth 0 would make every section look like it disagreed with the
    nested book it is matched against.
    """
    sections = assign_depths(
        [Section(i, str(i), 100, i + 1, level=2) for i in range(4)]
    )
    assert [s.depth for s in sections] == [None] * 4


def test_assign_depths_ranks_within_each_book():
    "Absolute tag levels are not comparable; their order is."
    sections = assign_depths(
        [
            Section(0, "a", 10, 1, level=2),
            Section(1, "b", 10, 2, level=4),
            Section(2, "c", 10, 3, level=2),
            Section(3, "d", 10, 4, level=None),
        ]
    )
    assert [s.depth for s in sections] == [0, 1, 0, None]


def test_shape_alone_is_not_enough_when_it_does_not_agree():
    "Mismatched depth and size stays below the commit threshold."
    a = assign_depths([Section(0, "Intro", 200, 1, level=1)])
    b = assign_depths([Section(0, "Capitulo", 9000, 1, level=3)])
    b[0].depth = 1

    alignment = align_sections(a, b, 10, 10)

    assert alignment[0][2] < 0.7


def test_empty_section_lists():
    assert align_sections([], []) == []
    a = _chapters("Chapter", 2)
    assert len(align_sections(a, [])) == 2
