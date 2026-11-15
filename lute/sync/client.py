"""
The device's side of sync: talk to a hub, pull what's new, push local edits.

Settings live in syncstate, not in Lute's settings table: settings are
user-facing and get backed up and exported, and the hub password shouldn't.

    sync_url, sync_user, sync_password   where the hub is
    pull_cursor   the hub's log position this device has caught up to;
                  absent until the first sync (which then bootstraps)
    push_cursor   the local log position already sent to the hub
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from lute.sync import engine
from lute.sync.hub import PROTOCOL_VERSION

# Changes per push request.  Pages of book text average ~2 KB, so this keeps
# a request to a couple of megabytes.
PUSH_BATCH = 500


class SyncError(Exception):
    "The hub couldn't be reached, refused, or isn't usable."


@dataclass
class SyncSummary:
    "What a sync run did."

    bootstrap: str = ""
    pulled: engine.ApplyResult = field(default_factory=engine.ApplyResult)
    pushed: int = 0
    push_skipped: int = 0
    push_orphaned: int = 0

    def as_dict(self):
        return {
            "bootstrap": self.bootstrap,
            "pulled": self.pulled.as_dict(),
            "pushed": self.pushed,
            "push_skipped": self.push_skipped,
            "push_orphaned": self.push_orphaned,
        }


@dataclass
class SyncConfig:
    "Where the hub is."

    url: str = ""
    user: str = ""
    password: str = ""

    @property
    def enabled(self):
        return self.url != ""

    @staticmethod
    def load(session):
        return SyncConfig(
            url=(engine.get_state(session, "sync_url", "") or "").strip().rstrip("/"),
            user=engine.get_state(session, "sync_user", "") or "",
            password=engine.get_state(session, "sync_password", "") or "",
        )

    def save(self, session):
        engine.set_state(session, "sync_url", self.url.strip().rstrip("/") or None)
        engine.set_state(session, "sync_user", self.user.strip() or None)
        engine.set_state(session, "sync_password", self.password or None)
        session.commit()


class HubClient:
    "HTTP calls to a hub's /sync/api."

    def __init__(self, config: SyncConfig, timeout=120):
        self.url = config.url
        self.timeout = timeout
        self.http = requests.Session()
        if config.password:
            self.http.auth = (config.user or "lute", config.password)

    def _call(self, method, path, **kwargs):
        try:
            res = self.http.request(
                method, f"{self.url}/sync/api{path}", timeout=self.timeout, **kwargs
            )
        except requests.RequestException as e:
            raise SyncError(f"can't reach {self.url}: {e}") from e
        if res.status_code == 401:
            raise SyncError("the server refused the user name or password")
        if res.status_code == 404:
            raise SyncError(f"{self.url} doesn't have sync (is it running this Lute?)")
        if res.status_code != 200:
            raise SyncError(f"server: {res.status_code} {res.text.strip()[:300]}")
        try:
            return res.json()
        except ValueError as e:
            raise SyncError(f"{self.url} didn't answer with sync data") from e

    def info(self):
        return self._call("GET", "/info")

    def push(self, device, changes, bootstrap=False):
        body = {"device": device, "changes": changes, "bootstrap": bootstrap}
        return self._call("POST", "/push", json=body)

    def pull(self, since, device):
        return self._call("GET", "/pull", params={"since": since, "device": device})

    def snapshot(self, table, after):
        return self._call("GET", "/snapshot", params={"table": table, "after": after})


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class DeviceSync:
    "One sync run on a device."

    def __init__(self, session, client: HubClient):
        self.session = session
        self.client = client
        self.device = engine.device_id(session)
        self.summary = SyncSummary()
        self.applier = None

    def run(self) -> SyncSummary:
        hub = self.client.info()
        if hub.get("version") != PROTOCOL_VERSION:
            raise SyncError(
                f"the server speaks sync version {hub.get('version')}, this Lute "
                f"{PROTOCOL_VERSION}; update whichever is older"
            )
        if hub.get("device") == self.device:
            raise SyncError("the server is this same database")

        push_cursor = int(engine.get_state(self.session, "push_cursor", "0"))
        self.applier = engine.Applier(
            self.session, engine.device_local_time(self.session, push_cursor)
        )
        if engine.get_state(self.session, "pull_cursor") is None:
            self._bootstrap(hub)
        self._pull()
        self._push()
        engine.refresh_derived(self.session, self.applier)
        engine.set_state(self.session, "last_sync", _now())
        self.session.commit()
        return self.summary

    def _apply(self, changes: List[Dict[str, Any]]):
        s = self.session
        try:
            engine.begin_apply(s)
            self.summary.pulled.add(self.applier.apply(changes))
            engine.end_apply(s)
        except Exception:
            s.rollback()
            raise

    def _bootstrap(self, hub):
        """
        First sync.  Either take the hub's library (this device has only the
        demo books, and the hub has a real library), or send this device's
        whole library up and then take the hub's.
        """
        s = self.session
        if engine.contains_demo_data(s) and hub.get("books", 0) > 0:
            engine.wipe_library(s)
            self.summary.bootstrap = "replaced the demo library with the server's"
        else:
            upto = engine.max_log_id(s)
            first = True
            for spec in engine.CARRIED:
                after = 0
                more = True
                while more:
                    changes, after, more = engine.snapshot_page(s, spec.name, after, PUSH_BATCH)
                    if changes or first:
                        self._send(changes, bootstrap=first)
                        first = False
            engine.set_state(s, "push_cursor", upto)
            s.commit()
            self.summary.bootstrap = "sent this library to the server"

        # The cursor is read before the copy, so anything that changes on the
        # hub during the copy is pulled again afterwards (applying it twice is
        # harmless).
        cursor = self.client.info()["cursor"]
        push_cursor = int(engine.get_state(s, "push_cursor", "0") or 0)
        if engine.get_state(s, "push_cursor") is None:
            push_cursor = engine.max_log_id(s)
            engine.set_state(s, "push_cursor", push_cursor)
        self.applier.local_time = engine.device_local_time(s, push_cursor)
        for table in hub.get("tables", [t.name for t in engine.CARRIED]):
            after = 0
            more = True
            while more:
                page = self.client.snapshot(table, after)
                self._apply(page["changes"])
                s.commit()
                after, more = page["after"], page["more"]
        engine.set_state(s, "pull_cursor", cursor)
        s.commit()

    def _pull(self):
        s = self.session
        since = int(engine.get_state(s, "pull_cursor"))
        more = True
        while more:
            page = self.client.pull(since, self.device)
            self._apply(page["changes"])
            since, more = page["cursor"], page["more"]
            engine.set_state(s, "pull_cursor", since)
            s.commit()

    def _push(self):
        s = self.session
        after = int(engine.get_state(s, "push_cursor", "0"))
        upto = engine.max_log_id(s)
        if upto <= after:
            return
        changes = engine.local_changes(s, after, upto)
        for i in range(0, len(changes), PUSH_BATCH):
            self._send(changes[i : i + PUSH_BATCH])
        # Only once every batch is in: a failure part way resends them all
        # next time, which is harmless.
        engine.set_state(s, "push_cursor", upto)
        s.commit()

    def _send(self, changes, bootstrap=False):
        res = self.client.push(self.device, changes, bootstrap)
        self.summary.pushed += res.get("applied", 0)
        self.summary.push_skipped += res.get("skipped", 0)
        self.summary.push_orphaned += res.get("orphaned", 0)
