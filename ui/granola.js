// Import from Granola: the entry in the Meetings tab, and the one sheet it
// opens. Python: Api.granola_status / granola_import / granola_cancel /
// granola_disconnect (app.py; src/meetings/granola_import.py). What the sheet
// says for each state is L.granolaView (logic.js).
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const L = window.WafflerLogic;
  let view = L.granolaView(null), status = null, timer = null, opener = null;

  // ── the sheet ─────────────────────────────────────────────────────────────
  function draw(s) {
    if (s) status = s;
    view = L.granolaView(status);
    const sheet = $('granolaSheet'); if (!sheet) return;
    if ($('grTitle').textContent !== view.title) $('grTitle').textContent = view.title;
    $('grTitle').classList.toggle('gr-long', view.title.length > 44);   // a failure's sentence, a size down
    $('grCount').textContent = view.count; $('grCount').hidden = !view.count;
    $('grBar').hidden = !view.count; $('grFill').style.width = view.percent + '%';
    $('grNote').textContent = view.note; $('grNote').hidden = !view.note;
    $('grGo').textContent = view.primaryText; $('grGo').hidden = !view.primary; $('grGo').disabled = false;
    $('grQuiet').textContent = view.quietText; $('grQuiet').hidden = !view.quiet;
    // Said aloud when the state changes, not at every meeting counted.
    const said = [view.title, view.note].filter(Boolean).join(' ');
    if ($('grLive').dataset.said !== said) { $('grLive').dataset.said = said; $('grLive').textContent = said; }
    window.clearTimeout(timer);
    const running = status && (status.state === 'connecting' || status.state === 'importing');
    if (running && !sheet.hidden) timer = window.setTimeout(refresh, 1000);
  }

  async function refresh() {
    const a = api(); if (!a || !a.granola_status) return;
    try { draw(await a.granola_status()); } catch (e) { /* the sheet stays as it is */ }
  }

  async function act(name) {
    const a = api(); if (!a || !a[name]) { draw({ ok: false, state: 'error' }); return; }
    $('grGo').disabled = true;
    try { draw(await a[name]()); } catch (e) { draw({ ok: false, state: 'error' }); }
  }

  function open() {
    const sheet = $('granolaSheet'); if (!sheet) return;
    opener = document.activeElement;
    sheet.hidden = false;
    draw(); refresh();
    $('grTitle').focus();
  }

  function close() {
    const sheet = $('granolaSheet'); if (!sheet || sheet.hidden) return;
    sheet.hidden = true;                    // an import that is running carries on; opening again shows it
    window.clearTimeout(timer);
    try { if (opener && opener.focus) opener.focus(); } catch (e) { /* the frame went */ }
  }

  // ── the entry: a button beside "Import audio" in the meetings interface ───
  // That interface is a build (ui/meetings-app) of another project's source,
  // so the button is put there from here, and put back whenever the
  // interface redraws its side column.
  function plant() {
    const f = $('mtFrame'); let doc = null;
    try { doc = f && f.contentDocument; } catch (e) { return; }
    if (!doc || !doc.body || doc.getElementById('wafflerGranola')) return;
    const audio = Array.from(doc.querySelectorAll('.foot button')).find((b) => /Import audio/.test(b.textContent));
    if (!audio) return;
    const b = doc.createElement('button');
    b.type = 'button'; b.id = 'wafflerGranola'; b.className = 'ghost'; b.textContent = 'Import from Granola';
    const icon = audio.querySelector('svg');              // the same drawing, its arrow pointing in
    if (icon) {
      const mark = icon.cloneNode(true), line = mark.querySelector('path');
      if (line) line.setAttribute('d', 'M12 5v11M7.5 11.5L12 16l4.5-4.5M5 19h14');
      b.prepend(mark);
    }
    b.addEventListener('click', open);
    audio.insertAdjacentElement('afterend', b);
    audio.parentNode.style.flexWrap = 'wrap';
  }
  function watch() {
    const f = $('mtFrame'); let doc = null;
    try { doc = f && f.contentDocument; } catch (e) { return; }
    if (!doc || !doc.body) return;
    plant();
    new MutationObserver(plant).observe(doc.body, { childList: true, subtree: true });
  }

  // Python calls this as the import goes (app.py _granola_changed).
  window.waffler_granola_changed = function (s) { if (s) draw(s); };
  window.openGranolaImport = open;

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('granolaSheet')) return;
    $('grGo').addEventListener('click', () => { if (view.primary === 'close') close(); else act('granola_import'); });
    $('grQuiet').addEventListener('click', () => act(view.quiet === 'cancel' ? 'granola_cancel' : 'granola_disconnect'));
    $('grClose').addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('granolaSheet').hidden) { e.preventDefault(); close(); } });
    if ($('mtFrame')) { $('mtFrame').addEventListener('load', watch); watch(); }
  });
})();
