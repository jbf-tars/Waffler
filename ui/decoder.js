// Import audio's converter on Windows: the one sheet an import puts up when
// the converter is not here yet ("Import audio needs a one-time download,
// 77 MB." Download / Not now), then its progress and how it ended. Python:
// app.py _decoder_show calls window.waffler_decoder_offer and waits for its
// answer; _decoder_progress and _decoder_done call the other two;
// Api.audio_decoder_answer and Api.audio_decoder_cancel are the buttons
// (src/audio_decoder_pack.py Offer). What the sheet says is L.decoderView
// (logic.js).
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const L = window.WafflerLogic;
  let state = { stage: 'offer' }, opener = null;

  function draw() {
    const v = L.decoderView(state);
    if ($('dcTitle').textContent !== v.title) $('dcTitle').textContent = v.title;
    $('dcCount').textContent = v.count; $('dcCount').hidden = !v.count;
    $('dcBar').hidden = !v.bar; $('dcFill').style.width = v.percent + '%';
    $('dcNote').textContent = v.note; $('dcNote').hidden = !v.note;
    $('dcGo').textContent = v.goText; $('dcGo').hidden = !v.go;
    $('dcQuiet').textContent = v.quietText; $('dcQuiet').hidden = !v.quiet;
    // Said aloud when the stage changes, not at every megabyte.
    const said = [v.title, v.note].filter(Boolean).join(' ');
    if ($('dcLive').dataset.said !== said) { $('dcLive').dataset.said = said; $('dcLive').textContent = said; }
  }

  function close() {
    const sheet = $('decoderSheet'); if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    try { if (opener && opener.focus) opener.focus(); } catch (e) { /* the frame went */ }
  }

  function tell(name, arg) {
    const a = api(); if (!a || !a[name]) return;
    try { a[name](arg); } catch (e) { /* the import answers as before after its wait */ }
  }

  window.waffler_decoder_offer = function (view) {
    const sheet = $('decoderSheet'); if (!sheet || !L || !L.decoderView) return false;
    state = { stage: 'offer', mb: view && view.mb };
    opener = document.activeElement;
    sheet.hidden = false; draw(); $('dcTitle').focus();
    return true;
  };

  window.waffler_decoder_progress = function (done, total) {
    const sheet = $('decoderSheet'); if (!sheet || sheet.hidden || state.stage === 'failed') return;
    state = { stage: done >= total ? 'checking' : 'downloading', mb: state.mb, done, total };
    draw();
  };

  window.waffler_decoder_done = function (ok, sentence) {
    const sheet = $('decoderSheet'); if (!sheet) return;
    if (ok || !sentence) { close(); return; }        // here, or Not now / Cancel: the import goes on as it may
    state = { stage: 'failed', sentence };
    sheet.hidden = false; draw(); $('dcGo').focus();
  };

  function go() {
    if (state.stage === 'offer') {
      state = { stage: 'downloading', mb: state.mb, done: 0, total: (Number(state.mb) || 0) * 1e6 };
      draw();
      tell('audio_decoder_answer', 'download');
    } else if (state.stage === 'failed') {
      close();
    }
  }

  function quiet() {
    if (state.stage === 'offer') { tell('audio_decoder_answer', 'not_now'); close(); }
    else if (state.stage === 'downloading' || state.stage === 'checking') tell('audio_decoder_cancel');
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('decoderSheet')) return;
    $('dcGo').addEventListener('click', go);
    $('dcQuiet').addEventListener('click', quiet);
    $('decoderSheet').addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      if (state.stage === 'failed') close(); else quiet();
    });
  });
})();
