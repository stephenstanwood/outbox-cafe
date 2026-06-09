// Left/right arrow navigation through the outbox.cafe archive.
// Loaded by every gen page + the homepage. Cabinet has its own listing,
// no arrows there. Wraps at the ends (oldest's prev → newest, etc.)
//
// Keyboard: ArrowLeft / ArrowRight also navigate. 'p' copies permalink.
// Bottom-left 'about' link mirrors the bottom-right permalink — points
// to /about/ so visitors who notice the rotating sign-offs can find
// out who's who.
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

      // Bounded — no wrap. At the oldest, no older. At the newest, no newer.
      // Stephen's call: RIGHT arrow goes back in time, LEFT arrow goes forward.
      var hasOlder = currentIdx > 0;
      var hasNewer = currentIdx < list.length - 1;
      var olderHref = hasOlder ? '/archive/' + list[currentIdx - 1] : null;
      var newerHref = hasNewer ? '/archive/' + list[currentIdx + 1] : null;
      var permaHref = '/archive/' + list[currentIdx];
      var permaUrl = location.origin + permaHref;

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
        '.outbox-perma { position: fixed; right: 6px; bottom: 6px;',
        '  z-index: 99999; padding: 4px 7px; background: transparent;',
        '  color: currentColor; border: 0;',
        '  font: 500 10px/1 ui-monospace, "SF Mono", "Menlo", monospace;',
        '  letter-spacing: 0.04em; text-transform: lowercase;',
        '  cursor: pointer; text-decoration: none !important;',
        '  opacity: 0.12;',
        '  transition: opacity 0.25s ease, background 0.25s ease;',
        '  -webkit-tap-highlight-color: transparent;',
        '  mix-blend-mode: difference; }',
        '.outbox-perma:hover { opacity: 0.65; background: rgba(127,127,127,0.08); }',
        '.outbox-perma.outbox-copied { opacity: 0.65; }',
        '.outbox-about { position: fixed; left: 6px; bottom: 6px;',
        '  z-index: 99999; padding: 4px 7px; background: transparent;',
        '  color: currentColor; border: 0;',
        '  font: 500 10px/1 ui-monospace, "SF Mono", "Menlo", monospace;',
        '  letter-spacing: 0.04em; text-transform: lowercase;',
        '  cursor: pointer; text-decoration: none !important;',
        '  opacity: 0.12;',
        '  transition: opacity 0.25s ease, background 0.25s ease;',
        '  -webkit-tap-highlight-color: transparent;',
        '  mix-blend-mode: difference; }',
        '.outbox-about:hover { opacity: 0.65; background: rgba(127,127,127,0.08); }',
        '@media (max-width: 540px) {',
        '  .outbox-nav { width: 16px; height: 36px; font-size: 12px; opacity: 0.18; }',
        '  .outbox-perma { opacity: 0.2; font-size: 11px; padding: 6px 9px; }',
        '  .outbox-about { opacity: 0.2; font-size: 11px; padding: 6px 9px; }',
        '}',
        '@media print { .outbox-nav, .outbox-perma, .outbox-about { display: none; } }',
      ].join('\n');
      document.head.appendChild(style);

      // Left side of screen → newer (forward in time)
      if (hasNewer) {
        var newer = document.createElement('a');
        newer.className = 'outbox-nav outbox-prev';
        newer.href = newerHref;
        newer.textContent = '←';
        newer.setAttribute('aria-label', 'newer entry');
        document.body.appendChild(newer);
      }

      // Right side of screen → older (back in time)
      if (hasOlder) {
        var older = document.createElement('a');
        older.className = 'outbox-nav outbox-next';
        older.href = olderHref;
        older.textContent = '→';
        older.setAttribute('aria-label', 'older entry');
        document.body.appendChild(older);
      }

      // Permalink button — copies the permanent /archive/... URL for the
      // current gen, so visitors (and Stephen) can share a specific piece
      // even when sitting on the rolling homepage.
      var perma = document.createElement('a');
      perma.className = 'outbox-perma';
      perma.href = permaHref;
      perma.textContent = 'permalink';
      perma.setAttribute('aria-label', 'copy permalink to this entry');
      perma.title = permaUrl;
      var copiedTimer = null;
      function flashCopied() {
        perma.textContent = 'copied';
        perma.classList.add('outbox-copied');
        if (copiedTimer) clearTimeout(copiedTimer);
        copiedTimer = setTimeout(function () {
          perma.textContent = 'permalink';
          perma.classList.remove('outbox-copied');
        }, 1400);
      }
      function copyPerma() {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(permaUrl).then(flashCopied, function () {
            // Clipboard blocked (rare on https) — fall through to letting the link navigate.
            location.href = permaHref;
          });
          return true;
        }
        return false;
      }
      perma.addEventListener('click', function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return; // let cmd-click open in new tab
        if (copyPerma()) e.preventDefault();
      });
      document.body.appendChild(perma);

      // About link — mirrors permalink on the opposite corner. Points to the
      // staff roster so visitors who see rotating sign-offs (—Robin, -j, etc.)
      // can find out who's who. Skip on the /about page itself.
      if (!/^\/about\/?$/.test(location.pathname)) {
        var about = document.createElement('a');
        about.className = 'outbox-about';
        about.href = '/about/';
        about.textContent = 'about';
        about.setAttribute('aria-label', 'about the cafe');
        document.body.appendChild(about);
      }

      document.addEventListener('keydown', function (e) {
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === 'ArrowLeft' && hasNewer) { e.preventDefault(); location.href = newerHref; }
        else if (e.key === 'ArrowRight' && hasOlder) { e.preventDefault(); location.href = olderHref; }
        // 'p' = copy permalink
        else if (e.key === 'p' || e.key === 'P') {
          e.preventDefault();
          copyPerma();
        }
        // 'r' = random gen — stumble-upon mode. Never re-pick the entry
        // you're already on (list[currentIdx] covers homepage too, where
        // the URL match `m` is undefined).
        else if ((e.key === 'r' || e.key === 'R') && list.length > 1) {
          e.preventDefault();
          var pick;
          do { pick = list[Math.floor(Math.random() * list.length)]; }
          while (pick === list[currentIdx]);
          location.href = '/archive/' + pick;
        }
      });

      // Touch swipe nav. Same convention as keys: swipe right → newer, swipe left → older.
      // Skip if the touch starts on an interactive element so gen-page sliders/buttons/links
      // keep working normally.
      function isInteractive(el) {
        while (el && el !== document.body) {
          var tag = el.tagName;
          if (tag === 'INPUT' || tag === 'BUTTON' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'A') return true;
          if (el.isContentEditable) return true;
          if (el.getAttribute && (el.getAttribute('role') === 'slider' || el.getAttribute('role') === 'button')) return true;
          if (el.draggable) return true;
          el = el.parentElement;
        }
        return false;
      }

      var sx = null, sy = null, st = null;
      document.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1) { sx = null; return; }
        if (isInteractive(e.target)) { sx = null; return; }
        sx = e.touches[0].clientX;
        sy = e.touches[0].clientY;
        st = Date.now();
      }, { passive: true });

      document.addEventListener('touchend', function (e) {
        if (sx === null) return;
        var t = e.changedTouches[0];
        var dx = t.clientX - sx;
        var dy = t.clientY - sy;
        var dt = Date.now() - st;
        sx = null;
        if (Math.abs(dx) < 60) return;             // not far enough
        if (Math.abs(dy) > Math.abs(dx) * 0.6) return; // too vertical
        if (dt > 700) return;                       // too slow, likely a drag
        if (dx > 0 && hasNewer) { location.href = newerHref; }
        else if (dx < 0 && hasOlder) { location.href = olderHref; }
      }, { passive: true });
    })
    .catch(function () {});
})();
