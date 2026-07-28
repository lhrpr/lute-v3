"""
Toga shell for the Lute iOS spike.

The desktop app spawns `python -m lute.main` and points a WebView at it.  iOS
has no fork/exec, so this boots Lute's WSGI app inside this process instead
(lute.embedded) and points a WKWebView -- via toga.WebView -- at the loopback
address waitress serves on.
"""

import asyncio
import traceback

import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from lute.embedded import find_free_port, start_in_thread

# Shown while the engine boots.  First launch has to create the schema, run
# migrations and load the demo data, so it isn't instantaneous.
LOADING_HTML = """
<html><head><meta name="viewport" content="width=device-width, initial-scale=1.0">
</head><body style="font-family: -apple-system, sans-serif; text-align: center;
padding-top: 40%;"><h2>Lute</h2><p>Starting the reading engine…</p></body></html>
"""

FAILED_HTML = """
<html><head><meta name="viewport" content="width=device-width, initial-scale=1.0">
</head><body style="font-family: -apple-system, sans-serif; padding: 2em;">
<h2>Lute couldn't start</h2><pre style="white-space: pre-wrap; font-size: 0.8em;"
>{details}</pre></body></html>
"""


def log(message):
    "Print to the iOS system log -- Briefcase wires stdout into it."
    print(f"[luteios] {message}", flush=True)


class LuteApp(toga.App):
    "Hosts the embedded Lute engine behind a WebView."

    def startup(self):
        """
        Build the UI only.  The engine boots in on_running, so that a slow
        first launch doesn't block the UI thread and trip the iOS watchdog.
        """
        self.webview = toga.WebView(style=Pack(flex=1))
        self.webview.set_content("http://localhost/", LOADING_HTML)

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = toga.Box(
            children=[self.webview],
            style=Pack(direction=COLUMN, flex=1),
        )
        self.main_window.show()

    async def on_running(self, **kwargs):  # pylint: disable=unused-argument
        "Boot the engine off the UI thread, then load it in the WebView."
        loop = asyncio.get_running_loop()
        try:
            url = await loop.run_in_executor(None, self._boot_engine)
        except Exception:  # pylint: disable=broad-exception-caught
            details = traceback.format_exc()
            log(f"startup failed:\n{details}")
            self.webview.set_content(
                "http://localhost/", FAILED_HTML.format(details=details)
            )
            return

        log(f"serving at {url}")
        self.webview.url = url

    def _boot_engine(self):
        "Runs on a worker thread.  Returns the URL Lute is serving on."
        datapath = str(self.paths.data)
        port = find_free_port()
        log(f"datapath {datapath}, port {port}")
        start_in_thread(datapath, port=port, output_func=log)
        return f"http://127.0.0.1:{port}/"


def main():
    "Briefcase entry point."
    return LuteApp()
