"""
Shared fixtures for AI service tests.

Keeps tests hermetic: no real network calls, no module state leaking between
tests.  In particular, disables the repo-root .env API-key fallback so tests
never pick up a developer's real key (which would make live calls).
"""

import pytest

from lute.ai import service as ai_service


@pytest.fixture(autouse=True)
def _hermetic_ai(monkeypatch):
    "Reset module-level caches/cooldown and block the .env key fallback."
    ai_service._SUGGESTION_CACHE._store.clear()  # pylint: disable=protected-access
    ai_service._EXPLANATION_CACHE._store.clear()  # pylint: disable=protected-access
    ai_service._DEEPL_CACHE._store.clear()  # pylint: disable=protected-access
    ai_service._MODEL_COOLDOWN.clear()
    monkeypatch.setattr(ai_service, "_env_api_key", lambda: "")
    monkeypatch.setattr(ai_service, "_env_deepl_key", lambda: "")
    yield
