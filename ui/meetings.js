// Meetings in Waffler's window. The Meetings tab is the meetings interface in a
// frame (ui/meetings-app); this file keeps the rest of the window in step with
// it: the record button and Ask on Home, a meeting's card among the dictations,
// and the switch for two taps of the hotkey. Python: Api.meetings_* (app.py).
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  let timer = null;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clock = (s) => { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60) % 60, h = Math.floor(s / 3600); return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s % 60).padStart(2, '0'); };
  const length = (s) => { s = Math.round(s || 0); return s < 60 ? s + ' sec' : Math.round(s / 60) + ' min'; };
  const timeOf = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };

  // Answers arrive as light markdown: ### headings, - bullets, **bold**. Everything is escaped first.
  function prose(md) {
    const out = []; let list = false;
    const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    String(md || '').split(/\r?\n/).forEach((raw) => {
      const line = raw.trim();
      const bullet = /^[-*•]\s+(.*)$/.exec(line);
      if (bullet) { if (!list) { out.push('<ul>'); list = true; } out.push('<li>' + inline(bullet[1]) + '</li>'); return; }
      if (list) { out.push('</ul>'); list = false; }
      const head = /^#{1,4}\s+(.*)$/.exec(line);
      if (head) out.push('<h3>' + inline(head[1]) + '</h3>');
      else if (line) out.push('<p>' + inline(line) + '</p>');
    });
    if (list) out.push('</ul>');
    return out.join('');
  }

  // ── the frame ────────────────────────────────────────────────────────────
  let pendingOpen = null;
  function frame() {
    const f = $('mtFrame');
    if (f && !f.getAttribute('src')) f.setAttribute('src', f.dataset.src);   // loaded the first time the tab opens
    return f;
  }
  function tell(message) {
    const f = $('mtFrame');
    try { if (f && f.contentWindow && f.getAttribute('src')) f.contentWindow.postMessage(message, '*'); } catch (e) { /* not loaded yet */ }
  }
  function openMeeting(id) {
    pendingOpen = id;
    showPage('meetings');
    const send = () => { if (pendingOpen) { tell({ type: 'meetings-open', id: pendingOpen }); } };
    send(); window.setTimeout(send, 400); window.setTimeout(() => { send(); pendingOpen = null; }, 1200);
  }

  // ── recording state, for Home's button ──────────────────────────────────
  async function refreshState() {
    const a = api(); if (!a) return;
    let s; try { s = await a.meetings_state(); } catch (e) { return; }
    const jr = $('jRecord');
    if (jr) {
      jr.classList.toggle('is-on', !!s.recording);
      jr.setAttribute('aria-label', s.recording ? 'Finish the meeting and write the notes' : 'Record a meeting');
      $('jRecordClock').textContent = s.recording ? clock(s.elapsed_s) : '';
    }
    window.clearInterval(timer);
    if (s.recording) timer = window.setInterval(refreshState, 1000);
  }

  async function toggle() {
    const a = api(); if (!a) { showToast('App not ready yet', 'error'); return; }
    const btn = $('jRecord'); if (btn) btn.disabled = true;
    try {
      const r = await a.meetings_toggle();
      if (!r || !r.ok) showToast((r && r.error) || 'The meeting couldn’t be recorded. Try again.', 'error');
      else if (r.ignored) { /* a second press straight after the first */ }
      else if (r.started) showToast('Recording the meeting. Tap your hotkey twice to finish.', 'success');
      else if (r.note_id) showToast('Meeting finished. The notes are being written.', 'success');
    } finally { if (btn) btn.disabled = false; refreshState(); tell({ type: 'meetings-refresh' }); }
  }

  // ── Home's Ask: one question across the dictations and the meetings ─────
  function wireHomeAsk() {
    const form = $('jAskForm'); if (!form) return;
    const input = $('jAskInput'), send = $('jAskSend'), box = $('jAskAnswer');
    $('jAskClose').addEventListener('click', () => { box.hidden = true; });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = input.value.trim(), a = api();
      if (!q || !a) return;
      input.value = ''; send.disabled = true; box.hidden = false;
      $('jAskQ').textContent = q; $('jAskSrc').textContent = '';
      const ans = $('jAskA'); ans.className = 'mt-a mt-busy'; ans.textContent = 'Thinking. This takes a moment.';
      try {
        const r = await a.meetings_ask(q, '', true);
        if (r && r.ok) {
          ans.className = 'mt-a mt-prose'; ans.innerHTML = prose(r.answer);
          const from = (r.sources || []).filter((x) => x.title).slice(0, 3);
          if (from.length) $('jAskSrc').textContent = 'Meetings looked at: ' + from.map((x) => x.title).join(' · ');
        } else { ans.className = 'mt-a mt-warn'; ans.textContent = (r && r.error) || 'That couldn’t be answered just now.'; }
      } catch (err) { ans.className = 'mt-a mt-warn'; ans.textContent = 'That couldn’t be answered just now.'; }
      finally { send.disabled = false; }
    });
  }

  // ── a meeting among the dictations on Home (app.js _withMeetings, makeCard) ─
  window.makeMeetingCard = function (n) {
    const busy = n.status === 'processing' || n.status === 'recording';
    const sub = n.status === 'recording' ? 'Recording now.' : n.status === 'processing' ? 'Writing the notes. This takes a moment.'
      : String(n.preview || '').replace(/^#{1,4}\s+/gm, '').replace(/^[-*•]\s+/gm, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
    const card = document.createElement('article');
    card.className = 'transcript-card j-ent j-meeting';
    // The same three columns as a dictation: the time and a figure, the words, nothing in the third.
    card.innerHTML = '<div class="j-g"><div class="j-t">' + esc(timeOf(n.created_at)) + '</div>'
      + '<div class="j-w">' + esc(n.duration_s ? length(n.duration_s) : 'Meeting') + '</div></div>'
      + '<div class="j-body"><button type="button" class="mt-open">'
      + '<span class="mt-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>'
      + '<span class="mt-open-t">' + esc(n.title || (busy ? 'New meeting' : 'Untitled meeting')) + '</span></button>'
      + (sub ? '<p class="mt-sum' + (busy ? ' mt-busy' : '') + '">' + esc(sub) + '</p>' : '') + '</div><div></div>';
    card.addEventListener('click', () => openMeeting(n.id));
    return card;
  };

  // ── the switch for two taps of the hotkey (settings.json meeting_double_tap) ─
  async function loadTapSetting() {
    const a = api(), tog = $('mtTapToggle'); if (!a || !tog) return;
    try { const s = await a.get_settings(); tog.checked = !s || s.meeting_double_tap !== false; } catch (e) { /* leave it as it is */ }
  }
  async function saveTapSetting() {
    const a = api(), tog = $('mtTapToggle'); if (!a || !tog) return;
    const on = tog.checked;
    try {
      const r = await a.save_settings({ meeting_double_tap: on });
      if (r && r.ok) showToast(on ? 'Two taps of your hotkey record a meeting.' : 'Your hotkey only dictates now.', 'success', 3500);
      else { tog.checked = !on; showToast('That couldn’t be saved. Try again.', 'error'); }
    } catch (e) { tog.checked = !on; showToast('That couldn’t be saved. Try again.', 'error'); }
  }

  // app.js's showPage calls this when the Meetings page opens.
  window.loadMeetingsPage = function () { frame(); refreshState(); loadTapSetting(); tell({ type: 'meetings-refresh' }); };
  // Python calls this when something changed (the hotkey, the bar, notes written).
  window.waffler_meetings_changed = function () {
    refreshState();
    tell({ type: 'meetings-refresh' });
    // Home shows meetings too: redraw it now, or when it is next opened (app.js showPage).
    if (document.body.dataset.page === 'home' && typeof renderFeed === 'function') { renderFeed(); return; }
    window._journalStale = true;
  };

  document.addEventListener('DOMContentLoaded', () => {
    if ($('jRecord')) $('jRecord').addEventListener('click', toggle);
    if ($('mtTapToggle')) $('mtTapToggle').addEventListener('change', saveTapSetting);
    window.addEventListener('pywebviewready', refreshState);
    wireHomeAsk();
  });
})();
