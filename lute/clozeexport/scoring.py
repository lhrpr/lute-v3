"""
Choosing which span of text becomes the card.

The rule the user actually wants isn't "10-20 words", it's "enough context to
be memorable, not so much that it's a chore".  Word count is a proxy for that,
so it's scored rather than enforced: every feasible span gets a cost, and the
cheapest wins.  A 23-word span that reads as a complete thought beats a 12-word
one that opens with "But he".

Two shapes of span are considered:

- *sentences* -- the target's sentence, optionally with neighbours pulled in.
  This is what rescues "I laughed a lot.": too short on its own, so the cost of
  its length dominates until the preceding sentence is included.
- *clauses* -- when the target's sentence is a monster, it's cut on punctuation
  and conjunctions and only the clauses around the target are kept.

Both use the same cost function, so they're directly comparable and the better
one simply wins.
"""

from lute.clozeexport.coverage import is_comprehensible, summarize
from lute.clozeexport.units import render_items, split_into_clauses


class SpanSource:
    """
    Where a span was cut from: the full unit list, and how far we may reach.

    Carried on the Span so a later step (the LLM refiner) can propose different
    boundaries over exactly the same units.
    """

    def __init__(self, units, target_index, reach):
        self.units = units
        self.target_index = target_index
        self.reach = reach


class Span:
    "A scored, feasible run of Units that could become a card."

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(self, units, target_index, coverage, cost, mode, source=None):
        self.units = units
        # Index *within self.units* of the unit holding the target term.
        self.target_index = target_index
        self.coverage = coverage
        self.cost = cost
        self.mode = mode  # "sentence" or "clause"
        # The unit list this span was cut from, kept so the LLM step can
        # reconsider the boundaries without redoing the sentence/clause split.
        self.source = source or SpanSource(units, target_index, len(units))

    @property
    def items(self):
        "Flat list of the span's TextItems."
        return [ti for u in self.units for ti in u.items]

    @property
    def word_count(self):
        "Words a reader would count on the card."
        return sum(u.word_count for u in self.units)

    @property
    def text(self):
        "Readable text of the span."
        return render_items(self.items)

    def cloze_text(self, target_items, opts):
        "Span text with the target occurrence wrapped for a cloze deletion."
        return render_items(
            self.items, target_items, opts.cloze_prefix, opts.cloze_suffix
        )

    def __repr__(self):
        return f'<Span {self.mode} cost={self.cost:.1f} "{self.text[:50]}">'


def _length_error(words, opts):
    """
    How far outside the comfortable band the span is, normalized to [0, ~1].

    Zero inside the band, so nothing distinguishes a 12-word span from an
    18-word one -- which is the point.  Being short is scaled against min_words
    and being long against max_words, so "half as long as I want" and "half
    again as long as I want" cost about the same.
    """
    if words < opts.min_words:
        return (opts.min_words - words) / max(opts.min_words, 1)
    if words > opts.max_words:
        return (words - opts.max_words) / max(opts.max_words, 1)
    return 0.0


def score_span(units, coverage, opts):
    """
    Cost of a candidate span.  Lower is better.

    Every term is a reason a card would be worse to study, except the
    informativeness reward, which is what stops the scorer from always picking
    the barest span that clears the length floor.
    """
    words = sum(u.word_count for u in units)

    cost = opts.weight_length * (_length_error(words, opts) ** 2)
    cost += opts.weight_learning * coverage.learning_count
    cost += opts.weight_unknown * coverage.unknown_count
    cost += opts.weight_extra_unit * (len(units) - 1)

    if not units[0].starts_sentence:
        cost += opts.weight_fragment
    if not units[-1].ends_sentence:
        cost += opts.weight_dangling

    # An opening pronoun, connective or conjunction means the span is leaning
    # on text we haven't included: "But he was late", "and hungry after a long
    # day".  This is what discriminates between clause cuts -- they all start
    # mid-sentence, so the fragment penalty above says nothing about which one
    # to pick, but the word they open with does.
    opening = units[0].first_word_lc()
    if opening in opts.anaphora_words or opening in opts.conjunction_words:
        cost += opts.weight_anaphora

    # Reward real content.  Capped, so this can't be gamed by length alone;
    # in practice it only bites on spans too bare to be memorable.
    informative = min(len(coverage.known), 8) / 8.0
    cost -= opts.weight_informative * informative

    return cost


# pylint: disable=too-many-arguments,too-many-positional-arguments
def build_span(source, start, end, exclude_term_ids, opts, mode):
    """
    Score source.units[start:end+1] as a span, or None if it fails the gate.

    Also the validator for LLM-proposed boundaries: whatever the model returns
    has to survive the same comprehensibility check as anything the heuristics
    produce, so a plausible-looking but unreadable suggestion is discarded
    rather than exported.
    """
    units = source.units
    target_index = source.target_index
    if not 0 <= start <= target_index <= end < len(units):
        return None
    candidate = units[start : end + 1]
    items = [ti for u in candidate for ti in u.items]
    coverage = summarize(
        items, exclude_term_ids, opts.learning_statuses, opts.known_statuses
    )
    if not is_comprehensible(coverage, opts.max_unknown, opts.max_learning):
        return None
    cost = score_span(candidate, coverage, opts)
    return Span(candidate, target_index - start, coverage, cost, mode, source)


def reachable_range(source):
    "The window of unit indices a span may be drawn from."
    lo = max(0, source.target_index - source.reach)
    hi = min(len(source.units) - 1, source.target_index + source.reach)
    return lo, hi


def _best_span(units, target_index, exclude_term_ids, opts, reach, mode):
    """
    Cheapest feasible span of `units` that contains units[target_index].

    Feasibility is monotone -- adding text can only add unknown/learning words,
    never remove them -- so an infeasible span means every span containing it is
    infeasible too, and the loops can stop early instead of scoring the rest.
    """
    best = None
    source = SpanSource(units, target_index, reach)
    first_start, last_end = reachable_range(source)

    for start in range(target_index, first_start - 1, -1):
        start_feasible = False
        for end in range(target_index, last_end + 1):
            span = build_span(source, start, end, exclude_term_ids, opts, mode)
            if span is None:
                # Growing rightwards can only make this worse.
                break
            start_feasible = True
            if best is None or span.cost < best.cost:
                best = span
        if not start_feasible:
            # Growing leftwards can only make this worse.
            break

    return best


def select_span(sentences, sentence_index, target_items, exclude_term_ids, opts):
    """
    Pick the best span for a target occurrence, or None if no card is possible.

    Returns (span, needs_review).  needs_review flags the candidates the
    heuristics aren't confident about -- a clause cut, or a span that only just
    scraped through -- which are the ones worth spending an LLM call on.
    """
    sentence_span = _best_span(
        sentences,
        sentence_index,
        exclude_term_ids,
        opts,
        opts.context_sentences,
        "sentence",
    )

    target_sentence = sentences[sentence_index]
    clause_span = None
    too_long = target_sentence.word_count > opts.long_sentence_words
    if too_long or sentence_span is None:
        clauses = split_into_clauses(target_sentence, opts.conjunction_words)
        if len(clauses) > 1:
            target_clause_index = _index_of_items(clauses, target_items)
            if target_clause_index is not None:
                clause_span = _best_span(
                    clauses,
                    target_clause_index,
                    exclude_term_ids,
                    opts,
                    opts.context_clauses,
                    "clause",
                )

    candidates = [s for s in (sentence_span, clause_span) if s is not None]
    if len(candidates) == 0:
        return None, False

    best = min(candidates, key=lambda s: s.cost)
    needs_review = best.mode == "clause" or best.cost > opts.review_threshold
    return best, needs_review


def _index_of_items(units, target_items):
    "Index of the unit containing the target TextItems, or None."
    wanted = {id(ti) for ti in target_items}
    for i, unit in enumerate(units):
        if any(id(ti) in wanted for ti in unit.items):
            return i
    return None
