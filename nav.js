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
        '  width: 44px; height: 68px;',
        '  background: rgba(253, 249, 236, 0.92);',
        '  color: #2a2418 !important;',
        '  border: 1px solid rgba(42, 36, 24, 0.30);',
        '  box-shadow: 0 4px 14px rgba(0,0,0,0.22);',
        '  font: 700 26px/1 ui-monospace, "SF Mono", "Menlo", monospace;',
        '  cursor: pointer; text-decoration: none !important;',
        '  opacity: 0.75; transition: opacity 0.15s, background 0.15s, transform 0.15s;',
        '  -webkit-tap-highlight-color: transparent; }',
        '.outbox-nav:hover { opacity: 1; background: #fffaeb; text-decoration: none; }',
        '.outbox-nav.outbox-prev { left: 0; border-radius: 0 8px 8px 0; }',
        '.outbox-nav.outbox-next { right: 0; border-radius: 8px 0 0 8px; }',
        '.outbox-nav.outbox-prev:hover { transform: translateY(-50%) translateX(2px); }',
        '.outbox-nav.outbox-next:hover { transform: translateY(-50%) translateX(-2px); }',
        '.outbox-nav-hint { position: fixed; top: 8px; right: 14px; z-index: 99999;',
        '  font: 11px/1.3 ui-monospace, monospace; color: rgba(42,36,24,0.55);',
        '  background: rgba(253,249,236,0.78); padding: 4px 8px; border-radius: 12px;',
        '  pointer-events: none; opacity: 0; transition: opacity 0.3s; }',
        '.outbox-nav-hint.show { opacity: 1; }',
        '@media (max-width: 540px) {',
        '  .outbox-nav { width: 36px; height: 56px; font-size: 20px; }',
        '  .outbox-nav-hint { display: none; }',
        '}',
        '@media print { .outbox-nav, .outbox-nav-hint { display: none; } }',
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

      // One-time hint for keyboard nav, fades after a few seconds
      try {
        if (!sessionStorage.getItem('outbox-nav-hint-shown')) {
          var hint = document.createElement('div');
          hint.className = 'outbox-nav-hint';
          hint.textContent = '← / → to browse';
          document.body.appendChild(hint);
          requestAnimationFrame(function () { hint.classList.add('show'); });
          setTimeout(function () { hint.classList.remove('show'); }, 4500);
          sessionStorage.setItem('outbox-nav-hint-shown', '1');
        }
      } catch (e) {}

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
