// Left/right arrow navigation through the outbox.cafe archive.
// Loaded by every gen page + the homepage. Cabinet has its own listing,
// no arrows there. Wraps at the ends (oldest's prev → newest, etc.)
//
// Keyboard: ArrowLeft / ArrowRight also navigate.
(function () {
  fetch('/archive/list.json', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (list) {
      if (!Array.isArray(list) || list.length === 0) return;

      var path = location.pathname.replace(/\/$/, '');
      var isHomepage = path === '' || path === '/index.html' || path === '/index';
      var currentIdx;
      if (isHomepage) {
        currentIdx = list.length - 1; // newest
      } else {
        var m = path.match(/\/archive\/([^/]+\.html)$/);
        if (!m) return;
        currentIdx = list.indexOf(m[1]);
        if (currentIdx < 0) return;
      }

      var prevIdx = currentIdx === 0 ? list.length - 1 : currentIdx - 1;
      var nextIdx = currentIdx === list.length - 1 ? 0 : currentIdx + 1;
      var prevHref = '/archive/' + list[prevIdx];
      var nextHref = '/archive/' + list[nextIdx];

      var style = document.createElement('style');
      style.id = 'outbox-nav-style';
      style.textContent = [
        '.outbox-nav { position: fixed; top: 50%; transform: translateY(-50%);',
        '  z-index: 99999; display: flex; align-items: center; justify-content: center;',
        '  width: 20px; height: 44px;',
        '  background: transparent;',
        '  color: currentColor;',
        '  border: 0;',
        '  font: 500 14px/1 ui-monospace, "SF Mono", "Menlo", monospace;',
        '  cursor: pointer; text-decoration: none !important;',
        '  opacity: 0.12;',
        '  transition: opacity 0.25s ease, background 0.25s ease, width 0.25s ease;',
        '  -webkit-tap-highlight-color: transparent;',
        '  mix-blend-mode: difference; }',
        '.outbox-nav:hover { opacity: 0.6; background: rgba(127,127,127,0.08); width: 28px; }',
        '.outbox-nav.outbox-prev { left: 0; }',
        '.outbox-nav.outbox-next { right: 0; }',
        '@media (max-width: 540px) {',
        '  .outbox-nav { width: 16px; height: 36px; font-size: 12px; opacity: 0.18; }',
        '}',
        '@media print { .outbox-nav { display: none; } }',
      ].join('\n');
      document.head.appendChild(style);

      var prev = document.createElement('a');
      prev.className = 'outbox-nav outbox-prev';
      prev.href = prevHref;
      prev.textContent = '←';
      prev.setAttribute('aria-label', 'previous in the collection');
      document.body.appendChild(prev);

      var next = document.createElement('a');
      next.className = 'outbox-nav outbox-next';
      next.href = nextHref;
      next.textContent = '→';
      next.setAttribute('aria-label', 'next in the collection');
      document.body.appendChild(next);

      document.addEventListener('keydown', function (e) {
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === 'ArrowLeft') { e.preventDefault(); location.href = prevHref; }
        else if (e.key === 'ArrowRight') { e.preventDefault(); location.href = nextHref; }
      });
    })
    .catch(function () {});
})();
