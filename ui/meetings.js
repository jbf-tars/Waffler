// Meetings in Waffler's window. The Meetings tab is the meetings interface in a
// frame (ui/meetings-app); this file keeps the rest of the window in step with
// it: the record button and Ask on Home, and a meeting's card among the
// dictations. Python: Api.meetings_* (app.py). (The switch for two taps of the
// hotkey is a row in Settings, Shortcuts: app.js _meetingTapRowHtml.)
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
  // ── one look: the frame wears this window's own tokens ───────────────────
  // The meetings interface is a page of its own, with its own stylesheet. So
  // that Home, Meetings and Settings are one app, it is handed every token of
  // tokens.css as this window has resolved it (the theme chosen, and anything
  // a later stylesheet changed), and the address of tokens.css for the font
  // files. It asks for them as it starts (window.parent.wafflerTokens(), or a
  // 'meetings-tokens-wanted' message), and is told again whenever the theme
  // changes. The names are tokens.css's: tests/test_one_look.py keeps the two
  // lists the same.
  const TOKENS = [
    // colours, shadows (the light and night themes)
    '--bg', '--surface', '--surface-2', '--card', '--line', '--ring', '--ring-strong', '--ring-ctl', '--tog-on-ring',
    '--dots', '--text', '--text-2', '--muted', '--faint', '--honey', '--honey-light', '--honey-deep', '--honey-tint',
    '--honey-wash', '--accent-ink', '--focus', '--tape', '--ok', '--ok-tint', '--danger', '--danger-tint', '--rec',
    '--rec-grid', '--waffle', '--waffle-grid',
    '--on-ink-ok', '--on-ink-err', '--btn-pri-bg', '--btn-pri-fg', '--btn-pri-hover', '--btn-sec-bg', '--btn-sec-hover',
    '--kc-fg', '--kc-bg', '--kc-shadow', '--scrim', '--stage', '--stage-line', '--stage-grid', '--stage-edge',
    '--heat-0', '--heat-1', '--heat-2', '--heat-3', '--heat-4', '--danger-solid', '--danger-solid-hover',
    '--shadow-btn', '--shadow-bubble', '--shadow-panel', '--shadow-pop', '--shadow-window',
    // type, space, shape, motion (the same in both themes)
    '--f-display', '--f-ui', '--f-mono', '--f-serif', '--t-title', '--t-heading', '--t-stat', '--t-body-lg', '--t-body',
    '--t-desc', '--t-mono', '--t-label', '--t-accent', '--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s8', '--s10',
    '--s12', '--gutter', '--column', '--bar-h', '--r-pill', '--r-input', '--r-tile', '--r-media', '--r-card-sm',
    '--r-card', '--r-card-lg', '--ease', '--ease-out', '--dur-fast', '--dur-ui', '--dur-enter',
  ];
  window.wafflerTokens = function () {
    const css = window.getComputedStyle(document.body), tokens = {};
    TOKENS.forEach((name) => { const v = css.getPropertyValue(name).trim(); if (v) tokens[name] = v; });
    const sheet = document.querySelector('link[rel="stylesheet"][href$="tokens.css"]');
    return { type: 'meetings-tokens', theme: document.body.dataset.theme || '', sheet: sheet ? sheet.href : '', tokens };
  };
  function sendTokens() { tell(window.wafflerTokens()); }

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
      if ($('jRecordLabel')) $('jRecordLabel').textContent = s.recording ? 'Finish' : 'Record a meeting';
      jr.title = s.recording ? 'Finish the meeting and write the notes (or tap your hotkey twice)' : 'Record a meeting (or tap your hotkey twice)';
    }
    window.clearInterval(timer);
    if (s.recording) timer = window.setInterval(refreshState, 1000);
  }

  async function toggle() {
    const a = api(); if (!a) { showToast('App not ready yet', 'error'); return; }
    const btn = $('jRecord'); if (btn) btn.disabled = true;
    try {
      // Nothing is said here: the meeting bar shows that a meeting is being
      // recorded and when its notes are being written, and one that could
      // not be recorded is said once, by the small pill (app.py
      // _say_meeting_failed).
      await a.meetings_toggle();
    } finally { if (btn) btn.disabled = false; refreshState(); tell({ type: 'meetings-refresh' }); }
  }

  // ── Home's Ask: one question across the dictations and the meetings ─────
  function wireHomeAsk() {
    const form = $('jAskForm'); if (!form) return;
    const input = $('jAskInput'), send = $('jAskSend'), box = $('jAskAnswer');
    $('jAskClose').addEventListener('click', () => { box.hidden = true; });
    // The answer is drawn as it is written (app.py _ask_listener): `asking`
    // is the question on screen, so a late piece of an old answer is ignored.
    let asking = '';
    window.waffler_ask_text = function (id, text) {
      if (!id || id !== asking) return;
      const ans = $('jAskA'); ans.className = 'mt-a mt-prose'; ans.innerHTML = prose(text);
      box.scrollTop = box.scrollHeight;
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = input.value.trim(), a = api();
      if (!q || !a) return;
      input.value = ''; send.disabled = true; box.hidden = false;
      $('jAskQ').textContent = q; $('jAskSrc').textContent = '';
      const ans = $('jAskA'); ans.className = 'mt-a mt-busy'; ans.textContent = 'Thinking';
      const mine = asking = 'ask' + Date.now();
      try {
        const r = await a.meetings_ask(q, '', true, mine);
        if (mine !== asking) return;               // a newer question took the box
        asking = '';
        if (r && r.ok) {
          ans.className = 'mt-a mt-prose'; ans.innerHTML = prose(r.answer);
          const from = (r.sources || []).filter((x) => x.title).slice(0, 3);
          if (from.length) $('jAskSrc').textContent = 'Meetings looked at: ' + from.map((x) => x.title).join(' · ');
        } else { ans.className = 'mt-a mt-warn'; ans.textContent = (r && r.error) || 'That couldn’t be answered just now.'; }
      } catch (err) { ans.className = 'mt-a mt-warn'; ans.textContent = 'That couldn’t be answered just now.'; }
      finally { if (mine === asking) asking = ''; send.disabled = false; }
    });
  }

  // What a meeting was about, in a line or two for Home: the points of the
  // notes without their headings (the title already says the first one), and
  // without a "Next steps" that only says there are none.
  function gist(preview) {
    const lines = String(preview || '').split(/\r?\n/).map((l) => l.trim())
      .filter((l) => l && !/^#{1,4}\s/.test(l))
      .map((l) => l.replace(/^[-*•]\s+/, '').replace(/\*\*/g, '').replace(/^\[[ xX]\]\s*/, ''))
      .filter((l) => l && !/^(none\.?|no (clear |specific )?(actions?|action items?|next steps?|follow[- ]?ups?)\b.{0,30})$/i.test(l));
    return lines.join(' ').replace(/\s+/g, ' ').trim();
  }
  window._meetingGist = gist;

  // ── a meeting among the dictations on Home (app.js _withMeetings, makeCard) ─
  window.makeMeetingCard = function (n) {
    const busy = n.status === 'processing' || n.status === 'recording';
    const sub = n.status === 'recording' ? 'Recording now.' : n.status === 'processing' ? 'Writing the notes. This takes a moment.'
      : gist(n.preview);
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

  // app.js's showPage calls this when the Meetings page opens.
  window.loadMeetingsPage = function () { frame(); refreshState(); sendTokens(); tell({ type: 'meetings-refresh' }); };
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
    window.addEventListener('pywebviewready', refreshState);
    wireHomeAsk();
    // The frame's look follows this window's: when it has loaded, when it
    // asks, and when the theme changes (app.js sets body[data-theme]; System
    // follows the computer's own light and dark).
    if ($('mtFrame')) $('mtFrame').addEventListener('load', sendTokens);
    window.addEventListener('message', (e) => {
      const f = $('mtFrame');
      if (f && e.source === f.contentWindow && e.data && e.data.type === 'meetings-tokens-wanted') sendTokens();
    });
    new MutationObserver(sendTokens).observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
    if (window.matchMedia) {
      const os = window.matchMedia('(prefers-color-scheme: dark)');
      if (os.addEventListener) os.addEventListener('change', () => window.setTimeout(sendTokens, 0));
    }
  });
})();
