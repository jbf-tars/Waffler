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
  };
  const STATUS_CLASSES = Object.keys(STATUS_VIEWS).map((k) => STATUS_VIEWS[k].cls);
  const DONE_RESET_MS = 3000;

  // An unknown status shows as Ready rather than as its raw name.
  function statusView(status) {
    return Object.prototype.hasOwnProperty.call(STATUS_VIEWS, status)
      ? STATUS_VIEWS[status] : STATUS_VIEWS.idle;
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
          kind: 'no_installer', icon: '⬆️', title,
          subtitle: r.no_installer_message || UPDATE_TEXT.noInstaller,
          primary: r.release_url
            ? { label: 'Open release page', url: r.release_url }
            : { label: 'Open download page', url: DOWNLOAD_PAGE },
          cancelLabel: 'Later',
        };
      }
      return {
        kind: 'available', icon: '⬆️', title,
        subtitle: `You're on v${r.current_version}. Download and install now?`,
        primary: { label: 'Download & Install', download: r.download_url },
        browserUrl: DOWNLOAD_PAGE, cancelLabel: 'Later',
      };
    }
    if (r.error) {
      const m = splitMessage(r.error);
      const on = r.current_version ? ` You're on v${r.current_version}.` : '';
      return { kind: 'error', icon: '⚠️', title: m.title, subtitle: (m.subtitle + on).trim() };
    }
    const latest = r.latest_version ? ` (latest: v${r.latest_version})` : '';
    return { kind: 'up_to_date', icon: '✓', title: "You're up to date",
             subtitle: `Running Waffler v${r.current_version || '?'}${latest}.` };
  }

  // A failed download or install: the backend's sentence, and a way to the
  // download page.
  function updateFailureView(r, fallback) {
    const m = splitMessage((r && r.error) || fallback || UPDATE_TEXT.downloadFailed);
    return { icon: '⚠️', title: m.title, subtitle: m.subtitle,
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

  function usageView(usage, stats) {
    usage = usage || {};
    stats = stats || {};
    const money = (n, digits) => '$' + (Number(n) || 0).toFixed(digits);
    return {
      dictations: formatCount(usage.transcription_count),
      words: formatCount(stats.total_words),
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

  return {
    STATUS_VIEWS, STATUS_CLASSES, DONE_RESET_MS, statusView,
    defaultHotkey, keyName, orderKeys, hotkeyName, keycaps, pressOrderHint, hotkeyPresets,
    DOWNLOAD_PAGE, UPDATE_TEXT, splitMessage, updateCheckView, updateFailureView,
    DEFAULT_PROVIDER_ORDER, PROVIDER_NAMES, providerHasKey, normalizeProviderOrder,
    providerOrderRows, activeProviders, backendsLine, aboutLine,
    USAGE_NOTE, formatCount, usageView,
  };
});
