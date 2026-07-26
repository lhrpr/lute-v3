"""
/ai routes: AI-backed candidate translation suggestions.
"""

from flask import Blueprint, request, jsonify
from lute.ai.service import (
    SuggestionService,
    ExplanationService,
    DeepLService,
    AIServiceException,
)
from lute.db import db

bp = Blueprint("ai", __name__, url_prefix="/ai")

# Zero-width space used to join tokens in saved term text.
_ZWS = "​"


@bp.route("/suggest_translations", methods=["POST"])
def suggest_translations():
    """
    Return candidate translations for a term in context.

    Request JSON: { language_id, term, sentence }
    Response JSON:
      enabled=false  -> { "enabled": false, "reason": "..." }
      ok             -> { "enabled": true, "suggestions": [...], "cached": bool }
      error          -> { "error": "..." }  (4xx/5xx)

    The API key is never included in any response.
    """
    svc = SuggestionService(db.session)
    if not svc.is_enabled():
        return jsonify({"enabled": False, "reason": "AI suggestions are disabled."})

    data = request.get_json(silent=True) or {}
    term = (data.get("term") or "").replace(_ZWS, "").strip()
    sentence = (data.get("sentence") or "").replace(_ZWS, "").strip()
    try:
        language_id = int(data.get("language_id") or 0)
    except (TypeError, ValueError):
        language_id = 0

    try:
        result = svc.get_suggestions(language_id, term, sentence)
    except AIServiceException as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return jsonify({"error": f"Unexpected error: {ex}"}), 500

    return jsonify(
        {
            "enabled": True,
            "suggestions": result["suggestions"],
            "cached": result["cached"],
            "model": result.get("model"),
        }
    )


@bp.route("/explain", methods=["POST"])
def explain():
    """
    Explain the highlighted term/phrase in the context of its sentence.

    Request JSON: { language_id, term, sentence }
    Response JSON:
      enabled=false  -> { "enabled": false, "reason": "..." }
      ok             -> { "enabled": true, "explanation": "...", "cached": bool }
      error          -> { "error": "..." }  (4xx/5xx)

    The API key is never included in any response.
    """
    svc = ExplanationService(db.session)
    if not svc.is_enabled():
        return jsonify({"enabled": False, "reason": "AI features are disabled."})

    data = request.get_json(silent=True) or {}
    term = (data.get("term") or "").replace(_ZWS, "").strip()
    sentence = (data.get("sentence") or "").replace(_ZWS, "").strip()
    try:
        language_id = int(data.get("language_id") or 0)
    except (TypeError, ValueError):
        language_id = 0

    try:
        result = svc.get_explanation(language_id, term, sentence)
    except AIServiceException as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return jsonify({"error": f"Unexpected error: {ex}"}), 500

    return jsonify(
        {
            "enabled": True,
            "explanation": result["explanation"],
            "cached": result["cached"],
            "model": result.get("model"),
        }
    )


@bp.route("/translate", methods=["POST"])
def translate():
    """
    Translate the current sentence or paragraph with DeepL.

    The client picks the text (sentence vs paragraph) from the reading page and
    sends it here; the source language is auto-detected by DeepL.

    Request JSON: { text }
    Response JSON:
      enabled=false  -> { "enabled": false, "reason": "..." }
      ok             -> { "enabled": true, "translation": "...", "cached": bool,
                          "detected_source_lang": str|None }
      error          -> { "error": "..." }  (4xx/5xx)

    The API key is never included in any response.
    """
    svc = DeepLService(db.session)
    if not svc.is_enabled():
        return jsonify({"enabled": False, "reason": "DeepL translation is disabled."})

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").replace(_ZWS, "").strip()

    try:
        result = svc.translate(text)
    except AIServiceException as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return jsonify({"error": f"Unexpected error: {ex}"}), 500

    return jsonify(
        {
            "enabled": True,
            "translation": result["translation"],
            "cached": result["cached"],
            "detected_source_lang": result.get("detected_source_lang"),
        }
    )
