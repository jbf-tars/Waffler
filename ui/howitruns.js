// Settings, Keys and providers: how speech becomes text. "On this computer"
// (one model, downloaded once, shared by dictation and meetings) or "with your
// own key". Python: Api.local_stt_status / _download / _cancel / _remove, and
// transcription_mode through save_settings (docs/OFFLINE-TRANSCRIPTION.md).
// Under it, where the computer is offered it, the optional writing model.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  let poll = null;

  const mb = (bytes) => { const n = Math.round((bytes || 0) / 1e6); return n >= 1000 ? (n / 1000).toFixed(1) + ' GB' : n + ' MB'; };

  function draw(s) {
    if (!$('howItRuns') || !s) return;
    const local = s.mode === 'local';
    $('hirLocalRadio').checked = local; $('hirCloudRadio').checked = !local;
    $('hirLocal').classList.toggle('is-on', local); $('hirCloud').classList.toggle('is-on', !local);
    const state = $('hirState'), bar = $('hirBar'), dl = $('hirDownload'), cancel = $('hirCancel'), remove = $('hirRemove');
    const downloading = s.state === 'downloading', ready = s.state === 'ready';
    bar.hidden = !downloading; cancel.hidden = !downloading; dl.hidden = downloading || ready || !s.available; remove.hidden = !ready;
    $('hirLocalRadio').disabled = !s.available;
    $('hirLocal').classList.toggle('is-off', !s.available);
    if (!s.available) state.textContent = 'Not available on this computer yet.';
    else if (downloading) {
      const pct = Math.max(0, Math.min(100, Math.round(s.percent || 0)));
      $('hirBarFill').style.width = pct + '%';
      state.textContent = 'Downloading the speech model: ' + pct + '%' + (s.bytes_total ? ' of ' + mb(s.bytes_total) : '') + '.';
    } else if (ready) state.textContent = 'The speech model is ready (' + mb(s.size_on_disk || s.bytes_total) + ' on this computer).';
    else {
      dl.textContent = 'Download the speech model' + (s.bytes_total ? ' (' + mb(s.bytes_total) + ')' : '');
      state.textContent = s.error ? s.error : (s.bytes_done ? 'The download was stopped part-way. It carries on from where it got to.' : 'One download, used for dictation and meetings.');
    }
    window.clearTimeout(poll);
    if (downloading) poll = window.setTimeout(refresh, 700);
  }

  async function refresh() {
    const a = api(); if (!a || !a.local_stt_status) return;
    try { draw(await a.local_stt_status()); } catch (e) { /* the page stays as it is */ }
  }

  async function setMode(mode) {
    const a = api(); if (!a) return;
    let s = null;
    try { s = await a.local_stt_status(); } catch (e) { /* below */ }
    if (mode === 'local' && s && s.state !== 'ready') {            // the model first, then the switch
      showToast('Download the speech model first. It only takes a minute.', 'info', 4000);
      await refresh(); return;
    }
    try {
      const r = await a.save_settings({ transcription_mode: mode });
      if (r && r.ok) showToast(mode === 'local' ? 'Speech is turned into text on this computer now.' : 'Speech is turned into text with your key now.', 'success', 3500);
      else showToast((r && r.error) || 'That couldn’t be saved. Try again.', 'error', 5000);
    } catch (e) { showToast('That couldn’t be saved. Try again.', 'error'); }
    refresh();
  }

  async function download() {
    const a = api(); if (!a) return;
    try { const r = await a.local_stt_download(); if (r && r.ok === false && r.error) showToast(r.error, 'error', 5000); } catch (e) { showToast('The download couldn’t start. Try again.', 'error'); }
    refresh();
  }
  async function cancel() { const a = api(); if (!a) return; try { await a.local_stt_cancel(); } catch (e) { /* refresh says */ } refresh(); }
  async function remove() {
    const a = api(); if (!a) return;
    if (!window.confirm('Remove the speech model from this computer? You can download it again later.')) return;
    try { await a.local_stt_remove(); } catch (e) { showToast('The model couldn’t be removed.', 'error'); }
    refresh();
  }

  // ── The writing model: tidy text and write notes on this computer too ──
  // Python: Api.local_llm_status / _download / _cancel / _remove, and
  // writing_mode through save_settings. What the row shows is decided in
  // logic.js (writingRowView, howItRunsNote).
  let pollLlm = null;
  let hasKey = false;

  function drawLlm(s) {
    const box = $('hirLlm'), L = window.WafflerLogic;
    if (!box || !L || !s) return;
    const v = L.writingRowView(s, hasKey);
    const note = $('hirNote');
    if (note) note.textContent = L.howItRunsNote(s, hasKey);
    box.hidden = !v.show;
    window.clearTimeout(pollLlm);
    if (!v.show) return;
    $('hirLlmAbout').textContent = v.about;
    const state = $('hirLlmState');
    state.textContent = v.state; state.hidden = !v.state;
    $('hirLlmBar').hidden = !v.bar;
    $('hirLlmBarFill').style.width = v.percent + '%';
    const dl = $('hirLlmDownload');
    dl.hidden = !v.download; if (v.download) dl.textContent = v.download;
    $('hirLlmCancel').hidden = !v.stop;
    $('hirLlmRemove').hidden = !v.remove;
    $('hirLlmPick').hidden = !v.choice;
    $('hirLlmChoice').checked = v.chosen;
    if (v.bar) pollLlm = window.setTimeout(() => refreshLlm(true), 700);
  }

  // `ticking`: a look while it downloads, which does not ask for the keys again.
  async function refreshLlm(ticking) {
    const a = api(); if (!a || !a.local_llm_status) return;
    try {
      if (!ticking && a.get_settings) {
        const st = await a.get_settings();
        hasKey = !!(st && (st.groq_key_set || st.api_key_set || st.cerebras_key_set));
      }
      const s = await a.local_llm_status();
      const wasReady = drawLlm.ready;
      drawLlm.ready = !!(s && s.state === 'ready');
      drawLlm(s);
      // It arrived, or it went: the rest of the page says who tidies now.
      if (wasReady !== undefined && wasReady !== drawLlm.ready && window.loadSettings) window.loadSettings();
    } catch (e) { /* the page stays as it is */ }
  }

  async function downloadLlm() {
    const a = api(); if (!a) return;
    try { const r = await a.local_llm_download(); if (r && r.ok === false && r.error) showToast(r.error, 'error', 6000); } catch (e) { showToast('The download couldn’t start. Try again.', 'error'); }
    refreshLlm(true);
  }
  async function cancelLlm() { const a = api(); if (!a) return; try { await a.local_llm_cancel(); } catch (e) { /* refresh says */ } refreshLlm(true); }
  async function removeLlm() {
    const a = api(); if (!a) return;
    if (!window.confirm('Remove the writing model from this computer? Tidying and notes will need a key again. You can download it again later.')) return;
    try { await a.local_llm_remove(); } catch (e) { showToast('The writing model couldn’t be removed.', 'error'); }
    refreshLlm(false);
  }
  async function chooseLlm(on) {
    const a = api(); if (!a) return;
    try {
      const r = await a.save_settings({ writing_mode: on ? 'local' : 'cloud' });
      if (r && r.ok) showToast(on ? 'Text is tidied and notes are written on this computer now.' : 'Your key tidies text and writes notes again.', 'success', 3500);
      else showToast((r && r.error) || 'That couldn’t be saved. Try again.', 'error', 5000);
    } catch (e) { showToast('That couldn’t be saved. Try again.', 'error'); }
    await refreshLlm(false);
    if (window.loadSettings) window.loadSettings();
  }
  window.waffler_local_llm_changed = (s) => { if (s && s.state !== 'downloading') refreshLlm(false); };

  window.loadHowItRuns = () => { refresh(); refreshLlm(false); };
  document.addEventListener('DOMContentLoaded', () => {
    if (!$('howItRuns')) return;
    if ($('hirLlm')) {
      $('hirLlmDownload').addEventListener('click', downloadLlm);
      $('hirLlmCancel').addEventListener('click', cancelLlm);
      $('hirLlmRemove').addEventListener('click', removeLlm);
      $('hirLlmChoice').addEventListener('change', (e) => chooseLlm(e.target.checked));
    }
    $('hirLocalRadio').addEventListener('change', () => setMode('local'));
    $('hirCloudRadio').addEventListener('change', () => setMode('cloud'));
    $('hirDownload').addEventListener('click', download);
    $('hirCancel').addEventListener('click', cancel);
    $('hirRemove').addEventListener('click', remove);
  });
})();
