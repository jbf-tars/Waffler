// Settings, Keys and providers: the ways Waffler runs, one chosen. "On this
// computer" (one model, downloaded once, shared by dictation and meetings) or
// "Your own key" (the key itself is app.js renderKeyCard). Python:
// Api.local_stt_status / _download / _cancel / _remove, and transcription_mode
// through save_settings (docs/OFFLINE-TRANSCRIPTION.md). Under the first,
// where the computer is offered it, the optional writing model.
//
// A third way, above the two, only for an account that has it: Waffler Plus
// (the account service does the dictation: no key, no download). The card is
// made here and exists for a signed-in Plus account alone (logic.js
// plusCardView, from Api.account_status); for anyone else there is no such
// element in the page. Chosen through save_settings({ dictation_plus: true });
// choosing either other way unchooses it.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  let poll = null;
  let acct = null;        // the last Api.account_status()
  let last = null;        // the last Api.local_stt_status()
  let lastLlm = null;     // the last Api.local_llm_status()
  let plusOn = false;     // Waffler Plus is the chosen way: the line under the cards says what it covers

  // The Plus card: made, kept up to date, or taken away. True when it is
  // the chosen way.
  function drawPlus() {
    const L = window.WafflerLogic, v = L ? L.plusCardView(acct) : null;
    let card = $('hirPlus');
    if (!v) { if (card) card.remove(); return false; }
    if (!card) {
      const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; };
      card = el('div', 'hir-card'); card.id = 'hirPlus'; card.dataset.mode = 'plus';
      const pick = el('label', 'hir-pick');
      const radio = document.createElement('input');
      radio.type = 'radio'; radio.name = 'hirMode'; radio.id = 'hirPlusRadio'; radio.value = 'plus';
      radio.addEventListener('change', choosePlus);
      pick.append(radio, el('span', 'hir-t', v.label));
      const model = el('div', 'hir-model');
      const state = el('span', 'hir-state'); state.id = 'hirPlusState'; state.setAttribute('role', 'status');
      model.append(state);
      card.append(pick, el('p', 'hir-d', v.helper), model);
      $('howItRuns').prepend(card);
    }
    $('hirPlusState').textContent = v.state;
    $('hirPlusRadio').checked = v.chosen;
    card.classList.toggle('is-on', v.chosen);
    return v.chosen;
  }

  // What the first row says and offers is decided in logic.js (speechRowView).
  function draw(s) {
    const L = window.WafflerLogic;
    if (!$('howItRuns') || !s || !L) return;
    last = s;
    const local = s.mode === 'local';
    $('hirLocalRadio').checked = local; $('hirCloudRadio').checked = !local;
    $('hirLocal').classList.toggle('is-on', local); $('hirCloud').classList.toggle('is-on', !local);
    const plus = drawPlus();
    if (plus !== plusOn) { plusOn = plus; drawPlusNote(); }
    if (plus) {             // the third way is the one chosen: neither of these two is
      $('hirLocalRadio').checked = false; $('hirCloudRadio').checked = false;
      $('hirLocal').classList.toggle('is-on', false); $('hirCloud').classList.toggle('is-on', false);
    }
    const v = L.speechRowView(s);
    const dl = $('hirDownload');
    $('hirBar').hidden = !v.bar; $('hirBarFill').style.width = v.percent + '%';
    $('hirCancel').hidden = !v.stop; $('hirRemove').hidden = !v.remove;
    dl.hidden = !v.download; if (v.download) dl.textContent = v.download;
    $('hirLocalRadio').disabled = v.off;
    $('hirLocal').classList.toggle('is-off', v.off);
    $('hirState').textContent = v.state;
    drawLocalLine();
    window.clearTimeout(poll);
    if (v.bar) poll = window.setTimeout(refresh, 700);
  }

  // ── The graphics card: NVIDIA's libraries, so speech runs on the card ──
  // Python: Api.gpu_pack_status / _download / _cancel / _remove. What the
  // row says is decided in logic.js (gpuRowView); the row is made here only
  // where it is offered (Windows, an NVIDIA card, the libraries missing), so
  // the page as written says nothing of it.
  let lastGpu = null;     // the last Api.gpu_pack_status()
  let pollGpu = null;

  // The line under "On this computer": slower on the processor, but free
  // and private; the plain line where it runs on the graphics (logic.js).
  function drawLocalLine() {
    const L = window.WafflerLogic, line = $('hirLocalD');
    if (L && line && last) line.textContent = L.localCardLine(last, lastGpu);
  }

  function gpuRow() {
    let row = $('hirGpu');
    if (row) return row;
    const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; };
    const button = (cls, id, label, text, act) => {
      const b = el('button', cls, text); b.type = 'button'; b.id = id; b.hidden = true;
      b.setAttribute('aria-label', label); b.addEventListener('click', act); return b;
    };
    row = el('div', 'hir-model hir-extra'); row.id = 'hirGpu';
    row.setAttribute('role', 'group'); row.setAttribute('aria-labelledby', 'hirGpuT');
    const title = el('span', 'hir-extra-t'); title.id = 'hirGpuT';
    const about = el('span', 'hir-extra-d'); about.id = 'hirGpuAbout';
    const state = el('span', 'hir-state'); state.id = 'hirGpuState'; state.setAttribute('role', 'status');
    const bar = el('span', 'hir-bar'); bar.id = 'hirGpuBar'; bar.hidden = true;
    const fill = document.createElement('i'); fill.id = 'hirGpuBarFill'; bar.append(fill);
    row.append(title, about, state, bar,
      button('btn btn-sm btn-sec', 'hirGpuDownload', 'Download the graphics card files', 'Download', downloadGpu),
      button('btn btn-sm btn-quiet', 'hirGpuCancel', 'Stop the download', 'Stop', cancelGpu),
      button('btn btn-sm btn-quiet', 'hirGpuRemove', 'Remove the graphics card files', 'Remove', removeGpu));
    $('hirModel').insertAdjacentElement('afterend', row);
    return row;
  }

  function drawGpu(g) {
    const L = window.WafflerLogic;
    if (!L || !$('hirModel')) return;
    lastGpu = g;
    const v = L.gpuRowView(g);
    window.clearTimeout(pollGpu);
    const had = $('hirGpu');
    if (!v.show) { if (had) had.hidden = true; drawLocalLine(); return; }
    gpuRow().hidden = false;
    $('hirGpuT').textContent = v.title;
    $('hirGpuAbout').textContent = v.about;
    const state = $('hirGpuState'); state.textContent = v.state; state.hidden = !v.state;
    $('hirGpuBar').hidden = !v.bar; $('hirGpuBarFill').style.width = v.percent + '%';
    const dl = $('hirGpuDownload'); dl.hidden = !v.download; if (v.download) dl.textContent = v.download;
    $('hirGpuCancel').hidden = !v.stop; $('hirGpuRemove').hidden = !v.remove;
    drawLocalLine();
    if (v.bar) pollGpu = window.setTimeout(refreshGpu, 700);
  }

  async function refreshGpu() {
    const a = api(); if (!a || !a.gpu_pack_status) return;
    try { drawGpu(await a.gpu_pack_status()); } catch (e) { /* the page stays as it is */ }
  }
  async function downloadGpu() {
    const a = api(); if (!a) return;
    try { const r = await a.gpu_pack_download(); if (r && r.ok === false && r.error) showToast(r.error, 'error', 6000); } catch (e) { showToast('The download couldn’t start. Try again.', 'error'); }
    refreshGpu();
  }
  async function cancelGpu() { const a = api(); if (!a) return; try { await a.gpu_pack_cancel(); } catch (e) { /* refresh says */ } refreshGpu(); }
  async function removeGpu() {
    const a = api(); if (!a) return;
    if (!window.confirm('Remove the graphics card files from this computer? Speech goes back to the processor, which is slower. You can download them again later.')) return;
    try {
      const r = await a.gpu_pack_remove();
      if (r && r.pending && r.note) showToast(r.note, 'info', 6000);
    } catch (e) { showToast('The graphics card files couldn’t be removed.', 'error'); }
    refreshGpu();
  }
  // The download moved or ended (app.py _gpu_pack_changed): how it ended is said once.
  window.waffler_gpu_pack_changed = (g) => {
    const was = lastGpu && lastGpu.state === 'downloading';
    if (was && g && g.state === 'ready') showToast('Your graphics card turns speech into text now.', 'success', 4000);
    else if (was && g && g.state === 'not_downloaded' && g.error) showToast(g.error, 'error', 6000);
    drawGpu(g);
  };

  async function refresh() {
    const a = api(); if (!a || !a.local_stt_status) return;
    try { if (a.account_status) acct = await a.account_status(); } catch (e) { /* as it was last known */ }
    try { draw(await a.local_stt_status()); } catch (e) { /* the page stays as it is */ }
  }

  async function choosePlus() {
    const a = api(); if (!a) return;
    try {
      const r = await a.save_settings({ dictation_plus: true });
      if (r && r.ok) showToast('Dictation runs on Waffler Plus now.', 'success', 3500);
      else showToast((r && r.error) || 'That couldn’t be saved. Try again.', 'error', 5000);
    } catch (e) { showToast('That couldn’t be saved. Try again.', 'error'); }
    refresh(); privacyWords();
  }
  // The account changed (signed in or out, the plan read again): account.js says so.
  window.wafflerPlusChanged = (status) => { acct = status; if (last) draw(last); privacyWords(); };
  // The privacy sentences name where speech and text go: said again when the way changes.
  function privacyWords() { if (window.refreshPrivacyWays) window.refreshPrivacyWays(); }

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
    refresh(); privacyWords();
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

  // With Waffler Plus the chosen way, the line under the cards says what it
  // covers (it said notes still need a key, which Plus makes untrue).
  function drawPlusNote() {
    const note = $('hirNote'), L = window.WafflerLogic;
    if (note && L && lastLlm && plusOn) note.textContent = L.howItRunsNote(lastLlm, hasKey, plusOn);
    else if (note && L && lastLlm) note.textContent = L.howItRunsNote(lastLlm, hasKey);
  }

  function drawLlm(s) {
    const box = $('hirLlm'), L = window.WafflerLogic;
    if (!box || !L || !s) return;
    const v = L.writingRowView(s, hasKey);
    const note = $('hirNote');
    if (note) note.textContent = L.howItRunsNote(s, hasKey);
    lastLlm = s;
    drawPlusNote();
    box.hidden = !v.show;
    const how = $('hirLlmHow');        // what it needs and how long it takes, under "How it works"
    if (how) { how.textContent = v.how || how.textContent; how.hidden = !v.how; }
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

  window.loadHowItRuns = () => { refresh(); refreshLlm(false); refreshGpu(); };
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
