"""
The LLM refinement pass.

No network: the provider cascade is stubbed, so these test the parts that
actually matter -- that a model can only move boundaries, and that what it
proposes still has to be readable.
"""

import pytest

from lute.clozeexport import llm as llm_module
from lute.clozeexport.llm import ClozeRefiner, ReviewItem, build_prompt, parse_response
from lute.clozeexport.scoring import select_span
from lute.db import db
from tests.unit.clozeexport.conftest import find_item


CONTENT = (
    "The postman brought a strange parcel. I laughed a lot. "
    "The dog barked loudly at him."
)


@pytest.fixture(name="review_item")
def fixture_review_item(known_paragraphs, opts):
    "A candidate whose heuristic span excludes the first sentence."
    paragraph = known_paragraphs(CONTENT, {"laughed": 2, "parcel": 0})[0]
    index, item = find_item(paragraph, "laughed")
    span, _ = select_span(paragraph, index, [item], {item.term.id}, opts)
    assert span.text == "I laughed a lot. The dog barked loudly at him."
    return ReviewItem("k", span, [item], {item.term.id}, "English")


def _stub_reply(monkeypatch, reply):
    "Make the provider cascade return a fixed reply."
    monkeypatch.setattr(
        ClozeRefiner,
        "_resolve_config",
        lambda self: (None, "key", None, ["model"]),
    )
    monkeypatch.setattr(
        llm_module,
        "_run_model_cascade",
        lambda *args, **kwargs: (reply, "model"),
    )


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('[{"passage": 0, "start": 1, "end": 2}]', {0: (1, 2)}),
        ('```json\n[{"passage": 0, "start": 0, "end": 0}]\n```', {0: (0, 0)}),
        (
            'Sure!\n[{"passage": 0, "start": 0, "end": 1}]\nHope that helps.',
            {0: (0, 1)},
        ),
        ("I'm not sure how to answer that.", {}),
        ('[{"passage": 0, "start": "one", "end": 2}]', {}),
        ('[{"passage": 9, "start": 0, "end": 1}]', {}),
        ("", {}),
    ],
)
def test_parse_response(reply, expected):
    "Malformed or out-of-range answers are dropped, never guessed at."
    assert parse_response(reply, count=1) == expected


def test_prompt_marks_the_target_and_numbers_the_fragments(review_item, opts):
    "The model is shown numbered source text and which fragment is tested."
    prompt = build_prompt([review_item], opts)
    assert "  0: The postman brought a strange parcel." in prompt
    assert "  1: I «laughed» a lot." in prompt
    assert "in fragment 1" in prompt


def test_proposal_is_applied(review_item, opts, monkeypatch):
    "A valid, different range replaces the heuristic span."
    _stub_reply(monkeypatch, '[{"passage": 0, "start": 1, "end": 1}]')
    refiner = ClozeRefiner(db.session, opts)
    spans = refiner.refine([review_item])
    assert spans["k"].text == "I laughed a lot."
    assert (refiner.refined, refiner.rejected) == (1, 0)


def test_incomprehensible_proposal_is_rejected(review_item, opts, monkeypatch):
    "The model doesn't get to override the comprehensibility gate."
    _stub_reply(monkeypatch, '[{"passage": 0, "start": 0, "end": 2}]')
    refiner = ClozeRefiner(db.session, opts)
    spans = refiner.refine([review_item])
    assert spans == {}, "the span with the unknown 'parcel' was dropped"
    assert (refiner.refined, refiner.rejected) == (0, 1)


def test_out_of_range_proposal_is_rejected(review_item, opts, monkeypatch):
    "Indices outside the offered fragments are refused, not clamped."
    _stub_reply(monkeypatch, '[{"passage": 0, "start": 1, "end": 47}]')
    refiner = ClozeRefiner(db.session, opts)
    assert refiner.refine([review_item]) == {}


def test_agreement_leaves_the_span_alone(review_item, opts, monkeypatch):
    "Confirming the heuristic isn't a change."
    _stub_reply(monkeypatch, '[{"passage": 0, "start": 1, "end": 2}]')
    refiner = ClozeRefiner(db.session, opts)
    assert refiner.refine([review_item]) == {}
    assert (refiner.refined, refiner.rejected) == (0, 0)


def test_batching(review_item, opts, monkeypatch):
    "Candidates go out in batches, not one call each."
    _stub_reply(monkeypatch, "[]")
    refiner = ClozeRefiner(db.session, opts, batch_size=2)
    refiner.refine([review_item] * 5)
    assert refiner.calls == 3
