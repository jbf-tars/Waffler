/* Waffler UI logic: small pure functions shared by app.js and the tests.
 *
 * Nothing here touches the page or the Python bridge, so
 * tests/test_ui_logic.py can run every function in Node on any platform.
 * index.html loads this file before app.js, where it is window.WafflerLogic.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WafflerLogic = api;
})(typeof window !== 'undefined' ? window : this, function () {
  'use strict';

  // ── Main-window status pill ───────────────────────────────────────────
  // Python calls window.waffler_status(<status>) at each stage of a
  // dictation (app.py notify_js_status). One label per stage, the same
  // words everywhere. "Done" shows briefly, then the pill says Ready again.
  const STATUS_VIEWS = {
    idle:       { cls: 'idle',       label: 'Ready' },
    listening:  { cls: 'listening',  label: 'Recording' },
    paused:     { cls: 'paused',     label: 'Paused' },
    processing: { cls: 'processing', label: 'Cleaning up' },
    done:       { cls: 'done',       label: 'Done' },
    // How a dictation can end besides Done (the pipeline watchdog in
    // src/pipeline_watchdog.py always ends it one of these ways).
    cancelled:  { cls: 'cancelled',  label: 'Cancelled' },
    not_sent:   { cls: 'not-sent',   label: 'Not sent' },
    error:      { cls: 'error',      label: 'Something went wrong' },
  };
  const STATUS_CLASSES = Object.keys(STATUS_VIEWS).map((k) => STATUS_VIEWS[k].cls);
  const DONE_RESET_MS = 3000;
  // An end state shows for a moment, then the pill says Ready again. Bad
  // news stays a little longer, so it can be read.
  const STATUS_RESET_MS = { done: DONE_RESET_MS, cancelled: DONE_RESET_MS, 'not-sent': 6000, error: 6000 };

  // An unknown status shows as Ready rather than as its raw name.
  function statusView(status) {
    return Object.prototype.hasOwnProperty.call(STATUS_VIEWS, status)
      ? STATUS_VIEWS[status] : STATUS_VIEWS.idle;
  }

  // Milliseconds before a status class goes back to Ready, or 0 to stay.
  function statusResetMs(cls) {
    return Object.prototype.hasOwnProperty.call(STATUS_RESET_MS, cls) ? STATUS_RESET_MS[cls] : 0;
  }

  // While processing, the pill counts the seconds beside its label: "4 s",
  // then "1 min 4 s". Nothing under a second, so a quick dictation never
  // flickers a number. workingLabel is the whole line, for the tooltip.
  function workingTime(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (s < 1) return '';
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
  }

  function workingLabel(label, seconds) {
    const t = workingTime(seconds);
    return t ? `${label} · ${t}` : label;
  }

  // While recording, the pill shows how long you've been talking: "0:04".
  function recordingTime(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  // ── Not sent recordings (Journal cards) ────────────────────────────────
  // app.py saves a recording that could not be turned into text and adds a
  // card with not_sent_reason. The card explains it in plain words; the raw
  // error never reaches the screen.
  const NOT_SENT_ID = /^recording-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d{1,3})?\.wav$/;

  // The recording's id. app.py sends unsent_id ("" once the file has gone);
  // only a card that never had the field falls back to its audio_path.
  function notSentId(item) {
    if (!item || !item.failed) return '';
    const id = Object.prototype.hasOwnProperty.call(item, 'unsent_id')
      ? String(item.unsent_id || '')
      : String(item.audio_path || '').split(/[\\/]/).pop();
    return NOT_SENT_ID.test(id) ? id : '';
  }

  function _reasonOf(item) {
    if (item.not_sent_reason) return item.not_sent_reason;
    // Cards saved before 3.14.100 only have the raw error text.
    const e = String(item.error || '').toLowerCase();
    if (/403|401|access denied|unauthori[sz]ed|permission/.test(e)) return 'blocked';
    if (/429|rate.?limit/.test(e)) return 'rate_limited';
    if (/timed out|timeout|deadline/.test(e)) return 'timeout';
    if (/connection|network|resolve|no transcription backend/.test(e)) return 'offline';
    return 'error';
  }

  function notSentView(item) {
    const id = notSentId(item);
    const p = (item && item.provider_name) || 'your speech service';
    const P = p.charAt(0).toUpperCase() + p.slice(1);
    const kept = "The recording is saved on this computer.";
    const reason = _reasonOf(item || {});
    const text = {
      offline: `Waffler couldn't reach ${p}, so this wasn't turned into text. ${kept}`,
      blocked: `${P} refused the connection, which usually means a VPN is on. ${kept}`,
      timeout: `${P} took too long to answer, so Waffler stopped waiting. ${kept}`,
      rate_limited: `${P} said you'd reached your limit for now. ${kept}`,
      later: `You chose to send this later. ${kept}`,
      stuck: `Waffler stopped waiting for this one. ${kept}`,
      cancelled: `You pressed Esc while this was being turned into text, so nothing was pasted. ${kept}`,
      // The same sentence as src/user_messages.py INTERRUPTED_MESSAGE (a test keeps them equal).
      interrupted: `Waffler was closed before this one was turned into text. ${kept}`,
      empty: "This recording was sent again, but no words could be heard in it.",
      error: `This wasn't turned into text. ${kept}`,
    }[reason] || `This wasn't turned into text. ${kept}`;
    if (!id) {
      return { id: '', badge: 'Not sent', text: "This wasn't turned into text, and the recording couldn't be saved.",
        next: '', canRetry: false, canReveal: false, canDelete: true };
    }
    let next = 'Click Try again to send it now.';
    if (reason === 'empty') next = '';
    else if (item.will_retry === true) next = `Waffler will send it by itself when ${p} answers.`;
    return { id, badge: 'Not sent', text, next, canRetry: reason !== 'empty', canReveal: true, canDelete: true };
  }

  // What Try again says when it did not work this time.
  function retryFailedMessage(reason) {
    return {
      offline: "Still couldn't connect. Check you're online and try again.",
      blocked: 'The connection was refused again. If a VPN is on, turn it off and try again.',
      timeout: 'No answer again. Try again in a moment.',
      rate_limited: "You're still at your limit. Try again later.",
      empty: 'It went through, but no words could be heard in this recording.',
      missing: 'The recording file is no longer there.',
      busy: 'Waffler is already sending this one.',
    }[reason] || "It didn't go through. Try again in a moment.";
  }

  // Settings, Data: how many recordings are waiting.
  function unsentSummary(s) {
    const n = (s && s.count) || 0;
    if (!n) return { label: 'Every recording has been turned into text.', canSend: false, count: 0,
                     confirm: '' };
    const p = (s && s.provider) || 'your speech service';
    return {
      label: `${n === 1 ? '1 recording is' : `${formatCount(n)} recordings are`} waiting to be sent. `
        + `Waffler sends ${n === 1 ? 'it' : 'them'} when ${p} answers.`,
      canSend: true,
      count: n,
      confirm: n === 1 ? 'Delete the recording waiting to be sent?'
        : `Delete the ${formatCount(n)} recordings waiting to be sent?`,
    };
  }

  // ── One Journal entry: delete, redo, undo ──────────────────────────────
  // How many recordings Waffler keeps (src/recent_audio.py KEEP): only that
  // many entries, the newest, can be redone; a new dictation pushes the oldest
  // one out.
  const RECENT_AUDIO_KEEP = 10;

  // What the window sends to find an entry (history_ops.find): its id when it
  // has one (entries saved with one), else its time and its words.
  function entryRef(item) {
    const id = item && Number.isInteger(item.id) && item.id > 0 ? item.id : null;
    return [id, String((item && item.timestamp) || ''), String((item && item.text) || '')];
  }

  // Where the next page starts: before the oldest entry held, by its id, so it
  // stays right when entries are added or deleted between pages; by offset
  // for an entry that is older than ids.
  function pageCursor(items) {
    const list = items || [];
    const last = list.length ? list[list.length - 1] : null;
    if (last && Number.isInteger(last.id) && last.id > 0) return { beforeId: last.id, offset: 0 };
    return { beforeId: null, offset: list.length };
  }

  // Where an entry that comes back belongs in a newest-first list: after
  // every entry that is not older than it.
  function insertIndex(items, item) {
    const list = items || [];
    const when = String((item && item.timestamp) || '');
    let i = 0;
    while (i < list.length && String(list[i].timestamp || '') >= when) i += 1;
    return i;
  }

  // The line that offers Undo in the entry's place.
  function undoLine(kind) {
    return kind === 'replaced' ? 'Entry redone.' : 'Entry deleted.';
  }

  function deleteFailedMessage(reason) {
    return {
      not_found: 'That entry has already gone.',
      busy: 'Waffler is busy. Try again in a moment.',
    }[reason] || "Couldn't delete that entry. Try again.";
  }

  function undoFailedMessage(reason) {
    return reason === 'expired'
      ? 'Too late to undo that.'
      : "Couldn't put that entry back. Try again.";
  }

  // What "Redo this take" says when it did not change the entry.
  function redoFailedMessage(reason) {
    return {
      offline: "Couldn't connect, so this entry is unchanged. Check you're online and try again.",
      blocked: 'The connection was refused, so this entry is unchanged. If a VPN is on, turn it off and try again.',
      timeout: 'No answer in time, so this entry is unchanged. Try again in a moment.',
      rate_limited: "You've reached your limit for now, so this entry is unchanged. Try again later.",
      empty: 'No words could be heard in this recording, so this entry is unchanged.',
      missing: "That recording isn't kept any more, so it can't be redone.",
      not_found: 'That entry has already gone.',
      busy: 'Waffler is busy with another recording. Try again in a moment.',
      not_ready: "Waffler isn't ready yet. Try again in a moment.",
    }[reason] || "That didn't go through, so this entry is unchanged. Try again in a moment.";
  }

  // ── Export history (Settings, Privacy and data) ─────────────────────────
  // src/history_export.py FORMATS, in the order the list shows them.
  const EXPORT_FORMATS = [
    { id: 'json', label: 'JSON' },
    { id: 'csv', label: 'CSV' },
    { id: 'txt', label: 'Plain text' },
  ];

  function exportFormat(id) {
    return EXPORT_FORMATS.some((f) => f.id === id) ? id : EXPORT_FORMATS[0].id;
  }

  // The toast after an export. The file is shown in its folder as well.
  function exportDone(result) {
    const where = /[\\/]Desktop[\\/]/.test(String((result && result.path) || ''))
      ? 'your Desktop' : 'your home folder';
    const n = Number(result && result.count) || 0;
    const what = n === 1 ? '1 entry' : `${formatCount(n)} entries`;
    return n ? `Saved ${what} to ${where}.` : `Saved to ${where}.`;
  }

  function exportFailed(result) {
    return (result && result.reason === 'empty')
      ? 'Your Journal is empty, so there is nothing to export.'
      : "Couldn't save the file. Try again.";
  }

  // ── History retention (Settings, Privacy and data) ─────────────────────
  // src/privacy_data.py HISTORY_CHOICES: 0 keeps everything.
  const HISTORY_KEEP = [0, 365, 90, 30];

  function historyKeepLabel(days) {
    const d = Number(days) || 0;
    if (d === 365) return 'a year';
    return d ? `${d} days` : '';
  }

  // What the panel asks before a shorter choice deletes dictations.
  function historyKeepConfirm(days, count) {
    const n = Number(count) || 0;
    return `Delete ${n === 1 ? '1 dictation' : `${formatCount(n)} dictations`} older than ${historyKeepLabel(days)}?`;
  }

  // The toast after a choice is saved.
  function historyKeepDone(days, deleted) {
    const n = Number(deleted) || 0;
    const keep = Number(days) ? `Waffler keeps your dictations for ${historyKeepLabel(days)}.` : 'Waffler keeps every dictation.';
    if (!n) return keep;
    return `${n === 1 ? '1 older dictation' : `${formatCount(n)} older dictations`} deleted. ${keep}`;
  }

  // ── Hotkeys ───────────────────────────────────────────────────────────
  // Names and order match src/hotkey_rules.py, so a hotkey reads the same
  // on every screen and on the website: Windows modifiers always come as
  // Win, Ctrl, Alt, Shift ("Win + Ctrl"); a Mac uses words ("Command").
  const WIN_MODIFIER_ORDER = ['win', 'ctrl', 'alt', 'shift'];
  const WIN_NAMES = {
    win: 'Win', ctrl: 'Ctrl', control: 'Ctrl', alt: 'Alt', option: 'Alt',
    shift: 'Shift', cmd: 'Win', command: 'Win', meta: 'Win', space: 'Space', fn: 'Fn',
  };
  const MAC_NAMES = {
    fn: 'Fn', cmd: 'Command', command: 'Command', shift: 'Shift', option: 'Option',
    alt: 'Option', ctrl: 'Control', control: 'Control', space: 'Space', win: 'Command',
  };
  const DEFAULT_KEYS = { mac: ['fn'], win: ['win', 'ctrl'] };

  function defaultHotkey(isMac) {
    return (isMac ? DEFAULT_KEYS.mac : DEFAULT_KEYS.win).slice();
  }

  function _clean(keys) {
    return (Array.isArray(keys) ? keys : [])
      .map((k) => String(k || '').trim().toLowerCase())
      .filter(Boolean);
  }

  function keyName(key, isMac) {
    const k = String(key || '').toLowerCase();
    const names = isMac ? MAC_NAMES : WIN_NAMES;
    if (names[k]) return names[k];
    if (k.length === 1 || /^f\d+$/.test(k)) return k.toUpperCase();
    return k.charAt(0).toUpperCase() + k.slice(1);
  }

  function orderKeys(keys, isMac) {
    const list = _clean(keys);
    if (isMac) return list;
    const mods = WIN_MODIFIER_ORDER.filter((m) => list.includes(m));
    return mods.concat(list.filter((k) => !WIN_MODIFIER_ORDER.includes(k)));
  }

  // "Win + Ctrl", "Command + Shift", "Fn". Empty keys mean the default.
  function hotkeyName(keys, isMac) {
    const list = orderKeys(keys, isMac);
    return (list.length ? list : defaultHotkey(isMac)).map((k) => keyName(k, isMac)).join(' + ');
  }

  // What each keycap shows: a Mac's fn key is printed "fn" with a globe,
  // the Windows key has the Windows logo; everything else is its name.
  function keycaps(keys, isMac) {
    const list = orderKeys(keys, isMac);
    return (list.length ? list : defaultHotkey(isMac)).map((k) => ({
      key: k,
      label: isMac && k === 'fn' ? 'fn' : keyName(k, isMac),
      icon: isMac && k === 'fn' ? 'globe' : (!isMac && k === 'win' ? 'windows' : null),
    }));
  }

  // The name is always "Win + Ctrl"; pressing Ctrl first is a separate tip,
  // so Windows never sees the Win key held on its own.
  function pressOrderHint(keys, isMac) {
    const list = _clean(keys);
    return !isMac && list.includes('win') && list.includes('ctrl')
      ? 'Tip: press Ctrl first, then Win.' : '';
  }

  // The hotkeys Settings and the setup wizard offer, per platform. Each one
  // must pass src/hotkey_rules.py on its platform (tests/test_ui_logic.py
  // checks): Windows used to be offered the Mac keys (Fn, Command, Option)
  // and "Ctrl + Alt + Space", all of which the backend refused. "Custom"
  // opens the dialog that records whatever keys you hold.
  const HOTKEY_PRESETS = {
    mac: [
      { keys: ['fn'], label: 'Fn', hint: 'Default · works on most MacBooks' },
      { keys: ['cmd', 'shift'], label: 'Command + Shift', hint: "If Fn doesn't work" },
      { keys: ['option', 'shift'], label: 'Option + Shift', hint: 'Another choice' },
    ],
    win: [
      { keys: ['win', 'ctrl'], label: 'Win + Ctrl', hint: 'Default · most reliable' },
      { keys: ['ctrl', 'shift'], label: 'Ctrl + Shift', hint: "If Win + Ctrl doesn't work" },
      { custom: true, label: 'Custom…', hint: 'Hold your own keys to choose them' },
    ],
  };

  function hotkeyPresets(isMac) {
    return (isMac ? HOTKEY_PRESETS.mac : HOTKEY_PRESETS.win)
      .map((p) => Object.assign({}, p, p.keys ? { keys: p.keys.slice() } : {}));
  }

  // ── Update messages ───────────────────────────────────────────────────
  // The backend (src/user_messages.py) answers with plain sentences; the
  // screen shows them as they are and never adds exception text. These
  // fallbacks are the same sentences, for when the call itself fails.
  const DOWNLOAD_PAGE = 'https://wafflerai.com/download/';
  // How long "Restart to update" waits before trying again while a
  // dictation finishes (app.py answers busy rather than lose it).
  const INSTALL_RETRY_MS = 1500;

  const UPDATE_TEXT = {
    checkFailed: "Couldn't check for updates. Try again later.",
    noInstaller: 'This update has no installer for this computer yet. Try again later, or see the release page.',
    downloadFailed: "The update didn't download. Try again, or get it from the download page.",
    installFailed: "The update couldn't be installed. Get it from the download page instead.",
  };

  // "Couldn't check for updates. Try again later." becomes a title
  // ("Couldn't check for updates") and a subtitle ("Try again later.").
  function splitMessage(msg) {
    const text = String(msg || '').trim();
    const m = text.match(/^(.+?[.!?])\s+(\S.*)$/s);
    if (!m) return { title: text.replace(/\.$/, ''), subtitle: '' };
    return { title: m[1].replace(/\.$/, ''), subtitle: m[2] };
  }

  // What the update dialog shows for a check_for_updates() answer.
  function updateCheckView(r) {
    r = r || {};
    if (r.update_available) {
      const title = `Waffler ${r.latest_version} is ready`;
      // No installer for this computer in the release: open its page rather
      // than trying to download (which used to fail as an "untrusted URL").
      if (r.no_installer || !r.download_url) {
        return {
          kind: 'no_installer', icon: 'circle-up', title,
          subtitle: r.no_installer_message || UPDATE_TEXT.noInstaller,
          primary: r.release_url
            ? { label: 'Open release page', url: r.release_url }
            : { label: 'Open download page', url: DOWNLOAD_PAGE },
          cancelLabel: 'Later',
        };
      }
      return {
        kind: 'available', icon: 'circle-up', title,
        subtitle: `You have ${r.current_version}. Download it and install it now?`,
        primary: { label: 'Download and install', download: r.download_url },
        browserUrl: DOWNLOAD_PAGE, cancelLabel: 'Later',
      };
    }
    if (r.error) {
      const m = splitMessage(r.error);
      const on = r.current_version ? ` You have ${r.current_version}.` : '';
      return { kind: 'error', icon: 'alert', title: m.title, subtitle: (m.subtitle + on).trim() };
    }
    const latest = r.latest_version && r.latest_version !== r.current_version ? ` The newest release is ${r.latest_version}.` : '';
    return { kind: 'up_to_date', icon: 'check-circle', title: "You're up to date",
             subtitle: (r.current_version ? `You have Waffler ${r.current_version}.${latest}` : latest.trim()) };
  }

  // A failed download or install: the backend's sentence, and a way to the
  // download page.
  function updateFailureView(r, fallback) {
    const m = splitMessage((r && r.error) || fallback || UPDATE_TEXT.downloadFailed);
    return { icon: 'alert', title: m.title, subtitle: m.subtitle,
             browserUrl: (r && r.download_page) || DOWNLOAD_PAGE };
  }

  // ── Settings: providers ───────────────────────────────────────────────
  // The engine's default order (src/style_openai.py _DEFAULT_PROVIDER_ORDER);
  // tests/test_ui_logic.py keeps the two equal. The UI used to start from
  // groq, cerebras, openai while the engine ran groq, openai, cerebras.
  const DEFAULT_PROVIDER_ORDER = ['groq', 'openai', 'cerebras'];
  const PROVIDER_NAMES = { groq: 'Groq', openai: 'OpenAI', cerebras: 'Cerebras' };
  const KEY_FLAGS = { groq: 'groq_key_set', openai: 'api_key_set', cerebras: 'cerebras_key_set' };

  function providerHasKey(provider, settings) {
    return !!(settings && settings[KEY_FLAGS[provider]]);
  }

  // Same rules as src/style_openai.py _normalize_provider_order: known
  // names only, no repeats, and any missing provider added in the default
  // order, so the list always has all three.
  function normalizeProviderOrder(order) {
    const out = [];
    (Array.isArray(order) ? order : []).forEach((p) => {
      const k = String(p == null ? '' : p).trim().toLowerCase();
      if (DEFAULT_PROVIDER_ORDER.includes(k) && !out.includes(k)) out.push(k);
    });
    DEFAULT_PROVIDER_ORDER.forEach((p) => { if (!out.includes(p)) out.push(p); });
    return out;
  }

  // One row per provider for Settings → Provider Order. A provider with no
  // key is shown greyed out: Waffler skips it.
  function providerOrderRows(order, settings) {
    return normalizeProviderOrder(order).map((id, i) => ({
      id, rank: i + 1, name: PROVIDER_NAMES[id], hasKey: providerHasKey(id, settings),
    }));
  }

  // What each stage uses first. get_settings() answers with the provider
  // name ("api" is OpenAI's speech to text, "mlx" and "faster" run on the
  // computer), "none" when no key can do that stage, or "unknown" while
  // Waffler is starting; then it's worked out from the keys and the order.
  const SPEECH_IDS = ['groq', 'api', 'mlx', 'faster'];
  const CLEANUP_IDS = ['groq', 'cerebras', 'openai'];

  function activeProviders(s) {
    s = s || {};
    const order = normalizeProviderOrder(s.provider_order);
    let speech = SPEECH_IDS.includes(s.transcription_backend) ? s.transcription_backend : null;
    if (!speech && s.transcription_backend !== 'none') {
      const p = order.find((id) => id !== 'cerebras' && providerHasKey(id, s));
      speech = p === 'openai' ? 'api' : (p || null);
    }
    let cleanup = CLEANUP_IDS.includes(s.styling_backend) ? s.styling_backend : null;
    if (!cleanup && s.styling_backend !== 'none') {
      cleanup = order.find((id) => providerHasKey(id, s)) || null;
    }
    return { speech, cleanup };
  }

  const SPEECH_BY = { groq: 'Groq', api: 'OpenAI', mlx: 'on this Mac', faster: 'on this computer' };
  const SPEECH_MODEL = { groq: 'Whisper large v3', api: 'gpt-4o-mini-transcribe', mlx: 'Whisper', faster: 'Whisper' };
  const CLEANUP_MODEL = { groq: 'gpt-oss-120b', cerebras: 'gpt-oss-120b', openai: 'gpt-4.1-mini' };

  // "Speech to text: Groq · Clean-up: Groq". It used to read
  // "STT: Groq Whisper · LLM: Groq gpt-oss-120b" under "Active Backends".
  function backendsLine(s) {
    const a = activeProviders(s);
    const speech = a.speech ? SPEECH_BY[a.speech] : 'no key yet';
    const cleanup = a.cleanup ? PROVIDER_NAMES[a.cleanup] : 'no key yet';
    return `Speech to text: ${speech} · Clean-up: ${cleanup}`;
  }

  // "v3.14.100 · Powered by Whisper large v3 and gpt-oss-120b": the models
  // in use. It said "Powered by Groq + Whisper + LLaMA" for everyone, and
  // clean-up hasn't used LLaMA since Groq retired it.
  function aboutLine(version, s) {
    const line = `v${version}`;
    const a = activeProviders(s);
    const speech = a.speech && SPEECH_MODEL[a.speech];
    const cleanup = a.cleanup && CLEANUP_MODEL[a.cleanup];
    return speech && cleanup ? `${line} · Powered by ${speech} and ${cleanup}` : line;
  }

  // ── Settings: Usage ───────────────────────────────────────────────────
  // The panel counts what you did, then shows what it would have cost at
  // each provider's published paid rates. It can't see anyone's bill or
  // plan, and a free Groq plan costs nothing, so the money is labelled an
  // estimate and never as spending.
  const USAGE_NOTE = "Estimated at each provider's published paid rates. Waffler can't see your bill.";

  function formatCount(n) {
    const v = Math.max(0, Math.round(Number(n) || 0));
    return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // Settings, Usage (3.15): counts first, from the Journal (get_stats), per
  // period; then the estimated money (get_usage_stats).
  const USAGE_PERIODS = [
    { id: 'today', label: 'Today', count: 'today_count', words: 'today_words' },
    { id: 'week', label: 'This week', count: 'week_count', words: 'week_words' },
    { id: 'month', label: 'This month', count: 'month_count', words: 'month_words' },
    { id: 'all', label: 'All time', count: 'total_count', words: 'total_words' },
  ];

  function usageView(usage, stats) {
    usage = usage || {};
    stats = stats || {};
    const money = (n, digits) => '$' + (Number(n) || 0).toFixed(digits);
    const plural = (n, one, many) => `${formatCount(n)} ${Math.round(Number(n) || 0) === 1 ? one : many}`;
    return {
      dictations: formatCount(usage.transcription_count),
      words: formatCount(stats.total_words),
      periods: USAGE_PERIODS.map((p) => ({
        id: p.id, label: p.label,
        count: formatCount(stats[p.count]),
        countLabel: Math.round(Number(stats[p.count]) || 0) === 1 ? 'dictation' : 'dictations',
        words: plural(stats[p.words], 'word', 'words'),
      })),
      costs: {
        today: money(usage.today_cost_usd, 2),
        week: money(usage.week_cost_usd, 2),
        month: money(usage.month_cost_usd, 2),
        total: money(usage.total_cost_usd, 2),
        perDictation: money(usage.avg_cost_per_transcription, 3),
      },
      note: USAGE_NOTE,
    };
  }

  // One bar per provider in Usage: the estimated cost of its calls, the
  // biggest in honey and the rest neutral. A provider whose rate isn't
  // published (Cerebras) is marked as an estimate.
  function usageProviderRows(byProvider) {
    const by = byProvider || {};
    const order = DEFAULT_PROVIDER_ORDER.filter((p) => by[p])
      .concat(Object.keys(by).filter((p) => !DEFAULT_PROVIDER_ORDER.includes(p)));
    const costs = order.map((p) => Number(by[p].cost_usd) || 0);
    const top = Math.max(0, ...costs);
    const sum = costs.reduce((a, b) => a + b, 0);
    return order.map((p, i) => {
      const b = by[p];
      const n = Math.round(Number(b.count) || 0);
      const estimate = (Number(b.estimated_count) || 0) > 0;
      return {
        id: p,
        name: PROVIDER_NAMES[p] || (p === 'local' ? 'On this computer' : p.charAt(0).toUpperCase() + p.slice(1)),
        calls: `${formatCount(n)} ${n === 1 ? 'call' : 'calls'}`,
        cost: `${estimate ? '~' : ''}$${costs[i].toFixed(costs[i] > 0 && costs[i] < 0.01 ? 4 : 2)}`,
        estimate,
        pct: sum > 0 ? Math.max(costs[i] > 0 ? 1 : 0, Math.round((costs[i] / sum) * 100)) : 0,
        top: top > 0 && costs[i] === top && costs.indexOf(top) === i,
      };
    });
  }

  // ── Settings, About: what Waffler uses right now ─────────────────────
  // "Whisper large v3 on Groq", "gpt-oss-120b on Groq". Nothing known yet
  // (no key, or still starting): "No key yet".
  function usesView(s) {
    const a = activeProviders(s);
    const speech = a.speech
      ? (a.speech === 'mlx' || a.speech === 'faster'
        ? `${SPEECH_MODEL[a.speech]}, ${SPEECH_BY[a.speech]}`
        : `${SPEECH_MODEL[a.speech]} on ${SPEECH_BY[a.speech]}`)
      : 'No key yet';
    const cleanup = a.cleanup ? `${CLEANUP_MODEL[a.cleanup]} on ${PROVIDER_NAMES[a.cleanup]}` : 'No key yet';
    return { speech, cleanup };
  }

  // ── Settings, Keys and providers ──────────────────────────────────────
  // One row per provider, in the order Waffler tries them. Groq is the
  // recommended one; OpenAI and Cerebras are optional. The billing words
  // are the ones checked for setup (Groq's free plan allows about 30
  // cleaned dictations a day; OpenAI's API is prepaid).
  const PROVIDER_INFO = {
    groq: { chip: 'Recommended', chipCls: 'chip-honey', desc: 'Speech to text and clean-up. Free for roughly 30 cleaned dictations a day.' },
    openai: { chip: 'Optional', chipCls: '', desc: 'Speech to text and clean-up. Prepaid: you buy credit first.' },
    cerebras: { chip: 'Optional', chipCls: '', desc: 'Clean-up only. No speech to text.' },
  };
  const MASK_FLAGS = { groq: 'groq_key_masked', openai: 'api_key_masked', cerebras: 'cerebras_key_masked' };

  function keyRows(order, settings) {
    const a = activeProviders(settings);
    const inUse = new Set([a.speech === 'api' ? 'openai' : a.speech, a.cleanup].filter(Boolean));
    return providerOrderRows(order, settings).map((r) => {
      const info = PROVIDER_INFO[r.id];
      const status = !r.hasKey ? 'Not set' : (inUse.has(r.id) ? 'In use' : 'Saved');
      return Object.assign({}, r, info, {
        masked: r.hasKey ? String((settings || {})[MASK_FLAGS[r.id]] || '') : '',
        status, statusCls: status === 'In use' ? 'dot-ok' : (status === 'Saved' ? 'dot-saved' : ''),
        button: r.hasKey ? 'Replace' : 'Add key',
      });
    });
  }

  // ── Journal cards ─────────────────────────────────────────────────────
  // The pipeline attaches item.quality only when a recording looks suspect,
  // so a clean dictation shows nothing. The chip says how sure, and the
  // reasons are plain sentences under the text (they used to hide in a
  // tooltip, in fragments like "cleanup did not run - this is the raw
  // transcript").
  const QUALITY_REASONS = {
    low_word_rate: "There are far fewer words than the recording's length suggests, so some may be missing.",
    styled_dropped_words: 'The clean-up took out more than usual. Show what you said to see every word.',
    styling_fallback: "The clean-up didn't run, so these are your words as you said them.",
    truncated_midsentence: 'Ends mid-sentence, so some of what you said may be missing.',
    unterminated_ending: 'Ends without a full stop, so the last words may be missing.',
    asr_filter_edited: "Waffler's transcript filter changed a few words.",
    retry_used: 'The first answer was too short, so Waffler asked again.',
    retry_rejected: 'This looks incomplete, and asking again gave a different answer, so check it.',
    styling_deadline: 'The clean-up ran out of time, so these are your words as you said them.',
  };

  function qualityView(item) {
    const q = item && item.quality;
    const limited = !!(item && item.as_said === 'limit');
    const out = { chip: '', level: '', reasons: [], asSaid: limited ? 'As said: limit reached' : '' };
    if (!q || !q.level || q.level === 'ok') return out;
    const flags = (q.flags || []).filter((f) => !(limited && (f === 'styling_fallback')));
    out.reasons = flags.map((f) => QUALITY_REASONS[f]).filter(Boolean);
    if (!flags.length) return out;
    out.level = q.level === 'low' ? 'low' : 'check';
    out.chip = q.level === 'low' ? 'Check this one' : 'Worth a look';
    return out;
  }

  // What the Vocabulary changed in a dictation: history.json's
  // "vocab_changes", [[heard, used], ...], saved since 3.15. Shown under the
  // Journal card, so a change Waffler made is never invisible.
  const VOCAB_MAX_ENTRY_LEN = 60;   // transcribe_whisper.VOCAB_MAX_ENTRY_LEN

  function vocabChanges(item) {
    const list = item && Array.isArray(item.vocab_changes) ? item.vocab_changes : [];
    return list
      .filter((p) => Array.isArray(p) && typeof p[0] === 'string' && typeof p[1] === 'string' && p[0] && p[1])
      .map(([heard, used]) => ({ heard, used }));
  }

  function vocabChangesLine(item) {
    const parts = vocabChanges(item).map((c) => `${c.heard} to ${c.used}`);
    if (!parts.length) return '';
    const joined = parts.length === 1 ? parts[0]
      : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
    return `Your Vocabulary changed ${joined}.`;
  }

  // The words to show behind "Show what you said" on a Journal card. Since
  // 3.14.97 an entry keeps Whisper's own words in asr_text and a filtered
  // copy in text; earlier entries have only text. Comparing the clean text
  // with the filtered copy hid the button on nearly half of recent entries
  // (they matched) although what was said differed, and the button showed
  // the filtered copy, not the speaker's words.
  function originalText(item) {
    if (!item) return '';
    const asr = (item.asr_text || '').trim();
    return asr || (item.text || '').trim();
  }
  function hasOriginal(item) {
    const shown = finalText(item).trim();
    const orig = originalText(item);
    return !!orig && orig !== shown;
  }

  // Typing a word into the Vocabulary box and pressing Add. Spaces are
  // trimmed and runs of them made one (as the app saves it). The same word
  // in other capitals is a respelling of the entry, not a second entry:
  // typing "COBie" with "Cobie" listed changes it to "COBie" (before 3.15
  // that was refused as a duplicate, and the only way to change an entry
  // was to remove it and add it again). Returns {action, word, existing,
  // next}: action is 'empty', 'too_long', 'same' (already listed exactly),
  // 'respell' or 'add'.
  function vocabAdd(words, input, maxLen) {
    const list = Array.isArray(words) ? words : [];
    const word = String(input == null ? '' : input).split(/\s+/).filter(Boolean).join(' ');
    if (!word) return { action: 'empty', word: '' };
    if (maxLen && word.length > maxLen) return { action: 'too_long', word };
    const i = list.findIndex((w) => w.toLowerCase() === word.toLowerCase());
    if (i < 0) return { action: 'add', word, next: list.concat([word]) };
    if (list[i] === word) return { action: 'same', word, existing: list[i] };
    const next = list.slice();
    next[i] = word;
    return { action: 'respell', word, existing: list[i], next };
  }

  // ── Snippets (the Vocabulary page, below the words) ─────────────────
  // A snippet is a phrase you say and the text Waffler pastes for it. The
  // limits are src/snippets.py's, and the page checks them before it saves
  // (the app checks again): a test compares the two.
  const SNIPPET_MAX_TRIGGER = 60;
  const SNIPPET_MAX_EXPANSION = 4000;
  const SNIPPET_MAX_COUNT = 200;

  // A trigger as it is kept: no outer space, any run of space one space.
  function snippetTrigger(text) {
    return String(text == null ? '' : text).split(/\s+/).filter(Boolean).join(' ');
  }

  // What to say about a snippet before it is saved. ``list`` is the saved
  // snippets; ``editing`` the index being changed, or -1 when adding. Returns
  // {ok: true, trigger, expansion} or {ok: false, field, error}: ``field`` is
  // the box to put the cursor in, and the same trigger in other capitals is a
  // second snippet for the same words, so it is refused.
  function snippetCheck(list, trigger, expansion, editing) {
    const have = Array.isArray(list) ? list : [];
    const t = snippetTrigger(trigger);
    const x = String(expansion == null ? '' : expansion).trim();
    if (!t) return { ok: false, field: 'trigger', error: "Type the phrase you'll say first." };
    if (t.length > SNIPPET_MAX_TRIGGER) {
      return { ok: false, field: 'trigger', error: `Keep the phrase to ${SNIPPET_MAX_TRIGGER} characters or fewer.` };
    }
    if (!x) return { ok: false, field: 'text', error: 'Type the text to paste.' };
    if (x.length > SNIPPET_MAX_EXPANSION) {
      return { ok: false, field: 'text', error: `Keep the text to ${formatCount(SNIPPET_MAX_EXPANSION)} characters or fewer.` };
    }
    const key = t.toLowerCase();
    const same = have.find((s, i) => i !== editing && snippetTrigger(s.trigger).toLowerCase() === key);
    if (same) return { ok: false, field: 'trigger', error: `"${snippetTrigger(same.trigger)}" is already a snippet.` };
    if (editing < 0 && have.length >= SNIPPET_MAX_COUNT) {
      return { ok: false, field: 'trigger', error: `You can keep up to ${SNIPPET_MAX_COUNT} snippets.` };
    }
    return { ok: true, trigger: t, expansion: x };
  }

  // "3 snippets" beside the heading.
  function snippetSummary(n) {
    const c = Math.max(0, Number(n) || 0);
    return c === 1 ? '1 snippet' : `${formatCount(c)} snippets`;
  }

  // A count under the text box, only once it is nearly full.
  function snippetCounter(length) {
    const n = Math.max(0, Number(length) || 0);
    return n >= SNIPPET_MAX_EXPANSION * 0.9
      ? `${formatCount(n)} of ${formatCount(SNIPPET_MAX_EXPANSION)} characters` : '';
  }

  // The undo line after a change: Removed "my address".
  function snippetDone(kind, trigger) {
    const verb = { remove: 'Removed', edit: 'Changed' }[kind] || 'Saved';
    return `${verb} "${snippetTrigger(trigger)}".`;
  }

  // The Journal's day rows: "Friday" on the left, "25 September" on the
  // right (with the year when it isn't this year). Keys are "YYYY-MM-DD".
  const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];

  function dayKey(ts) {
    const m = String(ts || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? m[0] : '';
  }

  function dayLabel(key, today) {
    const m = String(key || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return { day: 'Earlier', date: '' };
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    const now = today || new Date();
    const year = d.getFullYear() === now.getFullYear() ? '' : ` ${d.getFullYear()}`;
    return { day: WEEKDAYS[d.getDay()], date: `${d.getDate()} ${MONTHS[d.getMonth()]}${year}` };
  }

  // Numbers in the stat strip: "31,920", and "110k" past 100,000 so the
  // strip keeps its width.
  function statNumber(n) {
    const v = Math.max(0, Math.round(Number(n) || 0));
    return v >= 100000 ? `${Math.round(v / 1000)}k` : formatCount(v);
  }

  // ── Journal search ────────────────────────────────────────────────────
  // Search waits for a short pause in typing: every keystroke used to
  // rebuild every card, 340 to 713 ms each at 3,300 entries.
  const SEARCH_DEBOUNCE_MS = 150;

  function debounce(fn, ms, timers) {
    // Wrapped: a browser's setTimeout throws "Illegal invocation" when it is
    // called as a method of another object.
    const t = timers || { setTimeout: (f, d) => setTimeout(f, d), clearTimeout: (id) => clearTimeout(id) };
    let id = null;
    const run = function (...args) {
      if (id !== null) t.clearTimeout(id);
      id = t.setTimeout(() => { id = null; fn(...args); }, ms);
    };
    run.cancel = () => { if (id !== null) t.clearTimeout(id); id = null; };
    return run;
  }

  // What the Journal shows. A search with no matches used to show "Your
  // journal is empty.", as if the history had gone.
  function feedView(total, query, matches) {
    const q = String(query || '').trim();
    if (!total) return { kind: 'empty' };
    if (q && !matches) {
      const shown = q.length > 40 ? q.slice(0, 40) + '…' : q;
      return { kind: 'no_match', label: `No entries match "${shown}"` };
    }
    return { kind: 'list' };
  }

  // What a screen reader hears when a search's first page arrives. Only the
  // first page (up to pageSize) is loaded, so a full page says "at least".
  function searchAnnouncement(query, count, done) {
    const q = String(query || '').trim();
    if (!q) return '';
    const n = Number(count) || 0;
    if (!n) return 'No entries match.';
    if (!done) return `At least ${formatCount(n)} entries match.`;
    return n === 1 ? '1 entry matches.' : `${formatCount(n)} entries match.`;
  }

  // Arrow keys in a radio group (the theme picker): the index to move to,
  // wrapping at the ends, or -1 when the key isn't one of them.
  function radioMove(index, count, key) {
    if (!count || index < 0) return -1;
    if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % count;
    if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + count) % count;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    return -1;
  }

  // After removing the item at `index`, which of the `left` items keeps
  // focus: the next one (now at the same index), else the previous one, or
  // -1 when none are left.
  function focusAfterRemove(index, left) {
    if (!left || index < 0) return -1;
    return Math.min(index, left - 1);
  }

  // ── First-run setup (3.15) ────────────────────────────────────────────

  // What the key box says about what was typed or pasted, before asking
  // Groq. Setup only takes a Groq key; OpenAI and Cerebras are added later
  // in Settings, so their keys are named rather than called wrong.
  function keyInputView(value) {
    const v = String(value || '').trim();
    if (!v) return { kind: 'empty', message: '' };
    if (v.startsWith('gsk_')) {
      return v.length >= 20 ? { kind: 'ok', message: '' }
        : { kind: 'short', message: 'That key looks too short. Click Copy in Groq and try again.' };
    }
    if (v.startsWith('csk-')) return { kind: 'cerebras', message: "That's a Cerebras key. Setup needs a free Groq key. You can add Cerebras later in Settings." };
    if (v.startsWith('sk-')) return { kind: 'openai', message: "That's an OpenAI key. Setup needs a free Groq key. You can add OpenAI later in Settings." };
    return { kind: 'bad', message: "That doesn't look like a Groq key. Groq keys start with gsk_." };
  }

  // One row per job a new Groq key has to do (app.py validate_groq_key,
  // src/first_run.py groq_services).
  const SERVICE_CHIPS = {
    ok: { chip: 'Ready', cls: 'chip-ok', icon: 'check', tile: 'is-ok' },
    missing: { chip: 'Not listed', cls: 'chip-warn', icon: 'alert', tile: 'is-warn' },
    unchecked: { chip: 'Key saved', cls: '', icon: 'check', tile: 'is-ok' },
  };
  function serviceRows(services) {
    const list = Array.isArray(services) && services.length ? services
      : [{ name: 'Speech to text', provider: 'Groq', model: '', status: 'unchecked' },
         { name: 'Clean-up', provider: 'Groq', model: '', status: 'unchecked' }];
    return list.map((s) => {
      const v = SERVICE_CHIPS[s.status] || SERVICE_CHIPS.unchecked;
      const where = [s.provider || 'Groq', s.model].filter(Boolean).join(' · ');
      return {
        title: s.name, chip: v.chip, chipCls: v.cls, icon: v.icon, tile: v.tile,
        desc: s.status === 'missing' ? `${where}. Groq didn't list this model for your key.` : where,
      };
    });
  }

  // "You said", with the words the clean-up left out struck through, as on
  // the website: "Send it to [John, sorry,] James, by Wednesday at three."
  // A word-level longest common subsequence of the two texts, ignoring case
  // and punctuation. When the clean-up rewrote more than half the words,
  // striking most of the sentence would say nothing useful, so nothing is
  // struck. Returns [{text, cut}] pieces that join back to `said` exactly.
  // `maxCut` is that share (0.5 by default): a rewrite shown in the Journal
  // passes 1, because there the changes are the point.
  function saidDiff(said, wrote, maxCut) {
    const text = String(said || '');
    const tokens = text.match(/\S+\s*/g) || [];
    const lead = text.slice(0, text.length - tokens.join('').length);
    const norm = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');
    const a = tokens.map(norm);
    const b = (String(wrote || '').match(/\S+/g) || []).map(norm).filter(Boolean);
    const n = a.length, m = b.length;
    const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        L[i][j] = a[i] && a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
      }
    }
    const cut = new Array(n).fill(false);
    let i = 0, j = 0;
    while (i < n) {
      if (!a[i]) { i++; continue; }                       // punctuation on its own stays
      if (j < m && a[i] === b[j]) { i++; j++; continue; }
      if (j < m && L[i][j + 1] > L[i + 1][j]) { j++; continue; }
      cut[i] = true; i++;
    }
    const words = a.filter(Boolean).length;
    const cutWords = cut.filter(Boolean).length;
    const share = typeof maxCut === 'number' ? maxCut : 0.5;
    if (!cutWords || cutWords > words * share) return text ? [{ text, cut: false }] : [];
    const out = lead ? [{ text: lead, cut: false }] : [];
    tokens.forEach((t, k) => {
      if (cut[k]) {
        const body = t.replace(/\s+$/, ''), space = t.slice(body.length);
        const last = out[out.length - 1];
        // Join a run of cut words, keeping the spaces between them inside.
        if (last && last.cut && last.pendingSpace !== undefined) { last.text += last.pendingSpace + body; last.pendingSpace = space; }
        else out.push({ text: body, cut: true, pendingSpace: space });
      } else {
        const last = out[out.length - 1];
        const pre = last && last.cut ? last.pendingSpace : '';
        if (last && last.cut) delete last.pendingSpace;
        if (last && !last.cut) last.text += pre + t;
        else out.push({ text: pre + t, cut: false });
      }
    });
    const last = out[out.length - 1];
    if (last && last.cut) { const s = last.pendingSpace; delete last.pendingSpace; if (s) out.push({ text: s, cut: false }); }
    return out;
  }

  // The theme at start-up. settings.json (Python) is the record: the
  // window runs in private mode, so localStorage is empty after every
  // restart and its 'cream' default must never be written over the saved
  // choice. Returns what to show now ('apply') and what to save ('save').
  // A choice only this page holds (from an older build) is saved once.
  const THEMES = ['cream', 'dark', 'auto'];
  function startupTheme(saved, local) {
    const s = THEMES.includes(saved) ? saved : null;
    const l = THEMES.includes(local) ? local : null;
    if (s) return { apply: s, save: null };
    if (l && l !== 'cream') return { apply: l, save: l };
    return { apply: null, save: null };
  }

  // The steps of setup, in order. The Mac adds its permissions screen.
  function setupSteps(isMac) {
    return isMac ? ['connect', 'permissions', 'try', 'anywhere'] : ['connect', 'try', 'anywhere'];
  }

  // ══ Settings (3.15, the Atelier design) ════════════════════════════════

  // General: a few words in the chosen spelling, under "Spelling".
  const SPELLING_SPECIMEN = {
    'en-GB': { words: ['colour', 'organise', 'travelled'], not: 'not color' },
    'en-US': { words: ['color', 'organize', 'traveled'], not: 'not colour' },
  };
  function spellingSpecimen(dialect) {
    const s = SPELLING_SPECIMEN[dialect];
    return s ? { words: s.words.slice(), not: s.not } : null;
  }

  // General, Language: the options for the picker. get_languages() sends them
  // in src/languages.py order; Auto-detect and English stay first and the rest
  // go A to Z, which is easier to find a language in. Anything that is not a
  // {code, name} pair is dropped.
  function languageOptions(list) {
    const ok = (Array.isArray(list) ? list : []).filter((l) => l && typeof l.code === 'string'
      && l.code && typeof l.name === 'string' && l.name);
    const rank = (l) => (l.code === 'auto' ? 0 : (l.code === 'en' ? 1 : 2));
    return ok.map((l) => ({ code: l.code, name: l.name })).sort((a, b) => (rank(a) - rank(b))
      || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  }
  // The line under "Language": what Waffler does with the words.
  function languageDesc(code, name) {
    if (code === 'auto') return 'Waffler works out the language each time. It can be a little less sure on very short clips.';
    return `Waffler writes what you say in ${name || code || 'English'} and never translates it.`;
  }
  // The toast after saving it.
  function languageSaved(code, name) {
    if (code === 'auto') return 'Waffler will work out the language each time.';
    return `Language set to ${name || code}.`;
  }
  // Under "Spelling": British or American applies to English only (the
  // engine puts the language rule in its place), so say so in any other.
  function spellingNote(language) {
    return !language || language === 'en' ? '' : 'Used when your language is English.';
  }

  // General, under "Paste when I let go": what happens to the clipboard once
  // the text is pasted (settings.json clipboard_after_paste, Mac only: the
  // paste on Windows cannot put it back). "restore" gives you back what you
  // had copied; "keep" leaves the dictation there. With paste off the text is
  // only copied, so there is nothing to put back and the switch is fixed.
  function clipboardAfterPasteView(autoPaste, mode, isMac) {
    if (!isMac) return { visible: false, checked: true, disabled: true, desc: '' };
    if (autoPaste === false) {
      return { visible: true, checked: true, disabled: true, desc: 'Paste is off, so your text is only copied and stays on the clipboard.' };
    }
    return { visible: true, checked: mode === 'keep', disabled: false, desc: 'Off, Waffler puts back what you had copied once it has pasted.' };
  }
  // The toast after changing it.
  function clipboardAfterPasteSaved(mode) {
    return mode === 'keep' ? 'Waffler leaves your text on the clipboard.' : 'Waffler puts back what you had copied.';
  }

  // General, Feedback: the Sounds and Show tips switches (settings.json
  // "sounds" and "tips"). Sounds are off until asked for and tips are on
  // until turned off; anything that is not a real true or false in the
  // settings means that default, as the engine reads it.
  function feedbackView(settings) {
    const s = settings || {};
    return { sounds: s.sounds === true, tips: s.tips !== false };
  }
  // The toast after changing one of them.
  function feedbackSaved(key, on) {
    if (key === 'sounds') return on ? 'Waffler will play soft tones.' : 'Sounds are off.';
    if (key === 'tips') return on ? 'Waffler will show an occasional tip.' : 'Tips are off.';
    return '';
  }

  // ══ Transforms (beta): Settings, Transforms, and the Journal's rewrites ═══
  // The rules and limits are src/transforms.py's, and a test compares them.
  // The sentences are src/user_messages.py's, so the page and the app say the
  // same thing whichever of them notices a problem first.
  const TRANSFORM_RULES = [
    { key: 'grammar', label: 'Fix grammar, spelling and punctuation', on: true },
    { key: 'clarity', label: 'Make the wording clearer', on: true },
    { key: 'tone', label: 'Keep my tone', on: true },
    { key: 'sentences', label: 'Split long sentences', on: true },
    { key: 'paragraphs', label: 'Improve paragraph breaks', on: true },
    { key: 'structure', label: 'Add lists or headings where they help', on: false },
    { key: 'concise', label: 'Make it shorter', on: false },
  ];
  const TRANSFORM_LIMITS = { custom: 5, name: 40, prompt: 800, rule: 160, rules: 5, outline: 1200 };
  const TRANSFORM_BUILT_IN = [
    { value: 'polish', label: 'Polish' },
    { value: 'prompt_engineer', label: 'Prompt Engineer' },
  ];
  const TRANSFORM_TEXT = {
    nameEmpty: 'Give the transform a name.',
    nameLong: 'Keep the name to 40 characters or fewer.',
    nameTaken: 'You already have a transform with that name.',
    promptEmpty: 'Say what the transform should do.',
    promptLong: 'Keep the instruction to 800 characters or fewer.',
    tooMany: 'You can keep up to 5 transforms of your own.',
    ruleEmpty: 'Type the rule first.',
    ruleLong: 'Keep each rule to 160 characters or fewer.',
    ruleTwice: 'You already have that rule.',
    rulesTooMany: 'You can add up to 5 rules of your own.',
    outlineLong: 'Keep the outline to 1,200 characters or fewer.',
  };

  function _oneLine(text) {
    return String(text == null ? '' : text).split(/\s+/).filter(Boolean).join(' ');
  }

  // The Transforms settings (get_settings().transforms) as the section shows
  // them: anything missing or odd is the default (off, no shortcut, Polish).
  function transformView(settings, isMac) {
    const t = settings && typeof settings.transforms === 'object' && settings.transforms ? settings.transforms : {};
    const pol = t.polish && typeof t.polish === 'object' ? t.polish : {};
    const given = pol.rules && typeof pol.rules === 'object' ? pol.rules : {};
    const custom = (Array.isArray(t.custom) ? t.custom : [])
      .filter((c) => c && typeof c.id === 'string' && typeof c.name === 'string')
      .map((c) => ({ id: c.id, name: c.name, prompt: String(c.prompt || '') }));
    const own = (Array.isArray(pol.custom_rules) ? pol.custom_rules : []).filter((r) => typeof r === 'string');
    const choices = TRANSFORM_BUILT_IN.concat(custom.map((c) => ({ value: c.id, label: c.name })));
    const wanted = typeof t.default === 'string' ? t.default : 'polish';
    const keys = Array.isArray(t.hotkey) ? t.hotkey.filter((k) => typeof k === 'string') : [];
    const skeleton = typeof (t.prompt_engineer || {}).skeleton === 'string' ? t.prompt_engineer.skeleton : '';
    const outlineDefault = typeof t.skeleton_default === 'string' ? t.skeleton_default : '';
    return {
      enabled: t.enabled === true,
      hotkey: keys,
      hotkeyShown: keys.length ? hotkeyName(keys, !!isMac) : '',
      defaultId: choices.some((c) => c.value === wanted) ? wanted : 'polish',
      choices,
      rules: TRANSFORM_RULES.map((r) => ({ key: r.key, label: r.label, on: typeof given[r.key] === 'boolean' ? given[r.key] : r.on })),
      ownRules: own,
      canAddRule: own.length < TRANSFORM_LIMITS.rules,
      outline: skeleton || outlineDefault,
      outlineDefault,
      outlineIsDefault: !skeleton || skeleton === outlineDefault,
      custom,
      canAddCustom: custom.length < TRANSFORM_LIMITS.custom,
    };
  }

  // What to say about a transform of the person's own before it is saved.
  // `list` is the saved ones, `editing` the index being changed (-1 to add).
  function transformCheck(list, name, prompt, editing) {
    const have = Array.isArray(list) ? list : [];
    const n = _oneLine(name);
    const p = String(prompt == null ? '' : prompt).replace(/\r\n?/g, '\n').trim();
    if (!n) return { ok: false, field: 'name', error: TRANSFORM_TEXT.nameEmpty };
    if (n.length > TRANSFORM_LIMITS.name) return { ok: false, field: 'name', error: TRANSFORM_TEXT.nameLong };
    const key = n.toLowerCase();
    const builtIn = TRANSFORM_BUILT_IN.some((b) => b.label.toLowerCase() === key);
    if (builtIn || have.some((c, i) => i !== editing && _oneLine(c.name).toLowerCase() === key)) {
      return { ok: false, field: 'name', error: TRANSFORM_TEXT.nameTaken };
    }
    if (!p) return { ok: false, field: 'prompt', error: TRANSFORM_TEXT.promptEmpty };
    if (p.length > TRANSFORM_LIMITS.prompt) return { ok: false, field: 'prompt', error: TRANSFORM_TEXT.promptLong };
    if (editing < 0 && have.length >= TRANSFORM_LIMITS.custom) return { ok: false, field: 'name', error: TRANSFORM_TEXT.tooMany };
    return { ok: true, name: n, prompt: p };
  }

  // The same for one of the person's own Polish rules.
  function transformRuleCheck(rules, text) {
    const have = Array.isArray(rules) ? rules : [];
    const r = _oneLine(text);
    if (!r) return { ok: false, error: TRANSFORM_TEXT.ruleEmpty };
    if (r.length > TRANSFORM_LIMITS.rule) return { ok: false, error: TRANSFORM_TEXT.ruleLong };
    if (have.some((x) => _oneLine(x).toLowerCase() === r.toLowerCase())) return { ok: false, error: TRANSFORM_TEXT.ruleTwice };
    if (have.length >= TRANSFORM_LIMITS.rules) return { ok: false, error: TRANSFORM_TEXT.rulesTooMany };
    return { ok: true, rule: r };
  }

  // The count under the outline box, and whether it can be saved.
  function transformOutlineView(typed) {
    const text = String(typed == null ? '' : typed).replace(/\r\n?/g, '\n').trim();
    const n = Array.from(text).length;
    const over = n > TRANSFORM_LIMITS.outline;
    return {
      text, chars: n, over,
      counter: over ? `${formatCount(n - TRANSFORM_LIMITS.outline)} over the limit of ${formatCount(TRANSFORM_LIMITS.outline)}`
        : `${formatCount(n)} of ${formatCount(TRANSFORM_LIMITS.outline)}`,
    };
  }

  // The line under the shortcut.
  function transformShortcutLine(view) {
    if (view && view.hotkey && view.hotkey.length) {
      return 'Select text in any app and press it. The keys also reach the app, so pick a combination it does not use.';
    }
    return 'Not set. Choose keys, or use "Transform selected text" in the menu bar.';
  }

  // What the page says after a save.
  function transformSaved(what, value) {
    if (what === 'enabled') return value ? 'Transforms are on.' : 'Transforms are off.';
    if (what === 'hotkey') return value ? `Transform shortcut is now ${value}.` : 'Transform shortcut removed.';
    if (what === 'default') return `The shortcut now runs ${value}.`;
    if (what === 'added') return `Added "${value}".`;
    if (what === 'removed') return `Removed "${value}".`;
    if (what === 'outline') return 'Outline saved.';
    return 'Saved.';
  }

  // The hotkey dialog, for the dictation hotkey, the Transform shortcut or the
  // Command shortcut.
  function hotkeyDialogText(target) {
    if (target === 'command') {
      return { title: 'Hold your Command shortcut', sub: 'Hold the keys you want, then let go. Use a letter, a number or an F key with two modifier keys. You will hold these keys while you speak. Esc cancels.' };
    }
    return target === 'transform'
      ? { title: 'Hold your Transform shortcut', sub: 'Hold the keys you want, then let go. Use a letter, a number or an F key with two modifier keys. Esc cancels.' }
      : { title: 'Hold your new keys', sub: 'Hold the combination you want, then let go. Esc cancels.' };
  }

  // What became of the words, for a rewrite's entry in the Journal. `where`
  // is " in Notes" or nothing.
  const TRANSFORM_PASTE = {
    pasted: (where) => `Replaced your selection${where}.`,
    unverified: (where) => `Replaced your selection${where}.`,
    failed: (where) => `Couldn't paste${where}. The rewrite is on your clipboard.`,
    copied: () => 'Copied to the clipboard, not pasted.',
    not_pasted: () => 'Not pasted.',
  };

  // A rewrite's Journal entry (kind "transform"): the two texts with what
  // changed marked (words taken out struck through in the first, words put in
  // marked in the second), how many changes, and where it happened.
  function transformEntryView(item) {
    const e = item && typeof item === 'object' ? item : {};
    const before = String(e.before || ''), after = String(e.after || '');
    const left = saidDiff(before, after, 1);
    const right = saidDiff(after, before, 1);
    const changes = Math.max(left.filter((p) => p.cut).length, right.filter((p) => p.cut).length);
    const words = after.split(/\s+/).filter(Boolean).length;
    const where = typeof e.app === 'string' && e.app ? ` in ${e.app}` : '';
    const say = Object.prototype.hasOwnProperty.call(TRANSFORM_PASTE, e.paste) ? TRANSFORM_PASTE[e.paste] : null;
    return {
      id: Number.isInteger(e.id) && e.id > 0 ? e.id : null,
      name: String(e.transform_name || 'Transform'),
      before, after,
      beforePieces: left, afterPieces: right,
      changes,
      changesLabel: changes ? `${formatCount(changes)} ${changes === 1 ? 'change' : 'changes'}` : 'Punctuation and layout only',
      words: `${formatCount(words)} ${words === 1 ? 'word' : 'words'}`,
      status: say ? say(where) : '',
    };
  }

  // ══ Command mode (beta): Settings, Command mode, and the Journal's commands ═
  // The engines are src/command_mode.py's, and a test compares them. The
  // sentences are src/user_messages.py's where the app says them too.
  const COMMAND_ENGINES = [
    { value: 'perplexity', label: 'Perplexity' },
    { value: 'google', label: 'Google' },
    { value: 'duckduckgo', label: 'DuckDuckGo' },
    { value: 'chatgpt', label: 'ChatGPT' },
    { value: 'claude', label: 'Claude' },
  ];
  const COMMAND_EXAMPLES = [
    { say: 'Make this shorter', does: 'Edits the text you selected.' },
    { say: 'Translate to French', does: 'Edits the text you selected, into another language.' },
    { say: "Reply saying I'll be 10 minutes late", does: 'Writes new text where your cursor is.' },
  ];

  function commandEngineLabel(id) {
    const e = COMMAND_ENGINES.find((x) => x.value === id);
    return e ? e.label : COMMAND_ENGINES[0].label;
  }

  // The Command mode settings (get_settings().command_mode) as the section
  // shows them: anything missing or odd is the default (off, no shortcut,
  // Perplexity).
  function commandView(settings, isMac) {
    const c = settings && typeof settings.command_mode === 'object' && settings.command_mode ? settings.command_mode : {};
    const keys = Array.isArray(c.hotkey) ? c.hotkey.filter((k) => typeof k === 'string') : [];
    const wanted = typeof c.web_engine === 'string' ? c.web_engine : COMMAND_ENGINES[0].value;
    const engine = COMMAND_ENGINES.some((e) => e.value === wanted) ? wanted : COMMAND_ENGINES[0].value;
    return {
      enabled: c.enabled === true,
      hotkey: keys,
      hotkeyShown: keys.length ? hotkeyName(keys, !!isMac) : '',
      engine,
      engineLabel: commandEngineLabel(engine),
      engines: COMMAND_ENGINES,
      examples: COMMAND_EXAMPLES,
    };
  }

  // The line under the shortcut.
  function commandShortcutLine(view) {
    if (view && view.hotkey && view.hotkey.length) {
      return 'Hold these keys, say what you want, then let go. Esc stops it. The keys also reach the app, so pick a combination it does not use.';
    }
    return 'Not set. Choose keys to hold while you speak.';
  }

  // What the page says after a save.
  function commandSaved(what, value) {
    if (what === 'enabled') return value ? 'Command mode is on.' : 'Command mode is off.';
    if (what === 'hotkey') return value ? `Command shortcut is now ${value}.` : 'Command shortcut removed.';
    if (what === 'engine') return `Questions now open in ${value}.`;
    return 'Saved.';
  }

  // What became of the words, for a command's entry in the Journal.
  const COMMAND_PASTE = {
    edit: {
      pasted: (where) => `Replaced your selection${where}.`,
      unverified: (where) => `Replaced your selection${where}.`,
      failed: (where) => `Couldn't paste${where}. The result is on your clipboard.`,
      copied: () => 'Copied to the clipboard, not pasted.',
      not_pasted: () => 'Not pasted.',
    },
    draft: {
      pasted: (where) => `Added at your cursor${where}.`,
      unverified: (where) => `Added at your cursor${where}.`,
      failed: (where) => `Couldn't paste${where}. The draft is on your clipboard.`,
      copied: () => 'Copied to the clipboard, not pasted.',
      not_pasted: () => 'Not pasted.',
    },
  };

  // A command's Journal entry (kind "command"): what was said, what it did,
  // and for an edit the two texts with what changed marked.
  function commandEntryView(item) {
    const e = item && typeof item === 'object' ? item : {};
    const action = e.action === 'edit' || e.action === 'ask' ? e.action : 'draft';
    const before = String(e.before || ''), after = String(e.after || '');
    const query = String(e.query || '');
    const engine = commandEngineLabel(e.engine);
    const where = typeof e.app === 'string' && e.app ? ` in ${e.app}` : '';
    const table = COMMAND_PASTE[action];
    const say = table && Object.prototype.hasOwnProperty.call(table, e.paste) ? table[e.paste] : null;
    let beforePieces = [], afterPieces = [], changes = 0;
    if (action === 'edit') {
      beforePieces = saidDiff(before, after, 1);
      afterPieces = saidDiff(after, before, 1);
      changes = Math.max(beforePieces.filter((p) => p.cut).length, afterPieces.filter((p) => p.cut).length);
    }
    const words = after.split(/\s+/).filter(Boolean).length;
    return {
      id: Number.isInteger(e.id) && e.id > 0 ? e.id : null,
      action,
      chip: action === 'ask' ? `Searched ${engine}` : (action === 'edit' ? 'Edited' : 'Drafted'),
      icon: action === 'ask' ? 'search' : (action === 'edit' ? 'pencil' : 'text-cursor'),
      instruction: String(e.instruction || ''),
      before, after, query, engine,
      beforePieces, afterPieces, changes,
      changesLabel: action === 'edit'
        ? (changes ? `${formatCount(changes)} ${changes === 1 ? 'change' : 'changes'}` : 'Punctuation and layout only') : '',
      words: action === 'ask' ? '' : `${formatCount(words)} ${words === 1 ? 'word' : 'words'}`,
      status: action === 'ask' ? 'Opened in your browser.' : (say ? say(where) : ''),
    };
  }

  // ── Style (Settings, Style) ──────────────────────────────────────────
  // Clean-up On or Off (settings.json "cleanup"), a style for each kind of
  // app ("style_by_category") and the email signature ("email_signature").
  // The choices and limits are the engine's (src/style_layer.py); a test
  // compares them. The examples under each choice are not made here: the app
  // runs the real rules on a sample sentence (preview_style), so the page
  // never holds a second copy of them.
  const STYLE_KINDS = ['personal', 'work', 'email', 'other'];
  const STYLE_CHOICES = {
    personal: ['auto', 'formal', 'casual', 'very_casual'],
    work: ['auto', 'formal', 'casual', 'excited'],
    email: ['auto', 'formal', 'casual', 'excited'],
    other: ['auto', 'formal', 'casual', 'excited'],
  };
  const STYLE_KIND_TEXT = {
    personal: { title: 'Personal messages', apps: 'Messages, WhatsApp, Telegram, Signal, Discord' },
    work: { title: 'Work chat', apps: 'Slack, Teams, Zoom, Notion, Linear' },
    email: { title: 'Email', apps: 'Mail, Outlook, Spark, Thunderbird, and Gmail in a browser' },
    other: { title: 'Everything else', apps: 'Notes, documents, your browser, any other app' },
  };
  const STYLE_LABELS = {
    auto: 'Automatic', formal: 'Formal', casual: 'Casual', very_casual: 'Very casual', excited: 'Excited',
  };
  const STYLE_ABOUT = {
    auto: 'Waffler writes as it does today. Nothing is changed.',
    formal: 'Capital letters and full stops, as Waffler writes them.',
    casual: 'Takes the full stop off a one-sentence message.',
    very_casual: 'Lower case, and no last full stop. Names in your Vocabulary and acronyms keep their capitals.',
    excited: 'Ends short statements with an exclamation mark.',
  };
  const STYLE_NOTE = 'Applies to new dictations. Works in English and other Latin-script languages.';
  const STYLE_OFF_NOTE = "Clean-up is off, so styles aren't used until you turn it back on.";
  const SIGNATURE_MAX = 500;      // style_layer.SIGNATURE_MAX_CHARS

  function _styleOf(settings, kind) {
    const saved = settings && settings.style_by_category;
    const v = saved && typeof saved === 'object' ? saved[kind] : '';
    return STYLE_CHOICES[kind].includes(v) ? v : 'auto';
  }

  // The four rows: the kind, the apps it covers, the choices it offers and
  // the one saved.
  function styleRows(settings) {
    return STYLE_KINDS.map((kind) => ({
      kind,
      title: STYLE_KIND_TEXT[kind].title,
      apps: STYLE_KIND_TEXT[kind].apps,
      value: _styleOf(settings, kind),
      about: STYLE_ABOUT[_styleOf(settings, kind)],
      choices: STYLE_CHOICES[kind].map((v) => ({ value: v, label: STYLE_LABELS[v] })),
    }));
  }

  function styleAbout(style) {
    return STYLE_ABOUT[style] || STYLE_ABOUT.auto;
  }

  // Anything but a saved "off" is on, as the engine reads it.
  function cleanupView(settings) {
    const off = !!settings && settings.cleanup === 'off';
    return {
      on: !off,
      desc: off
        ? "Off, Waffler pastes exactly what it heard, with your Vocabulary and Snippets. Nothing is sent for clean-up. Styles aren't used, but an email signature still is."
        : 'Takes out ums and false starts, adds punctuation and tidies the sentence.',
      note: off ? STYLE_OFF_NOTE : '',
    };
  }

  // The toasts after saving.
  function cleanupSaved(on) {
    return on ? 'Clean-up is on.' : "Clean-up is off. Waffler pastes exactly what it hears.";
  }
  function styleSaved(kind, style) {
    const t = STYLE_KIND_TEXT[kind];
    return t ? `${t.title}: ${STYLE_LABELS[style] || 'Automatic'}.` : 'Style saved.';
  }
  function signatureSaved(enabled) {
    return enabled ? 'Waffler will add your signature to new emails.' : "Waffler won't add a signature.";
  }

  // The signature box: the text as the engine will keep it (outer space off),
  // the count shown under it, and whether it can be saved.
  function signatureView(sig, typed) {
    const saved = sig && typeof sig === 'object' ? sig : {};
    const text = String(typed == null ? (saved.text || '') : typed).replace(/\r\n?/g, '\n').trim();
    const n = Array.from(text).length;
    const over = n > SIGNATURE_MAX;
    return {
      enabled: saved.enabled === true,
      text,
      chars: n,
      over,
      counter: over ? `${formatCount(n - SIGNATURE_MAX)} over the limit of ${formatCount(SIGNATURE_MAX)}`
        : `${formatCount(n)} of ${formatCount(SIGNATURE_MAX)}`,
      hint: saved.enabled === true && !text ? 'Type your signature, or no signature will be added.' : '',
    };
  }

  // General: the microphone meter, a strip of cells like the recording
  // overlay's waffle. The level (0 to 1, app.py get_mic_level) goes through
  // the overlay's own curve (level ** 0.4) so quiet speech still moves it.
  // Returns one entry per cell: 'f' lit, 'p' part lit, '' dark.
  const METER_CELLS = 16;
  function micMeter(level, cells) {
    const n = cells || METER_CELLS;
    const v = Math.pow(Math.min(1, Math.max(0, Number(level) || 0)), 0.4) * n;
    const out = [];
    for (let i = 0; i < n; i++) {
      const fill = v - i;
      out.push(fill >= 0.75 ? 'f' : (fill >= 0.25 ? 'p' : ''));
    }
    return out;
  }

  // Usage: the last 30 days' words (journal_data.py daily_words, oldest
  // first, ending today) as bars. Guide lines at a round step (1, 2 or 5
  // times a power of ten) about half the busiest day, and the top a little
  // above the busiest bar. The last 7 days are stronger; today is ink.
  function _niceFloor(x) {
    if (!(x > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(x)));
    const m = x / p;
    return (m >= 5 ? 5 : (m >= 2 ? 2 : 1)) * p;
  }
  function _isoDay(iso, plus) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + (plus || 0)));
  }
  function _dayMonth(d) {
    return d ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` : '';
  }
  function usageChart(daily, startIso) {
    const days = Array.isArray(daily) ? daily.map((n) => Math.max(0, Math.round(Number(n) || 0))) : [];
    const max = Math.max(0, ...days);
    const step = Math.max(1, _niceFloor(max / 2));
    const grid = [];
    for (let g = step; g <= max; g += step) grid.push({ value: g, label: formatCount(g) });
    const top = max ? max * 1.08 : 1;
    const bars = days.map((n, i) => {
      const left = days.length - 1 - i;
      const d = _isoDay(startIso, i);
      return {
        words: n,
        h: n ? Math.max(2, Math.round((n / top) * 1000) / 10) : 0,
        cls: left === 0 ? 'today' : (left < 7 ? 'wk' : 'old'),
        label: `${left === 0 ? 'Today' : _dayMonth(d)}: ${formatCount(n)} ${n === 1 ? 'word' : 'words'}`,
      };
    });
    return {
      empty: max === 0,
      bars,
      grid: grid.map((g) => Object.assign(g, { pos: Math.round((g.value / top) * 1000) / 10 })),
      start: _dayMonth(_isoDay(startIso)),
    };
  }

  // Usage: the figures beside the big total.
  function usageFacts(stats) {
    stats = stats || {};
    const count = Math.round(Number(stats.total_count) || 0);
    const words = Math.round(Number(stats.total_words) || 0);
    return {
      dictations: formatCount(count),
      dictationsLabel: count === 1 ? 'dictation' : 'dictations',
      perDictation: count ? formatCount(words / count) : '0',
    };
  }

  // Keys and providers: what a dictation goes through, left to right.
  const SPEECH_NODE = { groq: 'Groq', api: 'OpenAI', mlx: 'This Mac', faster: 'This computer' };
  function flowView(s) {
    const a = activeProviders(s);
    return {
      speech: a.speech ? { by: SPEECH_NODE[a.speech], model: SPEECH_MODEL[a.speech] } : null,
      cleanup: a.cleanup ? { by: PROVIDER_NAMES[a.cleanup], model: CLEANUP_MODEL[a.cleanup] } : null,
      out: s && s.auto_paste === false ? 'Copied' : 'Pasted',
    };
  }

  // Keys and providers: each row's state. "Standby" is a saved key that
  // Waffler tries when the one before it doesn't answer.
  const PROVIDER_MARKS = { groq: 'g', openai: 'O', cerebras: 'C' };
  function providerPill(row) {
    if (!row || !row.hasKey) return { pill: 'No key', pillCls: '' };
    return row.status === 'In use' ? { pill: 'In use', pillCls: 'ok' } : { pill: 'Standby', pillCls: '' };
  }

  // Privacy and data: where your voice goes. Speech to text on a provider
  // gets the audio; speech to text on this computer sends only the words,
  // for the clean-up. No key yet: nothing goes anywhere.
  function privacyFlow(s) {
    const a = activeProviders(s);
    if (a.speech === 'groq' || a.speech === 'api') {
      const id = a.speech === 'api' ? 'openai' : 'groq';
      return { id, name: PROVIDER_NAMES[id], mark: PROVIDER_MARKS[id], what: 'your voice, while you hold' };
    }
    if (a.cleanup) {
      return { id: a.cleanup, name: PROVIDER_NAMES[a.cleanup], mark: PROVIDER_MARKS[a.cleanup], what: 'your words, for the clean-up' };
    }
    return null;
  }

  // Hotkey: holding the real hotkey on this page is heard by the app's own
  // listener (the status turns to Recording), which is the test.
  function hotkeyTestLine(heard, keys, isMac) {
    return heard ? `Heard ${hotkeyName(keys, isMac)}. That's working.` : 'Hold it now to test it.';
  }

  // About: what is new in this version (each line is in CHANGELOG.md).
  const WHATS_NEW = {
    '3.15': [
      'A new look, matching wafflerai.com, in light and dark.',
      'Setup in three steps, four on a Mac, with a real try-it.',
      'Choose how long your history is kept, and delete it any time.',
    ],
  };
  function whatsNew(version) {
    const m = String(version || '').match(/^(\d+)\.(\d+)/);
    const key = m ? `${m[1]}.${m[2]}` : '';
    return WHATS_NEW[key] ? { title: `New in ${key}`, items: WHATS_NEW[key].slice() } : null;
  }

  // ── Vocabulary (3.15, Atelier) ────────────────────────────────────────
  // The page lists each word with what it sounds like, how many dictations
  // it corrected and when it last did (get_vocab_book, from the corrections
  // recorded in the Journal), busiest first.
  const VOCAB_MAX_SOUNDS = 8;      // transcribe_whisper.VOCAB_MAX_SOUNDS
  const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function _localDate(ts) {
    const m = String(ts || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
  }
  function _daysBetween(a, b) {
    const da = new Date(a.getFullYear(), a.getMonth(), a.getDate());
    const db = new Date(b.getFullYear(), b.getMonth(), b.getDate());
    return Math.round((db - da) / 86400000);
  }

  // "Last used": today, yesterday, 3 days ago, last week, 2 weeks ago, in
  // September (with the year when it isn't this year), or not yet.
  function vocabLastUsed(ts, now) {
    const d = _localDate(ts);
    if (!d) return 'not yet';
    const today = now || new Date();
    const n = _daysBetween(d, today);
    if (n <= 0) return 'today';
    if (n === 1) return 'yesterday';
    if (n < 7) return `${n} days ago`;
    if (n < 14) return 'last week';
    if (n < 28) return `${Math.floor(n / 7)} weeks ago`;
    const year = d.getFullYear() === today.getFullYear() ? '' : ` ${d.getFullYear()}`;
    return `in ${MONTHS[d.getMonth()]}${year}`;
  }

  // Recently corrected: 21:13 today, Sun this week, 28 Sep before that.
  function vocabRecentWhen(ts, now) {
    const d = _localDate(ts);
    if (!d) return '';
    const n = _daysBetween(d, now || new Date());
    const pad = (x) => String(x).padStart(2, '0');
    if (n <= 0) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (n < 7) return DAYS_SHORT[d.getDay()];
    return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`;
  }

  // "grok, grock" typed in the "sounds like" box: one spelling between
  // commas, spaces tidied as the app saves them, repeats dropped.
  function vocabSoundsParse(input) {
    const out = [];
    String(input == null ? '' : input).split(/[,;\n]/).forEach((s) => {
      const v = s.split(/\s+/).filter(Boolean).join(' ');
      if (v && !out.some((o) => o.toLowerCase() === v.toLowerCase())) out.push(v);
    });
    return out;
  }

  // Can ``word`` have these spellings? ``entries`` is the page's list
  // ({word, sounds_like}). The rules are the app's (clean_sounds_like), said
  // in words: the app would quietly drop these.
  function vocabSoundsCheck(entries, word, spellings, maxLen) {
    const list = Array.isArray(entries) ? entries : [];
    const w = String(word || '').toLowerCase();
    const limit = maxLen || VOCAB_MAX_ENTRY_LEN;
    if (spellings.length > VOCAB_MAX_SOUNDS) return { ok: false, error: `Up to ${VOCAB_MAX_SOUNDS} spellings for each word.` };
    for (const s of spellings) {
      const f = s.toLowerCase();
      if (s.length > limit) return { ok: false, error: `Keep each spelling to ${limit} characters or fewer.` };
      if (!/[\p{L}\p{N}]/u.test(s)) return { ok: false, error: `"${s}" has no letters in it.` };
      if (f === w) return { ok: false, error: `"${s}" is how ${word} is written already. Type what speech to text writes instead.` };
      const other = list.find((e) => e.word.toLowerCase() === f);
      if (other) return { ok: false, error: `${other.word} is in your list as a word of its own, so it can't also be how ${word} sounds.` };
      const owner = list.find((e) => e.word.toLowerCase() !== w && (e.sounds_like || []).some((x) => x.toLowerCase() === f));
      if (owner) return { ok: false, error: `"${s}" already sounds like ${owner.word}.` };
    }
    return { ok: true, error: '' };
  }

  // The table: busiest first, then A to Z; each bar against the busiest.
  // ``opts`` (S10): { sort, query } as vocabSortRows and vocabFilter take them.
  // The bars stay against the busiest word of the whole list, so searching
  // does not make a rarely used word look busy.
  function vocabRows(entries, now, opts) {
    const o = opts || {};
    const list = (Array.isArray(entries) ? entries : []).filter((e) => e && typeof e.word === 'string');
    const max = list.reduce((m, e) => Math.max(m, Number(e.count) || 0), 0);
    return vocabSortRows(vocabFilter(list, o.query), o.sort)
      .map((e) => {
        const count = Math.max(0, Number(e.count) || 0);
        return {
          word: e.word,
          sounds: Array.isArray(e.sounds_like) ? e.sounds_like.slice() : [],
          count,
          bar: max ? Math.round((count / max) * 100) : 0,
          last: vocabLastUsed(e.last, now),
          starred: e.starred === true,
        };
      });
  }

  // ── Vocabulary: search, sort, select, import (S10) ─────────────────────
  // How the list can be ordered. "Most used" is the order the page always
  // had; the others are the person's choice (remembered by the page).
  const VOCAB_SORTS = [
    { id: 'used', label: 'Most used' },
    { id: 'recent', label: 'Recently used' },
    { id: 'starred', label: 'Starred first' },
    { id: 'az', label: 'A to Z' },
  ];
  const VOCAB_SORT_DEFAULT = 'used';

  function vocabSortId(id) {
    return VOCAB_SORTS.some((s) => s.id === id) ? id : VOCAB_SORT_DEFAULT;
  }

  // Lower case with the accents off, so "sinead" finds Sinéad.
  function foldText(text) {
    return String(text == null ? '' : text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  // Does the entry match what was typed in the search box? Every word typed
  // must be found in the word or in one of its "sounds like" spellings, in any
  // case and with or without accents.
  function vocabMatches(entry, query) {
    const terms = foldText(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    if (!entry || typeof entry.word !== 'string') return false;
    const hay = [entry.word].concat(Array.isArray(entry.sounds_like) ? entry.sounds_like : []).map(foldText).join('\n');
    return terms.every((t) => hay.indexOf(t) >= 0);
  }

  function vocabFilter(entries, query) {
    const list = Array.isArray(entries) ? entries : [];
    return foldText(query).trim() ? list.filter((e) => vocabMatches(e, query)) : list.slice();
  }

  function _byWord(a, b) {
    return a.word.localeCompare(b.word, 'en', { sensitivity: 'base' });
  }

  function _byCount(a, b) {
    return (Number(b.count) || 0) - (Number(a.count) || 0);
  }

  // The list in the chosen order. Every order ends with A to Z, so it is the
  // same each time.
  function vocabSortRows(entries, sort) {
    const by = vocabSortId(sort);
    const list = (Array.isArray(entries) ? entries : []).slice();
    const star = (e) => (e.starred === true ? 0 : 1);
    const last = (e) => (typeof e.last === 'string' ? e.last : '');
    const cmp = {
      used: (a, b) => _byCount(a, b) || _byWord(a, b),
      az: (a, b) => _byWord(a, b),
      starred: (a, b) => star(a) - star(b) || _byCount(a, b) || _byWord(a, b),
      recent: (a, b) => (last(a) === last(b) ? 0 : (!last(a) ? 1 : (!last(b) ? -1 : (last(a) < last(b) ? 1 : -1))))
        || _byCount(a, b) || _byWord(a, b),
    }[by];
    return list.sort(cmp);
  }

  // What a screen reader hears after a search: nothing without one.
  function vocabSearchAnnouncement(query, shown, total) {
    if (!foldText(query).trim()) return '';
    const n = Number(shown) || 0;
    if (!n) return 'No words match.';
    if (n === 1) return '1 word matches.';
    return Number(total) > n ? `${formatCount(n)} of ${formatCount(total)} words match.` : `${formatCount(n)} words match.`;
  }

  // The words kept selected when the list on screen changes: only those still
  // shown, so a bulk remove never takes a word that is out of sight.
  function vocabKeepSelected(selected, shownWords) {
    const shown = new Set(Array.isArray(shownWords) ? shownWords : []);
    return (Array.isArray(selected) ? selected : []).filter((w) => shown.has(w));
  }

  function vocabSelectedLabel(n) {
    const c = Math.max(0, Number(n) || 0);
    return c === 1 ? '1 selected' : `${formatCount(c)} selected`;
  }

  // The undo line after a bulk change.
  function vocabBulkDone(kind, words) {
    const list = Array.isArray(words) ? words : [];
    const n = list.length;
    const what = n === 1 ? list[0] : `${formatCount(n)} words`;
    if (kind === 'remove') return `Removed ${what}.`;
    if (kind === 'star') return `Starred ${what}.`;
    if (kind === 'unstar') return `Unstarred ${what}.`;
    return `Changed ${what}.`;
  }

  // The preview of an import (preview_vocab_import): the counts in plain
  // words, in the order they matter. Empty counts are left out.
  function _plural(n, one, many) {
    const c = Math.max(0, Number(n) || 0);
    return c === 1 ? `1 ${one}` : `${formatCount(c)} ${many}`;
  }

  function vocabImportView(view) {
    const v = view || {};
    const c = v.counts || {};
    const parts = [];
    if (c.new) parts.push({ kind: 'new', text: _plural(c.new, 'new word', 'new words') });
    if (c.updated) parts.push({ kind: 'updated', text: `${_plural(c.updated, 'word', 'words')} gain${c.updated === 1 ? 's' : ''} a spelling or a star` });
    if (c.already) parts.push({ kind: 'already', text: `${formatCount(c.already)} already there` });
    if (c.skipped) parts.push({ kind: 'skipped', text: `${formatCount(c.skipped)} left out` });
    const canApply = (Number(c.new) || 0) + (Number(c.updated) || 0) > 0;
    let headline;
    if (!c.rows) headline = 'Waffler found no rows in that.';
    else if (canApply) headline = 'This is what will change. Nothing is saved until you add it.';
    else headline = 'There is nothing new in that.';
    return { headline, parts, canApply, button: vocabImportButton(c), header: !!v.header };
  }

  function vocabImportButton(counts) {
    const c = counts || {};
    const n = (Number(c.new) || 0) + (Number(c.updated) || 0);
    if (!n) return 'Add to my Vocabulary';
    return Number(c.new) && !Number(c.updated) ? `Add ${_plural(c.new, 'word', 'words')}` : 'Add to my Vocabulary';
  }

  function vocabImportDone(view) {
    const c = (view && view.counts) || {};
    const bits = [];
    if (c.new) bits.push(`Added ${_plural(c.new, 'word', 'words')}`);
    if (c.updated) bits.push(`${bits.length ? 'changed' : 'Changed'} ${_plural(c.updated, 'word', 'words')}`);
    return bits.length ? `${bits.join(' and ')}.` : 'Nothing new to add.';
  }

  // The same for snippets.
  function snippetImportView(view) {
    const v = view || {};
    const c = v.counts || {};
    const parts = [];
    if (c.new) parts.push({ kind: 'new', text: _plural(c.new, 'new snippet', 'new snippets') });
    if (c.already) parts.push({ kind: 'already', text: `${formatCount(c.already)} already there` });
    if (c.skipped) parts.push({ kind: 'skipped', text: `${formatCount(c.skipped)} left out` });
    const canApply = (Number(c.new) || 0) > 0;
    let headline;
    if (!c.rows) headline = 'Waffler found no rows in that.';
    else if (canApply) headline = 'This is what will change. Nothing is saved until you add it.';
    else headline = 'There is nothing new in that.';
    return { headline, parts, canApply, button: canApply ? `Add ${_plural(c.new, 'snippet', 'snippets')}` : 'Add snippets',
             header: !!v.header };
  }

  function snippetImportDone(view) {
    const n = Number(view && view.counts && view.counts.new) || 0;
    return n ? `Added ${_plural(n, 'snippet', 'snippets')}.` : 'Nothing new to add.';
  }

  // The toast after Export on the Vocabulary page or the Snippets list.
  function listExportDone(result, what) {
    const where = /[\\/]Desktop[\\/]/.test(String((result && result.path) || ''))
      ? 'your Desktop' : 'your home folder';
    const n = Number(result && result.count) || 0;
    const names = what === 'snippets'
      ? (n === 1 ? '1 snippet' : `${formatCount(n)} snippets`)
      : (n === 1 ? '1 word' : `${formatCount(n)} words`);
    return n ? `Saved ${names} to ${where}.` : `Saved to ${where}.`;
  }

  // A file read in the page, ready for the bridge: its bytes as base64, so the
  // file's own encoding (UTF-16 from Excel, say) is kept and decoded by the app.
  function bytesToBase64(bytes) {
    const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < view.length; i += chunk) {
      binary += String.fromCharCode.apply(null, view.subarray(i, i + chunk));
    }
    return typeof btoa === 'function' ? btoa(binary) : Buffer.from(binary, 'binary').toString('base64');
  }

  // ── The Journal: Edit text, and the words it suggests (S10) ────────────
  // The words shown, copied and pasted again for a dictation: what the person
  // made of it in the Journal, else what was pasted (src/history_meta.py
  // final_text).
  function finalText(item) {
    if (!item) return '';
    if (item.kind !== 'transform' && item.kind !== 'command' && typeof item.edited_text === 'string' && item.edited_text.trim()) {
      return item.edited_text;
    }
    return item.styled || item.text || '';
  }

  function isEdited(item) {
    return !!item && item.kind !== 'transform' && item.kind !== 'command'
      && typeof item.edited_text === 'string' && !!item.edited_text.trim();
  }

  function _listed(items) {
    const list = items.map((t) => `"${t}"`);
    if (list.length <= 1) return list.join('');
    return `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`;
  }

  // One suggestion under an entry: the question, and what speech to text wrote.
  function suggestionView(s) {
    const word = String((s && s.word) || '');
    const heard = Array.isArray(s && s.sounds_like) ? s.sounds_like.filter((x) => typeof x === 'string' && x) : [];
    return {
      word,
      sounds: heard,
      question: `Add "${word}" to your Vocabulary?`,
      detail: heard.length ? `Speech to text wrote ${_listed(heard)}.` : 'Its capitals will be kept.',
      added: `Added "${word}".`,
    };
  }

  // The toast after the Vocabulary page's "Suggest words from my edits" switch.
  function learnSaved(on) {
    return on ? 'Waffler will offer words from your edits.' : "Waffler won't offer words from your edits.";
  }

  // What Edit text says when it did not save.
  function editFailedMessage(reason, error) {
    if (typeof error === 'string' && error) return error;
    return {
      not_found: "That dictation isn't in your Journal any more.",
      empty: 'Type the text first. To remove a dictation, delete it instead.',
    }[reason] || "Couldn't save your change. Try again.";
  }

  // The message after a saved edit.
  function editSavedLine(state) {
    return { edited: 'Saved. Copy and Paste last use your text now.', cleared: 'Back to the words that were pasted.' }[state] || '';
  }

  // "11 words · 181 corrections in your Journal"
  function vocabSummary(words, total) {
    const w = Math.max(0, Number(words) || 0);
    const t = Math.max(0, Number(total) || 0);
    return {
      words: `${formatCount(w)} ${w === 1 ? 'word' : 'words'}`,
      corrections: t ? `${formatCount(t)}` : '',
      correctionsLabel: t ? `${t === 1 ? 'correction' : 'corrections'} in your Journal` : '',
    };
  }

  // Text cut at [[start, end], ...] (try_vocab's marks): [{text, mark}].
  function markSegments(text, marks) {
    const s = String(text || '');
    const out = [];
    let pos = 0;
    (Array.isArray(marks) ? marks : [])
      .filter((m) => Array.isArray(m) && m[0] >= 0 && m[1] > m[0] && m[1] <= s.length)
      .sort((a, b) => a[0] - b[0])
      .forEach(([a, b]) => {
        if (a < pos) return;
        if (a > pos) out.push({ text: s.slice(pos, a), mark: false });
        out.push({ text: s.slice(a, b), mark: true });
        pos = b;
      });
    if (pos < s.length || !out.length) out.push({ text: s.slice(pos), mark: false });
    return out;
  }

  // The Journal marks the words the Vocabulary put in: each whole-word
  // occurrence of an entry it used, as written.
  function markWords(text, words) {
    const s = String(text || '');
    const ws = [...new Set((Array.isArray(words) ? words : []).filter((w) => typeof w === 'string' && w.trim()))]
      .sort((a, b) => b.length - a.length);
    if (!ws.length) return markSegments(s, []);
    const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // No look-behind: older Mac web views (before Safari 16.4) lack it.
    const re = new RegExp(`(?:${ws.map(esc).join('|')})`, 'gu');
    const wordChar = /[\p{L}\p{N}]/u;
    const marks = [];
    let m;
    while ((m = re.exec(s))) {
      const a = m.index, b = a + m[0].length;
      if (!m[0].length) { re.lastIndex++; continue; }
      if ((a > 0 && wordChar.test(s[a - 1])) || (b < s.length && wordChar.test(s[b]))) {
        re.lastIndex = a + 1;
        continue;
      }
      marks.push([a, b]);
    }
    return markSegments(s, marks);
  }

  // ── Journal (3.15, Atelier) ───────────────────────────────────────────
  // A day's heading: "Today  Monday 5 October", "Friday  2 October".
  function dayHeading(key, today) {
    const d = _localDate(key);
    if (!d) return { label: 'Earlier', date: '' };
    const now = today || new Date();
    const n = _daysBetween(d, now);
    const base = dayLabel(key, now);
    if (n === 0) return { label: 'Today', date: `${base.day} ${base.date}` };
    if (n === 1) return { label: 'Yesterday', date: `${base.day} ${base.date}` };
    return { label: base.day, date: base.date };
  }

  // This week, beside the Journal: the last seven days of get_stats'
  // daily_words, ending today, with each day's initial.
  function weekBars(dailyWords, dailyStart) {
    const list = Array.isArray(dailyWords) ? dailyWords : [];
    const start = _localDate(dailyStart);
    const last = list.slice(-7);
    while (last.length < 7) last.unshift(0);
    const max = Math.max(0, ...last.map((v) => Number(v) || 0));
    return last.map((v, i) => {
      const words = Math.max(0, Number(v) || 0);
      let initial = '';
      if (start && list.length) {
        const d = new Date(start);
        d.setDate(d.getDate() + list.length - 7 + i);
        initial = WEEKDAYS[d.getDay()].charAt(0);
      }
      return { words, height: max ? Math.max(4, Math.round((words / max) * 100)) : 4, initial, today: i === 6 };
    });
  }

  // "12 days in a row", and whether it is the longest yet.
  function streakView(streak, longest) {
    const n = Math.max(0, Number(streak) || 0);
    const best = Math.max(n, Number(longest) || 0);
    if (!n) return { title: '', sub: '' };
    const title = `${formatCount(n)} ${n === 1 ? 'day' : 'days'} in a row`;
    if (n >= 2 && n >= best) return { title, sub: 'Your longest yet' };
    if (best > n) return { title, sub: `Your longest: ${formatCount(best)} days` };
    return { title, sub: '' };
  }


  // ── The numbers beside the totals: week streak, this week against
  // last, minutes saved, words a minute, busiest hour, top apps and the
  // activity map. They are readings of get_stats (src/stats_insights.py,
  // worked out on this computer from the Journal); nothing here asks for
  // more. Every sentence is plain and calm: a quieter week is a fact, not a
  // fault, and an estimate says it is one.
  const TYPING_WPM = { min: 10, max: 200, def: 40 };     // stats_insights.TYPING_WPM_*
  const TYPING_SPEED_PROBLEM = 'Typing speed needs a whole number of words a minute, from 10 to 200.';
  const SMALL_WEEK = 50;          // under this many words, a percent jumps about: say the difference in words
  const HEAT_LEVELS = 5;

  function _n(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  function _count(v) {
    return Math.max(0, Math.round(_n(v)));
  }
  function _noun(n, one, many) {
    return `${formatCount(n)} ${Math.round(_n(n)) === 1 ? one : many}`;
  }
  function _upper(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
  }

  // "7 h 10 min", "45 min", "under a minute". Hours only from 100 h up.
  function formatMinutes(minutes) {
    const v = _n(minutes);
    if (v <= 0) return '0 min';
    if (v < 0.5) return 'under a minute';
    const total = Math.round(v);
    if (total < 60) return `${total} min`;
    const h = Math.floor(total / 60);
    const m = total % 60;
    if (h >= 100) return `${formatCount(h)} h`;
    return m ? `${h} h ${m} min` : `${h} h`;
  }

  // "about 7 h 10 min", but "under a minute" and "0 min" stand as they are.
  function _about(minutes) {
    const text = formatMinutes(minutes);
    return text === '0 min' || text.indexOf('under') === 0 ? text : `about ${text}`;
  }

  // The hour 14 as "2 pm", 0 as "midnight", 12 as "noon".
  function clockHour(h) {
    const x = ((Math.round(_n(h)) % 24) + 24) % 24;
    if (x === 0) return 'midnight';
    if (x === 12) return 'noon';
    return x < 12 ? `${x} am` : `${x - 12} pm`;
  }
  function hourRange(h) {
    return `${clockHour(h)} to ${clockHour(_n(h) + 1)}`;
  }

  // Typing speed (Settings, Usage): the box holds text, the setting is a
  // whole number from 10 to 200. The app checks it again.
  function typingSpeedCheck(raw) {
    const text = String(raw === null || raw === undefined ? '' : raw).trim();
    if (!/^\d{1,4}$/.test(text)) return { ok: false, value: null, message: TYPING_SPEED_PROBLEM };
    const value = parseInt(text, 10);
    if (value < TYPING_WPM.min || value > TYPING_WPM.max) return { ok: false, value: null, message: TYPING_SPEED_PROBLEM };
    return { ok: true, value, message: '' };
  }
  function typingSpeedSaved(value) {
    return `Typing speed set to ${formatCount(value)} words a minute. Minutes saved is updated.`;
  }

  // This week against last. The comparison is this week so far with the same
  // days of last week (a Monday is not "down 90%" for being one day long);
  // on Sunday those are the whole week. ``phrase`` is the comparison alone
  // ("up 12% on last week"), ``line`` the sentence for Settings, ``rail`` the
  // line under the Journal's bars (empty when there is nothing to compare).
  function weekView(stats) {
    stats = stats || {};
    const thisWords = _count(stats.words_this_week);
    const lastWords = _count(stats.words_last_week);
    const base = _count(stats.words_last_week_same_days);
    const partial = stats.weekday === undefined || stats.weekday === null ? false : _n(stats.weekday) < 6;
    const ref = partial ? 'the same days last week' : 'last week';
    const pct = stats.week_change_pct;
    let phrase = '';
    if (thisWords > 0 && base > 0 && pct !== null && pct !== undefined && Number.isFinite(Number(pct))) {
      const change = Math.round(Number(pct));
      if (Math.abs(change) < 3) phrase = `about the same as ${ref}`;
      else if (base < SMALL_WEEK) {
        const diff = thisWords - base;
        phrase = diff > 0 ? `${formatCount(diff)} more than ${ref}` : `${formatCount(-diff)} fewer than ${ref}`;
      } else if (change >= 300) phrase = `well up on ${ref}`;
      else if (change > 0) phrase = `up ${formatCount(change)}% on ${ref}`;
      else phrase = `down ${formatCount(-change)}% on ${ref}`;
    }
    let line = '';
    if (phrase) line = `${_upper(phrase)}.`;
    else if (thisWords === 0 && lastWords > 0) line = `A fresh week. Last week you dictated ${_noun(lastWords, 'word', 'words')}.`;
    else if (thisWords > 0 && lastWords === 0) line = 'Once there is a week behind you, this shows how the two compare.';
    return {
      words: formatCount(thisWords),
      wordsLabel: thisWords === 1 ? 'word' : 'words',
      phrase,
      line,
      last: lastWords > 0 ? `Last week in full: ${_noun(lastWords, 'word', 'words')}.` : '',
      rail: phrase ? `This week: ${_noun(thisWords, 'word', 'words')}, ${phrase}.` : '',
    };
  }

  // Weeks in a row with a dictation. Last week anchors it until this week
  // has one, so the sentence says what keeps it going.
  function weekStreakView(stats) {
    stats = stats || {};
    const n = _count(stats.week_streak);
    if (!n) return { value: '0 weeks', sub: 'Dictate on any day this week to start one.' };
    const thisWeek = _count(stats.week_count) > 0;
    return {
      value: _noun(n, 'week', 'weeks'),
      sub: thisWeek ? 'in a row, counting this week.' : 'in a row. Dictate this week to keep it going.',
    };
  }

  // Words a minute of speech. Nothing until enough dictations have a
  // measured length (the app says how many it has used).
  function wpmView(stats) {
    stats = stats || {};
    const wpm = stats.wpm;
    if (wpm === null || wpm === undefined || !(_n(wpm) > 0)) {
      return { known: false, value: 'Not yet', sub: 'A few more dictations and your speed shows up here.' };
    }
    const value = Math.round(_n(wpm));
    const samples = _count(stats.wpm_samples);
    const typing = _count((stats.minutes_saved_basis || {}).typing_wpm) || TYPING_WPM.def;
    const ratio = Math.round((value / typing) * 10) / 10;
    let sub = `words a minute, from your last ${_noun(samples, 'dictation', 'dictations')}.`;
    if (ratio >= 1.2) sub += ` About ${String(ratio).replace(/\.0$/, '')} times as fast as typing at ${typing}.`;
    return { known: true, value: formatCount(value), sub };
  }

  // Minutes saved, and the working behind it for "How this is estimated".
  function minutesSavedView(stats) {
    stats = stats || {};
    const b = stats.minutes_saved_basis || {};
    const words = _count(b.words);
    const typing = _count(b.typing_wpm) || TYPING_WPM.def;
    const saved = _n(stats.minutes_saved);
    const timed = _count(b.timed);
    const guessed = _count(b.estimated);
    let spoken = 'the length of each recording';
    if (timed === 0 && guessed > 0) spoken = '150 words a minute, because these dictations did not note how long they took';
    else if (timed > 0 && guessed > 0) spoken = 'the length of each recording where it was kept, and 150 words a minute otherwise';
    const how = [
      `Typing ${_noun(words, 'word', 'words')} at ${typing} words a minute would take ${_about(b.typing_minutes)}.`,
      `Saying them took ${_about(b.speaking_minutes)}, counted from ${spoken}.`,
      'The difference is the time saved, and it is never below zero. It is an estimate, not a measurement: it leaves out the moment Waffler takes to answer and the time you would spend fixing typing mistakes. Set the typing speed below to match yours.',
    ];
    let sub = `an estimate, if you typed at ${typing} words a minute`;
    if (!words) sub = 'Dictate a little and an estimate of the time saved shows up here.';
    else if (saved <= 0) sub = `At ${typing} words a minute, typing these words would have taken no longer than saying them.`;
    return { value: formatMinutes(saved), sub, how, words, typing };
  }

  // The busiest hour, with the 24 hours as bars.
  function peakHourView(stats) {
    stats = stats || {};
    const counts = Array.isArray(stats.hour_counts) && stats.hour_counts.length === 24
      ? stats.hour_counts.map(_count) : new Array(24).fill(0);
    const total = counts.reduce((a, b) => a + b, 0);
    const max = Math.max(0, ...counts);
    const need = _count(stats.peak_hour_needs) || 20;
    const peak = stats.peak_hour === null || stats.peak_hour === undefined ? null : Math.round(_n(stats.peak_hour));
    const bars = counts.map((c, h) => ({
      hour: h,
      count: c,
      height: c ? Math.max(6, Math.round((c / max) * 100)) : 0,
      peak: peak === h,
      label: `${hourRange(h)}: ${_noun(c, 'dictation', 'dictations')}`,
    }));
    if (peak === null || peak < 0 || peak > 23) {
      return {
        known: false, bars, total, range: '', title: '', sub: '',
        teach: total === 0
          ? `Your busiest hour shows up here after about ${need} dictations.`
          : 'A few more dictations and your busiest hour shows up here.',
      };
    }
    return {
      known: true, bars, total, teach: '',
      range: hourRange(peak),
      title: `Your busiest hour is ${hourRange(peak)}`,
      sub: `${formatCount(counts[peak])} of ${_noun(total, 'dictation', 'dictations')}, by the clock on this computer.`,
    };
  }

  // The apps dictated into most, as ranked bars, and how many dictations
  // never said which app (older ones, or where it could not be told).
  function topAppsView(stats) {
    stats = stats || {};
    const list = (Array.isArray(stats.top_apps) ? stats.top_apps : [])
      .filter((a) => a && typeof a.name === 'string' && a.name && _count(a.count) > 0);
    const top = Math.max(0, ...list.map((a) => _count(a.count)));
    const rows = list.map((a, i) => ({
      name: a.name,
      count: _count(a.count),
      countText: formatCount(a.count),
      pct: Math.max(4, Math.round((_count(a.count) / top) * 100)),
      top: i === 0,
    }));
    const recorded = _count(stats.apps_recorded);
    const missing = _count(stats.apps_not_recorded);
    const total = _count(stats.apps_total);
    return {
      rows,
      empty: recorded === 0,
      teach: 'Waffler notes which app you dictate into, on this computer only. A few dictations and your top apps show up here.',
      more: total > rows.length ? `${_noun(total - rows.length, 'more app', 'more apps')} not shown.` : '',
      missing: missing > 0
        ? { label: 'Not recorded', countText: formatCount(missing), note: 'Earlier dictations, and ones where Waffler could not tell, did not note the app.' }
        : null,
    };
  }

  // The activity map. Five steps, 0 for a day with no words; the others
  // scale with the square root of the day's words against the busiest day,
  // so one huge day does not turn every other day pale.
  function heatLevel(words, max) {
    const w = _n(words);
    const m = _n(max);
    if (!(w > 0) || !(m > 0)) return 0;
    const r = Math.sqrt(Math.min(1, w / m));
    return r > 0.75 ? 4 : (r > 0.5 ? 3 : (r > 0.25 ? 2 : 1));
  }

  // Cells in the order the grid draws them: a column a week, Monday first.
  // Dates are counted on the calendar (UTC midnights), never in 24-hour
  // steps, so a clock change cannot move a day.
  function heatmapView(hm, todayIso) {
    const weeks = hm && Array.isArray(hm.days) ? hm.days : [];
    const max = hm ? _n(hm.max) : 0;
    const thisYear = (_isoDay(todayIso) || new Date()).getUTCFullYear();
    const cells = [];
    weeks.forEach((wk, w) => {
      for (let dow = 0; dow < 7; dow++) {
        const raw = Array.isArray(wk) ? wk[dow] : null;
        const future = raw === null || raw === undefined;
        const words = future ? 0 : _count(raw);
        const d = _isoDay(hm.start, w * 7 + dow);
        const year = d && d.getUTCFullYear() !== thisYear ? ` ${d.getUTCFullYear()}` : '';
        const name = d ? `${DAYS_SHORT[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}${year}` : '';
        cells.push({
          i: w * 7 + dow, week: w, dow, future, words,
          iso: d ? d.toISOString().slice(0, 10) : '',
          level: future ? -1 : heatLevel(words, max),
          label: future ? '' : `${name}: ${words ? _noun(words, 'word', 'words') : 'no words'}`,
          name,
        });
      }
    });
    const real = cells.filter((c) => !c.future);
    const today = real.length ? real[real.length - 1] : null;
    if (today && today.iso === String(todayIso || '')) today.label = `Today, ${today.label}`;
    let busiest = null;
    real.forEach((c) => { if (c.words > 0 && (!busiest || c.words >= busiest.words)) busiest = c; });
    const active = real.filter((c) => c.words > 0).length;
    const months = [];
    for (let w = 0; w < weeks.length; w++) {
      const monday = _isoDay(hm.start, w * 7);
      if (monday && (w === 0 || monday.getUTCDate() <= 7)) months.push({ col: w, label: MONTHS_SHORT[monday.getUTCMonth()] });
    }
    return {
      weeks: weeks.length,
      cells,
      last: real.length ? real[real.length - 1].i : -1,
      months: months.filter((m, k) => k === months.length - 1 || months[k + 1].col - m.col >= 3),
      active,
      busiest: busiest ? { name: busiest.name, words: busiest.words } : null,
      summary: active === 0
        ? `No dictations in the last ${weeks.length} weeks yet. Each day you dictate fills in a square here.`
        : `${_noun(active, 'day', 'days')} with a dictation in the last ${weeks.length} weeks. Busiest: ${busiest.name}, ${_noun(busiest.words, 'word', 'words')}.`,
    };
  }

  // Arrow keys on the map: left and right are a week, up and down a day
  // within the week, Home the first day and End today. Returns the cell to
  // move to (the same one when there is nowhere to go), or null for any
  // other key.
  function heatMove(i, key, last) {
    const at = Math.max(0, Math.round(_n(i)));
    const end = Math.max(0, Math.round(_n(last)));
    let to;
    switch (key) {
      case 'ArrowRight': to = at + 7; break;
      case 'ArrowLeft': to = at - 7; break;
      case 'ArrowDown': to = at % 7 === 6 ? at : at + 1; break;
      case 'ArrowUp': to = at % 7 === 0 ? at : at - 1; break;
      case 'Home': to = 0; break;
      case 'End': to = end; break;
      default: return null;
    }
    return to < 0 || to > end ? at : to;
  }

  return {
    vocabLastUsed, vocabRecentWhen, vocabSoundsParse, vocabSoundsCheck, vocabRows, vocabSummary,
    VOCAB_SORTS, VOCAB_SORT_DEFAULT, vocabSortId, foldText, vocabMatches, vocabFilter, vocabSortRows,
    vocabSearchAnnouncement, vocabKeepSelected, vocabSelectedLabel, vocabBulkDone, vocabImportView,
    vocabImportDone, snippetImportView, snippetImportDone, listExportDone, bytesToBase64,
    finalText, isEdited, suggestionView, editFailedMessage, editSavedLine, learnSaved,
    VOCAB_MAX_SOUNDS, markSegments, markWords, dayHeading, weekBars, streakView,
    keyInputView, serviceRows, saidDiff, setupSteps, startupTheme,
    STATUS_VIEWS, STATUS_CLASSES, DONE_RESET_MS, statusView, statusResetMs, workingLabel, workingTime, recordingTime,
    notSentId, notSentView, retryFailedMessage, unsentSummary,
    RECENT_AUDIO_KEEP, entryRef, pageCursor, insertIndex, undoLine, deleteFailedMessage, undoFailedMessage,
    redoFailedMessage, EXPORT_FORMATS, exportFormat, exportDone, exportFailed,
    HISTORY_KEEP, historyKeepLabel, historyKeepConfirm, historyKeepDone,
    defaultHotkey, keyName, orderKeys, hotkeyName, keycaps, pressOrderHint, hotkeyPresets,
    DOWNLOAD_PAGE, UPDATE_TEXT, INSTALL_RETRY_MS, splitMessage, updateCheckView, updateFailureView,
    DEFAULT_PROVIDER_ORDER, PROVIDER_NAMES, providerHasKey, normalizeProviderOrder,
    providerOrderRows, activeProviders, backendsLine, aboutLine,
    USAGE_NOTE, formatCount, usageView, usageProviderRows, usesView, keyRows,
    qualityView, dayKey, dayLabel, statNumber, vocabChanges, vocabChangesLine, originalText, hasOriginal, vocabAdd, VOCAB_MAX_ENTRY_LEN,
    SEARCH_DEBOUNCE_MS, debounce, feedView, searchAnnouncement, radioMove, focusAfterRemove,
    SNIPPET_MAX_TRIGGER, SNIPPET_MAX_EXPANSION, SNIPPET_MAX_COUNT, snippetTrigger, snippetCheck,
    snippetSummary, snippetCounter, snippetDone,
    spellingSpecimen, languageOptions, languageDesc, languageSaved, spellingNote,
    STYLE_KINDS, STYLE_CHOICES, STYLE_LABELS, STYLE_NOTE, SIGNATURE_MAX, styleRows, styleAbout, cleanupView,
    cleanupSaved, styleSaved, signatureSaved, signatureView,
    TRANSFORM_RULES, TRANSFORM_LIMITS, TRANSFORM_BUILT_IN, TRANSFORM_TEXT, transformView, transformCheck,
    transformRuleCheck, transformOutlineView, transformShortcutLine, transformSaved, hotkeyDialogText,
    transformEntryView,
    COMMAND_ENGINES, COMMAND_EXAMPLES, commandEngineLabel, commandView, commandShortcutLine, commandSaved,
    commandEntryView,
    clipboardAfterPasteView, clipboardAfterPasteSaved, feedbackView, feedbackSaved, METER_CELLS, micMeter, usageChart, usageFacts, flowView, PROVIDER_MARKS, providerPill,
    privacyFlow, hotkeyTestLine, whatsNew,
    TYPING_WPM, TYPING_SPEED_PROBLEM, HEAT_LEVELS, formatMinutes, clockHour, hourRange, typingSpeedCheck,
    typingSpeedSaved, weekView, weekStreakView, wpmView, minutesSavedView, peakHourView, topAppsView,
    heatLevel, heatmapView, heatMove,
  };
});
