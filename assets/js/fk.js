/* Progressive enhancements, bundled and deferred by layouts/partials/head.html. */
(function () {
  "use strict";

  // 0) GA4 events: data-ga-event + data-ga-param-* on any element, and
  // automatic outbound-link tracking. Consent Mode handles storage; without
  // analytics configured this is a no-op.
  function track(name, params) {
    if (typeof window.gtag === "function") window.gtag("event", name, params || {});
  }
  function setupAnalytics() {
    document.addEventListener("click", function (ev) {
      var target = ev.target;
      if (!target || !target.closest) return;
      var tagged = target.closest("[data-ga-event]");
      if (tagged) {
        var params = {};
        Array.prototype.forEach.call(tagged.attributes, function (attr) {
          if (attr.name.indexOf("data-ga-param-") === 0) {
            params[attr.name.slice(14).replace(/-/g, "_")] = attr.value;
          }
        });
        track(tagged.getAttribute("data-ga-event"), params);
        return;
      }
      var link = target.closest("a[href]");
      if (!link || link.hostname === location.hostname) return;
      track("outbound_click", {
        link_domain: link.hostname,
        link_url: link.href,
        link_text: (link.textContent || "").trim().slice(0, 80)
      });
    }, true);
  }

  // 0b) Live demo: the iframe is created on click, with loading="lazy", a
  // descriptive title and a responsive 16/9 wrapper supplied by CSS.
  function setupEmbeds() {
    document.querySelectorAll("[data-embed]").forEach(function (frame) {
      var button = frame.querySelector("[data-embed-load]");
      if (!button) return;
      button.addEventListener("click", function () {
        var src = frame.getAttribute("data-embed");
        var title = frame.getAttribute("data-embed-title") || "Live demo";
        if (!src) return;
        var iframe = document.createElement("iframe");
        iframe.src = src;
        iframe.title = title;
        iframe.loading = "lazy";
        iframe.width = "960";
        iframe.height = "540";
        iframe.referrerPolicy = "strict-origin-when-cross-origin";
        iframe.setAttribute("allow", "fullscreen; clipboard-write");
        iframe.setAttribute("allowfullscreen", "");
        iframe.setAttribute(
          "sandbox",
          "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        );
        frame.classList.add("is-loading");
        iframe.addEventListener("load", function () {
          frame.classList.add("is-loaded");
          button.hidden = true;
        });
        frame.appendChild(iframe);
        track("demo_embed_open", { project: title, embed_url: src });
      });
    });
  }

  // 0c) Language suggestion strip, never a redirect. The bar is rendered by
  // header/basic.html and the head script decides before the first paint
  // whether to show it, so this only wires dismissal and remembers it.
  function setupLanguageSuggestion() {
    var bar = document.getElementById("fk-langbar");
    var data = document.getElementById("fk-lang-suggest-data");
    if (!bar || !data) return;
    try {
      var cfg = JSON.parse(data.textContent);
      var key = "fk_lang_suggest_" + cfg.lang + "_" + location.pathname;
      var close = bar.querySelector("[data-lang-dismiss]");
      if (!close) return;
      close.addEventListener("click", function () {
        try { localStorage.setItem(key, String(Date.now() + 30 * 864e5)); } catch (e) {}
        bar.remove();
        document.documentElement.removeAttribute("data-lang-suggest");
      });
    } catch (e) {}
  }

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

  function init() {
    openSearchFromQuery();
    setupGiscus();
    setupAnalytics();
    setupEmbeds();
    setupLanguageSuggestion();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
