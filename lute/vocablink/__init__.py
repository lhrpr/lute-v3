"""
Link to a vocab (anki-alternative) server.

Lute's learning terms (status 1-5) are created as vocab words, status and
field changes are sent on, and each read page sends the sentence a learning
term was met in, with the book as the word's source.  Runs on one instance
(the cloud Lute), configured by LUTE_VOCAB_* environment variables (see
config.py), every few minutes in the background and on demand.

The plan, including what isn't done yet, is in docs/vocab-integration-plan.md.
"""

import threading
import time
import traceback
from datetime import datetime

from lute.vocablink.config import VocabConfig, VocabConfigError

# One run at a time, whether from the background thread, the route or the CLI.
_run_lock = threading.Lock()

# The last run's outcome, for the status route.
last_run = {}


class AlreadyRunning(Exception):
    "Another run is in progress."


def run_once(session, config: VocabConfig = None):
    "Run the link once; returns a RunSummary."
    # Imported here so that importing the package doesn't pull in the models.
    from lute.vocablink.service import Service  # pylint: disable=import-outside-toplevel

    config = config or VocabConfig.from_env()
    if not _run_lock.acquire(blocking=False):  # pylint: disable=consider-using-with
        raise AlreadyRunning("a vocab link run is already in progress")
    started = datetime.now().isoformat(timespec="seconds")
    try:
        summary = Service(session, config).run()
        last_run.clear()
        last_run.update({"started": started, "ok": True, **summary.as_dict()})
        return summary
    except Exception as e:
        session.rollback()
        last_run.clear()
        last_run.update({"started": started, "ok": False, "error": str(e)})
        raise
    finally:
        _run_lock.release()


def start_background(app, outfunc=print):
    "Run the link every LUTE_VOCAB_INTERVAL seconds, if it's configured."
    try:
        config = VocabConfig.from_env()
    except VocabConfigError as e:
        # A typo in the link settings shouldn't stop Lute itself from starting.
        outfunc(f"vocab link off: {e}")
        return None
    if not config.enabled or config.interval <= 0:
        return None
    # pylint: disable=import-outside-toplevel
    from lute.db import db

    def loop():
        while True:
            time.sleep(config.interval)
            with app.app_context():
                try:
                    summary = run_once(db.session, config)
                    outfunc(f"vocab link: {summary.as_dict()}")
                except AlreadyRunning:
                    pass
                except Exception:  # pylint: disable=broad-exception-caught
                    outfunc(f"vocab link failed:\n{traceback.format_exc()}")
                finally:
                    db.session.remove()

    outfunc(f"vocab link: {config.url} every {config.interval}s, languages {config.languages}")
    thread = threading.Thread(target=loop, name="vocablink", daemon=True)
    thread.start()
    return thread
