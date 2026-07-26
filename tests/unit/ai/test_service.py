"""
Tests for the AI suggestion service.
"""

from unittest.mock import MagicMock, patch

import pytest

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.ai.service import (
    SuggestionService,
    ExplanationService,
    DeepLService,
    AIServiceException,
    AIRateLimited,
    AITransient,
    AITimeout,
    GeminiProvider,
    _parse_suggestions,
    _build_explanation_prompt,
    _MODEL_COOLDOWN,
)


# Module-state reset and .env isolation live in conftest.py (_hermetic_ai).


# ---- _parse_suggestions ----------------------------------------------------


def test_parse_plain_json_array():
    "A clean JSON array is parsed."
    assert _parse_suggestions('["mobility", "movement"]', 3) == [
        "mobility",
        "movement",
    ]


def test_parse_strips_code_fences_and_prose():
    "JSON wrapped in fences / prose is extracted."
    text = 'Here you go:\n```json\n["mobility", "movement"]\n```'
    assert _parse_suggestions(text, 3) == ["mobility", "movement"]


def test_parse_dedupes_and_limits():
    "Duplicates removed and list capped to n."
    text = '["a", "a", "b", "c", "d"]'
    assert _parse_suggestions(text, 2) == ["a", "b"]


def test_parse_falls_back_to_lines_for_non_json():
    "A non-JSON reply still yields something usable."
    text = "- mobility\n- movement"
    assert _parse_suggestions(text, 3) == ["mobility", "movement"]


def test_parse_handles_none():
    "None input is safe."
    assert _parse_suggestions(None, 3) == []


# ---- SuggestionService -----------------------------------------------------


def _configure(session, **overrides):
    "Set AI settings for a test."
    repo = UserSettingRepository(session)
    values = {
        "ai_suggestions_enabled": "1",
        "ai_provider": "gemini",
        "ai_api_key": "test-key",
        "ai_model": "gemini-2.0-flash",
        "ai_target_language": "English",
    }
    values.update(overrides)
    for k, v in values.items():
        repo.set_value(k, v)
    session.commit()


def test_disabled_by_default(app_context):
    "Suggestions are off unless enabled."
    svc = SuggestionService(db.session)
    assert svc.is_enabled() is False


def test_missing_key_raises(app_context, spanish):
    "Enabled but no key -> clear error."
    _configure(db.session, ai_api_key="")
    svc = SuggestionService(db.session)
    assert svc.is_enabled() is True
    with pytest.raises(AIServiceException):
        svc.get_suggestions(spanish.id, "movilidad", "la movilidad urbana")


def test_get_suggestions_calls_provider_and_caches(app_context, spanish):
    "Provider is called once, second identical call is served from cache."
    _configure(db.session)
    svc = SuggestionService(db.session)

    with patch.object(
        GeminiProvider, "suggest", return_value=["mobility", "movement"]
    ) as mock_suggest:
        first = svc.get_suggestions(spanish.id, "movilidad", "la movilidad urbana")
        second = svc.get_suggestions(spanish.id, "movilidad", "la movilidad urbana")

    assert first["suggestions"] == ["mobility", "movement"]
    assert first["cached"] is False
    assert second["suggestions"] == ["mobility", "movement"]
    assert second["cached"] is True
    assert mock_suggest.call_count == 1


def test_empty_term_returns_no_suggestions(app_context, spanish):
    "Blank term short-circuits, no provider call."
    _configure(db.session)
    svc = SuggestionService(db.session)
    with patch.object(GeminiProvider, "suggest") as mock_suggest:
        result = svc.get_suggestions(spanish.id, "   ", "")
    assert result["suggestions"] == []
    assert result["cached"] is False
    mock_suggest.assert_not_called()


def test_cascade_falls_through_on_rate_limit(app_context, spanish):
    "A 429 on the first model cools it down and drops to the next model."
    _configure(db.session, ai_model="model-a,model-b")
    svc = SuggestionService(db.session)

    def fake_suggest(self, *a, **k):  # pylint: disable=unused-argument
        if self.model == "model-a":
            raise AIRateLimited("busy", retry_after=30)
        return ["ok"]

    with patch.object(GeminiProvider, "suggest", new=fake_suggest):
        result = svc.get_suggestions(spanish.id, "gato", "el gato")

    assert result["suggestions"] == ["ok"]
    assert result["model"] == "model-b"
    assert "model-a" in _MODEL_COOLDOWN  # cooled down


def test_cascade_all_rate_limited_raises(app_context, spanish):
    "If every model is rate-limited, a friendly AIRateLimited is raised."
    _configure(db.session, ai_model="model-a,model-b")
    svc = SuggestionService(db.session)

    def always_429(self, *a, **k):  # pylint: disable=unused-argument
        raise AIRateLimited("busy", retry_after=30)

    with patch.object(GeminiProvider, "suggest", new=always_429):
        with pytest.raises(AIRateLimited):
            svc.get_suggestions(spanish.id, "gato", "el gato")


def test_transient_404_retried_then_next_model(app_context, spanish):
    "A transient 404 is retried once, then the cascade moves on."
    _configure(db.session, ai_model="model-a,model-b")
    svc = SuggestionService(db.session)
    calls = {"a": 0}

    def fake_suggest(self, *a, **k):  # pylint: disable=unused-argument
        if self.model == "model-a":
            calls["a"] += 1
            raise AITransient("404")
        return ["ok"]

    with patch.object(GeminiProvider, "suggest", new=fake_suggest):
        result = svc.get_suggestions(spanish.id, "gato", "el gato")

    assert result["model"] == "model-b"
    assert calls["a"] == 2  # tried twice before moving on


def test_timeout_drops_straight_to_next_model(app_context, spanish):
    "A stalled model (AITimeout) is abandoned at once -- no same-model retry."
    _configure(db.session, ai_model="model-a,model-b")
    svc = SuggestionService(db.session)
    calls = {"a": 0}

    def fake_suggest(self, *a, **k):  # pylint: disable=unused-argument
        if self.model == "model-a":
            calls["a"] += 1
            raise AITimeout("too slow")
        return ["ok"]

    with patch.object(GeminiProvider, "suggest", new=fake_suggest):
        result = svc.get_suggestions(spanish.id, "gato", "el gato")

    assert result["model"] == "model-b"
    assert calls["a"] == 1  # tried once, not retried -- unlike a transient 404


def test_request_timeout_becomes_aitimeout():
    "A requests timeout is surfaced as AITimeout so the cascade can fall through."
    import requests as real_requests

    provider = GeminiProvider(api_key="k", model="gemini-2.5-flash-lite")
    boom = real_requests.exceptions.Timeout("read timed out")
    with patch("lute.ai.service.requests.post", side_effect=boom):
        with pytest.raises(AITimeout):
            provider.suggest("t", "s", "Spanish", "English", 3)


def test_gemini_provider_parses_response():
    "GeminiProvider extracts text from the API shape and parses it."
    fake = MagicMock()
    fake.status_code = 200
    fake.ok = True
    fake.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '["mobility", "movement"]'}]}}]
    }
    provider = GeminiProvider(api_key="secret-k", model="gemini-2.0-flash")
    with patch("lute.ai.service.requests.post", return_value=fake) as mock_post:
        out = provider.suggest("movilidad", "la movilidad", "Spanish", "English", 3)
    assert out == ["mobility", "movement"]
    assert mock_post.called
    # Key must go in a header, never in the URL / query params.
    _args, kwargs = mock_post.call_args
    assert kwargs["headers"]["x-goog-api-key"] == "secret-k"
    assert "params" not in kwargs
    assert "secret-k" not in _args[0]  # not in the URL
    # "Thinking" is disabled to keep these short lookups fast.
    assert kwargs["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


def test_gemini_error_does_not_leak_key():
    "A provider error must never expose the API key (it's in the URL of raw errors)."
    import requests as real_requests

    provider = GeminiProvider(api_key="SUPERSECRET", model="gemini-2.0-flash")
    boom = real_requests.exceptions.RequestException(
        "429 Too Many Requests for url: https://x/y?key=SUPERSECRET"
    )
    with patch("lute.ai.service.requests.post", side_effect=boom):
        with pytest.raises(AIServiceException) as exc:
            provider.suggest("t", "s", "Spanish", "English", 3)
    assert "SUPERSECRET" not in str(exc.value)


# ---- ExplanationService ----------------------------------------------------


def test_build_explanation_prompt_includes_term_sentence_and_language():
    "The prompt carries the term, sentence, and the delivery language."
    prompt = _build_explanation_prompt(
        "movilidad", "la movilidad urbana", "Spanish", "English"
    )
    assert "movilidad" in prompt
    assert "la movilidad urbana" in prompt
    assert "English" in prompt
    assert "Spanish" in prompt


def test_explanation_language_falls_back_to_target_then_english(app_context):
    "Blank explanation language falls back to the translation language."
    _configure(db.session, ai_target_language="French", ai_explanation_language="")
    svc = ExplanationService(db.session)
    assert svc._explain_language() == "French"  # pylint: disable=protected-access

    _configure(db.session, ai_explanation_language="German")
    svc = ExplanationService(db.session)
    assert svc._explain_language() == "German"  # pylint: disable=protected-access


def test_get_explanation_calls_provider_and_caches(app_context, spanish):
    "Provider is called once; the second identical call is served from cache."
    _configure(db.session)
    svc = ExplanationService(db.session)
    reply = "Means 'mobility' here; a feminine noun."

    with patch.object(GeminiProvider, "explain", return_value=reply) as mock_explain:
        first = svc.get_explanation(spanish.id, "movilidad", "la movilidad urbana")
        second = svc.get_explanation(spanish.id, "movilidad", "la movilidad urbana")

    assert first["explanation"] == reply
    assert first["cached"] is False
    assert second["explanation"] == reply
    assert second["cached"] is True
    assert mock_explain.call_count == 1


def test_get_explanation_empty_term_short_circuits(app_context, spanish):
    "Blank term returns nothing without calling the provider."
    _configure(db.session)
    svc = ExplanationService(db.session)
    with patch.object(GeminiProvider, "explain") as mock_explain:
        result = svc.get_explanation(spanish.id, "   ", "")
    assert result["explanation"] == ""
    mock_explain.assert_not_called()


def test_gemini_429_raises_rate_limited_with_retry():
    "A 429 response becomes AIRateLimited carrying Google's retryDelay."
    fake = MagicMock()
    fake.status_code = 429
    fake.headers = {}
    fake.json.return_value = {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "28s",
                }
            ]
        }
    }
    provider = GeminiProvider(api_key="k", model="gemini-3.5-flash")
    with patch("lute.ai.service.requests.post", return_value=fake):
        with pytest.raises(AIRateLimited) as exc:
            provider.suggest("t", "s", "Spanish", "English", 3)
    assert exc.value.retry_after == 28.0


# ---- DeepLService ----------------------------------------------------------


def _configure_deepl(session, **overrides):
    "Set DeepL settings for a test."
    repo = UserSettingRepository(session)
    values = {
        "deepl_enabled": "1",
        "deepl_api_key": "deepl-secret",
        "deepl_target_lang": "EN-US",
    }
    values.update(overrides)
    for k, v in values.items():
        repo.set_value(k, v)
    session.commit()


def _fake_deepl_response(text="hello world", detected="ES", status=200):
    "A MagicMock shaped like a DeepL translate response."
    fake = MagicMock()
    fake.status_code = status
    fake.ok = 200 <= status < 300
    fake.json.return_value = {
        "translations": [{"detected_source_language": detected, "text": text}]
    }
    return fake


def test_deepl_disabled_by_default(app_context):
    "DeepL is off unless enabled."
    svc = DeepLService(db.session)
    assert svc.is_enabled() is False


def test_deepl_missing_key_raises(app_context):
    "Enabled but no key -> clear error, no network call."
    _configure_deepl(db.session, deepl_api_key="")
    svc = DeepLService(db.session)
    assert svc.is_enabled() is True
    with patch("lute.ai.service.requests.post") as mock_post:
        with pytest.raises(AIServiceException):
            svc.translate("hola mundo")
    mock_post.assert_not_called()


def test_deepl_empty_text_short_circuits(app_context):
    "Blank text returns nothing without a network call."
    _configure_deepl(db.session)
    svc = DeepLService(db.session)
    with patch("lute.ai.service.requests.post") as mock_post:
        result = svc.translate("   ")
    assert result["translation"] == ""
    mock_post.assert_not_called()


def test_deepl_translate_and_cache(app_context):
    "The API is called once; the second identical call is served from cache."
    _configure_deepl(db.session)
    svc = DeepLService(db.session)
    with patch(
        "lute.ai.service.requests.post", return_value=_fake_deepl_response()
    ) as mock_post:
        first = svc.translate("hola mundo")
        second = svc.translate("hola mundo")
    assert first["translation"] == "hello world"
    assert first["detected_source_lang"] == "ES"
    assert first["cached"] is False
    assert second["cached"] is True
    assert mock_post.call_count == 1


def test_deepl_sends_target_lang_and_auth_header(app_context):
    "Target language and the auth header are set; key stays out of the URL."
    _configure_deepl(db.session, deepl_target_lang="DE")
    svc = DeepLService(db.session)
    with patch(
        "lute.ai.service.requests.post", return_value=_fake_deepl_response()
    ) as mock_post:
        svc.translate("hello")
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["target_lang"] == "DE"
    assert kwargs["json"]["text"] == ["hello"]
    assert kwargs["headers"]["Authorization"] == "DeepL-Auth-Key deepl-secret"
    assert "deepl-secret" not in args[0]  # not in the URL


def test_deepl_free_vs_pro_endpoint(app_context):
    "A ':fx' key uses the free host; anything else uses the pro host."
    _configure_deepl(db.session, deepl_api_key="abc123:fx")
    svc = DeepLService(db.session)
    with patch(
        "lute.ai.service.requests.post", return_value=_fake_deepl_response()
    ) as mock_post:
        svc.translate("hello")
    assert "api-free.deepl.com" in mock_post.call_args[0][0]

    ai_service_cache = DeepLService(db.session)  # fresh key -> pro
    from lute.ai.service import _DEEPL_CACHE

    _DEEPL_CACHE._store.clear()  # pylint: disable=protected-access
    _configure_deepl(db.session, deepl_api_key="pro-key-no-suffix")
    with patch(
        "lute.ai.service.requests.post", return_value=_fake_deepl_response()
    ) as mock_post:
        ai_service_cache.translate("hello")
    assert "://api.deepl.com" in mock_post.call_args[0][0]


def test_deepl_quota_exceeded_raises(app_context):
    "A 456 quota response becomes a friendly error."
    _configure_deepl(db.session)
    svc = DeepLService(db.session)
    resp = MagicMock()
    resp.status_code = 456
    resp.ok = False
    with patch("lute.ai.service.requests.post", return_value=resp):
        with pytest.raises(AIServiceException) as exc:
            svc.translate("hola")
    assert "quota" in str(exc.value).lower()


def test_deepl_error_does_not_leak_key(app_context):
    "A request error must never expose the DeepL key."
    import requests as real_requests

    _configure_deepl(db.session, deepl_api_key="SUPERSECRET")
    svc = DeepLService(db.session)
    boom = real_requests.exceptions.RequestException(
        "connection error for DeepL-Auth-Key SUPERSECRET"
    )
    with patch("lute.ai.service.requests.post", side_effect=boom):
        with pytest.raises(AIServiceException) as exc:
            svc.translate("hola")
    assert "SUPERSECRET" not in str(exc.value)
