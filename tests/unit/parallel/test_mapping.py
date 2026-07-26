"""
Parallel page mapping tests.
"""

from lute.parallel.mapping import (
    add_anchor,
    companion_page,
    dump_anchors,
    is_anchored,
    parse_anchors,
    remove_anchor,
    surrounding_anchors,
)


def test_no_anchors_is_proportional():
    "An unpaired-but-linked book still tracks roughly."
    assert companion_page(1, [], 100, 200) == 1
    assert companion_page(100, [], 100, 200) == 200
    # Both book ends are pinned, so the mapping is linear across the
    # interior: page 50 of 100 sits 49/99 of the way in, not 50/100.
    assert companion_page(50, [], 100, 200) == 99


def test_single_anchor_shifts_the_mapping():
    anchors = [(40, 30)]
    assert companion_page(40, anchors, 100, 100) == 30
    # Between page 1 and the anchor, and between the anchor and the end.
    assert companion_page(20, anchors, 100, 100) == 15
    assert companion_page(70, anchors, 100, 100) == 65


def test_interpolates_between_anchors():
    anchors = [(10, 20), (20, 40)]
    assert companion_page(10, anchors, 100, 100) == 20
    assert companion_page(15, anchors, 100, 100) == 30
    assert companion_page(20, anchors, 100, 100) == 40


def test_anchors_are_exact():
    "An anchored page must map to its anchor, not near it."
    anchors = [(37, 41), (88, 90)]
    assert companion_page(37, anchors, 200, 200) == 41
    assert companion_page(88, anchors, 200, 200) == 90


def test_explicit_anchor_overrides_implicit_book_ends():
    anchors = [(1, 12)]
    assert companion_page(1, anchors, 100, 100) == 12


def test_result_is_clamped_to_the_companion_book():
    assert companion_page(500, [], 100, 50) == 50
    assert companion_page(-5, [], 100, 50) == 1


def test_degenerate_page_counts():
    assert companion_page(1, [], 0, 0) == 1
    assert companion_page(1, [], 1, 1) == 1


def test_anchor_roundtrip():
    anchors = [(10, 20), (5, 7)]
    assert parse_anchors(dump_anchors(anchors)) == [(5, 7), (10, 20)]


def test_parse_anchors_tolerates_junk():
    "A corrupt map costs the alignment, not the book."
    assert parse_anchors(None) == []
    assert parse_anchors("") == []
    assert parse_anchors("not json") == []
    assert parse_anchors('{"a": 1}') == []
    assert parse_anchors('[[1, 2], "x", [3], [0, 5], [4, 6]]') == [(1, 2), (4, 6)]


def test_add_anchor_replaces_on_same_primary_page():
    "Re-anchoring is a correction, not a second opinion."
    anchors = [(10, 20)]
    assert add_anchor(anchors, 10, 25) == [(10, 25)]
    assert add_anchor(anchors, 12, 25) == [(10, 20), (12, 25)]


def test_remove_anchor():
    assert remove_anchor([(10, 20), (12, 25)], 10) == [(12, 25)]
    assert remove_anchor([(10, 20)], 99) == [(10, 20)]


def test_is_anchored():
    assert is_anchored(10, [(10, 20)])
    assert not is_anchored(11, [(10, 20)])


def test_surrounding_anchors():
    anchors = [(10, 20), (30, 45)]
    assert surrounding_anchors(20, anchors) == ((10, 20), (30, 45))
    assert surrounding_anchors(5, anchors) == (None, (10, 20))
    assert surrounding_anchors(40, anchors) == ((30, 45), None)
