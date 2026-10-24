// Cookie banner for analytics consent. The choice is stored in a first-party
// cookie on the parent domain, so the portfolio, the blog and the project
// sites under furkankoykiran.com.tr all share it.
(function () {
  var tr = (document.documentElement.lang || 'tr').slice(0, 2) !== 'en';
  var T = tr ? {
    text: 'Siteyi geliştirmek için Google Analytics ve Microsoft Clarity ile anonim kullanım istatistikleri topluyorum. Kabul ederseniz bu araçlar tarayıcınıza analitik çerezleri yazar. Reddederseniz çerez yazılmaz; yalnızca çerezsiz, kimliksiz sayım yapılır.',
    accept: 'Kabul et', reject: 'Reddet', more: 'Gizlilik ve çerez politikası', href: '/gizlilik/',
    label: 'Çerez tercihi'
  } : {
    text: 'I use Google Analytics and Microsoft Clarity to collect anonymous usage statistics so I can improve this site. If you accept, these tools set analytics cookies in your browser. If you decline, no cookies are set and only cookieless, anonymous measurement happens.',
    accept: 'Accept', reject: 'Decline', more: 'Privacy & cookie policy', href: '/en/privacy/',
    label: 'Cookie preference'
  };
  var MAX_AGE = 15552000; // 6 months

  function save(v) {
    var domain = /(^|\.)furkankoykiran\.com\.tr$/.test(location.hostname) ? ';Domain=furkankoykiran.com.tr' : '';
    document.cookie = 'fk_consent=' + v + domain + ';Path=/;Max-Age=' + MAX_AGE + ';SameSite=Lax;Secure';
  }

  function apply(v) {
    var g = v === 'granted' ? 'granted' : 'denied';
    if (window.gtag) gtag('consent', 'update', { analytics_storage: g });
    if (window.clarity) {
      if (g === 'granted') clarity('consentv2', { ad_Storage: 'denied', analytics_Storage: 'granted' });
      else clarity('consent', false);
    }
  }

  function show() {
    if (document.getElementById('fk-cc')) return;
    var b = document.createElement('div');
    b.id = 'fk-cc';
    b.setAttribute('role', 'dialog');
    b.setAttribute('aria-live', 'polite');
    b.setAttribute('aria-label', T.label);
    b.innerHTML = '<p>' + T.text + ' <a href="' + T.href + '">' + T.more + '</a></p>' +
      '<div><button type="button" data-v="denied">' + T.reject + '</button>' +
      '<button type="button" data-v="granted">' + T.accept + '</button></div>';
    b.addEventListener('click', function (e) {
      var v = e.target && e.target.getAttribute('data-v');
      if (!v) return;
      save(v);
      apply(v);
      b.remove();
    });
    document.body.appendChild(b);
  }

  if (!window.__fkConsent) show();

  // The footer's "cookie preferences" link reopens the banner. The sites dispatch
  // a cancelable "fk:consent-open" event for it; data-cookie-settings also works.
  document.addEventListener('fk:consent-open', function (e) { e.preventDefault(); show(); });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('[data-cookie-settings]');
    if (a) { e.preventDefault(); show(); }
  });
})();
