# Lute iOS — Phase 0 spike

A [Briefcase](https://briefcase.readthedocs.io/) app that runs Lute's Flask
engine **inside the app process** and shows it in a WebView.  This is the
Phase 0 spike from [`docs/ios-build-plan.md`](../docs/ios-build-plan.md): its
job is to prove the in-process model, not to be the shipped app.

iOS can't `fork`/`exec`, so the desktop shell's approach — launch
`python -m lute.main` as a child process and point a WebView at it — doesn't
port.  Instead:

- [`lute/embedded.py`](../lute/embedded.py) starts `create_app()` →
  `data_initialization()` → waitress on `127.0.0.1:<port>`, on a background
  thread, with no argparse or stdout chatter.
- [`src/luteios/app.py`](src/luteios/app.py) is a Toga app whose only widget is
  a `WebView` (a `WKWebView` on iOS) pointed at that loopback URL.  It boots
  the engine off the UI thread, because first launch has to create the schema
  and load the demo data.

Japanese is off, as planned — see "Dependencies" below.

## Prerequisites

- Xcode, **plus the Command Line Tools** (`xcode-select --install`).  Briefcase
  refuses to run without them even when full Xcode is present.
- An iOS Simulator runtime: `xcodebuild -downloadPlatform iOS` (~10 GB).
- Python 3.11 on the host.  Briefcase targets the iOS Python matching the
  interpreter it runs under.

## Setup

```bash
python3.11 -m venv ios/.venv
ios/.venv/bin/pip install briefcase
./ios/build_pure_wheels.sh
```

## Build and run

```bash
cd ios
./.venv/bin/briefcase create iOS
./.venv/bin/briefcase build iOS
./.venv/bin/briefcase run iOS
```

## Dependencies

`lute3`'s own metadata isn't used here: the engine is pulled in through
`sources = ["../lute"]`, and the dependency list is spelled out in
`pyproject.toml` instead.  Two packages in the tree can't come along:

- **natto-py** — needs cffi, and `dlopen`s `libmecab`.  Dropping it is what
  turns Japanese off.  `lute/parse/mecab_parser.py` imports `natto` inside a
  `try/except ImportError`, so `JapaneseParser.is_supported()` returns False
  and the parser registry hides Japanese.  Demo data already filters on
  `is_supported`, so first-run seeding skips the Japanese stories too.
- **greenlet** — pure C with no fallback, and no iOS wheel exists.  SQLAlchemy
  declares it behind a `platform_machine` marker, and **pip evaluates
  environment markers against the host, not the `--platform` target** — so on
  an Intel Mac the marker matches and pip demands greenlet no matter which iOS
  target is being built.  SQLAlchemy only imports it for asyncio, guarded by
  `try/except` in `sqlalchemy/util/concurrency.py`, and Lute is synchronous.

Both are excluded by installing with `--no-deps` (via
`requirement_installer_args`) against an explicit dependency closure.  That
also makes the bundle contents identical on Intel and Apple Silicon hosts.

Everything else resolves to a `py3-none-any` wheel for `arm64-iphoneos`,
`x86_64-iphonesimulator` and `arm64-iphonesimulator`, with two exceptions:
**MarkupSafe** and **PyYAML** publish only C-extension wheels, though both have
a pure-Python fallback in their source.  `build_pure_wheels.sh` builds that
fallback into `ios/wheels/`, which `--find-links` picks up.

## iPad

The Briefcase template already builds universal (`TARGETED_DEVICE_FAMILY = "1,2"`,
`UIDeviceFamily = [1, 2]`, all four orientations on iPad), so the same build runs
natively on iPad — no separate target needed.

Verified on **iPad Pro 11-inch (M5), iOS 26.4**: the book list renders the full
desktop layout, and the reading screen parses and renders on-device (the Tutorial
comes out at 663 word spans). Tapping a word opens the term form with the status
buttons, tags and dictionary tabs all working.

Three things need doing before it's pleasant, all of them plan §7 work:

- **Portrait (834 pt) trips the existing `max-width: 980px` breakpoint**, so the
  term pane comes up as a bottom overlay covering the sentence you're reading.
  That is roughly the "portrait tablet → bottom panel" tier, but currently it
  isn't toggleable and isn't sized to leave the context visible.
- **The software keyboard takes ~40% of the screen and doesn't dismiss with the
  pane.** Closing the term form with ✕ leaves the keyboard up, hiding the
  dictionary area entirely. This is the "owns keyboard avoidance" item in §5.
- **External dictionary tabs** (`collinsdiction`, `conjugator.re`) open popups
  that still need the `WKUIDelegate` → `SFSafariViewController` reroute (§5.5).

Landscape (1210 pt) does **not** trip the 980px breakpoint and falls back to the
desktop three-pane side layout. Note that this was checked in a desktop browser
sized to the iPad's viewport, not on the device — GUI automation is blocked on
this Mac so the simulator can't be rotated from the CLI. Width-based rules were
confirmed that way; `pointer: coarse` / `hover: none` report differently in a
desktop browser than in WKWebView, so any CSS keyed off those still needs an
on-device check.

## Data

`lute.embedded.write_config` writes a `config.yml` with `DATAPATH` pointing at
the app's data directory (Toga's `App.paths.data`), and everything Lute derives
from it — the SQLite db, backups, user images/audio/themes, temp — lands in the
app sandbox.  It's rewritten on every launch, because iOS gives the app
container a new path whenever the app is reinstalled.
