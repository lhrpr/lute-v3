"""
Tuning knobs for cloze span selection.

Kept in one place (rather than threaded through as arguments) because the
scorer, the windower and the CLI all need the same values, and because the
weights are the part a user will actually want to fiddle with.
"""

from dataclasses import dataclass, field
from typing import Set, Tuple

from lute.clozeexport.coverage import (
    DEFAULT_KNOWN_STATUSES,
    DEFAULT_LEARNING_STATUSES,
)


@dataclass
class ClozeOptions:  # pylint: disable=too-many-instance-attributes
    """
    Everything that shapes which span becomes a card.

    The word counts are deliberately *soft*: min_words/max_words define a
    comfortable band, and going outside it costs score rather than
    disqualifying a span.  A 22-word sentence that reads perfectly beats a
    14-word one that starts with "But he".
    """

    # --- comprehensibility gate (hard) ---
    max_unknown: int = 0
    max_learning: int = 1
    learning_statuses: Tuple[int, ...] = DEFAULT_LEARNING_STATUSES
    known_statuses: Tuple[int, ...] = DEFAULT_KNOWN_STATUSES

    # --- which terms get cards ---
    target_statuses: Tuple[int, ...] = (1, 2, 3)

    # --- length band (soft) ---
    min_words: int = 10
    max_words: int = 20

    # A sentence longer than this is a candidate for being cut into clauses.
    long_sentence_words: int = 25

    # How many sentences either side of the target we may pull in, and how
    # many clauses either side when cutting a long sentence down.
    context_sentences: int = 2
    context_clauses: int = 2

    # --- scoring weights ---
    # Squared, normalized length error.  Deliberately the heaviest weight: a
    # 38-word sentence is a real cognitive-load problem, and it has to outweigh
    # the fragment penalties below or a clause cut could never win.
    weight_length: float = 20.0
    # Per distinct learning word other than the target.
    weight_learning: float = 6.0
    # Per distinct unknown word.  Normally unreachable (the gate gets there
    # first), but keeps scoring sane if the gate is loosened.
    weight_unknown: float = 40.0
    # Span begins mid-sentence (a clause cut).  Small: cutting a long sentence
    # is a mild cost, not a disqualification.  These mostly discriminate
    # *between* clause ranges, favouring ones that align with real boundaries.
    weight_fragment: float = 4.0
    # Span stops before the sentence's full stop.
    weight_dangling: float = 3.0
    # Span opens with a word that points at text we're not including -- a
    # pronoun, a connective, or (for a clause cut) a conjunction.
    weight_anaphora: float = 10.0
    # Reward (subtracted) for the span carrying real, known content.
    weight_informative: float = 6.0
    # Mild preference for the smallest span that does the job.
    weight_extra_unit: float = 1.5

    # Above this score, the heuristics aren't confident and the candidate is
    # worth an LLM opinion (when --use-llm is on).
    review_threshold: float = 14.0

    # --- language-specific hints (see lexicons.py) ---
    anaphora_words: Set[str] = field(default_factory=set)
    conjunction_words: Set[str] = field(default_factory=set)

    # --- cloze rendering ---
    cloze_prefix: str = "{{c1::"
    cloze_suffix: str = "}}"
