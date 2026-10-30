"""
Merge rules for the vocab link, without a database.
"""

import pytest

from lute.vocablink.config import VocabConfig, VocabConfigError, parse_languages
from lute.vocablink.mapping import lute_timestamp_ms, lute_values, merge


def vals(reading="", translation="", status="1"):
    return {"reading": reading, "translation": translation, "status": status}


def test_lute_statuses_map_to_vocab():
    assert lute_values(None, None, 0)["status"] is None
    assert lute_values(None, None, 3)["status"] == "3"
    assert lute_values(None, None, 98)["status"] == "I"
    assert lute_values(None, None, 99)["status"] == "K"
    assert lute_values(None, "cat", 1) == vals(translation="cat")


def test_new_word_sends_what_lute_has():
    m = merge(None, vals(translation="cat", status="2"), None, 0, 0)
    assert m.to_vocab == {"translation": "cat", "status": "2"}
    assert not m.to_lute


def test_one_side_changed_wins():
    base = vals(translation="cat", status="2")
    m = merge(base, vals(translation="cat", status="3"), vals(translation="kitty", status="2"), 0, 0)
    assert m.to_vocab == {"status": "3"}
    assert m.to_lute == {"translation": "kitty"}
    assert m.base == vals(translation="kitty", status="3")


def test_both_changed_status_goes_to_the_later_change():
    base = vals(status="1")
    lute, vocab = vals(status="2"), vals(status="4")
    assert merge(base, lute, vocab, 2000, 1000).to_vocab == {"status": "2"}
    assert merge(base, lute, vocab, 1000, 2000).to_lute == {"status": "4"}


def test_first_link_a_missing_status_never_wins():
    # Unrated in vocab, edited after Lute's status was set: Lute's still wins.
    assert merge(None, vals(status="3"), vals(status=None), 1000, 2000).to_vocab == {"status": "3"}
    assert merge(None, vals(status=None), vals(status="2"), 2000, 1000).to_lute == {"status": "2"}
    # Once linked, making a word unrated is a change like any other.
    assert merge(vals(status="3"), vals(status="3"), vals(status=None), 0, 0).to_lute == {"status": None}


def test_text_conflicts_go_to_vocab_unless_vocab_is_empty():
    m = merge(None, vals(translation="cat"), vals(translation="kitty"), 0, 0)
    assert m.to_lute == {"translation": "kitty"} and not m.to_vocab
    m = merge(None, vals(translation="cat"), vals(translation=""), 0, 0)
    assert m.to_vocab == {"translation": "cat"}


def test_values_too_long_for_lute_dont_cross():
    m = merge(None, vals(reading="x"), vals(reading="y" * 101), 0, 0)
    assert not m.to_lute and not m.to_vocab
    assert m.base["reading"] == "x"


def test_status_timestamps_are_utc():
    assert lute_timestamp_ms("1970-01-01 00:00:01") == 1000
    assert lute_timestamp_ms(None) == 0


def test_config():
    assert parse_languages(" Spanish , German=de,") == {"Spanish": "Spanish", "German": "de"}
    with pytest.raises(VocabConfigError):
        parse_languages("=de")
    env = {"LUTE_VOCAB_URL": "https://v.example/", "LUTE_VOCAB_TOKEN": "t", "LUTE_VOCAB_DECK": "Lute::{language}"}
    c = VocabConfig.from_env(env)
    assert c.enabled and c.url == "https://v.example" and c.deck_for("es") == "Lute::es"
    assert not VocabConfig.from_env({}).enabled
    with pytest.raises(VocabConfigError):
        VocabConfig.from_env({"LUTE_VOCAB_INTERVAL": "soon"})
