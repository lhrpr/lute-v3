# Lute Desktop (Tauri)

A native desktop shell for Lute v3. It launches the normal Lute Flask server on
a private local port and shows it in a native window — no browser needed. It can
also connect to a **remote** Lute server instead, so the same app can act as a
native client for a self-hosted instance.

This is the *lightweight* build: it runs Lute from a Python environment on the
machine (it does **not** bundle Python). See "Roadmap" for the fully
self-contained option.

## Requirements

- **Rust** (stable) + **Cargo** — https://rustup.rs
- **Node.js** 18+ and **npm**
- **Python** with Lute installed (see below)
- Platform WebView + Tauri build deps:
  - macOS: Xcode Command Line Tools (`xcode-select --install`)
  - Windows: WebView2 runtime (preinstalled on Win 11) + MSVC build tools
  - Linux: `webkit2gtk`, `libappindicator`, `librsvg`, `patchelf` (see the
    [Tauri prerequisites](https://tauri.app/start/prerequisites/))

## One-time setup

From the **repository root** (the parent of this `desktop/` folder):

```bash
# 1. Language definitions submodule (needed by Lute at first run)
git submodule update --init lute/db/language_defs

# 2. A Python environment with Lute installed
python3 -m venv .venv
./.venv/bin/pip install -e .        # Windows: .venv\Scripts\pip install -e .
```

Then install the desktop app's dev dependency and generate icons:

```bash
cd desktop
npm install
npm run icon -- ../<path-to-a-1024x1024-png>   # optional; icons/ is prebuilt-friendly
```

> The app auto-discovers Python in this order: `LUTE_PYTHON` env var → the repo
> `.venv` → `python3`/`python` on `PATH`. If your environment lives elsewhere,
> set `LUTE_PYTHON=/path/to/python`.

## Run (development)

```bash
cd desktop
npm run dev
```

A window opens showing a splash while the Lute server boots, then loads Lute.
Closing the window stops the server.

## Build a distributable app

```bash
cd desktop
npm run build
```

Output (per OS) lands in `src-tauri/target/release/bundle/`:

- macOS: `.app` and `.dmg`
- Windows: `.msi` / `.exe` (NSIS)
- Linux: `.AppImage` / `.deb`

> Tauri does **not** cross-compile. Build each platform on that platform.
> The produced app still needs a Python environment with Lute available; point
> it at one with `LUTE_PYTHON` (and `LUTE_ROOT` if the checkout moves).

### Troubleshooting the build

- **macOS `.dmg` step fails (`bundle_dmg.sh`)**: the DMG layout step scripts
  Finder via AppleScript. In non-interactive/SSH/CI sessions this fails with
  `Not authorised to send Apple events to Finder (-1743)`; the `.app` is still
  built fine. Fixes: run the build from a normal desktop login session (grant
  your terminal *System Settings → Privacy & Security → Automation → Finder* if
  prompted), or skip the DMG with `npm run build -- --bundles app` and
  distribute the `.app` (e.g. zipped). Detach a stale volume first with
  `hdiutil detach /Volumes/Lute -force` if one is mounted.
- **macOS "app is damaged / unidentified developer"**: the app is unsigned.
  Right-click the app → Open (once), or `xattr -dr com.apple.quarantine Lute.app`.
  Proper distribution needs an Apple Developer ID signature + notarization.

## Connecting to a remote Lute server

Instead of running a local server, the app can connect to a hosted Lute. Set
either:

- Environment variable: `LUTE_SERVER_URL=http://my-server:5001`
- Or a config file at the app config dir, `config.json`:

  ```json
  { "serverUrl": "http://my-server:5001" }
  ```

  The app config dir is:
  - macOS: `~/Library/Application Support/org.lute.desktop/config.json`
  - Linux: `~/.config/org.lute.desktop/config.json`
  - Windows: `%APPDATA%\org.lute.desktop\config.json`

When a remote URL is set, no local server is started. This is the basis for a
multi-device setup (phone/tablet/laptop all pointing at one server) — note the
security caveats in the Roadmap.

## How it works

`src-tauri/src/main.rs`:

1. Opens a splash window (`../ui/index.html`).
2. If a remote URL is configured, navigates straight to it.
3. Otherwise finds a free port, spawns `python -m lute.main --local --port N`,
   waits until the port accepts connections, then navigates the window to
   `http://127.0.0.1:N/`.
4. Kills the server process when the window closes or the app quits.
5. Reroutes Lute's `window.open()` dictionary popups to the system browser.

No changes are made to the Lute Python code; it reuses the existing
`lute.main` entry point and its `--local` / `--port` flags.

## Roadmap

- **Self-contained build**: freeze the server with PyInstaller and ship it as a
  Tauri sidecar so end users need nothing installed. (Japanese support via
  MeCab is a native dependency that needs extra bundling work.)
- **Hosted multi-device sync**: run `lute.main` without `--local` (binds
  `0.0.0.0`) behind a reverse proxy with TLS. **Lute has no built-in
  authentication and is single-user**, so any network exposure must be
  protected (reverse-proxy auth, VPN, or Tailscale); SQLite is single-writer.
