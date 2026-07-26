"""
Mapping a page of one book onto its parallel companion.

Anchors are exact correspondences established at import or by the
reader.  The stretches between them are interpolated proportionally,
which is what keeps a long unanchored run usable, and the two book ends
act as implicit anchors so a pair with no anchors at all still degrades
to a plain proportional mapping rather than to nothing.
"""

import json


def parse_anchors(raw):
    """
    Read anchors from their stored JSON form.

    Malformed content is treated as "no anchors": a corrupt map should
    cost the reader their alignment, not their book.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    anchors = []
    for entry in data:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        try:
            primary, companion = int(entry[0]), int(entry[1])
        except (ValueError, TypeError):
            continue
        if primary > 0 and companion > 0:
            anchors.append((primary, companion))
    return sorted(set(anchors))


def dump_anchors(anchors):
    "Serialize anchors for storage."
    return json.dumps([[int(p), int(c)] for p, c in sorted(set(anchors))])


def add_anchor(anchors, primary_page, companion_page):
    """
    Add an anchor, replacing any existing one on the same primary page.

    Re-anchoring a page the reader has already anchored is a correction,
    not a second opinion, so the newer value wins.
    """
    kept = [(p, c) for p, c in anchors if p != primary_page]
    kept.append((int(primary_page), int(companion_page)))
    return sorted(kept)


def remove_anchor(anchors, primary_page):
    "Drop the anchor on a primary page, if any."
    return sorted((p, c) for p, c in anchors if p != primary_page)


def _mapping_points(anchors, primary_page_count, companion_page_count):
    """
    Anchors plus the implicit book-end anchors, sorted by primary page.

    Explicit anchors override the implicit ends, so anchoring page 1
    (or the last page) behaves as the reader expects.
    """
    points = {1: 1, primary_page_count: companion_page_count}
    for primary, companion in anchors:
        points[primary] = companion
    return sorted(points.items())


def companion_page(page, anchors, primary_page_count, companion_page_count):
    "Map a page of the primary book onto the companion book."
    if primary_page_count <= 0 or companion_page_count <= 0:
        return 1

    page = max(1, min(int(page), primary_page_count))
    points = _mapping_points(anchors, primary_page_count, companion_page_count)

    def clamp(value):
        return max(1, min(int(value), companion_page_count))

    if page <= points[0][0]:
        return clamp(points[0][1])
    if page >= points[-1][0]:
        return clamp(points[-1][1])

    for (p0, c0), (p1, c1) in zip(points, points[1:]):
        if p0 <= page <= p1:
            if p1 == p0:
                return clamp(c0)
            fraction = (page - p0) / (p1 - p0)
            return clamp(round(c0 + fraction * (c1 - c0)))

    return clamp(points[-1][1])


def is_anchored(page, anchors):
    "True if the page has an explicit anchor."
    return any(p == int(page) for p, _ in anchors)


def surrounding_anchors(page, anchors):
    """
    The nearest anchors on either side of a page, as (before, after).

    Used to tell the reader whether the companion page is anchored or
    interpolated, and from where.
    """
    page = int(page)
    before = None
    after = None
    for primary, companion in sorted(anchors):
        if primary <= page:
            before = (primary, companion)
        elif after is None:
            after = (primary, companion)
    return before, after
