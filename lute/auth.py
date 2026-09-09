"""
Optional HTTP basic auth, for hosted Lute instances.

Lute is a single-user local app with no concept of accounts, so a
publicly-reachable deployment is wide open: anyone with the URL has full
read/write on the library.  This adds a single shared credential in front
of everything, which is enough for a personal cloud instance.

Auth is off unless LUTE_AUTH_PASSWORD is set, so local and desktop runs
are unaffected.
"""

import os
from secrets import compare_digest
from flask import request, Response


def _credentials():
    """
    Configured user/password, or (None, None) if auth is disabled.

    Only the password is required; the user defaults to "lute".
    """
    password = os.environ.get("LUTE_AUTH_PASSWORD", "").strip()
    if password == "":
        return (None, None)
    user = os.environ.get("LUTE_AUTH_USER", "").strip() or "lute"
    return (user, password)


def _is_authorized(auth, user, password):
    "True if the request's credentials match."
    if auth is None or auth.username is None or auth.password is None:
        return False
    # Both compared, and always both, so a wrong username doesn't
    # short-circuit and leak timing about the password.
    ok_user = compare_digest(auth.username, user)
    ok_password = compare_digest(auth.password, password)
    return ok_user and ok_password


def add_basic_auth(app, outfunc=None):
    """
    Guard every request with basic auth, if LUTE_AUTH_PASSWORD is set.

    No-op otherwise, so this is safe to call unconditionally.
    """
    user, password = _credentials()

    def _print(s):
        if outfunc is not None:
            outfunc(s)

    if password is None:
        _print("No LUTE_AUTH_PASSWORD set, running without auth.")
        return

    @app.before_request
    def _require_auth():  # pylint: disable=unused-variable
        "Challenge anything without valid credentials."
        if _is_authorized(request.authorization, user, password):
            return None
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Lute"'},
        )

    _print(f"Basic auth enabled for user '{user}'.")
