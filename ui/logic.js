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
    if (!n) return { label: 'Nothing waiting. Every recording has been sent.', canSend: false, count: 0,
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
      const title = `Waffler v${r.latest_version} is available`;
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
        subtitle: `You're on v${r.current_version}. Download and install now?`,
        primary: { label: 'Download & Install', download: r.download_url },
        browserUrl: DOWNLOAD_PAGE, cancelLabel: 'Later',
      };
    }
    if (r.error) {
      const m = splitMessage(r.error);
      const on = r.current_version ? ` You're on v${r.current_version}.` : '';
      return { kind: 'error', icon: 'alert', title: m.title, subtitle: (m.subtitle + on).trim() };
    }
    const latest = r.latest_version ? ` (latest: v${r.latest_version})` : '';
    return { kind: 'up_to_date', icon: 'check-circle', title: "You're up to date",
             subtitle: `Running Waffler v${r.current_version || '?'}${latest}.` };
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
    styled_dropped_words: 'The clean-up took out more than usual. Show transcript to see everything you said.',
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
  function saidDiff(said, wrote) {
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
    if (!cutWords || cutWords * 2 > words) return text ? [{ text, cut: false }] : [];
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

  // The steps of setup, in order. The Mac adds its permissions screen.
  function setupSteps(isMac) {
    return isMac ? ['connect', 'permissions', 'try', 'anywhere'] : ['connect', 'try', 'anywhere'];
  }

  return {
    keyInputView, serviceRows, saidDiff, setupSteps,
    STATUS_VIEWS, STATUS_CLASSES, DONE_RESET_MS, statusView, statusResetMs, workingLabel, workingTime, recordingTime,
    notSentId, notSentView, retryFailedMessage, unsentSummary,
    HISTORY_KEEP, historyKeepLabel, historyKeepConfirm, historyKeepDone,
    defaultHotkey, keyName, orderKeys, hotkeyName, keycaps, pressOrderHint, hotkeyPresets,
    DOWNLOAD_PAGE, UPDATE_TEXT, splitMessage, updateCheckView, updateFailureView,
    DEFAULT_PROVIDER_ORDER, PROVIDER_NAMES, providerHasKey, normalizeProviderOrder,
    providerOrderRows, activeProviders, backendsLine, aboutLine,
    USAGE_NOTE, formatCount, usageView, usageProviderRows, usesView, keyRows,
    qualityView, dayKey, dayLabel, statNumber,
    SEARCH_DEBOUNCE_MS, debounce, feedView, searchAnnouncement, radioMove, focusAfterRemove,
  };
});
