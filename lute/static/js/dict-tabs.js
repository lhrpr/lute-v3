"use strict";


/**
 * A general lookup button, for images, sentences, etc.
 */
class LookupButton {

  /** State required by the buttons. */
  static TERM_FORM_CONTAINER = null;
  static TERM_DICTS = null;
  static LANG_ID = null;


  /** All LookupButtons created. */
  static all = [];

  constructor(frameName) {
    let createIFrame = function(name) {
      const f = document.createElement("iframe");
      f.name = name;
      f.src = "about:blank";
      f.classList.add("dictframe");
      return f;
    };

    this.dictID = null;
    this.is_active = false;
    this.contentLoaded = false;

    this.frame = createIFrame(frameName);
    this.btn = document.createElement("button");
    this.btn.classList.add("dict-btn");
    this.btn.onclick = () => this.do_lookup();

    LookupButton.all.push(this);
  }

  /** Lookup. *************************/

  do_lookup() {
    throw new Error('Subclasses must override.');
  }

  /** Activate/deact. *************************/

  deactivate() {
    this.is_active = false;
    this.btn.classList.remove("dict-btn-active");
    this.frame.classList.remove("dict-active");
  }

  activate() {
    DictButton.all.forEach(button => button.deactivate());
    this.is_active = true;
    this.btn.classList.add("dict-btn-active");
    this.frame.classList.add("dict-active");
  }

};


/**
 * A general lookup button that's doesn't use dictionaries.
 * For buttons that get info about a term.
 * This content is never cached -- form values may change the search,
 * so it's fine to always reload.
 */
class GeneralLookupButton extends LookupButton {
  constructor(btn_id, btn_textContent, btn_title, btn_className, clickHandler) {
    super(`frame_for_${btn_id}`);

    const b = this.btn;
    b.setAttribute("id", btn_id);
    b.setAttribute("title", btn_title);
    b.textContent = btn_textContent;
    b.classList.add(btn_className);

    this.click_handler = clickHandler;
  }

  do_lookup() {
    this.click_handler(this.frame);
    this.activate();
  }

}  // end GeneralLookupButton


class SentenceLookupButton extends GeneralLookupButton {
  constructor() {
    let handler = function(iframe) {
      const txt = LookupButton.TERM_FORM_CONTAINER.querySelector("#text").value;
      // %E2%80%8B is the zero-width string.  The term is reparsed
      // on the server, so this doesn't need to be sent.
      const t = encodeURIComponent(txt).replaceAll('%E2%80%8B', '');
      const langid = `${LookupButton.LANG_ID ?? 0}`;
      if (langid == '0' || t == '')
        return;
      // Doing lookups by the term text (from the "#text" control) is
      // better than using a term ID, b/c it lets me search for new
      // multi-word terms in existing text, even before those
      // multi-word terms are actually created and saved.  e.g., I can
      // highlight the text "a big thing" and see if that phrase has
      // been used in anything I've read already.
      iframe.setAttribute("src", `/term/sentences/${LookupButton.LANG_ID}/${t}`);
    };

    super("sentences-btn", "Sentences", "See term usage", "dict-sentences-btn", handler);
  }
}


class ImageLookupButton extends GeneralLookupButton {
  constructor() {

    // Parents are in the tagify-managed #parentslist input box.
    let _get_parent_tag_values = function() {
      const pdata = LookupButton.TERM_FORM_CONTAINER.querySelector("#parentslist").value
      if ((pdata ?? '') == '')
        return [];
      return JSON.parse(pdata).map(e => e.value);
    };

    let handler = function(iframe) {
      const text = LookupButton.TERM_FORM_CONTAINER.querySelector("#text").value;
      const lang_id = LookupButton.LANG_ID;
      if (lang_id == null || lang_id == '' || parseInt(lang_id) == 0 || text == null || text == '') {
        alert('Please select a language and enter the term.');
        return;
      }
      let use_text = text;

      // If there is a single parent, use that as the basis of the lookup.
      const parents = _get_parent_tag_values();
      if (parents.length == 1)
        use_text = parents[0];

      const raw_bing_url = 'https://www.bing.com/images/search?q=[LUTE]&form=HDRSC2&first=1&tsc=ImageHoverTitle';
      const binghash = raw_bing_url.replace('https://www.bing.com/images/search?', '');
      const url = `/bing/search_page/${LookupButton.LANG_ID}/${encodeURIComponent(use_text)}/${encodeURIComponent(binghash)}`;

      iframe.setAttribute("src", url);
    };  // end handler

    super("dict-image-btn", null, "Lookup images", "dict-image-btn", handler);
  }
}


/**
 * AI "Explain in context" button.
 *
 * Asks the server (which cascades through the configured Gemini models, best
 * first) to explain the highlighted word/phrase in the context of its
 * sentence, and renders the reply in its own dict frame.  Only created when
 * AI features are enabled.  Triggered by click, never automatically.
 */
class AIExplainLookupButton extends GeneralLookupButton {
  constructor() {
    // do_lookup is overridden below, so the handler passed here is unused.
    super(
      "ai-explain-btn",
      "Explain",
      "Explain the highlighted word/phrase in context (AI)",
      "dict-ai-explain-btn",
      () => {}
    );
    this._userInitiated = false;
    // Only hit the API on an explicit button click -- never automatically
    // when this panel merely happens to be the active tab as new words open.
    this.btn.onclick = () => {
      this._userInitiated = true;
      this.do_lookup();
    };
  }

  do_lookup() {
    this.activate();
    if (this._userInitiated) {
      this._userInitiated = false;
      this._explain(this.frame);
    } else {
      // Programmatic (re)activation, e.g. a new word opened while this panel
      // was active: show a prompt instead of spending an API call.
      this._show_prompt(this.frame);
    }
  }

  _show_prompt(iframe) {
    const ctx = this._get_context();
    const esc = AIExplainLookupButton._esc;
    const msg =
      ctx.term === ""
        ? "Click a word, then press Explain."
        : `Press Explain to explain “${esc(ctx.term)}” in context.`;
    AIExplainLookupButton._write(iframe, `<p class="ai-explain-msg">${msg}</p>`);
  }

  /** Term (from the form), sentence and language (from the reading page). */
  _get_context() {
    const container = LookupButton.TERM_FORM_CONTAINER;
    const textEl = container ? container.querySelector("#text") : null;
    let term = textEl
      ? textEl.value.replaceAll("​", "").replace(/\s+/g, " ").trim()
      : "";

    let sentence = "";
    let langid = parseInt(LookupButton.LANG_ID) || 0;
    // dict-tabs.js runs in the reading page, so this global is same-window.
    const ctx = window.LUTE_LAST_CLICKED_CONTEXT;
    if (ctx) {
      sentence = ctx.sentence || "";
      if (ctx.langid) langid = ctx.langid;
      if (term === "" && ctx.term) term = ctx.term.replaceAll("​", "").trim();
    }
    return { term: term, sentence: sentence, language_id: langid };
  }

  _explain(iframe) {
    const ctx = this._get_context();
    const esc = AIExplainLookupButton._esc;
    if (ctx.term === "") {
      AIExplainLookupButton._write(
        iframe,
        `<p class="ai-explain-msg">Click a word first, then press Explain.</p>`
      );
      return;
    }
    AIExplainLookupButton._write(
      iframe,
      `<p class="ai-explain-msg ai-explain-loading">Explaining “${esc(ctx.term)}”…</p>`
    );
    fetch("/ai/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ctx),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.enabled === false) {
          AIExplainLookupButton._write(
            iframe,
            `<p class="ai-explain-msg">${esc(data.reason || "AI features are disabled.")}</p>`
          );
        } else if (data.error) {
          AIExplainLookupButton._write(
            iframe,
            `<p class="ai-explain-msg ai-explain-error">${esc(data.error)}</p>`
          );
        } else {
          AIExplainLookupButton._render(iframe, ctx.term, data.explanation, data.model);
        }
      })
      .catch(() =>
        AIExplainLookupButton._write(
          iframe,
          `<p class="ai-explain-msg ai-explain-error">Could not load explanation.</p>`
        )
      );
  }

  static _esc(s) {
    return String(s == null ? "" : s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  /** Escaped free text -> paragraphs (blank line splits, newline -> <br>). */
  static _format(text) {
    const esc = AIExplainLookupButton._esc(text || "").trim();
    if (esc === "")
      return `<p class="ai-explain-msg">No explanation returned.</p>`;
    return esc
      .split(/\n\s*\n/)
      .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  static _render(iframe, term, explanation, model) {
    const esc = AIExplainLookupButton._esc;
    const footer = model
      ? `<div class="ai-explain-model">via ${esc(model)}</div>`
      : "";
    const body =
      `<h3 class="ai-explain-term">${esc(term)}</h3>` +
      AIExplainLookupButton._format(explanation) +
      footer;
    AIExplainLookupButton._write(iframe, body);
  }

  /** Write a self-contained doc into the (same-origin, about:blank) frame. */
  static _write(iframe, bodyHtml) {
    const doc =
      iframe.contentDocument ||
      (iframe.contentWindow && iframe.contentWindow.document);
    if (!doc) return;
    doc.open();
    doc.write(
      `<!doctype html><html><head><meta charset="utf-8">` +
        `<style>${AIExplainLookupButton.CSS}</style></head>` +
        `<body>${bodyHtml}</body></html>`
    );
    doc.close();
  }
}

AIExplainLookupButton.CSS = `
  body { font-family: sans-serif; font-size: 0.95rem; line-height: 1.5;
         color: #222; margin: 0; padding: 0.6rem 0.8rem; }
  .ai-explain-term { margin: 0 0 0.5rem 0; font-size: 1.05rem; color: #2b6cb0; }
  p { margin: 0 0 0.6rem 0; }
  .ai-explain-msg { color: #555; }
  .ai-explain-loading { font-style: italic; }
  .ai-explain-error { color: #b00020; }
  .ai-explain-model { margin-top: 0.8rem; font-size: 0.75rem; color: #999; }
`;


/**
 * DeepL "translate sentence/paragraph" button.
 *
 * Adds a sidebar tab that translates the current sentence or paragraph with the
 * DeepL API.  A dropdown lets the user pick sentence vs paragraph; the choice
 * is remembered (localStorage) and stays active until changed again.
 *
 * Like the AI Explain panel, translation is only fetched on an explicit action
 * (clicking the tab, pressing Translate, or changing the dropdown) -- never
 * automatically as new words open.  Only created when DeepL is enabled.
 */
class DeepLTranslateLookupButton extends GeneralLookupButton {

  static MODE_STORAGE_KEY = "lute_deepl_translate_mode";

  constructor() {
    // do_lookup is overridden below, so the handler passed here is unused.
    super(
      "deepl-translate-btn",
      "DeepL",
      "Translate the current sentence/paragraph (DeepL)",
      "dict-deepl-btn",
      () => {}
    );
    this._userInitiated = false;
    // Only hit the API on an explicit button click -- never automatically when
    // this panel merely happens to be the active tab as new words open.
    this.btn.onclick = () => {
      this._userInitiated = true;
      this.do_lookup();
    };
  }

  /** Persisted sentence/paragraph choice. *************************/

  static _get_mode() {
    try {
      const m = window.localStorage.getItem(
        DeepLTranslateLookupButton.MODE_STORAGE_KEY
      );
      return m === "paragraph" ? "paragraph" : "sentence";
    } catch (e) {
      return "sentence";
    }
  }

  static _set_mode(mode) {
    try {
      window.localStorage.setItem(
        DeepLTranslateLookupButton.MODE_STORAGE_KEY,
        mode === "paragraph" ? "paragraph" : "sentence"
      );
    } catch (e) {
      /* ignore storage errors (e.g. private mode) */
    }
  }

  do_lookup() {
    this.activate();
    const initiated = this._userInitiated;
    this._userInitiated = false;
    // (Re)draw the controls, then either translate (explicit click) or just
    // prompt (programmatic reactivation when a new word opened).
    this._render_shell(this.frame);
    if (initiated)
      this._translate(this.frame);
    else
      this._show_prompt(this.frame);
  }

  /** Sentence/paragraph text of the last-clicked word, from the reading page. */
  _get_context() {
    let sentence = "";
    let paragraph = "";
    let langid = parseInt(LookupButton.LANG_ID) || 0;
    // dict-tabs.js runs in the reading page, so this global is same-window.
    const ctx = window.LUTE_LAST_CLICKED_CONTEXT;
    if (ctx) {
      sentence = ctx.sentence || "";
      paragraph = ctx.paragraph || "";
      if (ctx.langid) langid = ctx.langid;
    }
    return { sentence: sentence, paragraph: paragraph, language_id: langid };
  }

  /** The text to translate for the currently-selected mode. */
  _current_text() {
    const ctx = this._get_context();
    return DeepLTranslateLookupButton._get_mode() === "paragraph"
      ? ctx.paragraph
      : ctx.sentence;
  }

  _doc(iframe) {
    return (
      iframe.contentDocument ||
      (iframe.contentWindow && iframe.contentWindow.document)
    );
  }

  /** Write the dropdown + Translate button + result area, and wire handlers. */
  _render_shell(iframe) {
    const mode = DeepLTranslateLookupButton._get_mode();
    const sel = (m) => (mode === m ? " selected" : "");
    const shell =
      `<div class="deepl-controls">` +
      `<label for="deepl-mode">Translate</label>` +
      `<select id="deepl-mode">` +
      `<option value="sentence"${sel("sentence")}>Sentence</option>` +
      `<option value="paragraph"${sel("paragraph")}>Paragraph</option>` +
      `</select>` +
      `<button id="deepl-go" type="button">Translate</button>` +
      `</div>` +
      `<div id="deepl-result"></div>`;
    DeepLTranslateLookupButton._write(iframe, shell);

    const doc = this._doc(iframe);
    if (!doc) return;
    const select = doc.getElementById("deepl-mode");
    const go = doc.getElementById("deepl-go");
    if (select) {
      select.addEventListener("change", () => {
        DeepLTranslateLookupButton._set_mode(select.value);
        this._translate(iframe);
      });
    }
    if (go) go.addEventListener("click", () => this._translate(iframe));
  }

  _set_result(iframe, html) {
    const doc = this._doc(iframe);
    const el = doc ? doc.getElementById("deepl-result") : null;
    if (el) el.innerHTML = html;
  }

  _show_prompt(iframe) {
    const esc = DeepLTranslateLookupButton._esc;
    const mode = DeepLTranslateLookupButton._get_mode();
    const msg =
      (this._current_text() || "").trim() === ""
        ? "Click a word in the text, then press Translate."
        : `Press Translate to translate the current ${mode}.`;
    this._set_result(iframe, `<p class="deepl-msg">${esc(msg)}</p>`);
  }

  _translate(iframe) {
    const esc = DeepLTranslateLookupButton._esc;
    const mode = DeepLTranslateLookupButton._get_mode();
    const text = (this._current_text() || "").trim();
    if (text === "") {
      this._set_result(
        iframe,
        `<p class="deepl-msg">Click a word in the text first, then press Translate.</p>`
      );
      return;
    }
    this._set_result(
      iframe,
      `<p class="deepl-msg deepl-loading">Translating the ${esc(mode)}…</p>`
    );
    fetch("/ai/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.enabled === false) {
          this._set_result(
            iframe,
            `<p class="deepl-msg">${esc(data.reason || "DeepL translation is disabled.")}</p>`
          );
        } else if (data.error) {
          this._set_result(
            iframe,
            `<p class="deepl-msg deepl-error">${esc(data.error)}</p>`
          );
        } else {
          this._render_translation(
            iframe,
            text,
            data.translation,
            data.detected_source_lang
          );
        }
      })
      .catch(() =>
        this._set_result(
          iframe,
          `<p class="deepl-msg deepl-error">Could not load translation.</p>`
        )
      );
  }

  _render_translation(iframe, source, translation, detected) {
    const esc = DeepLTranslateLookupButton._esc;
    const fmt = DeepLTranslateLookupButton._format;
    const meta = detected
      ? `<div class="deepl-meta">Detected source: ${esc(detected)} · via DeepL</div>`
      : `<div class="deepl-meta">via DeepL</div>`;
    const body =
      `<div class="deepl-source">${fmt(source)}</div>` +
      `<div class="deepl-translation">${fmt(translation)}</div>` +
      meta;
    this._set_result(iframe, body);
  }

  static _esc(s) {
    return String(s == null ? "" : s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  /** Escaped free text -> paragraphs (blank line splits, newline -> <br>). */
  static _format(text) {
    const esc = DeepLTranslateLookupButton._esc(text || "").trim();
    if (esc === "") return "";
    return esc
      .split(/\n\s*\n/)
      .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  /** Write a self-contained doc into the (same-origin, about:blank) frame. */
  static _write(iframe, bodyHtml) {
    const doc =
      iframe.contentDocument ||
      (iframe.contentWindow && iframe.contentWindow.document);
    if (!doc) return;
    doc.open();
    doc.write(
      `<!doctype html><html><head><meta charset="utf-8">` +
        `<style>${DeepLTranslateLookupButton.CSS}</style></head>` +
        `<body>${bodyHtml}</body></html>`
    );
    doc.close();
  }
}

DeepLTranslateLookupButton.CSS = `
  body { font-family: sans-serif; font-size: 0.95rem; line-height: 1.5;
         color: #222; margin: 0; padding: 0.6rem 0.8rem; }
  .deepl-controls { display: flex; align-items: center; gap: 0.4rem;
         margin-bottom: 0.6rem; flex-wrap: wrap; }
  .deepl-controls label { color: #555; }
  .deepl-controls select, .deepl-controls button { font-size: 0.9rem;
         padding: 0.15rem 0.35rem; }
  .deepl-controls button { cursor: pointer; }
  p { margin: 0 0 0.6rem 0; }
  .deepl-source { color: #666; font-style: italic; margin-bottom: 0.5rem;
         padding-bottom: 0.5rem; border-bottom: 1px solid #eee; }
  .deepl-translation { color: #222; }
  .deepl-msg { color: #555; }
  .deepl-loading { font-style: italic; }
  .deepl-error { color: #b00020; }
  .deepl-meta { margin-top: 0.8rem; font-size: 0.75rem; color: #999; }
`;


/**
 * A "dictionary button" to be shown in the UI.
 * Manages display state, loading and caching content.
 *
 * The class *could* be broken up into things like
 * PopupDictButton, EmbeddedDictButton, etc, but no need for that yet.
 */
class DictButton extends LookupButton {

  constructor(dict, frameName) {
    super(frameName);

    this.dictID = LookupButton.TERM_DICTS.indexOf(dict);
    if (this.dictID == -1) {
      console.log(`Error: Dict url ${dict.url} not found (??)`);
      return;
    }

    this.label = (dict.url.length <= 10) ? dict.url : (dict.url.slice(0, 10) + '...');

    // If the URL is a real url, get icon and label.
    let fimg = null;
    try {
      const urlObj = new URL(dict.url);  // Throws if invalid.
      const domain = urlObj.hostname;
      this.label = domain.split("www.").splice(-1)[0];

      fimg = document.createElement("img");
      fimg.classList.add("dict-btn-fav-img");
      const favicon_src = `http://www.google.com/s2/favicons?domain=${domain}`;
      fimg.src = favicon_src;
    }
    catch(err) {}

    this.btn.textContent = this.label;

    // Must prepend after the textContent is set, or it is overwritten/lost.
    if (fimg != null)
      this.btn.prepend(fimg);

    this.btn.setAttribute("title", this.label);

    this.isExternal = (dict.dicttype == "popuphtml");
    if (this.isExternal) {
      const ext_img = document.createElement("img");
      ext_img.classList.add("dict-btn-external-img");
      this.btn.classList.add("dict-btn-external");
      this.btn.appendChild(ext_img);
    }
  }

  /** LOOKUPS *************************/

  do_lookup() {
    const dict = LookupButton.TERM_DICTS[this.dictID];
    if (LookupButton.TERM_FORM_CONTAINER == null || dict == null)
      return;
    const term = LookupButton.TERM_FORM_CONTAINER.querySelector("#text").value;
    if (this.isExternal) {
      this._load_popup(dict.url, term);
    }
    else {
      this._load_frame(dict.url, term);
    }
    this.activate();
  }

  _get_lookup_url(dicturl, term) {
    let ret = dicturl;
    // Terms are saved with zero-width space between each token;
    // remove that for dict searches.
    const zeroWidthSpace = '\u200b';
    const sqlZWS = '%E2%80%8B';
    const cleantext = term.
          replaceAll(zeroWidthSpace, '').
          replace(/\s+/g, ' ');
    const searchterm = encodeURIComponent(cleantext).
          replaceAll(sqlZWS, '');
    ret = ret.replace('[LUTE]', searchterm);
    ret = ret.replace('###', searchterm);  // TODO remove_old_###_placeholder
    return ret;
  }

  _load_popup(url, term) {
    if ((url ?? "") == "")
      return;
    const lookup_url = this._get_lookup_url(url, term);
    let settings = 'width=800, height=600, scrollbars=yes, menubar=no, resizable=yes, status=no'
    if (LUTE_USER_SETTINGS.open_popup_in_new_tab)
      settings = null;
    window.open(lookup_url, 'otherwin', settings);
  }

  _load_frame(dicturl, text) {
    if (this.isExternal || this.dictID == null) {
      return;
    }
    if (this.contentLoaded) {
      console.log(`${this.label} content already loaded.`);
      return;
    }

    let url = this._get_lookup_url(dicturl, text);

    const is_bing_image_search = (dicturl.indexOf('www.bing.com/images') != -1);
    if (is_bing_image_search) {
      // TODO handle_image_lookup_separately: don't mix term lookups with image lookups.
      let use_text = text;
      const binghash = dicturl.replace('https://www.bing.com/images/search?', '');
      url = `/bing/search/${LookupButton.LANG_ID}/${encodeURIComponent(use_text)}/${encodeURIComponent(binghash)}`;
    }

    this.frame.setAttribute("src", url);
    this.contentLoaded = true;
  }

}  // end DictButton


/**
 * Load excess buttons in a separate div.
 */
function _create_dict_dropdown_div(buttons_in_list) {
  // div containing all the buttons_in_list.
  const list_div = document.createElement("div");
  list_div.setAttribute("id", "dict-list-container");
  list_div.classList.add("dict-list-hide");
  buttons_in_list.forEach(button => {
    button.btn.classList.remove("dict-btn");
    button.btn.classList.add("dict-menu-item");
    list_div.appendChild(button.btn);
  });

  // Top level button to show/hide the list.
  const btn = document.createElement("button");
  btn.classList.add("dict-btn", "dict-btn-select");
  btn.innerHTML = "&hellip; &#9660;"
  btn.setAttribute("title", "More dictionaries");
  btn.addEventListener("click", (e) => {
    list_div.classList.toggle("dict-list-hide");
  });

  const menu_div = document.createElement("div");
  menu_div.setAttribute("id", "dict-menu-container");
  menu_div.appendChild(list_div);
  menu_div.appendChild(btn);
  menu_div.addEventListener("mouseleave", () => {
    list_div.classList.add("dict-list-hide");
  });

  return menu_div;
}

/**
 * Create all buttons.
 */
function createLookupButtons(tab_count = 5) {
  let destroy_existing_dictTab_controls = function() {
    document.querySelectorAll(".dict-btn").forEach(item => item.remove())
    document.querySelectorAll(".dictframe").forEach(item => item.remove())
    const el = document.getElementById("dict-menu-container");
    if (el)
      el.remove();
  }
  destroy_existing_dictTab_controls();
  LookupButton.all = [];

  if (LookupButton.TERM_DICTS.length <= 0) return;

  // const dev_hack_add_dicts = Array.from({ length: 5 }, (_, i) => `a${i}`);
  // LookupButton.TERM_DICTS.push(...dev_hack_add_dicts);

  if (tab_count == (LookupButton.TERM_DICTS.length - 1)) {
    // Don't bother making a list with a single item.
    tab_count += 1;
  }

  // Make all DictButtons, which loads LookupButton.all.
  LookupButton.TERM_DICTS.forEach((dict, index) => { new DictButton(dict,`dict${index}`); });
  const tab_buttons = LookupButton.all.slice(0, tab_count);
  const list_buttons = LookupButton.all.slice(tab_count);

  // Add elements to container.
  const container = document.getElementById("dicttabslayout");
  let grid_col_count = tab_buttons.length;
  tab_buttons.forEach(button => container.appendChild(button.btn));
  if (list_buttons.length > 0) {
    const dropdown_div = _create_dict_dropdown_div(list_buttons);
    container.appendChild(dropdown_div);
    grid_col_count += 1;
  }
  container.style.gridTemplateColumns = `repeat(${grid_col_count}, minmax(2rem, 8rem))`;

  const first_button = LookupButton.all[0];
  if (first_button) {
    first_button.activate();
    first_button.do_lookup();
  }

  const static_buttons = [new SentenceLookupButton(), new ImageLookupButton()];
  const have_settings = typeof LUTE_USER_SETTINGS !== "undefined";
  // Add the AI "Explain in context" panel only when AI features are enabled.
  const ai_enabled = have_settings && LUTE_USER_SETTINGS.ai_suggestions_enabled;
  if (ai_enabled)
    static_buttons.push(new AIExplainLookupButton());
  // Add the DeepL sentence/paragraph translation panel when DeepL is enabled.
  const deepl_enabled = have_settings && LUTE_USER_SETTINGS.deepl_enabled;
  if (deepl_enabled)
    static_buttons.push(new DeepLTranslateLookupButton());
  for (let b of static_buttons)
    document.getElementById("dicttabsstatic").appendChild(b.btn);

  const dictframes = document.getElementById("dictframes");
  LookupButton.all.forEach((button) => { dictframes.appendChild(button.frame); });
}


function loadDictionaries() {
  const dictContainer = document.querySelector(".dictcontainer");
  dictContainer.style.display = "flex";
  dictContainer.style.flexDirection = "column";
  LookupButton.all.forEach(button => button.contentLoaded = false);
  const active_button = LookupButton.all.find(button => button.is_active);
  if (active_button) {
    active_button.do_lookup();
  }
}
