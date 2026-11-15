"""
Multi-device sync.

Lute runs locally on each device (desktop shell, iOS host) against its own
SQLite file, and a hosted instance acts as the meeting point (the hub).  This
package carries changes between them so every device can be edited offline
and reconciled later.

  schema.py   what travels, addressed by natural keys
  engine.py   reading changes out of the log, applying a peer's changes in
  hub.py      the hosted instance's side (/sync/api/*)
  client.py   a device's side: bootstrap, pull, push
  routes.py   the API, and the Sync page where a device is pointed at a hub

Unrelated to words.WoSyncStatus, which means "this term follows its parent's
status".  See lute/db/schema/migrations_repeatable/trig_synclog.sql.
"""

import os
import threading
import time
import traceback
from datetime import datetime

# One run at a time, whether from the background thread or "Sync now".
_run_lock = threading.Lock()

# The last run's outcome, for the Sync page.
last_run = {}


class AlreadyRunning(Exception):
    "Another run is in progress."


def run_once(session):
    "Sync this device with its hub once; returns a SyncSummary."
    # pylint: disable=import-outside-toplevel
    from lute.sync.client import DeviceSync, HubClient, SyncConfig, SyncError

    config = SyncConfig.load(session)
    if not config.enabled:
        raise SyncError("no server set")
    if not _run_lock.acquire(blocking=False):  # pylint: disable=consider-using-with
        raise AlreadyRunning("a sync is already running")
    started = datetime.now().isoformat(timespec="seconds")
    try:
        summary = DeviceSync(session, HubClient(config)).run()
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
    """
    Sync every LUTE_SYNC_INTERVAL seconds (default 300; 0 = off), whenever a
    server is set on the Sync page.  The first run is shortly after startup.
    """
    try:
        interval = int(os.environ.get("LUTE_SYNC_INTERVAL", "300"))
    except ValueError:
        outfunc("sync: LUTE_SYNC_INTERVAL must be a number of seconds; background sync off")
        return None
    if interval <= 0:
        return None
    # pylint: disable=import-outside-toplevel
    from lute.db import db
    from lute.sync.client import SyncConfig

    def loop():
        delay = 15
        while True:
            time.sleep(delay)
            delay = interval
            with app.app_context():
                try:
                    if SyncConfig.load(db.session).enabled:
                        summary = run_once(db.session)
                        outfunc(f"sync: {summary.as_dict()}")
                except AlreadyRunning:
                    pass
                except Exception:  # pylint: disable=broad-exception-caught
                    outfunc(f"sync failed:\n{traceback.format_exc()}")
                finally:
                    db.session.remove()

    thread = threading.Thread(target=loop, name="sync", daemon=True)
    thread.start()
    return thread
