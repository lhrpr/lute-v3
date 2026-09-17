"""
In-process launcher, for hosts that can't spawn a Lute subprocess.

The desktop shell starts Lute by running `python -m lute.main` as a child
process and pointing a WebView at it.  iOS has no fork/exec, so an iOS host
has to run the WSGI app inside its own interpreter instead.

This module is that entry point: same startup sequence as lute.main._start,
but without argparse, stdout chatter, or sys.exit.  Hand it a data directory
and a port, and it serves.

e.g. from an embedded interpreter:

    from lute.embedded import start_in_thread
    start_in_thread("/path/to/Application Support/lute")
"""

import logging
import os
import socket
import threading
import time

import yaml
from waitress import serve

from lute.app_factory import create_app, data_initialization
from lute.db import db

logging.getLogger("waitress.queue").setLevel(logging.ERROR)
logging.getLogger("natto").setLevel(logging.CRITICAL)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001


def write_config(datapath, dbname="lute.db", config_path=None):
    """
    Write a config.yml pinning Lute's data to datapath, return its path.

    The bundled lute/config directory is read-only when Lute ships inside an
    app bundle, so the config lives alongside the user data instead.  It's
    rewritten on every launch on purpose: iOS gives the app container a new
    path whenever the app is reinstalled, so a stale DATAPATH would point at
    a directory that no longer exists.
    """
    os.makedirs(datapath, exist_ok=True)
    config_path = config_path or os.path.join(datapath, "config.yml")
    config = {
        "ENV": "prod",
        "DBNAME": dbname,
        "DATAPATH": datapath,
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False)
    return config_path


def create_lute_app(datapath, dbname="lute.db", output_func=None):
    """
    Create the Flask app rooted at datapath, running the usual first-run
    setup (directories, baseline schema, migrations, pre-migration backup,
    demo data).  Returns the app.
    """
    config_path = write_config(datapath, dbname)
    app = create_app(config_path, output_func=output_func)
    with app.app_context():
        data_initialization(db.session, output_func)
    return app


def find_free_port(host=DEFAULT_HOST):
    "Ask the OS for an unused port on host."
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def is_serving(port, host=DEFAULT_HOST, timeout=0.5):
    "True if something accepts a connection on host:port."
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def start(
    datapath, port=DEFAULT_PORT, host=DEFAULT_HOST, dbname="lute.db", output_func=None
):
    """
    Create the app and serve it.  Blocks forever -- call it on a background
    thread, or use start_in_thread.
    """
    app = create_lute_app(datapath, dbname=dbname, output_func=output_func)
    serve(app, host=host, port=port)


def start_in_thread(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    datapath,
    port=DEFAULT_PORT,
    host=DEFAULT_HOST,
    dbname="lute.db",
    output_func=None,
    timeout=120.0,
):
    """
    Start Lute on a daemon thread, and return that thread once the server is
    accepting connections.

    Raises RuntimeError if startup fails or takes longer than timeout.  First
    launch does the schema baseline and demo data load, so it isn't instant.
    """
    failures = []

    def _run():
        try:
            start(
                datapath,
                port=port,
                host=host,
                dbname=dbname,
                output_func=output_func,
            )
        except BaseException as e:  # pylint: disable=broad-exception-caught
            failures.append(e)

    thread = threading.Thread(target=_run, name="lute-server", daemon=True)
    thread.start()

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_serving(port, host):
            return thread
        if failures:
            raise RuntimeError(f"Lute failed to start: {failures[0]}") from failures[0]
        if not thread.is_alive():
            raise RuntimeError("Lute server thread exited without serving.")
        time.sleep(0.1)

    raise RuntimeError(f"Lute didn't start listening on {host}:{port} in {timeout}s.")
