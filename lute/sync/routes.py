"""
Sync routes.

  /sync/api/*   the hub API that devices call (JSON)
  /sync/        the Sync page: point this Lute at a hub, sync now
"""

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from lute.db import db
from lute.sync import AlreadyRunning, engine, hub, last_run, run_once
from lute.sync.client import SyncConfig, SyncError
from lute.sync.schema import SyncSchemaError

bp = Blueprint("sync", __name__, url_prefix="/sync")


def _bad(e, code=400):
    return jsonify({"error": str(e)}), code


@bp.route("/api/info", methods=["GET"])
def api_info():
    "Hub details."
    return jsonify(hub.info(db.session))


@bp.route("/api/push", methods=["POST"])
def api_push():
    "Receive a device's changes."
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("changes"), list):
        return _bad("expected {device, changes: [...]}")
    try:
        result = hub.receive(
            db.session, body.get("device"), body["changes"], bool(body.get("bootstrap"))
        )
    except SyncSchemaError as e:
        return _bad(e)
    return jsonify(result.as_dict())


@bp.route("/api/pull", methods=["GET"])
def api_pull():
    "Changes a device hasn't seen."
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        return _bad("since must be a number")
    device = request.args.get("device", "")
    if not device:
        return _bad("device missing")
    return jsonify(hub.pull(db.session, since, device))


@bp.route("/api/snapshot", methods=["GET"])
def api_snapshot():
    "A page of a full table copy."
    try:
        after = int(request.args.get("after", "0"))
        return jsonify(hub.snapshot(db.session, request.args.get("table", ""), after))
    except (ValueError, SyncSchemaError) as e:
        return _bad(e)


@bp.route("/", methods=["GET"])
def index():
    "The Sync page."
    s = db.session
    return render_template(
        "sync/index.html",
        config=SyncConfig.load(s),
        device=engine.device_id(s),
        last_sync=engine.get_state(s, "last_sync"),
        bootstrapped=engine.get_state(s, "pull_cursor") is not None,
        last_run=last_run,
    )


@bp.route("/settings", methods=["POST"])
def save_settings():
    "Save the hub address and credentials."
    s = db.session
    old = SyncConfig.load(s)
    config = SyncConfig(
        url=request.form.get("url", ""),
        user=request.form.get("user", ""),
        # Blank keeps the saved password, so it needn't be shown in the form.
        password=request.form.get("password", "") or old.password,
    )
    if request.form.get("clear_password"):
        config.password = ""
    if config.url.strip().rstrip("/") != old.url:
        # A different hub has a different log: start over with a bootstrap.
        engine.set_state(s, "pull_cursor", None)
    config.save(s)
    return redirect(url_for("sync.index"))


@bp.route("/run", methods=["POST"])
def run():
    "Sync now."
    try:
        return jsonify(run_once(db.session).as_dict())
    except AlreadyRunning as e:
        return _bad(e, 409)
    except (SyncError, SyncSchemaError) as e:
        return _bad(e, 502)


@bp.route("/status", methods=["GET"])
def status():
    "The last run."
    return jsonify({"last_run": last_run or None, "last_sync": engine.get_state(db.session, "last_sync")})
