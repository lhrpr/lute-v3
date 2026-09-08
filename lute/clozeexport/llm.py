"""
Optional LLM pass over the candidates the heuristics aren't sure about.

Two rules keep this cheap and safe:

1. *It picks indices, not text.*  The model is shown numbered fragments and
   answers with a start/end fragment number.  It has no way to return a
   sentence the author didn't write, which matters more here than anywhere
   else in Lute -- a subtly reworded card gets memorised.
2. *Its answer still has to pass the gate.*  A proposed range is re-scored with
   the same comprehensibility check as everything else, and dropped back to the
   heuristic span if it fails.

Only candidates flagged needs_review reach this module (a clause cut, or a span
that only just scored well enough), and they're sent in batches, so a book
typically costs a handful of calls rather than one per card.
"""

import json
import re

from lute.ai.service import (
    AIServiceException,
    _AIService,
    _run_model_cascade,
)
from lute.clozeexport.scoring import build_span, reachable_range
from lute.clozeexport.units import render_items

# Offline batch work, so there's no user waiting on it.  Far longer than the
# reading screen's budget: these prompts carry a dozen passages.
BATCH_TIMEOUT = (5.0, 90.0)

MARK_OPEN = "«"
MARK_CLOSE = "»"


class ReviewItem:
    "One candidate the heuristics want a second opinion on."

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(self, key, span, target_items, exclude_term_ids, language_name):
        self.key = key
        self.span = span
        self.target_items = target_items
        self.exclude_term_ids = exclude_term_ids
        self.language_name = language_name


def _fragments(item):
    """
    Numbered fragment texts for one item, plus the offset back into the units.

    The target's fragment is marked so the model knows which word the card is
    testing, without being told the word out of context.
    """
    source = item.span.source
    lo, hi = reachable_range(source)
    texts = []
    for i in range(lo, hi + 1):
        unit = source.units[i]
        if i == source.target_index:
            texts.append(
                render_items(unit.items, item.target_items, MARK_OPEN, MARK_CLOSE)
            )
        else:
            texts.append(unit.text)
    return texts, lo, source.target_index - lo


def build_prompt(items, opts):
    "Prompt asking for one fragment range per item, as JSON."
    blocks = []
    for n, item in enumerate(items):
        texts, _, target_local = _fragments(item)
        lines = [f"  {i}: {t}" for i, t in enumerate(texts)]
        blocks.append(
            f"### Passage {n}\n"
            f"Language: {item.language_name}\n"
            f"The tested word is in fragment {target_local}, marked "
            f"{MARK_OPEN}like this{MARK_CLOSE}.\n" + "\n".join(lines)
        )

    passages = "\n\n".join(blocks)
    return f"""You are helping build fill-in-the-blank flashcards for a language learner.

Each passage below is split into numbered fragments of the original text, in
order.  For each passage, choose the range of consecutive fragments that makes
the best flashcard for the marked word.

A good range:
- reads as one complete, self-contained thought,
- gives enough context that the marked word's meaning and use are clear,
- does not drag in unrelated material,
- is roughly {opts.min_words}-{opts.max_words} words, but a natural complete
  thought matters more than the exact length,
- must include the fragment containing the marked word.

{passages}

Reply with JSON only -- an array with one object per passage, no other text:
[{{"passage": 0, "start": 0, "end": 1}}]
"""


def parse_response(reply, count):
    """
    Pull {passage: (start, end)} out of the model's reply.

    Tolerates code fences and stray prose around the JSON; anything that isn't
    a plain pair of ints for a known passage is ignored rather than guessed at.
    """
    if not reply:
        return {}
    match = re.search(r"\[.*\]", reply, re.DOTALL)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    if not isinstance(data, list):
        return {}

    ret = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            passage = int(entry["passage"])
            start = int(entry["start"])
            end = int(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= passage < count:
            ret[passage] = (start, end)
    return ret


class ClozeRefiner(_AIService):
    """
    Re-cuts low-confidence spans using the configured LLM.

    Reuses the AI settings, provider cascade and rate-limit cooldowns the
    reading screen already uses (see lute/ai/service.py), so there's no second
    API key to configure.
    """

    def __init__(self, session, opts, batch_size=8):
        super().__init__(session)
        self.opts = opts
        self.batch_size = batch_size
        self.calls = 0
        self.refined = 0
        self.rejected = 0

    def refine(self, items):
        """
        Return {key: span} for the items the LLM improved.

        Items it declines, garbles, or proposes an incomprehensible range for
        are simply absent from the result -- the caller keeps its heuristic
        span.  A provider failure aborts the remaining batches and is raised,
        since it's almost always a config problem worth showing the user.
        """
        ret = {}
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            ret.update(self._refine_batch(batch))
        return ret

    def _refine_batch(self, batch):
        "Run one call and validate what comes back."
        provider_cls, api_key, base_url, models = self._resolve_config()
        prompt = build_prompt(batch, self.opts)
        reply, _ = _run_model_cascade(
            provider_cls,
            api_key,
            base_url,
            models,
            lambda p: p.complete(prompt, temperature=0.0),
            timeout=BATCH_TIMEOUT,
        )
        self.calls += 1

        ret = {}
        for n, (start, end) in parse_response(reply, len(batch)).items():
            item = batch[n]
            span = self._validate(item, start, end)
            if span is None:
                continue
            ret[item.key] = span
        return ret

    def _validate(self, item, start, end):
        """
        Turn a proposed fragment range into a span, or None to keep the
        heuristic's.

        The indices are relative to the fragments the model was shown, so they
        are shifted back onto the full unit list before being re-scored.
        """
        lo, _ = reachable_range(item.span.source)
        span = build_span(
            item.span.source,
            lo + start,
            lo + end,
            item.exclude_term_ids,
            self.opts,
            item.span.mode,
        )
        if span is None:
            self.rejected += 1
            return None
        if span.units == item.span.units:
            return None  # agreed with the heuristic; nothing to change
        self.refined += 1
        return span


def is_available(session):
    "True if the user has AI switched on and configured."
    try:
        service = _AIService(session)
        if not service.is_enabled():
            return False
        service._resolve_config()  # pylint: disable=protected-access
        return True
    except AIServiceException:
        return False
