"""
Tests for the /ai routes and the API-key redaction.
"""

from unittest.mock import patch

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.ai.service import (
    GeminiProvider,
    DeepLService,
    _SUGGESTION_CACHE,
    _EXPLANATION_CACHE,
    _DEEPL_CACHE,
)


def _enable(session, **overrides):
    "Turn on AI suggestions with a key."
    repo = UserSettingRepository(session)
    values = {
        "ai_suggestions_enabled": "1",
        "ai_provider": "gemini",
        "ai_api_key": "secret-key-123",
        "ai_model": "gemini-2.0-flash",
    }
    values.update(overrides)
    for k, v in values.items():
        repo.set_value(k, v)
    session.commit()
    from lute.settings.current import refresh_global_settings

    refresh_global_settings(session)


def _enable_deepl(session, **overrides):
    "Turn on DeepL translation with a key."
    repo = UserSettingRepository(session)
    values = {
        "deepl_enabled": "1",
        "deepl_api_key": "deepl-secret-999",
        "deepl_target_lang": "EN-US",
    }
    values.update(overrides)
    for k, v in values.items():
        repo.set_value(k, v)
    session.commit()
    from lute.settings.current import refresh_global_settings

    refresh_global_settings(session)


def test_suggest_disabled_returns_enabled_false(client):
    "Off by default -> enabled=false, no error."
    resp = client.post("/ai/suggest_translations", json={"term": "movilidad"})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "enabled": False,
        "reason": "AI suggestions are disabled.",
    }


def test_suggest_missing_key_returns_error(client, app_context):
    "Enabled but no key -> 400 with an error message."
    _enable(db.session, ai_api_key="")
    resp = client.post(
        "/ai/suggest_translations",
        json={"term": "movilidad", "sentence": "la movilidad", "language_id": 0},
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_suggest_with_mocked_provider(client, app_context):
    "Enabled + key + provider mocked -> suggestions returned."
    _SUGGESTION_CACHE._store.clear()  # pylint: disable=protected-access
    _enable(db.session)
    with patch.object(GeminiProvider, "suggest", return_value=["mobility", "movement"]):
        resp = client.post(
            "/ai/suggest_translations",
            json={"term": "movilidad", "sentence": "la movilidad", "language_id": 0},
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["suggestions"] == ["mobility", "movement"]


def test_api_key_never_reaches_browser(client, app_context):
    "The secret key must not be serialized into any page's LUTE_USER_SETTINGS."
    _enable(db.session)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "secret-key-123" not in html
    assert "ai_api_key" not in html
    # Non-secret AI flags are still exposed for the client JS.
    assert "ai_suggestions_enabled" in html


def test_settings_page_shows_ai_section(client):
    "The settings form exposes the new AI fields."
    resp = client.get("/settings/index")
    html = resp.get_data(as_text=True)
    assert "AI translation suggestions" in html
    assert 'name="ai_api_key"' in html
    assert 'type="password"' in html
    # The context-explanation language field is present.
    assert 'name="ai_explanation_language"' in html


def test_settings_page_shows_deepl_section(client):
    "The settings form exposes the DeepL fields, with the key as a password."
    resp = client.get("/settings/index")
    html = resp.get_data(as_text=True)
    assert "DeepL translation" in html
    assert 'name="deepl_api_key"' in html
    assert 'name="deepl_target_lang"' in html


def test_deepl_key_never_reaches_browser(client, app_context):
    "The DeepL key must not be serialized into any page's LUTE_USER_SETTINGS."
    _enable_deepl(db.session)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "deepl-secret-999" not in html
    assert "deepl_api_key" not in html
    # The non-secret enable flag is still exposed for the client JS.
    assert "deepl_enabled" in html


# ---- /ai/explain -----------------------------------------------------------


def test_explain_disabled_returns_enabled_false(client):
    "Off by default -> enabled=false, no error."
    resp = client.post("/ai/explain", json={"term": "movilidad"})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "enabled": False,
        "reason": "AI features are disabled.",
    }


def test_explain_missing_key_returns_error(client, app_context):
    "Enabled but no key -> 400 with an error message."
    _enable(db.session, ai_api_key="")
    resp = client.post(
        "/ai/explain",
        json={"term": "movilidad", "sentence": "la movilidad", "language_id": 0},
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_explain_with_mocked_provider(client, app_context):
    "Enabled + key + provider mocked -> explanation returned."
    _EXPLANATION_CACHE._store.clear()  # pylint: disable=protected-access
    _enable(db.session)
    reply = "It means 'mobility'; a feminine noun used here as the subject."
    with patch.object(GeminiProvider, "explain", return_value=reply):
        resp = client.post(
            "/ai/explain",
            json={
                "term": "movilidad",
                "sentence": "la movilidad urbana",
                "language_id": 0,
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["explanation"] == reply


# ---- /ai/translate (DeepL) -------------------------------------------------


def test_translate_disabled_returns_enabled_false(client):
    "Off by default -> enabled=false, no error."
    resp = client.post("/ai/translate", json={"text": "hola mundo"})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "enabled": False,
        "reason": "DeepL translation is disabled.",
    }


def test_translate_missing_key_returns_error(client, app_context):
    "Enabled but no key -> 400 with an error message."
    _enable_deepl(db.session, deepl_api_key="")
    resp = client.post("/ai/translate", json={"text": "hola mundo"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_translate_with_mocked_deepl(client, app_context):
    "Enabled + key + DeepL mocked -> translation returned."
    _DEEPL_CACHE._store.clear()  # pylint: disable=protected-access
    _enable_deepl(db.session)
    result = {"translation": "hello world", "cached": False, "detected_source_lang": "ES"}
    with patch.object(DeepLService, "translate", return_value=result):
        resp = client.post("/ai/translate", json={"text": "hola mundo"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["translation"] == "hello world"
    assert data["detected_source_lang"] == "ES"
