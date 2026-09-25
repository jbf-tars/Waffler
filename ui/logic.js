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

  return {
    STATUS_VIEWS, STATUS_CLASSES, DONE_RESET_MS, statusView,
    defaultHotkey, keyName, orderKeys, hotkeyName, keycaps, pressOrderHint, hotkeyPresets,
    DOWNLOAD_PAGE, UPDATE_TEXT, splitMessage, updateCheckView, updateFailureView,
  };
});
