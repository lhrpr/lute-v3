"""
Vocab link routes: run now, and the last run's outcome.
"""

from flask import Blueprint, jsonify

from lute.db import db
from lute.vocablink import AlreadyRunning, last_run, run_once
from lute.vocablink.client import VocabError
from lute.vocablink.config import VocabConfig, VocabConfigError

bp = Blueprint("vocablink", __name__, url_prefix="/vocablink")


@bp.route("/status", methods=["GET"])
def status():
    "Whether the link is configured, and how the last run went."
    config = VocabConfig.from_env()
    return jsonify(
        {
            "enabled": config.enabled,
            "url": config.url,
            "languages": config.languages,
            "last_run": last_run or None,
        }
    )


@bp.route("/sync", methods=["POST"])
def sync():
    "Run the link now."
    try:
        config = VocabConfig.from_env()
        if not config.enabled:
            return jsonify({"error": "set LUTE_VOCAB_URL and LUTE_VOCAB_TOKEN"}), 400
        return jsonify(run_once(db.session, config).as_dict())
    except AlreadyRunning as e:
        return jsonify({"error": str(e)}), 409
    except VocabConfigError as e:
        return jsonify({"error": str(e)}), 400
    except VocabError as e:
        return jsonify({"error": str(e)}), 502
