"""
AI-backed candidate translation suggestions.

Given a term and the sentence it appears in, ask a configured LLM provider for
a few short candidate meanings in the user's target language.  Results are
rendered as clickable candidates in the term form (see
static/js/ai-suggestions.js), giving a LingQ-style "pick a meaning" flow.

The provider is pluggable (see PROVIDERS); Gemini is the default.  A local
bilingual dictionary source could be added as another provider later.
"""

import json
import os
import re
import time

import requests

from lute.models.repositories import (
    UserSettingRepository,
    LanguageRepository,
)


# Settings keys read by this service.  All are UserSettings.
# NOTE: "ai_api_key" is a secret and must be redacted from the client-side
# LUTE_USER_SETTINGS blob (see lute/app_factory.py).
SETTING_ENABLED = "ai_suggestions_enabled"
SETTING_PROVIDER = "ai_provider"
SETTING_API_KEY = "ai_api_key"
SETTING_MODEL = "ai_model"
SETTING_TARGET_LANGUAGE = "ai_target_language"
SETTING_BASE_URL = "ai_base_url"
# Language the "explain in context" panel writes its explanation in.  Blank
# falls back to SETTING_TARGET_LANGUAGE, then "English".
SETTING_EXPLAIN_LANGUAGE = "ai_explanation_language"

# DeepL sentence/paragraph translation.  Independent of the LLM features above:
# it has its own enable flag and API key so DeepL can be used on its own.
# NOTE: "deepl_api_key" is a secret and must be redacted from the client-side
# LUTE_USER_SETTINGS blob (see lute/app_factory.py -- any "*_api_key" key is).
SETTING_DEEPL_ENABLED = "deepl_enabled"
SETTING_DEEPL_API_KEY = "deepl_api_key"
SETTING_DEEPL_TARGET_LANG = "deepl_target_lang"

# Network timeout for provider calls, in seconds.  Used by DeepL (whose calls
# are single-shot with no cascade to fall back to).
REQUEST_TIMEOUT = 20

# Per-attempt timeout for a single LLM cascade call, as (connect, read) seconds.
# Deliberately short: these are tiny, latency-sensitive lookups, so if a model
# stalls past this we abandon it and drop to the next model in the cascade
# rather than blocking the user.  This is what bounds worst-case latency to a
# few seconds regardless of how slow any one model is.
LLM_ATTEMPT_TIMEOUT = (3.05, 4.0)


class AIServiceException(Exception):
    "Raised for configuration or provider errors that the user should see."


class AIRateLimited(AIServiceException):
    "A model hit its rate limit (HTTP 429).  Carries seconds until retry."

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class AITransient(AIServiceException):
    "A transient, retryable error (e.g. a spurious 404 during propagation)."


class AITimeout(AIServiceException):
    "A model did not answer within LLM_ATTEMPT_TIMEOUT; drop to the next model."


def _parse_retry_after(resp):
    "Best-effort 'seconds to wait' from a 429 response, or None."
    header = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    if header:
        try:
            return float(header)
        except (TypeError, ValueError):
            pass
    try:
        data = resp.json()
    except (ValueError, AttributeError):
        return None
    err = data.get("error", {}) if isinstance(data, dict) else {}
    for detail in err.get("details", []) or []:
        if "RetryInfo" in detail.get("@type", "") and "retryDelay" in detail:
            m = re.match(r"([\d.]+)s", str(detail["retryDelay"]))
            if m:
                return float(m.group(1))
    m = re.search(r"retry in ([\d.]+)s", err.get("message", ""), re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _redact(text, secret):
    "Strip a secret (e.g. an API key) out of a string before showing/logging it."
    if secret and text and secret in text:
        return text.replace(secret, "***")
    return text


# Environment variable names checked (in order) for a dev-time API key when no
# key is set in Settings.  Also read from a repo-root .env file.
_ENV_KEY_NAMES = ("LUTE_AI_API_KEY", "GEMINI_API_KEY", "API_KEY")
_dotenv_cache = None


def _load_dotenv_values():
    "Minimal, dependency-free reader for a repo-root .env file.  Cached."
    global _dotenv_cache  # pylint: disable=global-statement
    if _dotenv_cache is not None:
        return _dotenv_cache
    _dotenv_cache = {}
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    envpath = os.path.join(repo_root, ".env")
    try:
        with open(envpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line == "" or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                _dotenv_cache[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return _dotenv_cache


def _env_api_key():
    "Return an API key from the environment or repo-root .env, or '' if none."
    dotenv = _load_dotenv_values()
    for name in _ENV_KEY_NAMES:
        v = os.environ.get(name) or dotenv.get(name)
        if v:
            return v.strip()
    return ""


# Environment / .env variable names checked (in order) for a dev-time DeepL key.
_ENV_DEEPL_KEY_NAMES = ("LUTE_DEEPL_API_KEY", "DEEPL_API_KEY")


def _env_deepl_key():
    "Return a DeepL key from the environment or repo-root .env, or '' if none."
    dotenv = _load_dotenv_values()
    for name in _ENV_DEEPL_KEY_NAMES:
        v = os.environ.get(name) or dotenv.get(name)
        if v:
            return v.strip()
    return ""


def _build_prompt(term, sentence, source_lang_name, target_lang, n):
    "Prompt asking for a JSON array of short glosses."
    src = source_lang_name or "the source language"
    parts = [
        f"You are a bilingual dictionary for {src} learners.",
        f"Give up to {n} short {target_lang} translations for the "
        f'{src} term "{term}".',
    ]
    if (sentence or "").strip() != "":
        parts.append(
            f"It appears in this sentence, so translate the sense used here: "
            f'"{sentence.strip()}".'
        )
    parts.append(
        "Order them most-likely first.  Each candidate must be concise "
        "(a word or short phrase), no explanations, no source-language text. "
        'Respond with ONLY a JSON array of strings, e.g. ["mobility", "movement"].'
    )
    return "  ".join(parts)


def _build_explanation_prompt(term, sentence, source_lang_name, explain_lang):
    "Prompt asking for a short, learner-friendly explanation of a term in context."
    src = source_lang_name or "the source language"
    parts = [
        f"You are a patient {src} language tutor.",
        f'Explain the {src} word or phrase "{term}" as it is used here.',
    ]
    if (sentence or "").strip() != "":
        parts.append(f'It appears in this sentence: "{sentence.strip()}".')
    parts.append(
        f"Write the explanation in {explain_lang}.  Give the meaning in this "
        "specific context, then briefly note any grammar that helps a learner "
        "(part of speech, tense/mood, gender/number, or whether it's an idiom). "
        "Keep it to a few short sentences.  Use plain text, no Markdown, and do "
        "not add greetings or extra commentary."
    )
    return "  ".join(parts)


def _parse_suggestions(text, n):
    """
    Pull a JSON array of strings out of an LLM response.

    Providers sometimes wrap the JSON in ```json ... ``` fences or add stray
    prose, so we extract the first bracketed array and parse that.
    """
    if text is None:
        return []
    cleaned = text.strip()
    # Grab the first [...] block if there's surrounding text/fences.
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Fall back to newline-splitting so a non-JSON reply is still usable.
        data = [line.strip("-*• \t") for line in text.splitlines()]
    result = []
    for item in data:
        s = str(item).strip()
        if s != "" and s not in result:
            result.append(s)
    return result[:n]


class Provider:
    "Base class for an AI provider."

    def __init__(self, api_key, model, base_url=None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def _complete(self, prompt, temperature=0.2):
        "Send a single prompt and return the model's raw text reply (or '')."
        raise NotImplementedError

    def suggest(self, term, sentence, source_lang_name, target_lang, n):
        "Return a list of candidate translation strings."
        prompt = _build_prompt(term, sentence, source_lang_name, target_lang, n)
        return _parse_suggestions(self._complete(prompt, temperature=0.2), n)

    def explain(self, term, sentence, source_lang_name, explain_lang):
        "Return a short contextual explanation of the term as free text."
        prompt = _build_explanation_prompt(
            term, sentence, source_lang_name, explain_lang
        )
        return (self._complete(prompt, temperature=0.3) or "").strip()

    def _post(self, url, headers, payload):
        "POST and return parsed JSON, raising typed errors for 429/404/timeout/other."
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=LLM_ATTEMPT_TIMEOUT
            )
        except requests.exceptions.Timeout as e:
            # Model is too slow -- treat as a fall-through so the cascade drops
            # to the next (usually faster) model instead of blocking.
            raise AITimeout(
                f"Model '{self.model}' did not respond within the time budget."
            ) from e
        except requests.exceptions.RequestException as e:
            raise AIServiceException(
                f"Request failed: {_redact(str(e), self.api_key)}"
            ) from e
        if resp.status_code == 429:
            raise AIRateLimited("Rate limited.", retry_after=_parse_retry_after(resp))
        if resp.status_code == 404:
            raise AITransient(f"Model '{self.model}' unavailable (404).")
        if not resp.ok:
            body = _redact(resp.text or "", self.api_key)[:300]
            raise AIServiceException(f"AI error {resp.status_code}: {body}")
        try:
            return resp.json()
        except ValueError as e:
            raise AIServiceException("AI returned an invalid response.") from e


class GeminiProvider(Provider):
    "Google Gemini (generativelanguage API)."

    DEFAULT_MODEL = "gemini-2.5-flash"
    ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "{model}:generateContent"
    )

    def _complete(self, prompt, temperature=0.2):
        model = self.model or self.DEFAULT_MODEL
        url = self.ENDPOINT.format(model=model)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                # Disable "thinking".  These prompts are short and low-complexity,
                # so the dynamic thinking budget on flash models just adds latency
                # for no quality gain.  0 = off; flash / flash-lite accept it.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        # Authenticate with a header, NOT a ?key= query param, so the key never
        # lands in a URL (and therefore never in an exception or log message).
        headers = {"x-goog-api-key": self.api_key}
        data = self._post(url, headers, payload)

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            # e.g. safety block, or empty candidates.
            return ""


class OpenAICompatibleProvider(Provider):
    """
    Any OpenAI-compatible chat endpoint (OpenAI, OpenRouter, local Ollama, ...).

    Configure ai_base_url to the server root, e.g. https://api.openai.com/v1
    or http://localhost:11434/v1 for Ollama.
    """

    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def _complete(self, prompt, temperature=0.2):
        base = (self.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        url = f"{base}/chat/completions"
        payload = {
            "model": self.model or self.DEFAULT_MODEL,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = self._post(url, headers, payload)

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""


PROVIDERS = {
    "gemini": GeminiProvider,
    "openai": OpenAICompatibleProvider,
}

# Default Gemini model cascade, fastest-first.  These are short, latency-
# sensitive lookups, so we lead with the quick "flash-lite" models (~1s each)
# and only fall through -- to the next model -- when one is rate-limited (429)
# or stalls past LLM_ATTEMPT_TIMEOUT.  The heavier "flash" model is kept only as
# a last-resort fallback; the slow "flash-preview" model is intentionally left
# out (it measured ~15s and would just be timed out anyway).  Used when the
# ai_model setting is left blank.  Pooling every model's free-tier rate limit
# still happens, just with speed rather than quality as the ordering key.
DEFAULT_GEMINI_CASCADE = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
]

# Fallback cooldown when a 429 response carries no retry delay.
DEFAULT_COOLDOWN_SECONDS = 60

# Shared across requests (single-user local app): model -> epoch secs until
# which that model is considered rate-limited and skipped.
_MODEL_COOLDOWN = {}


class _Cache:
    "Tiny in-memory TTL cache to avoid re-hitting the API for the same lookup."

    def __init__(self, maxsize=500, ttl=3600):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store = {}  # key -> (timestamp, value)

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key, value):
        if len(self._store) >= self.maxsize:
            # Drop the oldest entry.
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.time(), value)


# Module-level caches, shared across requests for the process lifetime.
_SUGGESTION_CACHE = _Cache()
_EXPLANATION_CACHE = _Cache()
_DEEPL_CACHE = _Cache()


def _run_model_cascade(provider_cls, api_key, base_url, models, call_fn):
    """
    Run call_fn against the configured models best-first, returning (result, model).

    Skips any model still cooling down from a recent 429, cools a model down and
    drops to the next on a rate limit, drops straight to the next model when one
    stalls past the per-attempt timeout, and retries a transient 404 once before
    moving on.  call_fn(provider) does the actual provider call and returns the
    result.  Raises AIServiceException / AIRateLimited on failure.
    """
    now = time.time()
    last_err = None
    tried_any = False
    for model in models:
        if _MODEL_COOLDOWN.get(model, 0) > now:
            continue
        tried_any = True
        provider = provider_cls(api_key=api_key, model=model, base_url=base_url)
        for attempt in range(2):  # one retry for a transient 404
            try:
                return call_fn(provider), model
            except AIRateLimited as e:
                _MODEL_COOLDOWN[model] = time.time() + (
                    e.retry_after or DEFAULT_COOLDOWN_SECONDS
                )
                last_err = e
                break  # drop to the next model
            except AITimeout as e:
                # Too slow -- don't retry the same model, go straight to the next.
                last_err = e
                break
            except AITransient as e:
                last_err = e
                if attempt == 0:
                    continue  # retry the same model once
                break  # give up on this model, try the next
            # Other AIServiceExceptions (bad key, etc.) propagate: they'd
            # fail identically on every model.

    if not tried_any and models:
        soonest = min(_MODEL_COOLDOWN.get(m, now) for m in models)
        wait = max(1, int(soonest - now))
        raise AIRateLimited(f"All AI models are rate-limited.  Try again in ~{wait}s.")
    if last_err is not None:
        raise last_err
    raise AIServiceException("No result could be generated.")


class _AIService:
    "Shared config loading / enablement for the AI-backed services."

    def __init__(self, session):
        self.session = session
        self.us_repo = UserSettingRepository(session)
        self.lang_repo = LanguageRepository(session)

    def is_enabled(self):
        "True if the user has switched on the AI features."
        return self.us_repo.get_value(SETTING_ENABLED) in (1, "1", True, "y")

    def _source_lang_name(self, language_id):
        "Name of the source (reading) language, or None."
        language = self.lang_repo.find(language_id) if language_id else None
        return language.name if language is not None else None

    def _resolve_config(self):
        "Return (provider_cls, api_key, base_url, [models]).  Raises on misconfig."
        name = (self.us_repo.get_value(SETTING_PROVIDER) or "gemini").strip()
        provider_cls = PROVIDERS.get(name)
        if provider_cls is None:
            raise AIServiceException(f"Unknown AI provider '{name}'.")

        # Dev convenience: fall back to an env var or repo-root .env
        # (LUTE_AI_API_KEY / GEMINI_API_KEY / API_KEY) if no key in Settings.
        api_key = (
            self.us_repo.get_value(SETTING_API_KEY) or ""
        ).strip() or _env_api_key()
        if api_key == "":
            raise AIServiceException(
                "No AI API key configured.  Add one in Settings (or a .env)."
            )

        base_url = (self.us_repo.get_value(SETTING_BASE_URL) or "").strip() or None

        # ai_model may be a single model or a comma-separated cascade.
        raw = (self.us_repo.get_value(SETTING_MODEL) or "").strip()
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if not models:
            models = (
                list(DEFAULT_GEMINI_CASCADE)
                if name == "gemini"
                else [provider_cls.DEFAULT_MODEL]
            )
        return provider_cls, api_key, base_url, models


class SuggestionService(_AIService):
    "Load config, dispatch to a provider, cache candidate translations."

    def get_suggestions(self, language_id, term, sentence, n=3):
        """
        Return {"suggestions": [...], "cached": bool, "model": str}.

        Tries the configured models best-first, skipping any that are still
        cooling down from a recent 429, and falling through on rate limits or
        transient 404s.  Raises AIServiceException on config/fatal errors.
        """
        term = (term or "").strip()
        if term == "":
            return {"suggestions": [], "cached": False, "model": None}

        cache_key = (
            int(language_id or 0),
            term.lower(),
            hash((sentence or "").strip()),
        )
        cached = _SUGGESTION_CACHE.get(cache_key)
        if cached is not None:
            return {"suggestions": cached, "cached": True, "model": None}

        source_lang_name = self._source_lang_name(language_id)
        target_lang = (
            self.us_repo.get_value(SETTING_TARGET_LANGUAGE) or "English"
        ).strip() or "English"

        provider_cls, api_key, base_url, models = self._resolve_config()
        suggestions, model = _run_model_cascade(
            provider_cls,
            api_key,
            base_url,
            models,
            lambda p: p.suggest(term, sentence, source_lang_name, target_lang, n),
        )
        _SUGGESTION_CACHE.set(cache_key, suggestions)
        return {"suggestions": suggestions, "cached": False, "model": model}


class ExplanationService(_AIService):
    "Explain the highlighted term/phrase in the context of its sentence."

    def _explain_language(self):
        "Language the explanation is written in.  Falls back to target, then English."
        return (
            (self.us_repo.get_value(SETTING_EXPLAIN_LANGUAGE) or "").strip()
            or (self.us_repo.get_value(SETTING_TARGET_LANGUAGE) or "").strip()
            or "English"
        )

    def get_explanation(self, language_id, term, sentence):
        """
        Return {"explanation": str, "cached": bool, "model": str}.

        Shares the model cascade / rate-limit handling with suggestions.
        """
        term = (term or "").strip()
        if term == "":
            return {"explanation": "", "cached": False, "model": None}

        explain_lang = self._explain_language()
        cache_key = (
            int(language_id or 0),
            term.lower(),
            hash((sentence or "").strip()),
            explain_lang.lower(),
        )
        cached = _EXPLANATION_CACHE.get(cache_key)
        if cached is not None:
            return {"explanation": cached, "cached": True, "model": None}

        source_lang_name = self._source_lang_name(language_id)

        provider_cls, api_key, base_url, models = self._resolve_config()
        explanation, model = _run_model_cascade(
            provider_cls,
            api_key,
            base_url,
            models,
            lambda p: p.explain(term, sentence, source_lang_name, explain_lang),
        )
        _EXPLANATION_CACHE.set(cache_key, explanation)
        return {"explanation": explanation, "cached": False, "model": model}


class DeepLService:
    """
    Translate the current sentence or paragraph via the DeepL REST API.

    Independent of the LLM-backed services: its own enable flag / API key, so a
    user can run DeepL on its own.  The source language is left to DeepL's
    auto-detection; only a target language *code* is configured.  The free vs
    pro endpoint is chosen automatically from the key (free keys end ":fx").
    """

    FREE_HOST = "https://api-free.deepl.com"
    PRO_HOST = "https://api.deepl.com"
    DEFAULT_TARGET_LANG = "EN-US"

    def __init__(self, session):
        self.session = session
        self.us_repo = UserSettingRepository(session)

    def is_enabled(self):
        "True if the user has switched on DeepL translation."
        return self.us_repo.get_value(SETTING_DEEPL_ENABLED) in (1, "1", True, "y")

    def _api_key(self):
        "Configured DeepL key, falling back to an env var / repo-root .env."
        key = (self.us_repo.get_value(SETTING_DEEPL_API_KEY) or "").strip()
        return key or _env_deepl_key()

    def _target_lang(self):
        "Configured DeepL target language code, e.g. EN-US."
        raw = (self.us_repo.get_value(SETTING_DEEPL_TARGET_LANG) or "").strip()
        return raw or self.DEFAULT_TARGET_LANG

    def _endpoint(self, api_key):
        "DeepL free keys end in ':fx'; everything else is the pro endpoint."
        host = self.FREE_HOST if api_key.rstrip().endswith(":fx") else self.PRO_HOST
        return f"{host}/v2/translate"

    def translate(self, text):
        """
        Return {"translation": str, "cached": bool, "detected_source_lang": str|None}.

        Raises AIServiceException (or AIRateLimited) on config / provider errors.
        The API key is never included in any raised message.
        """
        text = (text or "").strip()
        target = self._target_lang()
        if text == "":
            return {"translation": "", "cached": False, "detected_source_lang": None}

        cache_key = (target.lower(), hash(text))
        cached = _DEEPL_CACHE.get(cache_key)
        if cached is not None:
            translation, detected = cached
            return {
                "translation": translation,
                "cached": True,
                "detected_source_lang": detected,
            }

        api_key = self._api_key()
        if api_key == "":
            raise AIServiceException(
                "No DeepL API key configured.  Add one in Settings (or a .env)."
            )

        url = self._endpoint(api_key)
        headers = {
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": [text], "target_lang": target}
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
            )
        except requests.exceptions.RequestException as e:
            raise AIServiceException(
                f"DeepL request failed: {_redact(str(e), api_key)}"
            ) from e

        if resp.status_code in (401, 403):
            raise AIServiceException("DeepL rejected the API key.  Check it in Settings.")
        if resp.status_code == 429:
            raise AIRateLimited("DeepL is rate-limiting requests.  Try again shortly.")
        if resp.status_code == 456:
            raise AIServiceException("DeepL quota exceeded for this billing period.")
        if not resp.ok:
            body = _redact(resp.text or "", api_key)[:300]
            raise AIServiceException(f"DeepL error {resp.status_code}: {body}")

        try:
            translation_obj = resp.json()["translations"][0]
            translation = translation_obj.get("text", "")
            detected = translation_obj.get("detected_source_language")
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AIServiceException("DeepL returned an unexpected response.") from e

        _DEEPL_CACHE.set(cache_key, (translation, detected))
        return {
            "translation": translation,
            "cached": False,
            "detected_source_lang": detected,
        }
