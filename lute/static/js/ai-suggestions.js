"use strict";

/**
 * AI translation suggestions for the term form.
 *
 * Renders a short list of clickable candidate meanings under the translation
 * box (LingQ-style).  Clicking a candidate inserts it into #translation.
 *
 * Runs inside the term-form iframe.  When embedded in the reading pane, the
 * parent page (lute.js) exposes the clicked word's sentence via the same-origin
 * global window.parent.LUTE_LAST_CLICKED_CONTEXT, which we use as context.
 *
 * Behaviour is driven by user settings exposed in LUTE_USER_SETTINGS:
 *   ai_suggestions_enabled : master on/off
 *   ai_trigger             : "auto_new" | "auto_always" | "on_demand"
 * The API key is never exposed here; all provider calls happen server-side.
 */

(function () {
  const settings =
    typeof LUTE_USER_SETTINGS !== "undefined" ? LUTE_USER_SETTINGS : {};

  if (!settings.ai_suggestions_enabled) return;

  const container = document.getElementById("ai-suggestions");
  const translation = document.getElementById("translation");
  if (!container || !translation) return;

  const trigger = settings.ai_trigger || "auto_new";

  /** Read the term / sentence / language context for the lookup. */
  function get_context() {
    let sentence = "";
    let langid = 0;
    try {
      const ctx = window.parent && window.parent.LUTE_LAST_CLICKED_CONTEXT;
      if (ctx) {
        sentence = ctx.sentence || "";
        langid = ctx.langid || 0;
      }
    } catch (e) {
      // Cross-origin or standalone term page - fall back to no sentence.
    }

    const textEl = document.getElementById("text");
    const term = textEl ? textEl.value.replace(/​/g, "").trim() : "";

    const langEl = document.getElementById("language_id");
    if (langEl && parseInt(langEl.value)) langid = parseInt(langEl.value);

    return { term: term, sentence: sentence, language_id: langid };
  }

  /** Insert a chosen meaning into the translation field. */
  function insert_meaning(text) {
    const current = translation.value.trim();
    if (current === "") {
      translation.value = text;
    } else {
      // Avoid obvious duplicates.
      const parts = current.split(/;\s*/).map((s) => s.trim());
      if (parts.indexOf(text) === -1) translation.value = current + "; " + text;
    }
    // Notify the form so unsaved-change tracking picks it up.
    translation.dispatchEvent(new Event("change", { bubbles: true }));
    translation.focus();
  }

  function render_status(message) {
    body.textContent = "";
    const div = document.createElement("div");
    div.className = "ai-suggestions-status";
    div.textContent = message;
    body.appendChild(div);
  }

  function render_suggestions(list) {
    body.textContent = "";
    if (!list || list.length === 0) {
      render_status("No suggestions.");
      return;
    }
    list.forEach((text) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ai-suggestion";
      btn.title = "Add to translation";

      const label = document.createElement("span");
      label.className = "ai-suggestion-text";
      label.textContent = text;

      const add = document.createElement("span");
      add.className = "ai-suggestion-add";
      add.textContent = "+";

      btn.appendChild(label);
      btn.appendChild(add);
      btn.addEventListener("click", () => insert_meaning(text));
      body.appendChild(btn);
    });
  }

  let in_flight = false;

  function fetch_suggestions() {
    if (in_flight) return;
    const ctx = get_context();
    if (ctx.term === "" || !ctx.language_id) {
      render_status("Enter a term to get suggestions.");
      return;
    }
    in_flight = true;
    refreshBtn.disabled = true;
    render_status("Loading suggestions…");

    fetch("/ai/suggest_translations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ctx),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.enabled === false) {
          container.style.display = "none";
        } else if (data.error) {
          render_status(data.error);
        } else {
          render_suggestions(data.suggestions);
          // Show which model answered (helpful with the cascade).
          title.title = data.model ? "via " + data.model : "";
        }
      })
      .catch(() => render_status("Could not load suggestions."))
      .finally(() => {
        in_flight = false;
        refreshBtn.disabled = false;
      });
  }

  // Build the UI.
  const header = document.createElement("div");
  header.className = "ai-suggestions-header";

  const title = document.createElement("span");
  title.className = "ai-suggestions-title";
  title.textContent = "✨ Suggested meanings";

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "ai-refresh-btn";
  refreshBtn.textContent = "↻";
  refreshBtn.title = "Get AI suggestions";
  refreshBtn.addEventListener("click", fetch_suggestions);

  header.appendChild(title);
  header.appendChild(refreshBtn);

  const body = document.createElement("div");
  body.className = "ai-suggestions-body";

  container.appendChild(header);
  container.appendChild(body);

  // Auto-fetch depending on the configured trigger.
  const should_auto =
    trigger === "auto_always" ||
    (trigger === "auto_new" && translation.value.trim() === "");
  if (should_auto) {
    fetch_suggestions();
  } else {
    render_status("Click ↻ for AI suggestions.");
  }
})();
