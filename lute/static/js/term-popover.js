/**
 * Touch term popover -- a LingQ-style, no-typing way to deal with a word.
 *
 * On a touch device, tapping a word in the reading pane opens a small card
 * anchored to that word instead of loading the term form into the side/bottom
 * pane.  The card shows any translation already saved, a status row, the
 * candidate meanings from Gemini, and an "explain in context" action.  Every
 * one of those is a tap -- the keyboard never opens.
 *
 * Interaction:
 *   - Statuses 1-5 select; the selection is applied when a meaning is tapped.
 *     1 is preselected, so the common case stays a single tap.
 *   - Well Known / Ignore commit straight away: they're complete decisions in
 *     themselves, and there's no meaning to attach.
 *   - Tapping a meaning saves it with the selected status and closes.
 *
 * Only active when the pointer is coarse; desktop keeps the existing pane.
 */

"use strict";

const LUTE_POPOVER_STATUSES = [
  { value: 1, label: "1" },
  { value: 2, label: "2" },
  { value: 3, label: "3" },
  { value: 4, label: "4" },
  { value: 5, label: "5" },
  { value: 99, label: "✓", title: "Well Known", commits: true },
  { value: 98, label: "⊘", title: "Ignore", commits: true },
];

const LUTE_POPOVER_DEFAULT_STATUS = 1;

/** True on touch devices.  Mirrors the tier detection in docs/ios-build-plan.md
 * section 7: gate on the interaction media features, not on raw width, so a
 * narrow desktop window never gets the touch UI. */
function lute_popover_is_touch() {
  return window.matchMedia("(pointer: coarse)").matches;
}

let _lute_popover_el = null;
let _lute_popover_word = null;
let _lute_popover_status = LUTE_POPOVER_DEFAULT_STATUS;
let _lute_popover_opened_at = 0;

function lute_popover_close() {
  if (_lute_popover_el) {
    _lute_popover_el.remove();
    _lute_popover_el = null;
  }
  if (_lute_popover_word) {
    _lute_popover_word.classList.remove("kwordmarked");
    _lute_popover_word = null;
  }
}

/** Reload the text so the word picks up its new status colour.  Same approach
 * as post_bulk_update in lute.js -- the page is re-rendered server-side. */
function _lute_popover_refresh_text() {
  const bookid = document.getElementById("book_id");
  const pagenum = document.getElementById("page_num");
  if (!bookid || !pagenum) return;
  const url = `/read/refresh_page/${bookid.value}/${pagenum.value}`;
  if (window.jQuery) jQuery("#thetext").load(url);
}

function _lute_popover_context(word) {
  return {
    language_id: parseInt(word.dataset.langId) || 0,
    term: word.dataset.text || word.textContent,
    sentence:
      typeof lute_get_sentence_for_element === "function"
        ? lute_get_sentence_for_element(jQuery(word))
        : "",
  };
}

async function _lute_popover_save(word, translation) {
  const ctx = _lute_popover_context(word);
  const resp = await fetch("/term/quick_save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      langid: ctx.language_id,
      text: ctx.term,
      translation: translation || "",
      status: _lute_popover_status,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `Save failed (${resp.status})`);
  }
  return resp.json();
}

function _lute_popover_build_status_row(card) {
  const row = document.createElement("div");
  row.className = "lute-popover-statuses";

  LUTE_POPOVER_STATUSES.forEach((s) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `lute-popover-status status${s.value}`;
    btn.textContent = s.label;
    if (s.title) btn.title = s.title;
    if (s.value === _lute_popover_status) btn.classList.add("selected");

    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      _lute_popover_status = s.value;

      if (s.commits) {
        // Well Known / Ignore: nothing left to choose, so save and get out
        // of the way.
        try {
          await _lute_popover_save(_lute_popover_word, "");
          lute_popover_close();
          _lute_popover_refresh_text();
        } catch (ex) {
          _lute_popover_show_error(card, ex.message);
        }
        return;
      }

      row
        .querySelectorAll(".lute-popover-status")
        .forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });

    row.appendChild(btn);
  });

  return row;
}

function _lute_popover_show_error(card, message) {
  let err = card.querySelector(".lute-popover-error");
  if (!err) {
    err = document.createElement("div");
    err.className = "lute-popover-error";
    card.appendChild(err);
  }
  err.textContent = message;
}

function _lute_popover_render_suggestions(listEl, suggestions, card) {
  listEl.textContent = "";

  if (!suggestions || suggestions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "lute-popover-empty";
    empty.textContent = "No suggestions.";
    listEl.appendChild(empty);
    return;
  }

  suggestions.forEach((s) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lute-popover-suggestion";
    btn.textContent = s;
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.classList.add("saving");
      try {
        await _lute_popover_save(_lute_popover_word, s);
        lute_popover_close();
        _lute_popover_refresh_text();
      } catch (ex) {
        btn.classList.remove("saving");
        _lute_popover_show_error(card, ex.message);
      }
    });
    listEl.appendChild(btn);
  });
}

/** Place the card near the word, flipping above it when there isn't room
 * below, and clamping to the viewport so it never runs off a narrow screen. */
function _lute_popover_position(card, word) {
  const r = word.getBoundingClientRect();
  const margin = 8;
  card.style.visibility = "hidden";
  document.body.appendChild(card);
  const cw = card.offsetWidth;
  const ch = card.offsetHeight;

  let left = r.left + r.width / 2 - cw / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - cw - margin));

  let top = r.bottom + margin;
  if (top + ch > window.innerHeight - margin) {
    const above = r.top - ch - margin;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - ch - margin);
  }

  card.style.left = `${left + window.scrollX}px`;
  card.style.top = `${top + window.scrollY}px`;
  card.style.visibility = "visible";
}

function _lute_popover_add_explain(card, word) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "lute-popover-explain";
  btn.textContent = "Explain in context";

  const out = document.createElement("div");
  out.className = "lute-popover-explanation";

  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    btn.disabled = true;
    out.textContent = "Thinking…";
    try {
      const resp = await fetch("/ai/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(_lute_popover_context(word)),
      });
      const data = await resp.json();
      out.textContent = data.explanation || data.reason || data.error || "No explanation.";
    } catch (ex) {
      out.textContent = `Couldn't explain: ${ex.message}`;
    }
    btn.disabled = false;
  });

  card.appendChild(btn);
  card.appendChild(out);
}

/** Open the popover for a word span. */
function lute_popover_open(word) {
  lute_popover_close();

  _lute_popover_word = word;
  _lute_popover_status = LUTE_POPOVER_DEFAULT_STATUS;
  _lute_popover_opened_at = Date.now();
  word.classList.add("kwordmarked");

  const card = document.createElement("div");
  card.className = "lute-popover";
  card.addEventListener("click", (e) => e.stopPropagation());
  _lute_popover_el = card;

  const header = document.createElement("div");
  header.className = "lute-popover-header";
  const title = document.createElement("span");
  title.className = "lute-popover-term";
  title.textContent = word.dataset.text || word.textContent;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "lute-popover-close";
  close.textContent = "✕";
  close.addEventListener("click", (e) => {
    e.stopPropagation();
    lute_popover_close();
  });
  header.appendChild(title);
  header.appendChild(close);
  card.appendChild(header);

  const saved = document.createElement("div");
  saved.className = "lute-popover-saved";
  card.appendChild(saved);

  card.appendChild(_lute_popover_build_status_row(card));

  const list = document.createElement("div");
  list.className = "lute-popover-suggestions";
  list.textContent = "Looking up…";
  card.appendChild(list);

  _lute_popover_add_explain(card, word);
  _lute_popover_position(card, word);

  const ctx = _lute_popover_context(word);

  // Any translation already saved, so re-tapping a known word reminds you
  // what you decided rather than only offering fresh suggestions.
  fetch(`/term/popover_info/${ctx.language_id}/${encodeURIComponent(ctx.term)}`)
    .then((r) => r.json())
    .then((info) => {
      if (_lute_popover_el !== card) return;
      if (info.translation) {
        saved.textContent = info.translation;
        saved.classList.add("has-translation");
      }
      if (info.status) {
        _lute_popover_status = info.status;
        card
          .querySelectorAll(".lute-popover-status")
          .forEach((b) => b.classList.toggle("selected", b.textContent === String(info.status)));
      }
      _lute_popover_position(card, word);
    })
    .catch(() => {});

  fetch("/ai/suggest_translations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ctx),
  })
    .then((r) => r.json())
    .then((data) => {
      if (_lute_popover_el !== card) return;
      if (data.enabled === false) {
        list.textContent = data.reason || "AI suggestions are off.";
        return;
      }
      if (data.error) {
        list.textContent = data.error;
        return;
      }
      _lute_popover_render_suggestions(list, data.suggestions, card);
      _lute_popover_position(card, word);
    })
    .catch((ex) => {
      if (_lute_popover_el !== card) return;
      list.textContent = `Couldn't load suggestions: ${ex.message}`;
    });
}

/**
 * Close when the user taps away.
 *
 * The tap that opens the popover is still travelling: on iOS a tap on a word
 * synthesises mousedown -> mouseup -> click, the popover opens on mouseup, and
 * the click that follows then reaches document.  Without the guard below that
 * click closes the popover immediately, so it never appears at all.
 */
document.addEventListener("click", (e) => {
  if (!_lute_popover_el) return;
  if (Date.now() - _lute_popover_opened_at < 400) return;
  if (_lute_popover_el.contains(e.target)) return;
  lute_popover_close();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") lute_popover_close();
});

/** Flag the body on touch devices so the CSS can drop the term pane and the
 * dictionary tabs, leaving just the text and the popover. */
document.addEventListener("DOMContentLoaded", () => {
  if (lute_popover_is_touch()) document.body.classList.add("lute-popover-mode");
});
