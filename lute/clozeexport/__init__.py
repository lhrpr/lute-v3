"""
Cloze card export.

Builds cloze (fill-in-the-blank) cards from the books you've read, choosing a
span of text around each target term that is worth studying:

- the span is comprehensible (few/no unknown words -- an "i+1" gate),
- it carries enough context to be memorable (short sentences pull in their
  neighbours),
- it isn't a wall of text (long sentences are cut down to clauses).

See lute/clozeexport/service.py for the pipeline, which runs cheap
deterministic filters first and only consults an LLM for the minority of
candidates the heuristics can't settle.
"""
