// Prevents an extra console window on Windows in release builds. DO NOT REMOVE.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// macOS only: WKWebView needs help before `alert()`/`confirm()`/`prompt()` work.
#[cfg(target_os = "macos")]
mod js_dialogs;

/// Holds the spawned Lute server process so it can be killed on exit.
struct ServerProcess(Mutex<Option<Child>>);

/// PID of the spawned Lute server (0 = none). Read by the `atexit` handler,
/// which is the only cleanup path that runs when macOS terminates the app via
/// `NSApplication` (Cmd+Q / Dock / Apple-event quit all bypass Tauri's exit
/// events).
static SERVER_PID: AtomicI32 = AtomicI32::new(0);

/// C `atexit` callback: terminate the Lute server if one is still tracked.
/// Runs on any normal process exit, including the macOS app-quit path.
#[cfg(unix)]
extern "C" fn cleanup_on_exit() {
    let pid = SERVER_PID.swap(0, Ordering::SeqCst);
    if pid > 0 {
        // SIGTERM lets waitress shut down cleanly; the process is exiting
        // anyway so there is no need to escalate.
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
    }
}

/// Injected into every page. Routes `window.open()` (used by Lute for
/// dictionary popups and the term-list link) to the system browser via the
/// `opener` plugin, falling back to the webview's native behavior if the Tauri
/// bridge is unavailable (e.g. when connected to an unlisted remote server).
const INIT_SCRIPT: &str = r#"
(function () {
  if (window.__lute_open_patched) return;
  window.__lute_open_patched = true;
  var nativeOpen = window.open ? window.open.bind(window) : function () { return null; };

  function openExternal(u) {
    var t = window.__TAURI__;
    if (t && t.opener && typeof t.opener.openUrl === "function") {
      return Promise.resolve(t.opener.openUrl(u));
    }
    var invoke = (t && t.core && t.core.invoke) || (t && t.invoke);
    if (invoke) {
      return Promise.resolve(invoke("plugin:opener|open_url", { url: u }));
    }
    return Promise.reject(new Error("no tauri bridge"));
  }

  window.open = function (url, name, features) {
    try {
      if (url) {
        var abs = new URL(url, window.location.href).href;
        openExternal(abs).catch(function () {
          try {
            nativeOpen(abs, name, features);
          } catch (e) {}
        });
        return null;
      }
    } catch (e) {
      /* fall through to native */
    }
    return nativeOpen(url, name, features);
  };
})();
"#;

/// Resolve the Lute source/repo root (the parent of `desktop/`).
///
/// Overridable with `LUTE_ROOT` so a bundled app can point at any checkout.
fn lute_root() -> PathBuf {
    if let Ok(r) = std::env::var("LUTE_ROOT") {
        if !r.trim().is_empty() {
            return PathBuf::from(r);
        }
    }
    // <root>/desktop/src-tauri -> <root>
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Find the Python interpreter used to run Lute.
///
/// Order: `LUTE_PYTHON` env -> project `.venv` -> `python3`/`python` on PATH.
fn find_python(root: &PathBuf) -> String {
    if let Ok(p) = std::env::var("LUTE_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }

    #[cfg(windows)]
    let venv = root.join(".venv").join("Scripts").join("python.exe");
    #[cfg(not(windows))]
    let venv = root.join(".venv").join("bin").join("python");

    if venv.exists() {
        return venv.to_string_lossy().into_owned();
    }

    #[cfg(windows)]
    {
        "python".to_string()
    }
    #[cfg(not(windows))]
    {
        "python3".to_string()
    }
}

/// Pick a free TCP port on localhost by binding to port 0 and reading it back.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("could not find a free local port")
}

/// Block until something is listening on `port`, or `timeout` elapses.
fn wait_for_server(port: u16, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

/// A configured remote Lute server URL, if any.
///
/// Checked in order: `LUTE_SERVER_URL` env, then a `config.json` in the app
/// config dir with a `"serverUrl"` string. When set, no local server is
/// spawned and the window connects to that URL instead.
fn remote_url(app: &AppHandle) -> Option<String> {
    if let Ok(u) = std::env::var("LUTE_SERVER_URL") {
        let u = u.trim().to_string();
        if !u.is_empty() {
            return Some(u);
        }
    }

    let cfg = app.path().app_config_dir().ok()?.join("config.json");
    let text = std::fs::read_to_string(cfg).ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    json.get("serverUrl")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Navigate the given window to `url`.
fn navigate(window: &tauri::WebviewWindow, url: &str) {
    let js = format!(
        "window.location.replace({})",
        serde_json::to_string(url).unwrap_or_else(|_| "\"about:blank\"".into())
    );
    let _ = window.eval(&js);
}

/// Show an error message on the splash page.
fn show_error(window: &tauri::WebviewWindow, msg: &str) {
    let js = format!(
        "window.__luteError && window.__luteError({})",
        serde_json::to_string(msg).unwrap_or_else(|_| "\"Unknown error\"".into())
    );
    let _ = window.eval(&js);
}

/// Kill the spawned Lute server, if one is running. Idempotent.
fn kill_server(app: &AppHandle) {
    SERVER_PID.store(0, Ordering::SeqCst);
    if let Some(state) = app.try_state::<ServerProcess>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn main() {
    // Safety net: kill the server on any normal process exit, including the
    // macOS app-quit path that bypasses Tauri's exit events.
    #[cfg(unix)]
    unsafe {
        libc::atexit(cleanup_on_exit);
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(ServerProcess(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            let window = WebviewWindowBuilder::new(
                &handle,
                "main",
                WebviewUrl::App("index.html".into()),
            )
            .title("Lute")
            .inner_size(1200.0, 820.0)
            .min_inner_size(800.0, 600.0)
            .initialization_script(INIT_SCRIPT)
            .build()?;

            // Lute guards its destructive actions with `confirm()`, so without
            // this they silently do nothing on macOS. See `js_dialogs`.
            #[cfg(target_os = "macos")]
            window.with_webview(|webview| js_dialogs::install(webview.inner()))?;

            // Closing the window shuts down the server and quits the app
            // (matters on macOS, where closing a window otherwise keeps the
            // process alive).
            let close_handle = handle.clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { .. } = event {
                    kill_server(&close_handle);
                    close_handle.exit(0);
                }
            });

            // Boot the server (or connect to a remote one) off the main thread
            // so the splash window stays responsive.
            let thread_handle = handle.clone();
            let win = window.clone();
            thread::spawn(move || {
                if let Some(url) = remote_url(&thread_handle) {
                    navigate(&win, &url);
                    return;
                }

                let root = lute_root();
                let python = find_python(&root);
                let port = free_port();

                let spawn_result = Command::new(&python)
                    .args(["-m", "lute.main", "--local", "--port", &port.to_string()])
                    .current_dir(&root)
                    .spawn();

                match spawn_result {
                    Ok(child) => {
                        SERVER_PID.store(child.id() as i32, Ordering::SeqCst);
                        let state = thread_handle.state::<ServerProcess>();
                        *state.0.lock().unwrap() = Some(child);
                    }
                    Err(e) => {
                        show_error(
                            &win,
                            &format!(
                                "Could not launch the Lute server with '{}'.\n\n{}\n\n\
                                 Set LUTE_PYTHON to a Python that has Lute installed, \
                                 or create a .venv in the project root.",
                                python, e
                            ),
                        );
                        return;
                    }
                }

                if wait_for_server(port, Duration::from_secs(90)) {
                    navigate(&win, &format!("http://127.0.0.1:{}/", port));
                } else {
                    show_error(
                        &win,
                        "The Lute server did not become ready in time. \
                         Check the terminal output for errors.",
                    );
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Lute desktop app")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => kill_server(app_handle),
            _ => {}
        });
}
