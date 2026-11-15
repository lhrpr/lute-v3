"""
The hub's side of sync: what a hosted Lute does when devices talk to it.

Any Lute can act as a hub - it's the same app - but in practice it's the cloud
instance, protected by LUTE_AUTH_PASSWORD (see lute/auth.py).
"""

import threading

from lute.sync import engine
from lute.sync.schema import SPECS, SyncSchemaError

# Version of the device <-> hub protocol.  A device refuses a hub with a
# different one rather than half-syncing.
PROTOCOL_VERSION = 1

# One apply at a time: two devices pushing at once would otherwise compare
# against each other's half-written changes.
_apply_lock = threading.Lock()


def info(session):
    "What a device needs before it starts."
    return {
        "version": PROTOCOL_VERSION,
        "device": engine.device_id(session),
        "cursor": engine.max_log_id(session),
        "books": engine.book_count(session),
        "demo": engine.contains_demo_data(session),
        "tables": [s.name for s in engine.CARRIED],
    }


def receive(session, device, changes, bootstrap=False):
    """
    Apply a device's changes, and log them for everyone else.

    `bootstrap` marks the first batch of a device's initial upload.  If this
    hub still holds nothing but Lute's demo library, it's cleared first, so
    the demo books don't end up in the device's real library.
    """
    if not device:
        raise SyncSchemaError("device id missing")
    if device == engine.device_id(session):
        raise SyncSchemaError("that device id is the hub's own")
    with _apply_lock:
        if bootstrap and engine.contains_demo_data(session):
            engine.wipe_library(session)
        applier = engine.Applier(session, engine.hub_local_time(session))
        try:
            engine.begin_apply(session)
            result = applier.apply(changes)
            engine.log_received(session, result.written, device)
            engine.end_apply(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        engine.refresh_derived(session, applier)
    return result


def pull(session, since, device, limit=2000):
    "Changes the device hasn't seen."
    changes, cursor, more = engine.changes_for_peer(session, since, device, limit)
    return {"changes": changes, "cursor": cursor, "more": more}


def snapshot(session, table, after, limit=1000):
    "One page of a full copy of a table."
    if table not in SPECS or table in engine.NOT_CARRIED:
        raise SyncSchemaError(f"'{table}' is not carried")
    changes, last, more = engine.snapshot_page(session, table, after, limit)
    return {"changes": changes, "after": last, "more": more}
