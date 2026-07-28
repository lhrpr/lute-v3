# Lute iOS Build Plan

A native iOS app for Lute that runs the existing Python/Flask engine **on-device
and standalone** (no external server required), with an optional **remote-server
mode** for a future multi-device setup. This document consolidates the
architecture, the Python packaging strategy, the responsive UI adaptation, and
the dual local/remote design.

Status: planning. No iOS code written yet.

---

## 1. Goals & constraints

**Goals**

- Run Lute fully on-device (embedded Python engine) with no external dependency.
- Reuse the existing Flask app, routes, and templates as-is wherever possible.
- Adapt the reading UI to phone and tablet form factors.
- Support a future **remote mode**: point the app at a self-hosted Lute instead
  of the embedded engine (mirrors the desktop app's `LUTE_SERVER_URL` /
  `config.json {serverUrl}` behaviour).

**The one constraint that shapes everything**

iOS apps **cannot spawn subprocesses** (`fork`/`exec` are unavailable in the
sandbox). The desktop shell (`desktop/src-tauri/src/main.rs`) works by launching
`python -m lute.main --local --port N` as a child process and pointing a WebView
at it. **That model does not port to iOS.** On iOS the interpreter must be
**embedded in-process** and Lute's WSGI app run on a **background thread inside
the app**, with a native `WKWebView` as the UI.

Everything below follows from that.

---

## 2. Current architecture (what we're wrapping)

- **Engine:** Flask app created by `lute.app_factory.create_app`, served by
  **waitress** (`lute/main.py:_start` → `serve(app, host, port)`).
- **Storage:** single-user **SQLite**. Data paths come from `platformdirs`
  (`lute/config/app_config.py`) but are overridable via the `DATAPATH` config
  key — the hook we use to redirect data into the iOS sandbox.
- **Reading screen** (`lute/templates/read/index.html`): three side-by-side
  panes inside `#read_pane_container`:
  - `#read_pane_left` — the text (`#thetext`) + header (title / page slider /
    nav) + footer (page turn).
  - `#read_pane_companion` — the **bilingual / parallel reader**: an
    `<iframe id="companion_frame">` of the paired book's matching page. Only
    rendered when `companion` is set.
  - `#read_pane_right` — the term/dictionary pane:
    - `#wordframeid` — the **term form** (`term/_form.html`): the translation
      field (**"existing definition"**) + `#ai-suggestions` (**Gemini** candidate
      meanings via `/ai/suggest_translations`).
    - `.dictcontainer` → `#dicttabs` + `#dictframes` — external/embedded
      **dictionary tabs**, plus the AI **"Explain in context"** button.
- **Existing responsive hooks:** a `matchMedia("(max-width: 980px)")` breakpoint
  in `read/index.html`, a matching `@media` in `parallel-styles.css` that stacks
  the companion, and a drag splitter in `resize.js` (`resizeCol` for width,
  `resizeRow` for height). `base.html:14` already sets
  `<meta name="viewport" content="width=device-width, initial-scale=1.0">`.

---

## 3. Target architecture (Option B: native Swift shell + embedded CPython)

```
┌─────────────────────────── iOS app (single process) ───────────────────────┐
│  Swift shell (Xcode)                                                        │
│   • WKWebView  ──▶  local: http://127.0.0.1:<port>                          │
│                     remote: https://<server>       (future)                 │
│   • Mode selection (local vs remote) from app settings / first-run          │
│   • Injects a form-factor hint (device idiom + size class)                  │
│   • Routes external dictionary popups to SFSafariViewController             │
│   • Owns safe-area insets + keyboard avoidance                              │
│                                                                             │
│  Embedded CPython (Python-Apple-support xcframework, in-process)            │
│   • LOCAL MODE ONLY: Py_Initialize → import lute → waitress on a bg thread  │
│   • Lute Flask app + pure-Python deps, frozen into the app bundle           │
│   • SQLite DB in the app sandbox (Application Support)                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

Chosen over the alternatives:

- **Briefcase (BeeWare)** — great for a *Phase 0 spike* because it automates the
  CPython + wheel bundling. Recommended to prototype, then graduate to a native
  Swift shell for the shipped app.
- **Tauri v2 iOS** — reuses the existing desktop wrapper conceptually, but still
  can't spawn Python, so CPython must be embedded in the Rust/Swift layer
  (pyo3 against a static iOS Python) — the fiddliest toolchain of the three.
  Not recommended.

**Loopback vs. custom scheme.** Start with the WebView loading
`http://127.0.0.1:<port>` (matches the desktop model; iOS allows loopback inside
the sandbox — may need `NSAllowsLocalNetworking`). A more robust,
review-friendly alternative is a `WKURLSchemeHandler` that feeds WebView
requests straight into the WSGI callable (no TCP). Keep it as a fallback.

---

## 4. Python engine packaging

- **Interpreter:** **Python-Apple-support** (BeeWare) or Python 3.13+ official
  iOS support (PEP 730, Tier 3), shipped as an `xcframework` (device +
  simulator slices).
- **No dynamic loading of arbitrary dylibs** — everything must be pure-Python or
  statically linked.
- **Frozen stdlib + site-packages** shipped read-only in the app bundle;
  writable data goes to the sandbox.
- **TLS:** bundle `certifi` and point `requests` at it (no system OpenSSL trust
  store on iOS).
- **Threads:** waitress uses a thread pool; start it off the UI thread and
  initialise the interpreter for threaded embedding (Python-Apple-support
  handles this).

### Dependency audit

Nearly all of Lute's declared dependencies are pure-Python and bundle cleanly:

| Status | Packages |
|---|---|
| ✅ Pure-Python | Flask-SQLAlchemy, Flask-WTF, jaconv, platformdirs, requests (+certifi), beautifulsoup4 (stdlib `html.parser`), toml, **waitress**, openepub, pyparsing, pypdf, subtitle-parser, ahocorapy |
| ✅ Pure fallback / stdlib | PyYAML (skip libyaml C ext), SQLAlchemy (pure-Python mode), **sqlite3** (in CPython) |
| ⛔ Native blocker | **natto-py → MeCab** (Japanese): `dlopen`s `libmecab`, not allowed on iOS |

**MeCab / Japanese:** the parser registry already gates on `is_supported()`
(`lute/parse/registry.py`), and `mecab_parser` reports unsupported when MeCab
can't load — so **ship v1 without Japanese** and it degrades gracefully (every
space/regex-based language still works). Restoring Japanese later = statically
cross-compile MeCab + a static binding, or swap in a pure-Python tokenizer.
Treat as a separate follow-on.

---

## 5. Lute code changes (small, additive, desktop-safe)

1. **In-process launcher.** Factor a reusable core out of `lute/main.py:_start`
   — `create_app()` → `data_initialization()` → `serve(app, host="127.0.0.1",
   port=N)` — callable from the embedded interpreter on a thread, without
   argparse/CLI/stdout printing.
2. **Sandbox data path.** Set `DATAPATH` (already supported by
   `app_config.py`) to the app's Application Support dir. All derived paths
   (backups, temp, userimages, useraudio) follow.
3. **First-run bootstrap.** Ship the seed config (`config.yml.prod`) and the
   `lute/db/language_defs` submodule inside the app bundle; seed on first launch
   and let Lute's normal startup migration/backup run against the sandbox DB.
4. **Guard MeCab** so nothing attempts to load it in the iOS build (registry
   already does most of this).
5. **External popups.** The `window.open()` dictionary reroute the desktop app
   does becomes a `WKUIDelegate` → `SFSafariViewController` on iOS.
6. **Form-factor + mode signalling** (see §6, §7).

None of these change desktop/server behaviour — they're additive.

---

## 6. Local vs. remote mode

The app supports two runtime modes, chosen at startup (settings / first-run
chooser), mirroring the desktop shell's `remote_url()` logic:

**Local mode (default, standalone)**
- Boot the embedded CPython engine + waitress on `127.0.0.1:<port>`.
- WebView loads the loopback URL.
- Data lives in the on-device SQLite sandbox.

**Remote mode (future)**
- **Skip** booting the embedded engine entirely (saves memory + launch time).
- WebView loads `https://<server>` directly.
- Data lives on the server; the device is a thin client.

Config sources, in order (mirror the desktop precedence):
1. A native app setting (Settings screen), stored as e.g.
   `Application Support/config.json` → `{ "serverUrl": "https://…" }`.
2. Absent/empty ⇒ local mode.

**Caveats to design around:**

- **No merge/sync between modes.** Local and remote are separate libraries;
  switching modes switches which data you see. A true sync story is out of
  scope for now.
- **Auth / security.** Lute has **no built-in authentication** and is
  single-user / single-writer SQLite. Any remote exposure must be protected
  externally (reverse-proxy auth, VPN, Tailscale). This is the user's
  responsibility; the app just points at the URL.
- **ATS.** Remote mode should require **HTTPS** (avoid ATS exceptions). Local
  loopback `http` is fine via the localhost exception / `NSAllowsLocalNetworking`.
- **Responsive UI in remote mode.** The mobile adaptations in §7 are partly
  server-rendered (companion-off, reduced dict tabs). Those only exist if the
  **remote runs this modded Lute**. Mitigation: keep the responsive layer as
  **universal client-side CSS/JS** (works against any Lute), and treat the
  server-side gating as an enhancement. The form-factor hint is sent either way;
  the server honours it if it can.

---

## 7. Responsive UI adaptation

### Form-factor tiers

Decide the tier by **device / input class**, not raw window width (see below).

| Tier | Term / definition pane | Bilingual reader | Dictionaries |
|---|---|---|---|
| **Desktop / landscape tablet** (regular width) | **Side** panel (`#read_pane_right`, vertical splitter) | available | full tab set |
| **Portrait tablet** | **Bottom** panel (row), toggleable, horizontal splitter | toggle (mutually exclusive with dict) | full tab set |
| **Phone** | **Bottom sheet** (modal overlay, dismissible), reduced content | **disabled** | **existing definition + Gemini only** |

The pane is the **same `#read_pane_right` DOM** in all three; only its container
positioning (column vs. row vs. modal) and the splitter axis change. The
phone bottom-sheet is the bottom-panel styling made modal + content-restricted —
one component, three modes. `resize.js` already has both `resizeCol` and
`resizeRow`, so the horizontal splitter reuses an existing pattern.

### Phone specifics

- Single column; gate off the `resize.js` splitter.
- Tapping a word opens the term bottom-sheet (rounded top, drag handle, backdrop,
  swipe-to-dismiss); keep the existing `#wordframeid` term form inside it so all
  save/AI logic is unchanged.
- **Restrict content to existing definition + Gemini:** skip *building* the
  external dictionary tabs (don't just hide them — avoid firing external iframe
  loads, which are slow on cellular and often `X-Frame-Options`-blocked). Leaves
  the translation field + `#ai-suggestions` (+ optionally "Explain in context").
- **Disable the bilingual reader:** don't render `#read_pane_companion` at all
  when compact; hide its toggle.
- Add `viewport-fit=cover` + `env(safe-area-inset-*)` padding (notch / home
  indicator); enlarge touch targets; suppress the selection callout / double-tap
  zoom on word taps; consider a compacted reading header.

### Tier detection — screen/device class, not pixel count

CSS media-query `px` are **density-normalised reference pixels**, not hardware
pixels (browser divides by device-pixel-ratio first). So:

- 1080p laptop → ~1920 CSS px (or ~1366–1536 scaled), DPR ~1.
- 720p monitor → ~1280 CSS px.
- 1080p phone → ~390 CSS px, DPR ~3.

A phone/tablet breakpoint below ~1024 therefore **never** trips on a low-res
desktop. The residual risk of pure-width — a narrow/resized desktop window
getting the phone UI — is removed by gating on **interaction media features**:

- `@media (pointer: coarse)` / `(hover: none)` ⇒ touch device (eligible for
  mobile layouts).
- `@media (pointer: fine) and (hover: hover)` ⇒ mouse/trackpad ⇒ **always** the
  reading layout, at any resolution or window size.

**On the iOS build, don't guess — the shell knows:**

- `UIDevice.userInterfaceIdiom` → `.phone` / `.pad` (device class).
- `UITraitCollection.horizontalSizeClass` → `.compact` in a narrow iPad Split
  View slot (the iPad analog of "narrow window" — adapt here on purpose).

The shell injects a definitive class at load (`<html class="ff-phone|ff-tablet|
ff-compact">`, via a custom User-Agent token or `evaluateJavaScript`). CSS keys
off that class first; `pointer`/`hover` + width media queries are the fallback
for the **desktop/web build only**.

Resolution:
- **iOS:** trust idiom + size class.
- **Desktop/web:** `(pointer: coarse)` gates eligibility; width picks the tier;
  mouse-driven machines always get the reading layout.
- Base reading layout stays **fluid** (max text measure, relative units) so a
  small desktop window degrades gracefully without ever flipping to the phone UI.

### Where the work lives

- **Mostly CSS + JS in the existing web app** — testable in a desktop browser and
  the iOS Simulator before native plumbing.
- **A few server-side gates** for efficiency (skip companion block, reduced dict
  tabs) keyed off the form-factor hint.
- **Native shell does little UI** — hosts the WebView, supplies the hint, routes
  external dicts to Safari, owns safe-area/keyboard. Optional Phase-2 polish: a
  truly native definition sheet via `UISheetPresentationController` bridged with
  `WKScriptMessageHandler`.

**Recommendation:** build the responsive layer **width+pointer driven and
universal** (desktop/web benefit too, no fork); gate only the *content
restrictions* (dict subset, companion-off) on the compact hint.

---

## 8. Build, distribution, signing

- Build on macOS with Xcode; produce device + simulator slices.
- Apple Developer Program membership; code signing + provisioning profiles (the
  desktop app is unsigned — iOS won't allow that).
- Test matrix: **Simulator first** (can be driven from Claude Code), then a
  **real device** early (static-linking / no-dylib issues often only surface on
  hardware).
- App Store review: an embedded interpreter running a localhost server is
  allowed but be ready to justify it; the custom-scheme variant sidesteps most
  questions.

---

## 9. Risks, ranked

1. **CPython + wheel bundling for iOS** — the classic pain; Briefcase absorbs it
   for the spike.
2. **MeCab / Japanese** — can't ship as-is; launch without it.
3. **Loopback vs. review friction** — mitigated by the custom-scheme fallback.
4. **Static-linking surprises on device** — test on hardware early.
5. **Remote-mode UI drift** — mobile adaptations depend on the server; keep the
   responsive layer client-side/universal.

---

## 10. Phased roadmap

- **Phase 0 — Spike.** Briefcase app: embedded CPython + waitress on a thread +
  WebView loads Lute in the Simulator. Japanese off. Proves the in-process model.
- **Phase 1 — Phone usable.** Native Swift shell (or continue Briefcase);
  sandbox `DATAPATH`, first-run seeding/migrations, certs. Add the **phone tier**
  (single column + bottom-sheet term popup, companion off, dict subset) + the
  form-factor hint. Verify on a real device.
- **Phase 1.5 — Tablet.** Portrait-tablet **bottom panel** + companion/dict
  toggle.
- **Phase 2 — Polish + remote.** Native-feel details (safe areas, optional native
  sheet, `SFSafariViewController`); **remote-server mode** (settings screen,
  `config.json {serverUrl}`, HTTPS/ATS, skip embedded engine when remote).
- **Phase 3 — Optional.** Japanese via static MeCab or an alternative tokenizer;
  future local↔remote sync story.
