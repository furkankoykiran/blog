/* Progressive enhancements, bundled and deferred by layouts/partials/head.html. */
(function () {
  "use strict";

  // 1) Deep-linkable search: /?q=term opens the Fuse search modal pre-filled (WebSite SearchAction target).
  function openSearchFromQuery() {
    var q = new URLSearchParams(window.location.search).get("q");
    if (!q) return;
    var btn = document.getElementById("search-button") || document.getElementById("search-button-mobile");
    var input = document.getElementById("search-query");
    if (!btn || !input) return;
    btn.click();
    var tries = 0;
    (function fill() {
      input.value = q;
      input.dispatchEvent(new Event("keyup"));
      input.dispatchEvent(new Event("input"));
      if (++tries < 10) setTimeout(fill, 150);
    })();
  }

  // 2) giscus comments: injected only when the comments section scrolls into view.
  function giscusTheme() {
    return document.documentElement.classList.contains("dark") ? "dark_dimmed" : "light";
  }
  function loadGiscus(box) {
    if (box.dataset.loaded) return;
    box.dataset.loaded = "1";
    var s = document.createElement("script");
    s.src = "https://giscus.app/client.js";
    s.async = true;
    s.crossOrigin = "anonymous";
    var d = box.dataset;
    var attrs = {
      "data-repo": d.repo, "data-repo-id": d.repoId, "data-category": d.category,
      "data-category-id": d.categoryId, "data-mapping": d.mapping, "data-strict": d.strict,
      "data-reactions-enabled": d.reactions, "data-emit-metadata": d.emitMetadata,
      "data-input-position": d.inputPosition, "data-theme": giscusTheme(),
      "data-lang": d.lang, "data-loading": "lazy"
    };
    Object.keys(attrs).forEach(function (k) { if (attrs[k] != null) s.setAttribute(k, attrs[k]); });
    box.querySelector(".fk-giscus-target").appendChild(s);
  }
  function setupGiscus() {
    var box = document.getElementById("fk-comments");
    if (!box) return;
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { loadGiscus(box); io.disconnect(); } });
      }, { rootMargin: "400px 0px" });
      io.observe(box);
    } else {
      loadGiscus(box);
    }
    // keep giscus in sync with the site's light/dark switch
    new MutationObserver(function () {
      var frame = document.querySelector("iframe.giscus-frame");
      if (frame) frame.contentWindow.postMessage({ giscus: { setConfig: { theme: giscusTheme() } } }, "https://giscus.app");
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  }

  function init() { openSearchFromQuery(); setupGiscus(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
