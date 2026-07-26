/**
 * Companion pane for parallel-text reading.
 *
 * The pane shows the matching page of the paired book.  That page is
 * worked out server-side from the anchors, so this file only deals with
 * the reader's corrections to it: nudging to a neighbouring page,
 * pinning a pair of pages together, and finding a page by its words.
 */

const companionPane = document.getElementById("read_pane_companion");

if (companionPane) {
  const companionFrame = document.getElementById("companion_frame");
  const companionState = document.getElementById("companion_state");
  const pageIndicator = document.getElementById("companion_page_indicator");
  const searchBox = document.getElementById("companion_search");
  const searchInput = document.getElementById("companion_search_input");
  const searchResults = document.getElementById("companion_search_results");

  const companionBookId = companionPane.dataset.companionBookId;
  const companionPageCount = parseInt(companionPane.dataset.companionPageCount, 10);
  const bookId = document.getElementById("book_id").value;

  let companionPage = parseInt(companionPane.dataset.companionPage, 10);

  /**
   * The page currently being read.
   *
   * Pages are ajax'd into the reading pane, so this is read each time
   * it is needed: the number this file loaded with goes stale on the
   * reader's first page turn.
   *
   * @returns {string} The page number, as the reading pane holds it.
   */
  function currentPage() {
    return document.getElementById("page_num").value;
  }

  /**
   * Point the companion frame at a page and update the indicator.
   *
   * Does not anchor: nudging around to find the right place is a
   * different act from declaring two pages to be the same place.
   *
   * @param {number} page - Companion page number, clamped to the book.
   */
  function showCompanionPage(page) {
    companionPage = Math.max(1, Math.min(page, companionPageCount));
    companionFrame.src = `/parallel/text/${companionBookId}/${companionPage}`;
    pageIndicator.textContent = `${companionPage}/${companionPageCount}`;
  }

  /**
   * Describe how the companion page was arrived at.
   *
   * Drift between distant anchors is invisible until it is bad, so the
   * pane says outright whether it is on an anchor or interpolating, and
   * from where.
   *
   * @param {Object} data - A /parallel/companion response.
   */
  function showState(data) {
    if (data.anchored) {
      companionState.textContent = "anchored";
      companionState.className = "companion-anchored";
      return;
    }

    const from = [];
    if (data.before) from.push(`p.${data.before[0]}`);
    if (data.after) from.push(`p.${data.after[0]}`);
    companionState.textContent = from.length
      ? `interpolated from ${from.join(" and ")}`
      : "proportional";
    companionState.className = "companion-interpolated";
  }

  /**
   * Move the pane to the companion page for the page being read.
   *
   * Called on every page turn, and after anchoring: both change where
   * the mapping lands, and the answer comes from the server because
   * that is where the anchors are.  Any nudging the reader had done is
   * dropped, which is the point -- a page turn is a fresh question.
   */
  async function syncToPage() {
    const response = await fetch(`/parallel/companion/${bookId}/${currentPage()}`);
    const data = await response.json();
    if (!data.paired) return;
    showCompanionPage(data.page);
    showState(data);
  }

  /**
   * Pin the page being read to the companion page on screen.
   */
  async function setAnchor() {
    await fetch("/parallel/anchor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bookid: bookId,
        pagenum: currentPage(),
        companion_page: companionPage,
      }),
    });
    syncToPage();
  }

  /**
   * Remove the anchor on the page being read, if it has one.
   */
  async function clearAnchor() {
    await fetch("/parallel/unanchor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bookid: bookId, pagenum: currentPage() }),
    });
    syncToPage();
  }

  /**
   * Search the companion book for pages containing the given words.
   *
   * Typing two character names is the fastest way to find your place in
   * a long book, so hits are ranked by how many of the words they carry.
   */
  async function runSearch() {
    const query = searchInput.value.trim();
    if (query === "") {
      searchResults.innerHTML = "";
      return;
    }

    const url = `/parallel/search/${companionBookId}?q=${encodeURIComponent(query)}`;
    const response = await fetch(url);
    const data = await response.json();

    if (data.hits.length === 0) {
      searchResults.innerHTML = '<div class="companion-search-empty">No pages found.</div>';
      return;
    }

    searchResults.innerHTML = "";
    data.hits.forEach((hit) => {
      const row = document.createElement("div");
      row.className = "companion-search-hit";
      row.innerHTML =
        `<span class="companion-search-page">p.${hit.page}</span>` +
        `<span class="companion-search-snippet"></span>`;
      // Snippets are book text, so they go in as text, never as markup.
      row.querySelector(".companion-search-snippet").textContent = hit.snippet;
      row.addEventListener("click", () => showCompanionPage(hit.page));
      searchResults.appendChild(row);
    });
  }

  /**
   * Debounce a function, so typing does not fire a request per keystroke.
   *
   * @param {Function} fn - Function to defer.
   * @param {number} delay - Quiet period in milliseconds.
   * @returns {Function} The debounced wrapper.
   */
  function debounce(fn, delay) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delay);
    };
  }

  document
    .getElementById("companion_prev")
    .addEventListener("click", () => showCompanionPage(companionPage - 1));
  document
    .getElementById("companion_next")
    .addEventListener("click", () => showCompanionPage(companionPage + 1));
  document.getElementById("companion_anchor").addEventListener("click", setAnchor);
  document.getElementById("companion_unanchor").addEventListener("click", clearAnchor);

  document.getElementById("companion_search_toggle").addEventListener("click", () => {
    searchBox.classList.toggle("hide");
    if (!searchBox.classList.contains("hide")) searchInput.focus();
  });

  searchInput.addEventListener("input", debounce(runSearch, 300));

  document.addEventListener("lute-page-changed", syncToPage);

  syncToPage();
}
