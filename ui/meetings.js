// Meetings: the list and one meeting's notes. Python: Api.meetings_* (app.py),
// the engine in src/meetings. The page is told of changes through
// window.waffler_meetings_changed (a recording started or finished, notes written).
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  let open = null;          // the meeting on screen, or null for the list
  let tab = 'notes';
  let timer = null;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clock = (s) => { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60) % 60, h = Math.floor(s / 3600); return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s % 60).padStart(2, '0'); };
  const length = (s) => { s = Math.round(s || 0); return s < 60 ? s + ' sec' : Math.round(s / 60) + ' min'; };
  const timeOf = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  const dayKey = (iso) => { const d = new Date(iso); return d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate(); };
  function dayLabel(iso) {
    const d = new Date(iso), now = new Date();
    const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    return d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  }

  // Notes arrive as light markdown: ### headings, - bullets, **bold**. Everything is escaped first.
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

  async function refreshState() {
    const a = api(); if (!a) return;
    const s = await a.meetings_state();
    const btn = $('mtRecord'), live = $('mtLive');
    if (btn) { btn.classList.toggle('is-on', !!s.recording); $('mtRecordLabel').textContent = s.recording ? 'Finish and write notes' : 'Record a meeting'; }
    if (live) {
      live.classList.toggle('is-on', !!s.recording);
      $('mtClock').textContent = clock(s.elapsed_s);
      const mic = s.sources.includes('mic'), sys = s.sources.includes('system');
      $('mtSources').textContent = sys && mic ? 'Recording your microphone and the computer’s sound.' : mic ? 'Recording your microphone.' : 'Recording.';
      $('mtWarn').textContent = (s.warnings || []).join(' ');
    }
    const jr = $('jRecord');
    if (jr) {
      jr.classList.toggle('is-on', !!s.recording);
      jr.setAttribute('aria-label', s.recording ? 'Finish the meeting and write the notes' : 'Record a meeting');
      $('jRecordClock').textContent = s.recording ? clock(s.elapsed_s) : '';
    }
    window.clearInterval(timer);
    if (s.recording) timer = window.setInterval(refreshState, 1000);
  }

  async function loadList() {
    const a = api(); if (!a) return;
    const q = ($('mtSearch') && $('mtSearch').value || '').trim();
    const notes = (await a.meetings_list(q)) || [];
    const list = $('mtList'); if (!list) return;
    $('mtEmpty').style.display = notes.length ? 'none' : 'block';
    let html = '', last = '';
    notes.forEach((n) => {
      const k = dayKey(n.created_at);
      if (k !== last) { html += '<p class="mt-day">' + esc(dayLabel(n.created_at)) + '</p>'; last = k; }
      const busy = n.status === 'processing' || n.status === 'recording';
      const sub = n.status === 'recording' ? 'Recording now.' : n.status === 'processing' ? 'Writing the notes. This takes a moment.'
        : String(n.preview || '').replace(/^#{1,4}\s+/gm, '').replace(/^[-*•]\s+/gm, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
      html += '<button type="button" class="mt-row" data-id="' + esc(n.id) + '">'
        + '<span class="mt-time">' + esc(timeOf(n.created_at)) + '</span>'
        + '<span class="mt-body"><span class="mt-title">' + esc(n.title || (busy ? 'New meeting' : 'Untitled'))
        + (n.duration_s ? '<span class="mt-dur">' + esc(length(n.duration_s)) + '</span>' : '') + '</span>'
        + (sub ? '<span class="mt-sum' + (busy ? ' mt-busy' : '') + '">' + esc(sub) + '</span>' : '') + '</span></button>';
    });
    list.innerHTML = html;
    list.querySelectorAll('.mt-row').forEach((b) => b.addEventListener('click', () => openNote(b.dataset.id)));
  }

  function drawNote(n) {
    $('mtTitle').textContent = n.title || (n.status === 'processing' ? 'Naming this meeting…' : 'Untitled');
    $('mtChips').innerHTML = '<span>' + esc(dayLabel(n.created_at)) + ' · ' + esc(timeOf(n.created_at)) + '</span>'
      + (n.duration_s ? '<span>' + esc(length(n.duration_s)) + '</span>' : '');
    document.querySelectorAll('#mtTabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    const body = $('mtBody');
    if (tab === 'transcript') {
      body.className = '';
      body.innerHTML = (n.segments || []).length
        ? n.segments.map((s) => '<p class="mt-line"><span class="mt-who">' + (s.source === 'mic' ? 'Me' : 'Them') + '</span>' + esc(s.text) + '</p>').join('')
        : '<p class="mt-empty">' + (n.status === 'processing' ? 'Transcribing. This takes a moment.' : 'Nothing was heard in this recording.') + '</p>';
    } else {
      body.className = 'mt-prose';
      body.innerHTML = n.summary ? prose(n.summary)
        : '<p class="mt-empty">' + (n.status === 'processing' ? 'Writing the notes. This takes a moment.' : esc(n.error || 'No notes yet.')) + '</p>';
    }
  }

  async function openNote(id) {
    const a = api(); if (!a) return;
    const n = await a.meetings_get(id);
    if (!n) { open = null; $('meetingsPanel').classList.remove('is-note'); showToast('That meeting isn’t here any more.', 'error'); loadList(); return; }
    if (!open || open.id !== n.id) { const l = $('mtAskOneLog'); if (l) l.textContent = ''; }
    open = n; $('meetingsPanel').classList.add('is-note'); drawNote(n);
  }

  function closeNote() { open = null; tab = 'notes'; $('meetingsPanel').classList.remove('is-note'); loadList(); }

  async function toggle() {
    const a = api(); if (!a) { showToast('App not ready yet', 'error'); return; }
    const btns = [$('mtRecord'), $('jRecord')].filter(Boolean);
    btns.forEach((b) => { b.disabled = true; });
    try {
      const r = await a.meetings_toggle();
      if (!r || !r.ok) showToast((r && r.error) || 'The meeting couldn’t be recorded. Try again.', 'error');
      else if (r.ignored) { /* a second press straight after the first */ }
      else if (r.started) showToast('Recording the meeting. Tap your hotkey twice to finish.', 'success');
      else if (r.note_id && document.body.dataset.page === 'meetings') openNote(r.note_id);
      else if (r.note_id) showToast('Meeting finished. The notes are being written.', 'success');
    } finally { btns.forEach((b) => { b.disabled = false; }); refreshState(); if (document.body.dataset.page === 'meetings') loadList(); }
  }

  async function copyNotes() {
    if (!open) return;
    const text = (open.title || 'Meeting') + '\n\n' + (open.summary || (open.segments || []).map((s) => s.text).join('\n'));
    try { await navigator.clipboard.writeText(text); showToast('Notes copied', 'success'); }
    catch (e) { showToast('Couldn’t copy the notes.', 'error'); }
  }

  async function removeNote() {
    if (!open) return;
    const a = api(); if (!a) return;
    if (!window.confirm('Delete this meeting and its recording? This can’t be undone.')) return;
    await a.meetings_delete(open.id); window._journalStale = true; showToast('Meeting deleted', 'success'); closeNote();
  }

  // Ask: one box on the list (every meeting) and one on a meeting (that one).
  function wireAsk(id, noteId) {
    const form = $(id + 'Form'), input = $(id + 'Input'), log = $(id + 'Log'), send = $(id + 'Send');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = input.value.trim(); const a = api();
      if (!q || !a) return;
      input.value = ''; send.disabled = true;
      const item = document.createElement('div'); item.className = 'mt-qa';
      item.innerHTML = '<p class="mt-q">' + esc(q) + '</p><div class="mt-a mt-busy">Thinking. This takes a moment.</div>';
      log.prepend(item);
      try {
        const r = await a.meetings_ask(q, noteId() || '');
        const ans = item.querySelector('.mt-a'); ans.classList.remove('mt-busy');
        if (r && r.ok) {
          ans.classList.add('mt-prose'); ans.innerHTML = prose(r.answer);
          const from = (r.sources || []).filter((x) => x.title).slice(0, 4);
          if (!noteId() && from.length) {
            const src = document.createElement('p'); src.className = 'mt-src';
            src.textContent = 'From: ' + from.map((x) => x.title).join(' · ');
            item.appendChild(src);
          }
        } else { ans.classList.add('mt-warn'); ans.textContent = (r && r.error) || 'That couldn’t be answered just now.'; }
      } catch (err) {
        const ans = item.querySelector('.mt-a'); ans.className = 'mt-a mt-warn'; ans.textContent = 'That couldn’t be answered just now.';
      } finally { send.disabled = false; input.focus(); }
    });
  }

  // A meeting among the dictations in the Journal (app.js _withMeetings, makeCard).
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
    card.addEventListener('click', () => { showPage('meetings'); openNote(n.id); });
    return card;
  };

  // The switch for two taps of the hotkey (settings.json meeting_double_tap).
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
  window.loadMeetingsPage = function () { refreshState(); loadTapSetting(); if (open) openNote(open.id); else loadList(); };
  // Python calls this when something changed (double tap, notes written).
  window.waffler_meetings_changed = function () {
    refreshState();
    // The Journal shows meetings too: redraw it now, or when it is next opened (app.js showPage).
    if (document.body.dataset.page === 'home' && typeof renderFeed === 'function') { renderFeed(); return; }
    window._journalStale = true;
    if (document.body.dataset.page !== 'meetings') return;
    if (open) openNote(open.id); else loadList();
  };

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('meetingsPanel')) return;
    $('mtRecord').addEventListener('click', toggle);
    if ($('jRecord')) $('jRecord').addEventListener('click', toggle);
    window.addEventListener('pywebviewready', refreshState);
    $('mtLiveFinish').addEventListener('click', toggle);
    $('mtBack').addEventListener('click', closeNote);
    $('mtCopy').addEventListener('click', copyNotes);
    $('mtDelete').addEventListener('click', removeNote);
    let t = null;
    $('mtSearch').addEventListener('input', () => { window.clearTimeout(t); t = window.setTimeout(loadList, 180); });
    document.querySelectorAll('#mtTabs button').forEach((b) => b.addEventListener('click', () => { tab = b.dataset.tab; if (open) drawNote(open); }));
    if ($('mtTapToggle')) $('mtTapToggle').addEventListener('change', saveTapSetting);
    wireAsk('mtAskAll', () => '');
    wireAsk('mtAskOne', () => (open ? open.id : ''));
  });
})();
