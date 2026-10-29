/* Waffler: the window's code (top bar, Journal, Vocabulary, Settings,
   dialogs; first-run setup is further down). */

// Pure helpers (labels, presets, formatting) live in logic.js, which
// index.html loads first, so the tests can run them without a page.
const WL = window.WafflerLogic;
// Line icons and the waffle (icons.js, loaded before this file).
const WI = window.WafflerIcons;

// ── Theme ─────────────────────────────────────────────────────────────
// Apply the saved theme as early as possible so the page doesn't flash
// in the wrong colours. Settings calls them Light, Dark and System; they
// are saved as "cream", "dark" and "auto" (settings.json and the window's
// background colour use the same names). Default for new installs: light.
//
// "auto" (System) is resolved here to "cream" or "dark" from the OS
// setting, and followed live, so every light- and night-specific rule
// applies to System too. The choice itself is kept in data-theme-pref.
const THEME_CHOICES = ['cream', 'dark', 'auto'];
const _darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
let _themePref = 'cream';

function _resolveTheme(pref) {
  if (pref !== 'auto') return pref;
  return _darkQuery && _darkQuery.matches ? 'dark' : 'cream';
}

function _applyTheme(pref) {
  _themePref = THEME_CHOICES.includes(pref) ? pref : 'cream';
  document.body.setAttribute('data-theme', _resolveTheme(_themePref));
  document.body.setAttribute('data-theme-pref', _themePref);
}

// Also saved in settings.json, so the app can paint its window in the
// theme's colour before this page loads (no dark flash on open).
function _syncThemeToApp(pref) {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_theme) {
      window.pywebview.api.set_theme(pref);
    }
  } catch (_) {}
}

(function applyStoredTheme() {
  let t = 'cream';
  try { t = localStorage.getItem('waffler_theme') || 'cream'; } catch (_) {}
  _applyTheme(t);
  if (_darkQuery) {
    const follow = () => { if (_themePref === 'auto') _applyTheme('auto'); };
    if (_darkQuery.addEventListener) _darkQuery.addEventListener('change', follow);
    else if (_darkQuery.addListener) _darkQuery.addListener(follow);
  }
  window.addEventListener('pywebviewready', _loadSavedTheme);
})();

// The saved theme comes from settings.json: localStorage does not survive a
// restart (the window runs in private mode), so it is only a first guess.
// Nothing is saved here unless this page holds a choice Python lacks.
async function _loadSavedTheme() {
  let saved = null, local = null;
  try { local = localStorage.getItem('waffler_theme'); } catch (_) {}
  try {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.get_theme) return;
    const r = await api.get_theme();
    saved = r && r.theme;
  } catch (_) { return; }
  const d = WL.startupTheme(saved, local);
  if (d.apply && d.apply !== _themePref) {
    _applyTheme(d.apply);
    try { localStorage.setItem('waffler_theme', d.apply); } catch (_) {}
    refreshThemePicker();
  }
  if (d.save) _syncThemeToApp(d.save);
}

function setAppTheme(theme) {
  if (!THEME_CHOICES.includes(theme)) return;
  _applyTheme(theme);
  try { localStorage.setItem('waffler_theme', theme); } catch (_) {}
  _syncThemeToApp(theme);
  refreshThemePicker();
}

// Settings, General: Light / Dark / System. A radio group: one Tab stop
// (the chosen theme), and the arrow keys move and choose, as radios do.
function refreshThemePicker() {
  const cur = _themePref || 'cream';
  document.querySelectorAll('#themeSeg button').forEach((el) => {
    const on = el.getAttribute('data-theme') === cur;
    el.setAttribute('aria-checked', String(on));
    el.tabIndex = on ? 0 : -1;
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const seg = document.getElementById('themeSeg');
  if (!seg) return;
  seg.addEventListener('keydown', (e) => {
    const radios = [...seg.querySelectorAll('button[role="radio"]')];
    const i = radios.indexOf(document.activeElement);
    const to = WL.radioMove(i, radios.length, e.key);
    if (to < 0) return;
    e.preventDefault();
    radios[to].focus();
    setAppTheme(radios[to].getAttribute('data-theme'));
  });
});

// ── State ──────────────────────────────────────────────────────────────
// The Journal loads a page at a time (get_history limit/offset), newest
// first: `history` is what has been loaded for the current search.
const PAGE_SIZE = 50;
let history = [];
let _histTotal = 0;          // entries in the whole Journal (get_stats entries)
let _histDone = false;       // every matching entry has been loaded
let _histLoading = false;
let _histSeq = 0;            // the newest request; older answers are dropped
let _feedDays = { first: '', last: '' };   // the day rows at the top and bottom
let stats = { today_words: 0, today_count: 0, total_words: 0, streak_days: 0, entries: 0 };
let toastTimer = null;

// ── Hotkey capture state ──────────────────────────────────────────
let _capturedKeys = new Set();

// Platform-specific defaults
const isMacPlatform = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
let _lastCapturedKeys = isMacPlatform ? ["fn"] : ["win", "ctrl"];
let _currentHotkeyKeys = isMacPlatform ? ["fn"] : ["win", "ctrl"];
// What cancels a recording, as the Journal's live line names it: Esc until the
// person has chosen another key (get_hotkey_config and Settings, Shortcuts).
let _cancelLabel = 'Esc';

const MODIFIER_IDS = new Set(isMacPlatform ?
  ["control", "option", "shift", "cmd", "fn"] :
  ["ctrl", "alt", "shift", "win"]
);

// The name the app saves for a key a keyboard event reports (logic.js
// captureKeyId: a Windows letter is the key the layout types, a Mac's the
// physical key, as each platform's keyboard listener reports it). In the cancel
// key's dialog Esc is a key to record.
function jsKeyToId(e) {
  return WL.captureKeyId(e, isMacPlatform, _captureTarget);
}

function hotkeyDisplayStr(keys) {
  // Plain key names, in the same order as the backend and the website.
  return WL.hotkeyName(keys, isMacPlatform);
}

// ── DOM refs ────────────────────────────────────────────────────────────
const $main          = document.getElementById('mainArea');
const $feed          = document.getElementById('transcriptFeed');
const $empty         = document.getElementById('emptyState');
const $noMatch       = document.getElementById('noMatchState');
const $feedMore      = document.getElementById('feedMore');
const $statusInd     = document.getElementById('statusIndicator');
const $statusText    = document.getElementById('statusText');
const $statusTime    = document.getElementById('statusTime');
const $hotkeyCaps    = document.getElementById('hotkeyHint');
const $toast         = document.getElementById('toast');
const $statWords     = document.getElementById('statWords');
const $statCount     = document.getElementById('statCount');

// ── Pause animations while the window can't be seen ───────────────────
// Idle, the window used about 11% of a core, almost all of it the WebView2
// GPU process drawing animations nobody could see. style.css pauses every
// animation under html.waffler-hidden. The page's own visibility covers
// most cases; app.py also reports hide, minimise and restore through
// waffler_window_visible(), because a minimised WebView2 window may still
// count as visible. Any sign of use (focus, a click, a key) clears it.
function _setWindowHidden(hidden) {
  document.documentElement.classList.toggle('waffler-hidden', !!hidden);
}
document.addEventListener('visibilitychange', () => _setWindowHidden(document.hidden));
window.waffler_window_visible = function(visible) {
  _setWindowHidden(!visible);
  // Back from the tray or a minimise during setup: look for a copied key,
  // as on focus (WebView2 may not report focus for a title bar click).
  if (visible && _wizClipOnFocus) _wizClipOnFocus();
};
['focus', 'pointerdown', 'keydown'].forEach((type) => {
  window.addEventListener(type, () => _setWindowHidden(false), true);
});
_setWindowHidden(document.hidden);

function _windowHidden() {
  return document.documentElement.classList.contains('waffler-hidden');
}
function _prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// ── Start-up ──────────────────────────────────────────────────────────
// Once: whether setup is needed, then the Journal (one page and the
// counts), the hotkey, the microphones, the clean-up pause, and an update
// check a few seconds later. It used to run from three places, so each
// start asked for the whole history five times.
let _booted = false;

function boot() {
  if (_booted || !window.pywebview || !window.pywebview.api) return;
  _booted = true;
  checkOnboarding();
  loadHotkeyConfig();
  loadAudioDevices();
  loadCleanupPause();
  // Check for updates after a short delay (don't block startup)
  setTimeout(checkForUpdates, 3000);
}
window.addEventListener('pywebviewready', boot);

document.addEventListener('DOMContentLoaded', () => {
  renderHotkeyCaps(_currentHotkeyKeys);
  renderSettingsHotkeyPresets();
  _watchFeedEnd();
  document.getElementById('emptyEditorLabel').textContent =
    isMacPlatform ? 'Open TextEdit and try it' : 'Open Notepad and try it';
  document.getElementById('themeDesc').textContent = `follows ${isMacPlatform ? 'macOS' : 'Windows'}`;

  // Prevent Mac error sound when space is pressed in the app
  // (Space monitor observes at OS level, but we need to handle it in UI to avoid "bonk" sound)
  document.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.code === 'Space') {
      // Only prevent default if NOT in an input field or on a button
      const target = e.target;
      if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA' && target.tagName !== 'BUTTON'
          && target.tagName !== 'SELECT' && !target.isContentEditable) {
        e.preventDefault();
      }
    }
  });

  // In case the bridge was there before this listener (pywebview can
  // announce itself early).
  setTimeout(boot, 300);
});

// A notice card (components.css .notice): an icon tile, a title, a line of
// detail, actions and a close button. Used for the update notices and the
// clean-up pause, at the top of the Journal.
function makeNotice({ icon, tone, title, desc, actions, id }) {
  const box = document.createElement('div');
  box.className = 'notice update-banner' + (tone === 'honey' || tone === 'warn' ? ' notice-honey' : '');
  box.setAttribute('role', 'status');
  if (id) box.id = id;
  const tile = document.createElement('span');
  tile.className = 'itile' + (tone === 'warn' ? ' is-warn' : '');
  tile.innerHTML = WI.icon(icon || 'info');
  const body = document.createElement('div');
  body.className = 'notice-body';
  const t = document.createElement('div');
  t.className = 'notice-title';
  t.textContent = title || '';
  body.appendChild(t);
  if (desc) {
    const d = document.createElement('div');
    d.className = 'notice-desc';
    d.textContent = desc;
    body.appendChild(d);
  }
  box.append(tile, body);
  if (actions && actions.length) {
    const acts = document.createElement('div');
    acts.className = 'notice-acts';
    acts.append(...actions);
    box.appendChild(acts);
  }
  const x = document.createElement('button');
  x.className = 'rbtn rbtn-sm dismiss';
  x.setAttribute('aria-label', 'Dismiss');
  x.innerHTML = WI.icon('x');
  x.addEventListener('click', () => box.remove());
  box.appendChild(x);
  return box;
}

function _journalNotices() {
  return document.getElementById('journalNotices');
}

async function checkForUpdates() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const r = await pywebview.api.check_for_updates();
    _noteUpdateChecked(r);
    const host = _journalNotices();
    if (!host) return;
    // A previous update that silently did nothing used to leave no trace at
    // all: the app just restarted on the old version. Say so plainly.
    if (r.last_update_failed && r.last_update_failed.message) {
      const m = WL.splitMessage(r.last_update_failed.message);
      host.prepend(makeNotice({ icon: 'alert', tone: 'warn', title: m.title, desc: m.subtitle }));
    }
    if (r.update_available) {
      // The update notice goes in the Journal's margin. Update opens the
      // same in-app download-and-install dialog as Settings, About, which
      // falls back to the download page; What's new opens the release.
      const download = document.createElement('button');
      download.className = 'btn btn-pri btn-sm';
      download.textContent = 'Update';
      const notes = document.createElement('button');
      notes.className = 'btn btn-quiet btn-sm';
      notes.textContent = "What's new";
      notes.addEventListener('click', () => {
        try { pywebview.api.open_url(r.release_url || _ABOUT_LINKS.releases); } catch (_) {}
      });
      const banner = makeNotice({ icon: 'circle-up', tone: 'plain', title: `Waffler ${r.latest_version} is ready`, actions: [download, notes] });
      download.addEventListener('click', () => {
        openUpdateModalFromCheck(r);
        banner.remove();
      });
      host.prepend(banner);
    }
  } catch(e) {
    console.warn('Update check failed:', e);
  }
}

// ── Clean-up paused by a provider's limit ─────────────────────────────
// While every clean-up provider is at its limit, dictation carries on and
// pastes the words as they were said. The Journal says so at the top until
// it ends (src/cleanup_pause.py), with a way to add a backup key.
let _pauseTimer = null;

window.waffler_cleanup_paused = function(view) { showCleanupPause(view); };

async function loadCleanupPause() {
  try {
    if (!pywebview.api.get_cleanup_pause) return;
    showCleanupPause(await pywebview.api.get_cleanup_pause());
  } catch (_) {}
}

function showCleanupPause(view) {
  const old = document.getElementById('cleanupPause');
  if (old) old.remove();
  clearTimeout(_pauseTimer);
  const host = _journalNotices();
  if (!view || !host) return;
  const actions = [];
  const s = _lastSettings || {};
  const keys = ['groq_key_set', 'api_key_set', 'cerebras_key_set'].filter((k) => s[k]).length;
  if (!_lastSettings || keys < 2) {
    const add = document.createElement('button');
    add.className = 'btn btn-sec btn-sm';
    add.textContent = 'Add a backup key';
    add.addEventListener('click', () => { showPage('settings'); showSettingsSection('keys'); });
    actions.push(add);
  }
  host.appendChild(makeNotice({ id: 'cleanupPause', icon: 'clock', tone: 'warn', title: view.title, desc: view.detail, actions }));
  if (view.seconds_left > 0) {
    _pauseTimer = setTimeout(() => showCleanupPause(null), Math.min(view.seconds_left, 86400) * 1000);
  }
}

// ── Updates (Settings, About: Check for updates) ──────────────────────
let _updatePollTimer = null;
let _downloadedPath = null;
let _lastUpdateInfo = null;

// ── Dialogs: focus goes in, stays in, and comes back ─────────────────────
// A dialog (.modal-overlay) takes focus when it opens, Tab and Shift+Tab
// wrap inside it, and closing it puts focus back on what opened it. They
// all declare aria-modal, and used to leave focus on the button behind.
const _modalOpeners = {};

function _focusables(root) {
  return [...root.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.disabled && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
}

function _openModalOverlay() {
  const open = [...document.querySelectorAll('.modal-overlay')]
    .filter((el) => el.isConnected && getComputedStyle(el).display !== 'none');
  return open[open.length - 1] || null;
}

function modalOpened(overlay) {
  if (!overlay) return;
  if (!overlay.contains(document.activeElement)) _modalOpeners[overlay.id] = document.activeElement;
  (overlay.querySelector('[role="dialog"]') || overlay).focus();
}

function modalClosed(overlay) {
  if (!overlay) return;
  const back = _modalOpeners[overlay.id];
  delete _modalOpeners[overlay.id];
  const active = document.activeElement;
  const lost = !active || active === document.body || overlay.contains(active);
  if (lost && back && back.isConnected && back.getClientRects().length && !back.disabled) back.focus();
}

document.addEventListener('keydown', (e) => {
  // Setup fills the window and is a dialog too: Tab goes round inside it.
  const wizard = document.getElementById('wizardOverlay');
  const overlay = _openModalOverlay() || (wizard && wizard.style.display !== 'none' ? wizard : null);
  if (!overlay) return;
  if (e.key === 'Escape' && overlay.id === 'updateModal') { e.preventDefault(); closeUpdateModal(); return; }
  if (e.key !== 'Tab') return;
  const items = _focusables(overlay);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  const inside = overlay.contains(document.activeElement);
  if (e.shiftKey && (!inside || document.activeElement === first || !items.includes(document.activeElement))) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
    e.preventDefault(); first.focus();
  }
}, true);

function showUpdateModal() {
  const m = document.getElementById('updateModal');
  if (!m) return;
  const wasOpen = m.style.display !== 'none';
  m.style.display = 'flex';
  if (!wasOpen) modalOpened(m);
}
function closeUpdateModal(ev) {
  if (ev && ev.target && ev.target.id !== 'updateModal') return;
  const m = document.getElementById('updateModal');
  if (m) m.style.display = 'none';
  stopProgressPolling();
  if (_installRetryTimer) { clearTimeout(_installRetryTimer); _installRetryTimer = null; }
  modalClosed(m);
}
function setUpdateModal({ icon, title, subtitle, showProgress, primaryLabel, primaryHandler, cancelLabel, browserUrl, browserLabel }) {
  // icon is a name from icons.js ('circle-up', 'download', 'alert'...).
  const tile = document.getElementById('updateModalIcon');
  tile.innerHTML = WI.icon(icon || 'circle-up');
  tile.className = 'itile' + (icon === 'alert' ? ' is-warn' : icon === 'check-circle' ? ' is-ok' : '');
  document.getElementById('updateModalTitle').textContent = title || '';
  document.getElementById('updateModalSubtitle').textContent = subtitle || '';
  document.getElementById('updateProgressWrap').style.display = showProgress ? 'block' : 'none';
  const primary = document.getElementById('updatePrimaryBtn');
  if (primaryLabel) {
    primary.textContent = primaryLabel;
    primary.style.display = '';
    primary.onclick = primaryHandler;
  } else {
    primary.style.display = 'none';
  }
  const browserBtn = document.getElementById('updateBrowserBtn');
  if (browserUrl) {
    browserBtn.textContent = browserLabel || 'Open download page';
    browserBtn.style.display = '';
    browserBtn.onclick = () => pywebview.api.open_url(browserUrl);
  } else {
    browserBtn.style.display = 'none';
    browserBtn.onclick = null;
  }
  // "Later" and "Close" are quiet links; "Cancel" stops a download.
  const cancel = document.getElementById('updateCancelBtn');
  cancel.textContent = cancelLabel || 'Close';
  cancel.className = 'btn ' + (cancelLabel === 'Cancel' ? 'btn-sec' : 'btn-quiet');
}

async function checkForUpdatesManual() {
  showUpdateModal();
  setUpdateModal({ icon: 'refresh', title: 'Checking for updates…', subtitle: 'Asking GitHub for the latest version.' });
  let r;
  try {
    r = await pywebview.api.check_for_updates();
  } catch(e) {
    console.warn('check_for_updates failed:', e);
    r = { error: WL.UPDATE_TEXT.checkFailed };
  }
  _noteUpdateChecked(r);
  if (r && r.update_available) { openUpdateModalFromCheck(r); return; }
  // The backend's sentence as it is ("Couldn't check for updates. Try
  // again later."), never "GitHub API returned HTTP 403".
  setUpdateModal(WL.updateCheckView(r));
}

// Settings, About: "Up to date" or "3.15.1 available", after a check.
function _noteUpdateChecked(r) {
  const chip = document.getElementById('aboutUpdateChip');
  if (!chip || !r || r.error) return;
  if (r.update_available) {
    chip.className = 'pill honey';
    chip.innerHTML = `<i></i>${escHtml(r.latest_version)} available`;
  } else {
    chip.className = 'pill ok';
    chip.innerHTML = '<i></i>Up to date';
  }
  chip.hidden = false;
  const again = document.getElementById('checkUpdateBtn');
  if (again) again.textContent = 'Check again';
}

function openUpdateModalFromCheck(r) {
  showUpdateModal();
  _lastUpdateInfo = r;

  // One click does it all: stream the installer in-app (with a progress
  // bar), run the platform installer and open Waffler again. A click on
  // Check for updates, or on Update in the Journal, is the person asking for
  // the update, so the dialog does not ask again. A release with no
  // installer for this computer opens its page instead.
  const v = WL.updateCheckView(r);
  if (v.kind === 'available' && v.primary.download) {
    startDownloadFlow(v.primary.download);
    return;
  }
  setUpdateModal({
    ...v,
    primaryLabel: v.primary.label,
    primaryHandler: v.primary.download
      ? () => startDownloadFlow(v.primary.download)
      : () => pywebview.api.open_url(v.primary.url),
  });
}

async function startDownloadFlow(url) {
  _downloadedPath = null;
  // On any failure, or a click on "Open download page", send people to the
  // website's download page: the friendly place to get the installer.
  setUpdateModal({
    ...WL.updatingView(_lastUpdateInfo, 'downloading'),
    showProgress: true,
    browserUrl: WL.DOWNLOAD_PAGE,
    cancelLabel: 'Cancel',
  });
  let r;
  try {
    r = await pywebview.api.start_update_download(url);
  } catch(e) {
    console.warn('start_update_download failed:', e);
    r = { ok: false };
  }
  if (r && r.ok) { startProgressPolling(); return; }
  setUpdateModal(WL.updateFailureView(r, WL.UPDATE_TEXT.downloadFailed));
}

function startProgressPolling() {
  stopProgressPolling();
  _updatePollTimer = setInterval(pollUpdateProgress, 300);
}
function stopProgressPolling() {
  if (_updatePollTimer) { clearInterval(_updatePollTimer); _updatePollTimer = null; }
}

async function pollUpdateProgress() {
  try {
    const p = await pywebview.api.get_update_progress();
    if (p.error) {
      // One plain sentence from the updater; its raw detail stays in the log.
      stopProgressPolling();
      setUpdateModal(WL.updateFailureView(p, WL.UPDATE_TEXT.downloadFailed));
      return;
    }
    if (p.total_bytes > 0) {
      const pct = Math.floor((p.bytes_downloaded / p.total_bytes) * 100);
      const mb = (p.bytes_downloaded / 1048576).toFixed(1);
      const totalMb = (p.total_bytes / 1048576).toFixed(1);
      document.getElementById('updateProgressBar').style.width = pct + '%';
      document.getElementById('updateProgressText').textContent = `${pct}% · ${mb} of ${totalMb} MB`;
    } else {
      document.getElementById('updateProgressText').textContent = 'Starting…';
    }
    if (p.done) {
      stopProgressPolling();
      _downloadedPath = p.path;
      // Straight on to the install: the person asked for the update when they
      // clicked. If a dictation is being recorded or cleaned up the install
      // waits for it (installDownloadedUpdate) and says so.
      installDownloadedUpdate();
    }
  } catch(e) {
    console.warn('progress poll failed', e);
  }
}

// While a dictation is recorded or cleaned up, Python says "busy" instead
// of restarting (that would lose it); the install is tried again shortly.
let _installRetryTimer = null;

async function installDownloadedUpdate() {
  if (!_downloadedPath) return;
  if (_installRetryTimer) { clearTimeout(_installRetryTimer); _installRetryTimer = null; }
  setUpdateModal(WL.updatingView(_lastUpdateInfo, 'installing'));
  let r;
  try {
    r = await pywebview.api.install_update_and_restart(_downloadedPath);
  } catch(e) {
    console.warn('install_update_and_restart failed:', e);
    r = { ok: false };
  }
  if (r && r.ok === false && r.busy) {
    const m = WL.splitMessage(r.error || '');
    setUpdateModal({ icon: 'settings', title: m.title, subtitle: m.subtitle, cancelLabel: 'Later' });
    _installRetryTimer = setTimeout(() => {
      _installRetryTimer = null;
      const modal = document.getElementById('updateModal');
      // Closing the dialog ("Later") stops the waiting.
      if (!modal || modal.style.display === 'none') return;
      installDownloadedUpdate();
    }, WL.INSTALL_RETRY_MS);
    return;
  }
  // Success usually never answers (Waffler quits); a refusal used to be
  // ignored and left "Installing…" on screen.
  if (r && r.ok === false) setUpdateModal(WL.updateFailureView(r, WL.UPDATE_TEXT.installFailed));
}

// ── The hotkey, wherever it is shown ──────────────────────────────────
// The saved hotkey drawn as keycaps: in the top bar's pill, the empty
// Journal and Settings, Hotkey. A platform default shows until
// loadHotkeyConfig() has the saved one.
function _capsHtml(keys, cls) {
  return WL.keycaps(keys, isMacPlatform)
    .map((c) => `<kbd class="kc${cls ? ' ' + cls : ''}">${c.icon === 'globe' ? WI.icon('globe') : ''}${escHtml(c.label)}</kbd>`)
    .join('<span class="plus" aria-hidden="true">+</span>');
}

// The hotkey as keycaps: in the top bar's status at Ready, on the empty
// Journal and in Settings' Hotkey section.
function renderHotkeyCaps(keys) {
  const name = hotkeyDisplayStr(keys);
  if ($hotkeyCaps) {
    $hotkeyCaps.innerHTML = _capsHtml(keys);
    $hotkeyCaps.setAttribute('aria-label', 'Hotkey: ' + name);
  }
  const empty = document.getElementById('emptyKeys');
  if (empty) empty.innerHTML = _capsHtml(keys);
  const big = document.getElementById('settingsHotkeyCaps');
  if (big) {
    big.innerHTML = _capsHtml(keys, 'kc-xl');
    big.setAttribute('aria-label', 'Your hotkey: ' + name);
  }
  const emptyKeys = document.getElementById('usageEmptyKeys');
  if (emptyKeys) emptyKeys.textContent = name;
  _renderHotkeyTest();
}

// ── Permissions (Mac) ────────────────────────────────────────────────

async function openAccessibilitySettings() {
  if (!window.pywebview || !window.pywebview.api) return;
  try {
    const result = await pywebview.api.open_accessibility_settings();
    if (!result.ok) {
      console.warn('open settings failed:', result.error);
      showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
    }
  } catch (e) {
    console.error("openAccessibilitySettings error:", e);
    showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
  }
}

async function openInputMonitoringSettings() {
  if (!window.pywebview || !window.pywebview.api) return;
  try {
    const result = await pywebview.api.open_input_monitoring_settings();
    if (!result.ok) {
      console.warn('open settings failed:', result.error);
      showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
    }
  } catch (e) {
    console.error("openInputMonitoringSettings error:", e);
    showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
  }
}

// Settings, Privacy and data: Logs. The bundle (no keys, no transcripts)
// is saved as a zip on the Desktop and shown in its folder. The toast used
// to print the whole path, which wrapped over several lines.
async function downloadLogs(btn) {
  if (!window.pywebview || !window.pywebview.api) return;
  if (btn) btn.disabled = true;
  try {
    const result = await pywebview.api.download_logs();
    if (result && result.ok) {
      const onDesktop = /[\\/]Desktop[\\/]/.test(String(result.path || ''));
      showToast(onDesktop ? 'Logs saved to your Desktop.' : 'Logs saved in your home folder.', 'success', 5000);
    } else {
      console.warn('download_logs failed:', result && result.error);
      showToast("Couldn't save the logs. Try again.", "error", 6000);
    }
  } catch (e) {
    console.error("downloadLogs error:", e);
    showToast("Couldn't save the logs. Try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Settings, Privacy and data: Delete all my data. It asks first, in the
// panel (it used to be a native confirm() box listing bullet points).
// "Delete my data" keeps the keys, words, snippets and settings and Waffler keeps
// running (src/privacy_data.py). Deleting the keys too is the full reset,
// asked about separately.
function askFactoryReset(ask) {
  const row = document.getElementById('resetRow');
  const confirmRow = document.getElementById('resetConfirm');
  if (!row || !confirmRow) return;
  confirmRow.hidden = !ask;
  const keys = document.getElementById('resetKeysConfirm');
  if (keys) keys.hidden = true;
  document.getElementById('resetAsk').hidden = !!ask;
  if (ask) document.getElementById('resetNo').focus();
}

function askFactoryResetKeys(ask) {
  const confirmRow = document.getElementById('resetConfirm');
  const keys = document.getElementById('resetKeysConfirm');
  if (!confirmRow || !keys) return;
  keys.hidden = !ask;
  confirmRow.hidden = !!ask;
  document.getElementById(ask ? 'resetKeysNo' : 'resetNo').focus();
}

async function deleteMyData(btn) {
  if (!window.pywebview || !window.pywebview.api) return;
  if (btn) btn.disabled = true;
  try {
    const r = await pywebview.api.delete_my_data();
    if (r && r.ok) {
      askFactoryReset(false);
      showToast('Deleted your history, usage, recordings and logs.', 'success', 5000);
      await refreshAll();
      await loadSettings();
    } else {
      showToast((r && r.error) || "Couldn't delete your data. Try again.", 'error', 6000);
    }
  } catch (e) {
    console.error('deleteMyData error:', e);
    showToast("Couldn't delete your data. Try again.", 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Settings, Privacy and data: Export history. A file on the Desktop in the
// chosen format, shown in its folder (src/history_export.py).
async function exportHistory(btn) {
  const api = _pageApi();
  if (!api || !api.export_history) return;
  const sel = document.getElementById('exportFormat');
  const fmt = WL.exportFormat(sel && sel.value);
  if (btn) btn.disabled = true;
  try {
    const r = await api.export_history(fmt);
    if (r && r.ok) showToast(WL.exportDone(r), 'success', 5000);
    else showToast(WL.exportFailed(r), 'error', 6000);
  } catch (e) {
    console.error('exportHistory error:', e);
    showToast(WL.exportFailed(null), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function factoryReset() {
  if (!window.pywebview || !window.pywebview.api) return;
  try {
    const result = await pywebview.api.factory_reset();
    if (result.ok) {
      showToast("Deleting your data. Waffler is closing.", "success");
      // App will quit automatically
    } else {
      console.warn('factory_reset failed:', result.error);
      showToast("Couldn't delete your data. Try again.", "error");
    }
  } catch (e) {
    console.error("factoryReset error:", e);
    showToast("Couldn't delete your data. Try again.", "error");
  }
}

// ── Hotkey Config ────────────────────────────────────────────────────

async function loadHotkeyConfig() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const config = await window.pywebview.api.get_hotkey_config();
    if (config.ok) {
      _currentHotkeyKeys = config.keys;
      _cancelLabel = WL.cancelLabel(config.cancel_display);
      renderHotkeyCaps(config.keys);
      renderSettingsHotkeyPresets();
    }
  } catch (e) {
    console.error("loadHotkeyConfig error:", e);
  }
}

// The hotkey dialog offers this platform's hotkeys only (logic.js
// hotkeyPresets). It used to show the Mac keys on Windows too; the backend
// refused them, the screen never checked, and the badge flashed green anyway.
// "Custom" is the dialog itself, so it isn't listed there.
function renderSettingsHotkeyPresets() {
  renderHotkeyPresetCards();
  const host = document.getElementById('settingsHotkeyPresets');
  if (!host) return;
  host.textContent = '';
  const cur = WL.hotkeyName(_currentHotkeyKeys, isMacPlatform);
  WL.hotkeyPresets(isMacPlatform).forEach((p) => {
    if (p.custom) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-sec btn-sm';
    b.innerHTML = `<span class="keys">${_capsHtml(p.keys)}</span>`;
    b.title = p.hint;
    b.setAttribute('aria-label', p.label);
    b.setAttribute('aria-pressed', String(p.label === cur));
    b.addEventListener('click', () => changeSettingsHotkey(p.keys));
    host.appendChild(b);
  });
}

// Settings, Hotkey: this computer's presets as cards, the current one
// chosen. Windows' "Custom…" opens the dialog that records any keys; while
// a hotkey of your own is in use, that card is chosen and shows it.
function renderHotkeyPresetCards() {
  const host = document.getElementById('hotkeyPresetCards');
  if (!host) return;
  const cur = WL.hotkeyName(_currentHotkeyKeys, isMacPlatform);
  const presets = WL.hotkeyPresets(isMacPlatform);
  const isPreset = presets.some((p) => !p.custom && p.label === cur);
  // Redrawn after a change: the focus stays on the same preset.
  const had = [...host.children].indexOf(document.activeElement);
  host.textContent = '';
  presets.forEach((p) => {
    const on = p.custom ? !isPreset : p.label === cur;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'preset' + (on ? ' on' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(on));
    // A radio group is one Tab stop, the chosen preset; the arrows move.
    b.tabIndex = on ? 0 : -1;
    if (p.custom) b.dataset.custom = '1';
    const top = p.custom
      ? (on ? `<span class="keys">${_capsHtml(_currentHotkeyKeys)}</span>` : '<span class="preset-cus">Custom…</span>')
      : `<span class="keys">${_capsHtml(p.keys)}</span>`;
    const hint = p.custom && on ? 'Your own keys. Choose to change them.' : p.hint.replace(' · ', ', ');
    b.innerHTML = `<span class="preset-top">${top}<span class="preset-r" aria-hidden="true"></span></span><span class="preset-h">${escHtml(hint)}</span>`;
    b.setAttribute('aria-label', `${p.custom ? (on ? cur + ', your own keys' : 'Custom') : p.label}. ${hint}`);
    b.addEventListener('click', () => (p.custom ? openHotkeyCapture() : (on ? null : changeSettingsHotkey(p.keys))));
    host.appendChild(b);
  });
  if (had >= 0 && host.children[had]) host.children[had].focus();
}

// The arrow keys, Home and End move round the presets and choose the one
// they land on, as in the theme picker. Custom opens a dialog, so landing
// on it only moves the focus; Space or Enter opens it.
document.addEventListener('DOMContentLoaded', () => {
  const host = document.getElementById('hotkeyPresetCards');
  if (!host) return;
  host.addEventListener('keydown', (e) => {
    const radios = [...host.querySelectorAll('button[role="radio"]')];
    const i = radios.indexOf(document.activeElement);
    const to = WL.radioMove(i, radios.length, e.key);
    if (to < 0) return;
    e.preventDefault();
    radios.forEach((r, n) => { r.tabIndex = n === to ? 0 : -1; });
    radios[to].focus();
    if (!radios[to].dataset.custom && radios[to].getAttribute('aria-checked') !== 'true') radios[to].click();
  });
});

// Settings, Hotkey: holding the hotkey here is heard by Waffler's own
// listener, which turns the status to Recording (waffler_status). The keys
// light while held, and the line says it worked. A real dictation starts,
// as it would anywhere; with nothing to paste into it goes to the Journal.
let _hotkeyHeard = false;
let _hotkeyHeld = false;

function _renderHotkeyTest() {
  const text = document.getElementById('hkTestText');
  const box = document.getElementById('hkTest');
  if (text) text.textContent = WL.hotkeyTestLine(_hotkeyHeard, _currentHotkeyKeys, isMacPlatform);
  if (box) box.classList.toggle('is-heard', _hotkeyHeard);
  document.querySelectorAll('#settingsHotkeyCaps .kc').forEach((k) => k.classList.toggle('held', _hotkeyHeld));
}

function _hotkeyTestStatus(cls) {
  const held = cls === 'listening' || cls === 'paused';
  if (held && !_hotkeyHeld && _currentPage === 'settings' && _settingsSection === 'hotkey') _hotkeyHeard = true;
  _hotkeyHeld = held;
  _renderHotkeyTest();
}

function _showSettingsHotkeyError(msg) {
  const el = document.getElementById('settingsHotkeyError');
  if (!el) return;
  el.textContent = msg || '';
  el.hidden = !msg;
}

// Everything that shows the hotkey follows a successful save: the top bar,
// Settings, and the wizard's keycaps, tiles and Try-it chips. The keys come
// from the backend's answer, which may be normalised (Windows names are
// always saved as Win, Ctrl, Alt, Shift).
async function _onHotkeySaved(result) {
  if (Array.isArray(result.keys) && result.keys.length) {
    _currentHotkeyKeys = result.keys.slice();
    _currentWizardHotkey = result.keys.slice();
    wizRenderHotkey(result.keys);
  }
  await loadHotkeyConfig();
  loadShortcuts();
}

// One hotkey save at a time: each one makes the keyboard listener again, and a
// double-click used to start a second before the first had finished.
const _hotkeySaving = WL.savingGate();

// The preset buttons show that they wait while a save is under way. aria-disabled
// rather than disabled: a disabled button loses the focus, and the cards are
// redrawn with the focus kept on the preset the person was on.
function _hotkeyButtonsBusy(on) {
  document.querySelectorAll('#hotkeyPresetCards button, #settingsHotkeyPresets button, #wizHotkeyPresetList button')
    .forEach((b) => { if (on) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled'); });
}

// A preset in the hotkey dialog: saved at once, and the dialog closes.
async function changeSettingsHotkey(keys) {
  if (!_hotkeySaving.enter()) return;
  _hotkeyButtonsBusy(true);
  try {
    let result;
    try {
      result = await window.pywebview.api.save_hotkey_config(keys);
    } catch (e) {
      console.error("Failed to change hotkey:", e);
      result = { ok: false, error: "Couldn't change the hotkey. Try again." };
    }
    if (!result || !result.ok) {
      const msg = (result && result.error) || "Couldn't change the hotkey. Try again.";
      _showSettingsHotkeyError(msg);
      const errEl = document.getElementById("hotkeyError");
      if (errEl) { errEl.textContent = msg; errEl.style.display = "block"; }
      return;
    }
    _showSettingsHotkeyError('');
    closeHotkeyCapture();
    await _onHotkeySaved(result);
    showToast(`Hotkey is now ${result.display || WL.hotkeyName(result.keys, isMacPlatform)}`, 'success');
  } finally {
    _hotkeySaving.leave();
    _hotkeyButtonsBusy(false);
  }
}

function _showCaptured(keys) {
  const el = document.getElementById("hotkeyCaptureKeys");
  if (el) el.innerHTML = keys.length ? _capsHtml(keys, 'kc-xl') : '';
}

// The dialog sets the dictation hotkey, or (from Settings, Transforms) the
// Transform shortcut, or (Settings, Command mode) the keys held to give a
// command, or (Settings, Shortcuts) Paste last, Copy last or the cancel key:
// `_captureTarget` says which, and what it offers follows.
let _captureTarget = 'dictation';

function openHotkeyCapture(target) {
  _captureTarget = target === 'transform' || target === 'command' ? target : 'dictation';
  if (WL.shortcutIsTarget(target)) _captureTarget = target;       // Paste last, Copy last, cancel too
  const forTransform = _captureTarget !== 'dictation';      // an action shortcut: no default, no presets
  _capturedKeys.clear();
  _lastCapturedKeys = _captureTarget === 'transform' ? WL.transformView(_lastSettings, isMacPlatform).hotkey.slice()
    : (_captureTarget === 'command' ? WL.commandView(_lastSettings, isMacPlatform).hotkey.slice() : [..._currentHotkeyKeys]);
  if (_captureTarget !== 'dictation' && _captureTarget !== 'transform' && _captureTarget !== 'command') {
    const row = WL.shortcutRows(_shortcuts, isMacPlatform).find((r) => r.id === _captureTarget);
    _lastCapturedKeys = row ? row.keys.slice() : [];
  }
  _showCaptured(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
  const text = WL.hotkeyDialogText(_captureTarget, isMacPlatform);
  const title = document.getElementById('hotkeyModalTitle');
  const sub = document.getElementById('hotkeyModalSub');
  if (title) title.textContent = text.title;
  if (sub) sub.textContent = text.sub;
  // An action shortcut (Transform, Command) has no default and no presets to pick from.
  const reset = document.getElementById('hotkeyResetBtn');
  if (reset) reset.hidden = forTransform;
  // The cancel key has a default: Esc.
  if (reset && _captureTarget === 'cancel') reset.hidden = false;
  // Setup has its own list of choices ("Pick another key").
  const presets = document.getElementById('hotkeyPresetRow');
  if (presets) presets.hidden = forTransform || (typeof _wizardVisible === 'function' && _wizardVisible());
  renderSettingsHotkeyPresets();
  _announceCaptured('');
  _liveSeq += 1;                           // an answer to an earlier check is not this dialog's
  _tellShortcutRecording(true);
  const modal = document.getElementById("hotkeyModal");
  modal.style.display = "flex";
  modalOpened(modal);
  document.addEventListener("keydown", _onCaptureKeyDown);
  document.addEventListener("keyup", _onCaptureKeyUp);
}

// While the dialog is open the app's shortcuts neither run nor keep their keys
// from it, so a combination in use can be pressed to be replaced. It closes the
// moment the dialog does (and times out on its own if this page is reloaded).
let _recordingChain = Promise.resolve();

function _tellShortcutRecording(active) {
  // One after the other, so "closed" can never overtake "open" on its way to the app.
  _recordingChain = _recordingChain.then(async () => {
    try {
      if (window.pywebview && pywebview.api && pywebview.api.shortcut_recording) await pywebview.api.shortcut_recording(!!active);
    } catch (e) {
      console.warn('shortcut_recording error:', e);
    }
  });
}

function closeHotkeyCapture() {
  const modal = document.getElementById("hotkeyModal");
  modal.style.display = "none";
  clearTimeout(_liveTimer);
  _liveSeq += 1;
  _tellShortcutRecording(false);
  document.removeEventListener("keydown", _onCaptureKeyDown);
  document.removeEventListener("keyup", _onCaptureKeyUp);
  _capturedKeys.clear();
  _captureTarget = 'dictation';
  modalClosed(modal);
}

// Screen readers hear the keys once they are let go ("Ctrl + Shift"), not
// every key on the way down.
function _announceCaptured(text) {
  const live = document.getElementById('hotkeyCaptureLive');
  if (live) live.textContent = text;
}

function _onCaptureKeyDown(e) {
  if (e.key === 'Escape' && _captureTarget !== 'cancel') { e.preventDefault(); closeHotkeyCapture(); return; }
  // Tab and Enter still reach the dialog's buttons.
  if (e.key === 'Tab' || (e.key === 'Enter' && !_capturedKeys.size)) return;
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (!id) return;
  _capturedKeys.add(id);
  _lastCapturedKeys = [..._capturedKeys];
  _showCaptured(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
  _liveSeq += 1;
  // A Mac sends no key-up for a letter while Command is held, so the check
  // does not wait for it: it runs a moment after the last key went down.
  clearTimeout(_liveTimer);
  _liveTimer = setTimeout(_liveCheckCaptured, 600);
}

function _onCaptureKeyUp(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (id) _capturedKeys.delete(id);
  if (!_capturedKeys.size && _lastCapturedKeys.length && id) {
    _announceCaptured(WL.hotkeyName(_lastCapturedKeys, isMacPlatform));
    _liveCheckCaptured();
  }
}

// When the keys are let go, ask the app whether it would accept them (the same
// rules as a save) and say so in the dialog, before Save is pressed. Only the
// latest answer counts: more keys, or closing the dialog, drops a slower one.
let _liveSeq = 0;
let _liveTimer = null;

async function _liveCheckCaptured() {
  clearTimeout(_liveTimer);
  const seq = ++_liveSeq;
  const keys = _lastCapturedKeys.slice();
  const errEl = document.getElementById("hotkeyError");
  if (!errEl || !keys.length || !(window.pywebview && pywebview.api && pywebview.api.check_shortcut)) return;
  let r = null;
  try {
    r = await pywebview.api.check_shortcut(_captureTarget, JSON.stringify(keys));
  } catch (err) {
    console.warn('check_shortcut error:', err);
    return;
  }
  if (seq !== _liveSeq || !r) return;
  if (r.ok === false && r.error) {
    errEl.textContent = r.error;
    errEl.style.display = "block";
  } else {
    errEl.style.display = "none";
  }
}

function resetHotkeyDefault() {
  _lastCapturedKeys = _captureTarget === 'cancel' ? ['esc'] : WL.defaultHotkey(isMacPlatform);
  _capturedKeys.clear();
  _showCaptured(_lastCapturedKeys);
  _announceCaptured(WL.hotkeyName(_lastCapturedKeys, isMacPlatform));
  document.getElementById("hotkeyError").style.display = "none";
}

async function saveHotkeyCapture() {
  const keys = _lastCapturedKeys;
  const errEl = document.getElementById("hotkeyError");
  const fail = (msg) => { errEl.textContent = msg; errEl.style.display = "block"; };
  if (!keys.length) { fail("Hold the keys you want, then click Save."); return; }
  if (_captureTarget === 'transform') { await saveTransformHotkey(keys, fail); return; }
  if (_captureTarget === 'command') { await saveCommandHotkey(keys, fail); return; }
  if (WL.shortcutIsTarget(_captureTarget)) { await saveShortcutCapture(_captureTarget, keys, fail); return; }
  // The backend decides (src/hotkey_rules.py) and says why in one sentence.
  if (!_hotkeySaving.enter()) return;
  let result;
  try {
    result = await window.pywebview.api.save_hotkey_config(JSON.stringify(keys));
  } catch (e) {
    console.error("saveHotkeyCapture error:", e);
    result = { ok: false, error: "Couldn't change the hotkey. Try again." };
  } finally {
    _hotkeySaving.leave();
  }
  if (!result || !result.ok) { fail((result && result.error) || "Couldn't change the hotkey. Try again."); return; }
  closeHotkeyCapture();
  _showSettingsHotkeyError('');
  await _onHotkeySaved(result);
  // Opened from setup's "Pick another key": the practice listens for it
  // (wizHotkeyChanged says so).
  if (_wizardStep === 'try' && _wizardVisible()) { wizHotkeyChanged(result); return; }
  showToast(`Hotkey is now ${result.display || WL.hotkeyName(result.keys, isMacPlatform)}`, 'success');
}

// ── Settings, Shortcuts ──────────────────────────────────────────────────
// Every shortcut on one page (src/shortcut_settings.py): a row each for Hold to
// dictate, Hands-free, Cancel, Transform, Command mode, Paste last and Copy
// last. The rows, the rules and the refused combinations come from the app
// (get_shortcuts); the page holds no rule of its own. A shortcut is set in the
// hotkey dialog (openHotkeyCapture), which asks the app whether the keys pass
// before Save, and is saved through save_shortcut, the same checks as the
// Transforms and Command mode sections make for their own.
let _shortcuts = null;       // the last get_shortcuts() answer
let _scSeq = 0;              // the newest request; an older answer is dropped
let _scFocus = '';           // the row whose button gets focus after the next draw

function _scError(msg) {
  const el = document.getElementById('shortcutsError');
  if (!el) return;
  el.textContent = msg || '';
  el.hidden = !msg;
}

async function loadShortcuts() {
  const seq = ++_scSeq;
  let r = null;
  try {
    if (window.pywebview && pywebview.api && pywebview.api.get_shortcuts) r = await pywebview.api.get_shortcuts();
  } catch (e) {
    console.warn('get_shortcuts error:', e);
  }
  if (seq !== _scSeq) return;
  if (r && r.ok) {
    _shortcuts = r;
    _scError('');
    const cancel = WL.shortcutRows(r, isMacPlatform).find((row) => row.id === 'cancel');
    if (cancel && cancel.set) _cancelLabel = WL.cancelLabel(cancel.shown);
  } else if (!_shortcuts) {
    _scError(WL.SHORTCUT_TEXT.loadFailed);
  }
  renderShortcuts();
}

function _shortcutRowHtml(r) {
  const id = escHtml(r.id);
  const caps = r.set
    ? `<span class="keys sc-caps" role="img" aria-label="${escHtml(r.capsLabel)}">${_capsHtml(r.keys)}</span>`
    : '<span class="sc-unset">Not set</span>';
  const described = `sc-${id}-t${r.conflict ? ` sc-${id}-c` : ''}`;
  const buttons = r.fixed ? '' : `<div class="sc-acts">
      <button type="button" class="btn btn-sec btn-sm" data-act="change" data-id="${id}" aria-label="${escHtml(r.changeLabel)}" aria-describedby="${described}"${r.canChange ? '' : ' disabled'}>${r.changeText}</button>${r.canClear ? `
      <button type="button" class="btn btn-quiet btn-sm" data-act="clear" data-id="${id}" aria-label="${escHtml(r.clearLabel)}" aria-describedby="${described}">${escHtml(r.clearText)}</button>` : ''}
    </div>`;
  return `<div class="row sc-row${r.off ? ' is-off' : ''}" role="listitem" data-id="${id}">
    <div class="row-main">
      <div class="row-t" id="sc-${id}-t">${escHtml(r.label)}</div>
      <div class="row-d">${escHtml(r.description)}</div>
      <div class="sc-keyline">${caps}</div>${r.conflict ? `
      <p class="s-error sc-conflict" id="sc-${id}-c">${escHtml(r.conflict)}</p>` : ''}${r.needs ? `
      <p class="sc-needs"><button type="button" class="link-btn" data-act="enable" data-sec="${escHtml(r.needsSection)}" data-id="${id}">${escHtml(r.needs)}</button></p>` : ''}
    </div>${buttons}
  </div>`;
}

// The "Double-tap to go hands-free" switch, a row of its own right under
// Hands-free (src/double_tap.py). Not one of the shortcut rows: it has no keys.
function _doubleTapRowHtml() {
  const v = WL.doubleTapView(_shortcuts);
  return `<div class="row sc-row sc-dt" role="listitem" data-id="double_tap">
    <div class="row-main">
      <div class="row-t"><label for="doubleTapToggle">${escHtml(v.label)}</label></div>
      <div class="row-d" id="doubleTapDesc">${escHtml(v.desc)}</div>
    </div>
    <input type="checkbox" class="tog" role="switch" id="doubleTapToggle" aria-describedby="doubleTapDesc"${v.on ? ' checked' : ''}>
  </div>`;
}

// Save the switch; a refused save puts it back.
async function setDoubleTap(on) {
  const ok = await saveSetting('double_tap_handsfree', !!on);
  if (ok) {
    if (_shortcuts) _shortcuts.double_tap_handsfree = !!on;
    if (_lastSettings) _lastSettings.double_tap_handsfree = !!on;
  } else {
    const tog = document.getElementById('doubleTapToggle');
    if (tog) tog.checked = !on;
  }
}

function renderShortcuts() {
  const list = document.getElementById('shortcutsList');
  if (!list) return;
  const rows = WL.shortcutRows(_shortcuts, isMacPlatform);
  list.hidden = !rows.length;
  list.innerHTML = rows.map((r) => _shortcutRowHtml(r) + (r.id === 'hands_free' ? _doubleTapRowHtml() : '')).join('');
  if (_scFocus) {
    const target = list.querySelector(`[data-act="change"][data-id="${_scFocus}"]:not(:disabled)`)
      || list.querySelector(`[data-act="enable"][data-id="${_scFocus}"]`);
    _scFocus = '';
    if (target) target.focus();
  }
  renderShortcutRules();
  // The Transforms and Command mode sections say the same sentence about their own shortcut.
  [['transform', 'transformsConflict'], ['command', 'commandConflict']].forEach(([id, elId]) => {
    const node = document.getElementById(elId);
    const row = rows.find((r) => r.id === id);
    if (!node) return;
    node.textContent = row ? row.conflict : '';
    node.hidden = !(row && row.conflict);
  });
}

// One listener for every row's buttons (the rows are drawn again after each change).
(function wireShortcuts() {
  const list = document.getElementById('shortcutsList');
  if (!list) return;
  list.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b || b.disabled) return;
    const id = b.getAttribute('data-id');
    const act = b.getAttribute('data-act');
    if (act === 'change') openHotkeyCapture(id === 'dictation' ? undefined : id);
    else if (act === 'clear') clearShortcut(id);
    else if (act === 'enable' && b.getAttribute('data-sec')) showSettingsSection(b.getAttribute('data-sec'));
  });
  list.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'doubleTapToggle') setDoubleTap(e.target.checked);
  });
})();

function _shortcutLabel(id) {
  const row = WL.shortcutRows(_shortcuts, isMacPlatform).find((r) => r.id === id);
  return row ? row.label : 'Shortcut';
}

function renderShortcutRules() {
  const v = WL.shortcutRulesView(_shortcuts);
  const fill = (id, items) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = items.map((t) => `<li>${escHtml(t)}</li>`).join('');
  };
  fill('shortcutsRuleLines', v.lines);
  fill('shortcutsReserved', v.reserved);
}

function toggleShortcutRules() {
  const btn = document.getElementById('shortcutsRulesBtn');
  const box = document.getElementById('shortcutsRules');
  if (!btn || !box) return;
  const open = box.hidden;
  box.hidden = !open;
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

// Save from the dialog: Paste last, Copy last or the cancel key (Transform and
// Command mode keep their own saves, which also refresh their sections).
async function saveShortcutCapture(target, keys, fail) {
  let result;
  try {
    result = await window.pywebview.api.save_shortcut(target, JSON.stringify(keys));
  } catch (e) {
    console.error('saveShortcutCapture error:', e);
    result = { ok: false, error: WL.SHORTCUT_TEXT.saveFailed };
  }
  if (!result || !result.ok) { fail((result && result.error) || WL.SHORTCUT_TEXT.saveFailed); return; }
  const label = _shortcutLabel(target);
  closeHotkeyCapture();
  _scError('');
  _scFocus = target;
  await loadShortcuts();
  showToast(WL.shortcutSaved(label, result.display, false, target), 'success', 3500);
}

async function clearShortcut(id) {
  const label = _shortcutLabel(id);
  let result = null;
  try { result = await window.pywebview.api.save_shortcut(id, '[]'); } catch (e) { result = null; }
  if (!result || !result.ok) { _scError((result && result.error) || WL.SHORTCUT_TEXT.clearFailed); return; }
  _scError('');
  _scFocus = id;
  await loadShortcuts();
  if (id === 'transform') refreshTransformSettings();
  if (id === 'command') refreshCommandSettings();
  showToast(id === 'cancel' ? WL.shortcutSaved(label, 'Esc', false, id) : WL.shortcutSaved(label, '', true, id), 'success', 3500);
}

function askShortcutsReset(ask) {
  const row = document.getElementById('shortcutsResetRow');
  const confirmRow = document.getElementById('shortcutsResetConfirm');
  if (!row || !confirmRow) return;
  document.getElementById('shortcutsResetAskText').textContent = WL.SHORTCUT_TEXT.resetAsk;
  document.getElementById('shortcutsResetBody').textContent = WL.SHORTCUT_TEXT.resetBody;
  confirmRow.hidden = !ask;
  document.getElementById('shortcutsResetAsk').hidden = !!ask;
  const to = document.getElementById(ask ? 'shortcutsResetNo' : 'shortcutsResetAsk');
  if (to) to.focus();
}

async function resetShortcuts(btn) {
  if (btn) btn.disabled = true;
  let r = null;
  try { r = await window.pywebview.api.reset_shortcuts(); } catch (e) { r = null; }
  if (btn) btn.disabled = false;
  if (!r || !r.ok) { _scError((r && r.error) || WL.SHORTCUT_TEXT.resetFailed); return; }
  _scError('');
  _shortcuts = r;
  askShortcutsReset(false);
  renderShortcuts();
  // The sections that show the same shortcuts follow.
  loadHotkeyConfig();
  refreshTransformSettings();
  refreshCommandSettings();
  showToast(WL.SHORTCUT_TEXT.resetDone, 'success', 3500);
}

// ── Journal: loading ─────────────────────────────────────────────────
// The newest page and the counts. Older pages load as you scroll.
async function refreshAll() {
  try {
    if (window.pywebview && window.pywebview.api) {
      const s = await window.pywebview.api.get_stats();
      stats = s || stats;
      renderStats();
      await renderFeed();
    }
  } catch (e) {
    console.warn('API not ready yet:', e);
  }
}

// The first page for the current search, drawn from scratch. Searching
// asks the backend (get_history's query), so a search covers the whole
// Journal, not just what has been drawn.
async function renderFeed() {
  const seq = ++_histSeq;
  const query = _searchText;
  _histLoading = true;
  let page = [];
  try {
    page = (await window.pywebview.api.get_history(PAGE_SIZE, 0, query)) || [];
  } catch (e) {
    console.warn('get_history failed:', e);
  }
  if (seq !== _histSeq) return;          // a newer search took over
  _histLoading = false;
  history = page;
  _histDone = page.length < PAGE_SIZE;
  drawFeed();
  const said = document.getElementById('searchStatus');
  if (said) said.textContent = WL.searchAnnouncement(query, page.length, _histDone);
}

// Draw `history` from scratch: first run, a search with no matches, or the
// entries grouped by day (logic.js feedView). A search with no matches used
// to show "Your journal is empty."
function drawFeed() {
  _histTotal = Math.max(Number(stats.entries) || 0, history.length);
  const view = WL.feedView(_histTotal, _searchText, history.length);
  $empty.hidden = view.kind !== 'empty';
  // First run: the title stays, the search has nothing to search, and the
  // first entry's row is under today's heading. The margin stays too, with
  // today's real numbers.
  const search = document.querySelector('#statStrip .j-search-wrap');
  if (search) search.hidden = view.kind === 'empty';
  // The first-entry row has its own Teach it your words link.
  const layout = document.getElementById('journalLayout');
  if (layout) layout.classList.toggle('is-first', view.kind === 'empty');
  if (view.kind === 'empty') {
    const day = document.getElementById('emptyDay');
    const now = new Date();
    const key = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    if (day) day.textContent = WL.dayHeading(key, now).date;
  }
  $noMatch.hidden = view.kind !== 'no_match';
  if (view.kind === 'no_match') {
    const label = document.getElementById('noMatchLabel');
    if (label && view.label) label.textContent = view.label;
  }
  $feed.textContent = '';
  _feedDays = { first: '', last: '' };
  if (view.kind === 'list') _appendCards(history);
  _updateMore();
  _updateSearchPlaceholder();
}

function _updateSearchPlaceholder() {
  const searchEl = document.getElementById('searchInput');
  if (!searchEl) return;
  const total = Number(stats.entries) || 0;
  searchEl.placeholder = total ? `Search ${WL.formatCount(total)} ${total === 1 ? 'entry' : 'entries'}` : 'Search';
}

function _updateMore() {
  if (!$feedMore) return;
  $feedMore.hidden = _histDone || !history.length;
  const btn = document.getElementById('feedMoreBtn');
  if (btn) { btn.disabled = _histLoading; btn.textContent = _histLoading ? 'Loading…' : 'Show older entries'; }
}

// The next page, added below what is there (nothing is redrawn).
async function loadMoreEntries() {
  if (_histLoading || _histDone || !window.pywebview || !window.pywebview.api) return;
  const seq = _histSeq;
  _histLoading = true;
  _updateMore();
  let page = [];
  try {
    // After the oldest entry held, by its id (steady when entries are added
    // or deleted meanwhile), or by count for one that predates ids.
    const at = WL.pageCursor(history);
    page = (await window.pywebview.api.get_history(PAGE_SIZE, at.offset, _searchText, at.beforeId)) || [];
  } catch (e) {
    console.warn('get_history failed:', e);
  }
  _histLoading = false;
  if (seq !== _histSeq) return;
  history.push(...page);
  _histDone = page.length < PAGE_SIZE;
  _appendCards(page);
  _updateMore();
}

// Older entries load when the end of the list comes near.
function _watchFeedEnd() {
  if (!$feedMore || !('IntersectionObserver' in window)) return;
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) loadMoreEntries();
  }, { root: $main, rootMargin: '0px 0px 800px 0px' });
  io.observe($feedMore);
}

// ── Journal: drawing ─────────────────────────────────────────────────
function _dayRow(key) {
  const d = WL.dayHeading(key, new Date());
  const row = document.createElement('div');
  row.className = 'j-date-divider';
  row.dataset.day = key;
  // A heading per day, so a screen reader can jump between days.
  row.setAttribute('role', 'heading');
  row.setAttribute('aria-level', '2');
  row.innerHTML = `<span class="j-date-month">${escHtml(d.label)}</span><span class="j-date-day">${escHtml(d.date)}</span><span class="j-date-line" aria-hidden="true"></span>`;
  return row;
}

// Cards at the bottom, each under its day's row.
function _appendCards(items) {
  const frag = document.createDocumentFragment();
  items.forEach((item) => {
    const key = WL.dayKey(item.timestamp);
    if (key !== _feedDays.last || !_feedDays.first) {
      frag.appendChild(_dayRow(key));
      _feedDays.last = key;
      if (!_feedDays.first) _feedDays.first = key;
    }
    frag.appendChild(makeCard(item, false));
  });
  $feed.appendChild(frag);
}

// One new card at the top, under today's row (made if it isn't there).
function _prependCard(item) {
  const key = WL.dayKey(item.timestamp);
  const card = makeCard(item, true);
  const firstRow = $feed.querySelector('.j-date-divider');
  if (firstRow && firstRow.dataset.day === key) {
    firstRow.after(card);
  } else {
    $feed.prepend(_dayRow(key), card);
    _feedDays.first = key;
    if (!_feedDays.last) _feedDays.last = key;
  }
}

// A card goes; so does its day's row if it was the last card that day.
function _removeCard(el) {
  const prev = el.previousElementSibling;
  const next = el.nextElementSibling;
  el.remove();
  if (prev && prev.classList.contains('j-date-divider') && (!next || next.classList.contains('j-date-divider'))) {
    prev.remove();
  }
}

async function copyItem(text, btnEl) {
  try {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.copy_item(text);
    } else {
      await navigator.clipboard.writeText(text);
    }
    btnEl.classList.add('copied', 'is-done');
    btnEl.innerHTML = WI.icon('check') + '<span>Copied</span>';
    const label = btnEl.getAttribute('aria-label');
    if (label) btnEl.setAttribute('aria-label', 'Copied');
    const idle = btnEl.dataset.idle || 'Copy';
    setTimeout(() => {
      btnEl.classList.remove('copied', 'is-done');
      btnEl.innerHTML = WI.icon('copy') + `<span>${escHtml(idle)}</span>`;
      if (label) btnEl.setAttribute('aria-label', label);
    }, 2500);
  } catch (e) {
    showToast("Couldn't copy that. Try again.", 'error');
  }
}

async function _refreshStats() {
  try {
    stats = (await window.pywebview.api.get_stats()) || stats;
    renderStats();
    _updateSearchPlaceholder();
  } catch (_) {}
}

// ── Called by Python after each transcription ─────────────────────────
// The new entry is added at the top; nothing else is redrawn (every card
// used to be rebuilt, about half a second at 3,300 entries).
window.waffler_refresh = function(newItem) {
  if (!newItem) { refreshAll(); return; }
  // Its recording was kept a moment ago, before it was turned into text, so
  // it can be redone now (get_history says the same for a page).
  if (newItem.audio && !newItem.failed) newItem.can_redo = true;
  const first = !history.length && !(Number(stats.entries) > 0);
  stats.entries = (Number(stats.entries) || 0) + 1;
  // During a search, it joins the list only if it matches.
  const shown = !_searchQuery ||
    (newItem.kind === 'transform'
      ? ((newItem.after || '') + ' ' + (newItem.before || ''))
      : (newItem.kind === 'command'
        ? [newItem.instruction, newItem.after, newItem.before, newItem.query].map((x) => x || '').join(' ')
        : ((newItem.styled || '') + ' ' + (newItem.text || '')))).toLowerCase().includes(_searchQuery);
  if (shown) {
    history.unshift(newItem);
    if (first || !$noMatch.hidden) drawFeed();
    else _prependCard(newItem);
  }
  _trimRedo();
  if (window.pywebview && window.pywebview.api) _refreshStats();
  // A Not sent card is not a finished transcription. A rewrite or a command
  // has said what it did on the overlay already (with its own "Show in Journal").
  if (newItem.failed) showToast('Not sent. The recording is saved in the Journal.', 'error');
  else if (newItem.kind !== 'transform' && newItem.kind !== 'command') showToast('Added to your Journal.', 'success');
};

// ── Called by Python for status updates ──────────────────────────────
// Only the state class changes (the pill keeps its own classes; an old
// `className = ...` assignment wiped them). A version that touched a
// missing #recordingOverlay threw before the "Done" to "Ready" reset was
// scheduled, so the label stuck on "Done".
let _statusResetTimer = null;
// The pill shows the seconds while you record ("0:04") and while the
// dictation is cleaned up ("4 s"), so working and stuck no longer look the
// same. One timer, only in those states; it stops on the next status.
let _statusTimer = null;
let _recordingStarted = 0;   // when this recording began (0: not recording)
let _recordingPausedAt = 0;  // while paused: when the pause began

function _showStatus(view) {
  if (!$statusInd || !$statusText) return;
  $statusInd.classList.remove(...WL.STATUS_CLASSES);
  $statusInd.classList.add(view.cls);
  $statusText.textContent = view.label;
  if ($statusTime) $statusTime.textContent = '';
  $statusInd.removeAttribute('title');
}

function _recordingSeconds() {
  const now = _recordingPausedAt || Date.now();
  return _recordingStarted ? (now - _recordingStarted) / 1000 : 0;
}

window.waffler_status = function(status) {
  clearTimeout(_statusResetTimer);
  _statusResetTimer = null;
  clearInterval(_statusTimer);
  _statusTimer = null;
  const view = WL.statusView(status);
  _showStatus(view);
  _hotkeyTestStatus(view.cls);
  if (view.cls === 'listening') {
    // A new recording starts the clock; coming back from a pause carries on.
    if (!_recordingStarted) _recordingStarted = Date.now();
    else if (_recordingPausedAt) _recordingStarted += Date.now() - _recordingPausedAt;
    _recordingPausedAt = 0;
    const tick = () => {
      const t = WL.recordingTime(_recordingSeconds());
      if ($statusTime) $statusTime.textContent = t;
      _journalLive('listening', t);
    };
    tick();
    _statusTimer = setInterval(tick, 1000);
  } else if (view.cls === 'paused') {
    if (_recordingStarted && !_recordingPausedAt) _recordingPausedAt = Date.now();
    if ($statusTime) $statusTime.textContent = WL.recordingTime(_recordingSeconds());
    _journalLive('paused', WL.recordingTime(_recordingSeconds()));
  } else {
    _recordingStarted = 0;
    _recordingPausedAt = 0;
    _journalLive(view.cls, '');
  }
  if (view.cls === 'processing') {
    const started = Date.now();
    _statusTimer = setInterval(() => {
      const secs = (Date.now() - started) / 1000;
      if ($statusTime) $statusTime.textContent = WL.workingTime(secs);
      $statusInd.title = WL.workingLabel(view.label, secs);
      _journalLive('processing', WL.workingTime(secs));
    }, 1000);
  }
  // Done, Cancelled, Not sent and the error show for a moment, then Ready.
  const resetMs = WL.statusResetMs(view.cls);
  if (resetMs) {
    _statusResetTimer = setTimeout(() => {
      _statusResetTimer = null;
      _showStatus(WL.statusView('idle'));
    }, resetMs);
  }
};

// The Journal's own line while you dictate: Recording, the time, and Esc
// cancels (as it does while recording; app.py _on_hotkey_cancel). The
// words arrive as a new entry when the dictation is done.
const _LIVE_TEXT = {
  listening: 'Recording. The words arrive here when you finish.',
  paused: 'Paused.',
  processing: 'Turning it into text.',
};
function _journalLive(cls, time) {
  const box = document.getElementById('journalLive');
  if (!box) return;
  const text = _LIVE_TEXT[cls];
  box.hidden = !text;
  if (!text) return;
  box.dataset.state = cls;
  const t = document.getElementById('journalLiveText');
  const tm = document.getElementById('journalLiveTime');
  const esc = document.getElementById('journalLiveEsc');
  if (t) t.textContent = text;
  if (tm) tm.textContent = time || '';
  if (esc) {
    esc.hidden = cls === 'processing';
    const caps = esc.querySelector('kbd');
    if (caps) caps.textContent = _cancelLabel;
  }
}

// ── Render ─────────────────────────────────────────────────────────────
function renderStats() {
  $statWords.textContent = WL.formatCount(stats.today_words);
  $statCount.textContent = WL.formatCount(stats.today_count);
  const countLabel = document.getElementById('statCountLabel');
  if (countLabel) countLabel.textContent = Number(stats.today_count) === 1 ? 'dictation' : 'dictations';

  // This week: the last seven days of the same counts (get_stats
  // daily_words), today in ink.
  const week = document.getElementById('journalWeek');
  const days = document.getElementById('journalWeekDays');
  if (week && days) {
    const bars = WL.weekBars(stats.daily_words, stats.daily_start);
    week.innerHTML = bars.map((b) => `<i class="${b.today ? 'is-today' : ''}${b.words ? '' : ' is-zero'}" style="height:${b.height}%" title="${WL.formatCount(b.words)} words"></i>`).join('');
    days.innerHTML = bars.map((b) => `<span>${escHtml(b.initial)}</span>`).join('');
    week.setAttribute('aria-label', 'Words a day, the last seven days: ' + bars.map((b) => WL.formatCount(b.words)).join(', '));
  }
  // This week against last, in one calm line; nothing when there is nothing
  // to compare (WL.weekView, from get_stats).
  const weekLine = document.getElementById('journalWeekLine');
  if (weekLine) {
    const line = WL.weekView(stats).rail;
    weekLine.textContent = line;
    weekLine.hidden = !line;
  }

  // Days in a row: hidden at 0, so the first day doesn't say "0 days".
  const streakChip = document.getElementById('streakChip');
  const v = WL.streakView(stats.streak_days, stats.longest_streak_days);
  if (streakChip) {
    streakChip.hidden = !v.title;
    const t = document.getElementById('streakTitle');
    const sub = document.getElementById('streakSub');
    if (t) t.textContent = v.title;
    if (sub) { sub.textContent = v.sub; sub.hidden = !v.sub; }
  }
}

// Quality chip, and the reason in words under the text. The pipeline
// attaches item.quality only when a recording looks suspect, so a clean
// dictation shows nothing: if ordinary recordings lit up, the chip would
// become noise and get ignored. It reports; it never blocks or alters the
// text. A dictation cleaned up without the clean-up because of a limit is
// tagged "As said: limit reached".
function qualityBadge(item) {
  const q = WL.qualityView(item);
  let html = '';
  if (q.asSaid) html += `<span class="chip chip-warn">${escHtml(q.asSaid)}</span>`;
  if (q.chip) {
    const mark = q.level === 'low' ? WI.icon('alert') : WI.icon('eye');
    html += `<span class="q-badge chip ${q.level === 'low' ? 'q-low chip-err' : 'q-check chip-warn'}">${mark}${escHtml(q.chip)}</span>`;
  }
  return html;
}

// ── Not sent cards ──────────────────────────────────────────────────────
// A recording that was not turned into text: its own card with a plain
// sentence (logic.js notSentView), Try again, Show the file and Delete. It
// used to be a normal card whose Copy button copied the error. Messages
// from the last try are kept per recording, because a card is rebuilt when
// its entry changes.
const _unsentMessages = {};

function makeNotSentCard(item, isNew) {
  const v = WL.notSentView(item, _cancelLabel);
  const div = document.createElement('article');
  div.className = 'transcript-card j-ent not-sent-card' + (isNew ? ' new' : '');
  if (v.id) div.dataset.unsentId = v.id;
  const msg = v.id ? (_unsentMessages[v.id] || '') : '';
  div.innerHTML = `
    <div class="j-g"><div class="j-t">${escHtml(_clock(item.timestamp))}</div><div class="j-w">Not sent</div></div>
    <div class="ns-main">
      <p class="ns-title">${v.canRetry ? "A recording that hasn't been turned into text yet." : escHtml(v.text)}</p>
      ${v.canRetry ? `<p class="j-meta ns-text">${WI.icon('wifi-off')}<span>${escHtml(v.text)}${v.next ? ' ' + escHtml(v.next) : ''}</span></p>` : ''}
      <p class="ns-status" role="status" aria-live="polite"${msg ? '' : ' hidden'}>${escHtml(msg)}</p>
      <div class="ns-confirm" hidden>
        <span>Delete this recording? This can't be undone.</span>
        <button type="button" class="btn btn-danger btn-sm ns-confirm-yes">Delete</button>
        <button type="button" class="btn btn-sec btn-sm ns-confirm-no">Keep it</button>
      </div>
    </div>
    <div class="j-acts card-actions ns-actions">
      ${v.canRetry ? `<button type="button" class="btn btn-sec btn-sm ns-retry">${WI.icon('retry')}<span>Try again</span></button>` : ''}
      ${v.canReveal ? '<button type="button" class="btn btn-quiet btn-sm ns-reveal">Show the file</button>' : ''}
      ${v.canDelete ? '<button type="button" class="btn btn-quiet btn-sm ns-delete">Delete</button>' : ''}
    </div>
  `;
  const say = (text) => {
    const el = div.querySelector('.ns-status');
    if (v.id) _unsentMessages[v.id] = text;
    if (el) { el.textContent = text; el.hidden = !text; }
  };
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const retry = div.querySelector('.ns-retry');
  if (retry) {
    retry.addEventListener('click', async () => {
      const a = api(); if (!a || !a.retry_unsent) return;
      div.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      retry.innerHTML = WI.icon('retry') + '<span>Sending…</span>';
      say('');
      try {
        const r = await a.retry_unsent(v.id);
        if (r && r.ok && r.item) {
          delete _unsentMessages[v.id];
          _applyItemUpdate(v.id, r.item);
          return;
        }
        const message = WL.retryFailedMessage(r && r.reason);
        // The rebuilt card (with this try counted) shows the message too.
        _unsentMessages[v.id] = message;
        if (r && r.item && _applyItemUpdate(v.id, r.item)) return;
        say(message);
      } catch (e) {
        say(WL.retryFailedMessage(''));
      }
      div.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      retry.innerHTML = WI.icon('retry') + '<span>Try again</span>';
    });
  }
  const reveal = div.querySelector('.ns-reveal');
  if (reveal) {
    reveal.addEventListener('click', async () => {
      const a = api(); if (!a || !a.reveal_unsent) return;
      try {
        const r = await a.reveal_unsent(v.id);
        if (!r || !r.ok) say(WL.retryFailedMessage((r && r.reason) || 'missing'));
      } catch (e) { say(WL.retryFailedMessage('missing')); }
    });
  }
  const del = div.querySelector('.ns-delete');
  const confirmRow = div.querySelector('.ns-confirm');
  if (del && confirmRow) {
    del.addEventListener('click', () => {
      confirmRow.hidden = false;
      div.querySelector('.ns-actions').hidden = true;
      const no = confirmRow.querySelector('.ns-confirm-no');
      if (no) no.focus();
    });
    confirmRow.querySelector('.ns-confirm-no').addEventListener('click', () => {
      confirmRow.hidden = true;
      div.querySelector('.ns-actions').hidden = false;
    });
    confirmRow.querySelector('.ns-confirm-yes').addEventListener('click', async () => {
      const a = api();
      let ok = false;
      if (a && a.delete_unsent) {
        // With no recording (v.id is ""), only the Journal entry goes, found
        // by its time.
        try { const r = await a.delete_unsent(v.id, item.timestamp); ok = !!(r && r.ok); if (!ok) say(WL.retryFailedMessage(r && r.reason)); }
        catch (e) { say(WL.retryFailedMessage('')); }
      }
      if (ok) {
        const i = history.indexOf(item);
        if (i >= 0) history.splice(i, 1);
        if (v.id) delete _unsentMessages[v.id];
        _removeCard(div);
        _refreshStats().then(() => { if (!history.length) drawFeed(); });
      } else {
        confirmRow.hidden = true;
        div.querySelector('.ns-actions').hidden = false;
      }
    });
  }
  if (isNew) setTimeout(() => div.classList.remove('new'), 3000);
  return div;
}

// Swap a Not sent entry for its update, in the list and on screen. True when
// the entry was found. A card that became a normal entry gets a toast.
function _applyItemUpdate(unsentId, item) {
  const i = history.findIndex((h) => WL.notSentId(h) === unsentId);
  if (i < 0 || !item) return false;
  history[i] = item;
  const el = $feed.querySelector(`[data-unsent-id="${unsentId}"]`);
  if (el) el.replaceWith(makeCard(item, !item.failed));
  else drawFeed();
  if (!item.failed) {
    showToast('Sent. The words are in the Journal.', 'success');
    if (window.pywebview && window.pywebview.api) _refreshStats();
  }
  return true;
}

// Called by Python when a Not sent recording changes: an automatic try
// failed again, or it went through and the card is now a normal entry.
window.waffler_item_updated = function(unsentId, item) {
  _applyItemUpdate(unsentId, item);
  if (_currentPage === 'settings') loadUnsentSummary();
};

let _cardSeq = 0;   // ids for each card's text (the toggle's aria-controls)

function makeCard(item, isNew) {
  if (item && item.failed) return makeNotSentCard(item, isNew);
  if (item && item.kind === 'transform') return makeTransformCard(item, isNew);
  if (item && item.kind === 'command') return makeCommandCard(item, isNew);
  const div = document.createElement('article');
  div.className = 'transcript-card j-ent' + (isNew ? ' new' : '');

  const displayText = WL.finalText(item);          // the person's version, if they edited it
  const rawText     = WL.originalText(item);
  const hasStyled   = WL.hasOriginal(item);
  const words       = (displayText.split(/\s+/).filter(Boolean)).length;
  const q           = WL.qualityView(item);
  const changes     = WL.vocabChanges(item);

  const when = formatTime(item.timestamp);
  const textId = `jt${++_cardSeq}`;
  // What the Vocabulary changed: "Vocabulary changed post hog to PostHog".
  const vocabLine = changes.length
    ? `<p class="j-meta card-vocab" aria-label="${escHtml(WL.vocabChangesLine(item))}"><span class="j-vd" aria-hidden="true"></span><span aria-hidden="true">Vocabulary changed ${changes.map((c, i) => `${i ? (i === changes.length - 1 ? ' and ' : ', ') : ''}<b>${escHtml(c.heard)}</b> to <b>${escHtml(c.used)}</b>`).join('')}</span></p>`
    : '';
  // Edited by the person in the Journal (Edit text): Copy and Paste last use it.
  const editedLine = WL.isEdited(item)
    ? `<p class="j-meta card-edited">${WI.icon('pencil')}<span>Edited by you. Copy and Paste last use your text.</span></p>`
    : '';
  const qualityLine = (q.chip || q.asSaid || q.reasons.length)
    ? `<p class="j-meta card-reason">${qualityBadge(item)}<span>${escHtml(q.reasons.join(' '))}</span></p>`
    : '';
  // Redo this take only while its recording is still among the last ten
  // kept (get_history says so with can_redo).
  const redoButton = item.can_redo
    ? `<button type="button" class="btn btn-quiet btn-sm card-redo" aria-label="${escHtml(when ? `Redo this take from ${when}` : 'Redo this take')}">${WI.icon('retry')}<span>Redo this take</span></button>`
    : '';
  div.innerHTML = `
    <div class="j-g"><div class="j-t">${escHtml(_clock(item.timestamp))}</div><div class="j-w">${WL.formatCount(words)} ${words === 1 ? 'word' : 'words'}</div></div>
    <div class="j-body">
      <div class="j-said-k" hidden>What you said</div>
      <div class="card-text styled" id="${textId}">${_markedHtml(displayText, changes)}</div>
      ${editedLine}${vocabLine}${qualityLine}
      <p class="j-status" role="status" aria-live="polite" hidden></p>
    </div>
    <div class="j-acts card-actions">
      <button type="button" class="btn btn-sec btn-sm btn-copy" aria-label="${escHtml(when ? `Copy the dictation from ${when}` : 'Copy')}">${WI.icon('copy')}<span>Copy</span></button>
      <button type="button" class="btn btn-quiet btn-sm card-edit" aria-label="${escHtml(when ? `Edit the text of the dictation from ${when}` : 'Edit the text of this dictation')}">${WI.icon('pencil')}<span>Edit text</span></button>
      ${hasStyled ? `<button type="button" class="btn btn-quiet btn-sm text-toggle" aria-controls="${textId}" aria-pressed="false">${WI.icon('file-text')}<span>What you said</span></button>` : ''}
      ${redoButton}
      <button type="button" class="rbtn rbtn-sm card-del" aria-label="${escHtml(when ? `Delete the dictation from ${when}` : 'Delete this dictation')}" title="Delete">${WI.icon('trash')}</button>
    </div>
  `;

  const textEl = div.querySelector('.card-text');
  div.querySelector('.btn-copy').addEventListener('click', function() {
    // Copies what is shown: the clean text, or the transcript when shown.
    copyItem(textEl.classList.contains('raw') ? rawText : displayText, this);
  });

  const toggleBtn = div.querySelector('.text-toggle');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', function() {
      toggleRawHandler(this, textEl, rawText, displayText);
      const showing = textEl.classList.contains('raw');
      const k = div.querySelector('.j-said-k');
      if (k) k.hidden = !showing;
      div.classList.toggle('is-raw', showing);
      if (!showing) textEl.innerHTML = _markedHtml(displayText, changes);
    });
  }

  div.querySelector('.card-edit').addEventListener('click', () => beginEdit(item, div));
  const redo = div.querySelector('.card-redo');
  if (redo) redo.addEventListener('click', () => redoEntry(item, div));
  div.querySelector('.card-del').addEventListener('click', () => deleteEntry(item, div));

  if (isNew) {
    setTimeout(() => div.classList.remove('new'), 3000);
  }

  return div;
}

// ── Journal: a rewrite (Settings, Transforms) ────────────────────────────
// What was selected and what replaced it, the changes marked in both (words
// taken out struck through, words put in highlighted), and Copy for either
// text. It has a card of its own because it is not a dictation: no word
// count, no "What you said", nothing to redo. The app's own Undo took the
// rewrite back from the document; this is the record of both.
function makeTransformCard(item, isNew) {
  const v = WL.transformEntryView(item);
  const div = document.createElement('article');
  div.className = 'transcript-card j-ent tx-card' + (isNew ? ' new' : '');
  if (v.id) div.dataset.id = String(v.id);
  const when = formatTime(item.timestamp);
  const piece = (p, tag) => (p.cut ? `<${tag}>${escHtml(p.text)}</${tag}>` : escHtml(p.text));
  const paragraphs = (pieces, tag) => `<p>${pieces.map((p) => piece(p, tag)).join('')}</p>`;
  const kid = `txk${++_cardSeq}`;
  const aria = (what) => escHtml(when ? `${what} of the ${v.name} rewrite from ${when}` : `${what} of the ${v.name} rewrite`);
  div.innerHTML = `
    <div class="j-g"><div class="j-t">${escHtml(_clock(item.timestamp))}</div><div class="j-w">${escHtml(v.words)}</div></div>
    <div class="j-body">
      <p class="j-meta tx-head"><span class="chip chip-honey">${WI.icon('pencil')}${escHtml(v.name)}</span><span>${escHtml(v.changesLabel)}</span></p>
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}b">Before</div>
        <div class="card-text styled tx-text tx-before" role="group" aria-labelledby="${kid}b">${paragraphs(v.beforePieces, 'del')}</div>
      </div>
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}a">After</div>
        <div class="card-text styled tx-text tx-after" role="group" aria-labelledby="${kid}a">${paragraphs(v.afterPieces, 'ins')}</div>
      </div>
      ${v.status ? `<p class="j-meta tx-status">${escHtml(v.status)}</p>` : ''}
      <p class="j-status" role="status" aria-live="polite" hidden></p>
    </div>
    <div class="j-acts card-actions">
      <button type="button" class="btn btn-sec btn-sm tx-copy-after" data-idle="Copy result" aria-label="${aria('Copy the result')}">${WI.icon('copy')}<span>Copy result</span></button>
      <button type="button" class="btn btn-quiet btn-sm tx-copy-before" data-idle="Copy original" aria-label="${aria('Copy the original')}">${WI.icon('copy')}<span>Copy original</span></button>
      <button type="button" class="rbtn rbtn-sm card-del" aria-label="${escHtml(when ? `Delete the rewrite from ${when}` : 'Delete this rewrite')}" title="Delete">${WI.icon('trash')}</button>
    </div>
  `;
  div.querySelector('.tx-copy-after').addEventListener('click', function() { copyItem(v.after, this); });
  div.querySelector('.tx-copy-before').addEventListener('click', function() { copyItem(v.before, this); });
  div.querySelector('.card-del').addEventListener('click', () => deleteEntry(item, div));
  if (isNew) setTimeout(() => div.classList.remove('new'), 3000);
  return div;
}

// "Show in Journal" on a rewrite's message (app.py _on_toast_action): the
// Journal opens, scrolled to that entry and marked for a moment. An entry
// further down than the page has loaded is found by drawing the list again.
window.waffler_show_transform = async function(id) {
  showPage('home');
  const find = () => $feed.querySelector(`.tx-card[data-id="${Number(id)}"]`);
  let card = find();
  if (!card) {
    if (_searchText) { _searchText = ''; _searchQuery = ''; const box = document.getElementById('searchInput'); if (box) box.value = ''; }
    await renderFeed();
    card = find();
  }
  if (!card) return;
  card.scrollIntoView({ block: 'center' });
  card.classList.add('new');
  setTimeout(() => card.classList.remove('new'), 3000);
  const first = card.querySelector('button');
  if (first) first.focus({ preventScroll: true });
};

// ── Journal: a spoken command (Settings, Command mode) ───────────────────
// What was said, what it did (Edited, Drafted, or Searched with the engine),
// and for an edit the text before and after with the changes marked. Like a
// rewrite it has a card of its own because it is not a dictation: no "What you
// said", nothing to redo (no recording is kept).
function makeCommandCard(item, isNew) {
  const v = WL.commandEntryView(item);
  const div = document.createElement('article');
  div.className = 'transcript-card j-ent tx-card cmd-card' + (isNew ? ' new' : '');
  if (v.id) div.dataset.id = String(v.id);
  const when = formatTime(item.timestamp);
  const piece = (p, tag) => (p.cut ? `<${tag}>${escHtml(p.text)}</${tag}>` : escHtml(p.text));
  const paragraphs = (pieces, tag) => `<p>${pieces.map((p) => piece(p, tag)).join('')}</p>`;
  const kid = `cmk${++_cardSeq}`;
  const what = v.action === 'ask' ? 'search' : (v.action === 'edit' ? 'edit' : 'draft');
  const aria = (label, of) => escHtml(of ? (when ? `${label} of the ${what} from ${when}` : `${label} of the ${what}`)
    : (when ? `${label} from ${when}` : label));
  let panes = '';
  if (v.action === 'edit') {
    panes = `
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}b">Before</div>
        <div class="card-text styled tx-text tx-before" role="group" aria-labelledby="${kid}b">${paragraphs(v.beforePieces, 'del')}</div>
      </div>
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}a">After</div>
        <div class="card-text styled tx-text tx-after" role="group" aria-labelledby="${kid}a">${paragraphs(v.afterPieces, 'ins')}</div>
      </div>`;
  } else if (v.action === 'draft') {
    panes = `
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}a">Draft</div>
        <div class="card-text styled tx-text tx-after" role="group" aria-labelledby="${kid}a"><p>${escHtml(v.after)}</p></div>
      </div>`;
  } else {
    panes = `
      <div class="tx-pane">
        <div class="j-said-k" id="${kid}q">Question</div>
        <div class="card-text styled tx-text tx-after" role="group" aria-labelledby="${kid}q"><p>${escHtml(v.query)}</p></div>
      </div>`;
  }
  const copies = v.action === 'edit'
    ? `<button type="button" class="btn btn-sec btn-sm tx-copy-after" data-idle="Copy result" aria-label="${aria('Copy the result', true)}">${WI.icon('copy')}<span>Copy result</span></button>
       <button type="button" class="btn btn-quiet btn-sm tx-copy-before" data-idle="Copy original" aria-label="${aria('Copy the original', true)}">${WI.icon('copy')}<span>Copy original</span></button>`
    : (v.action === 'draft'
      ? `<button type="button" class="btn btn-sec btn-sm tx-copy-after" data-idle="Copy draft" aria-label="${aria('Copy the draft')}">${WI.icon('copy')}<span>Copy draft</span></button>`
      : `<button type="button" class="btn btn-sec btn-sm tx-copy-after" data-idle="Copy question" aria-label="${aria('Copy the question')}">${WI.icon('copy')}<span>Copy question</span></button>`);
  div.innerHTML = `
    <div class="j-g"><div class="j-t">${escHtml(_clock(item.timestamp))}</div><div class="j-w">${escHtml(v.words)}</div></div>
    <div class="j-body">
      <p class="j-meta tx-head"><span class="chip chip-honey">${WI.icon(v.icon)}${escHtml(v.chip)}</span>${v.changesLabel ? `<span>${escHtml(v.changesLabel)}</span>` : ''}</p>
      <p class="cmd-said"><span class="j-said-k">You said</span> <q>${escHtml(v.instruction)}</q></p>
      ${panes}
      ${v.status ? `<p class="j-meta tx-status">${escHtml(v.status)}</p>` : ''}
      <p class="j-status" role="status" aria-live="polite" hidden></p>
    </div>
    <div class="j-acts card-actions">
      ${copies}
      <button type="button" class="rbtn rbtn-sm card-del" aria-label="${escHtml(when ? `Delete the ${what} from ${when}` : `Delete this ${what}`)}" title="Delete">${WI.icon('trash')}</button>
    </div>
  `;
  const result = v.action === 'ask' ? v.query : v.after;
  div.querySelector('.tx-copy-after').addEventListener('click', function() { copyItem(result, this); });
  const original = div.querySelector('.tx-copy-before');
  if (original) original.addEventListener('click', function() { copyItem(v.before, this); });
  div.querySelector('.card-del').addEventListener('click', () => deleteEntry(item, div));
  if (isNew) setTimeout(() => div.classList.remove('new'), 3000);
  return div;
}

// "Show in Journal" on a command's message (app.py _on_toast_action): the same
// scroll-to-the-card as a rewrite's.
window.waffler_show_command = (id) => window.waffler_show_transform(id);

// ── Journal: Edit text, and the words it suggests (S10) ─────────────────
// "Edit text" turns a card's words into a box. Save keeps the person's version
// beside the entry (edit_history_text: edited_text), so Copy and Paste last use
// it from then on; the words that were pasted and the words speech to text
// wrote are not touched. If the change looks like a name or a word the
// Vocabulary should know, the card offers to add it, with what speech to text
// wrote. Nothing is added without a click, and Add shows the same Undo line as
// the Vocabulary page.

function beginEdit(item, card) {
  const open = card.querySelector('.j-edit');
  if (open) { open.querySelector('.j-edit-ta').focus(); return; }
  const textEl = card.querySelector('.card-text');
  if (!textEl) return;
  // "What you said" shows the speech service's words, not the ones to edit.
  if (textEl.classList.contains('raw')) {
    const toggle = card.querySelector('.text-toggle');
    if (toggle) toggle.click();
  }
  const when = formatTime(item.timestamp);
  const form = document.createElement('form');
  form.className = 'j-edit';
  form.setAttribute('aria-label', when ? `Edit the text of the dictation from ${when}` : 'Edit the text of this dictation');
  const keys = isMacPlatform ? 'Cmd+Enter' : 'Ctrl+Enter';
  form.innerHTML = `
    <textarea class="input j-edit-ta" rows="4" aria-label="Text of this dictation" aria-describedby="${textEl.id}-h" spellcheck="true"></textarea>
    <p class="j-edit-hint" id="${textEl.id}-h">Your version is kept with this dictation, on this computer. Esc cancels, ${keys} saves.</p>
    <p class="s-error j-edit-err" role="alert" hidden></p>
    <div class="j-edit-acts">${WL.isEdited(item) ? '<button type="button" class="btn btn-quiet btn-sm" data-act="reset">Use the pasted text</button>' : ''}<span class="j-edit-sp"></span><button type="button" class="btn btn-quiet btn-sm" data-act="cancel">Cancel</button><button type="submit" class="btn btn-pri btn-sm">Save</button></div>`;
  const ta = form.querySelector('.j-edit-ta');
  ta.value = WL.finalText(item);
  textEl.hidden = true;
  textEl.after(form);
  card.classList.add('is-editing');
  const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, 360) + 'px'; };
  ta.addEventListener('input', grow);
  grow();
  form.addEventListener('submit', (e) => { e.preventDefault(); saveEdit(item, card, form, ta.value); });
  form.querySelector('[data-act="cancel"]').addEventListener('click', () => endEdit(card, true));
  const reset = form.querySelector('[data-act="reset"]');
  if (reset) reset.addEventListener('click', () => saveEdit(item, card, form, item.styled || item.text || ''));
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); endEdit(card, true); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveEdit(item, card, form, ta.value); }
  });
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

// Close the box and show the words again; focus goes back to Edit text.
function endEdit(card, refocus) {
  const form = card.querySelector('.j-edit');
  if (form) form.remove();
  const textEl = card.querySelector('.card-text');
  if (textEl) textEl.hidden = false;
  card.classList.remove('is-editing');
  const btn = card.querySelector('.card-edit');
  if (refocus && btn) btn.focus();
}

async function saveEdit(item, card, form, value) {
  const api = _pageApi();
  const err = form.querySelector('.j-edit-err');
  const say = (msg) => { if (err) { err.textContent = msg || ''; err.hidden = !msg; } };
  if (!api || !api.edit_history_text) return;
  say('');
  const buttons = form.querySelectorAll('button');
  buttons.forEach((b) => { b.disabled = true; });
  let r = null;
  try { r = await api.edit_history_text(...WL.entryRef(item), value); } catch (e) { r = null; }
  buttons.forEach((b) => { b.disabled = false; });
  if (!r || !r.ok || !r.item) {
    say(WL.editFailedMessage(r && r.reason, r && r.error));
    const ta = form.querySelector('.j-edit-ta');
    if (ta) ta.focus();
    return;
  }
  if (r.state === 'unchanged') { endEdit(card, true); return; }
  // What the card was given keeps its other flags (can_redo); the edit is the
  // app's word on whether there is one.
  const merged = Object.assign({}, item, r.item);
  if (!r.item.edited_text) delete merged.edited_text;
  const at = history.indexOf(item);
  if (at >= 0) history[at] = merged;
  const fresh = makeCard(merged, false);
  card.replaceWith(fresh);
  showToast(WL.editSavedLine(r.state), 'success', 3500);
  showSuggestions(fresh, r.suggestions || []);
  const btn = fresh.querySelector('.card-edit');
  if (btn) btn.focus();
}

function showSuggestions(card, list) {
  const old = card.querySelector('.j-sug');
  if (old) old.remove();
  const items = (Array.isArray(list) ? list : []).map(WL.suggestionView).filter((v) => v.word);
  const body = card.querySelector('.j-body');
  if (!items.length || !body) return;
  const box = document.createElement('div');
  box.className = 'j-sug';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', 'Words to add to your Vocabulary');
  items.forEach((v) => box.appendChild(_suggestionRow(card, box, v)));
  body.appendChild(box);
}

function _suggestionRow(card, box, v) {
  const row = document.createElement('div');
  row.className = 'j-sug-r';
  row.innerHTML = `<div class="j-sug-t"><b>${escHtml(v.question)}</b><span>${escHtml(v.detail)}</span></div>
    <div class="j-sug-b"><button type="button" class="btn btn-quiet btn-sm" data-act="dismiss" aria-label="${escHtml(`Don't add ${v.word}`)}">Dismiss</button><button type="button" class="btn btn-sec btn-sm" data-act="add" aria-label="${escHtml(`Add ${v.word} to your Vocabulary`)}">Add</button></div>
    <p class="s-error j-sug-err" role="alert" hidden></p>`;
  row.querySelector('[data-act="dismiss"]').addEventListener('click', () => _dropSuggestion(card, box, row));
  row.querySelector('[data-act="add"]').addEventListener('click', () => addSuggestedWord(card, box, row, v));
  return row;
}

// A suggestion is gone (dismissed, or its time is up): focus moves on.
function _dropSuggestion(card, box, row) {
  const next = row.nextElementSibling || row.previousElementSibling;
  const hadFocus = row.contains(document.activeElement);
  row.remove();
  if (!box.querySelector('.j-sug-r')) box.remove();
  if (!hadFocus) return;
  const target = (next && next.isConnected && next.querySelector('button')) || card.querySelector('.card-edit');
  if (target) target.focus();
}

// Add goes the Vocabulary page's way: the same checks (logic.js vocabAdd,
// vocabSoundsCheck), the same save (set_vocab), the same Undo for ten seconds.
async function addSuggestedWord(card, box, row, v) {
  const api = _pageApi();
  const err = row.querySelector('.j-sug-err');
  const say = (msg) => { if (err) { err.textContent = msg || ''; err.hidden = !msg; } };
  if (!api || !api.set_vocab) return;
  const buttons = row.querySelectorAll('button');
  buttons.forEach((b) => { b.disabled = true; });
  say('');
  const back = () => buttons.forEach((b) => { b.disabled = false; });
  await _vocabReloadBook();
  const entries = _vocabBook.entries.map((e) => Object.assign({}, e, { sounds_like: (e.sounds_like || []).slice() }));
  const r = WL.vocabAdd(_vocabWords, v.word, WL.VOCAB_MAX_ENTRY_LEN);
  if (r.action === 'same' || r.action === 'respell') {
    say(`"${v.word}" is already in your Vocabulary.`);
    back();
    return;
  }
  if (r.action !== 'add') { say("That can't be added."); back(); return; }
  if (entries.length >= 500) {
    say('Your Vocabulary is full (500 words). Remove one you no longer need first.');
    back();
    return;
  }
  const check = WL.vocabSoundsCheck(entries, r.word, v.sounds, WL.VOCAB_MAX_ENTRY_LEN);
  if (!check.ok) { say(check.error); back(); return; }
  const before = _vocabPayload(entries);
  entries.push({ word: r.word, sounds_like: v.sounds.slice(), count: 0, last: '', starred: false });
  const p = _vocabPayload(entries);
  let res = null;
  try { res = await api.set_vocab(p.words, p.sounds); } catch (e) { res = null; }
  if (!res || !res.ok) {
    say((res && res.error) || "Couldn't add that word. Try again.");
    back();
    return;
  }
  _vocabTakeSaved({ words: Array.isArray(res.words) ? res.words : p.words, sounds_like: res.sounds_like || p.sounds, starred: res.starred }, entries);
  _suggestionAdded(card, box, row, v, before);
}

// "Added "Waffler". Undo": for ten seconds, in the suggestion's place.
function _suggestionAdded(card, box, row, v, before) {
  const done = document.createElement('div');
  done.className = 'j-sug-r is-done';
  done.setAttribute('role', 'status');
  done.innerHTML = `<span>${escHtml(v.added)}</span> <button type="button" class="link-btn" aria-label="${escHtml(`Undo adding ${v.word}`)}">Undo</button>`;
  row.replaceWith(done);
  const undoBtn = done.querySelector('button');
  const gone = setTimeout(() => _dropSuggestion(card, box, done), UNDO_MS);
  undoBtn.addEventListener('click', async () => {
    clearTimeout(gone);
    undoBtn.disabled = true;
    let res = null;
    try { res = await pywebview.api.set_vocab(before.words, before.sounds, before.starred); } catch (e) { res = null; }
    if (!res || !res.ok) {
      done.replaceWith(row);
      row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      const err = row.querySelector('.j-sug-err');
      if (err) { err.textContent = (res && res.error) || "Couldn't take that back. Try again."; err.hidden = false; }
      return;
    }
    await _vocabReloadBook();
    done.replaceWith(row);
    row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    const err = row.querySelector('.j-sug-err');
    if (err) err.hidden = true;
    const add = row.querySelector('[data-act="add"]');
    if (add) add.focus();
  });
  if (card.contains(document.activeElement) || document.activeElement === document.body) undoBtn.focus();
}

// A new dictation keeps its recording and pushes the oldest kept one out, so
// "Redo this take" goes from the entry that has just lost its recording.
function _trimRedo() {
  let n = 0;
  history.forEach((h) => { if (h.can_redo && ++n > WL.RECENT_AUDIO_KEEP) delete h.can_redo; });
  $feed.querySelectorAll('.card-redo').forEach((b, i) => { if (i >= WL.RECENT_AUDIO_KEEP) b.remove(); });
}

// ── Journal: delete, redo, undo ──────────────────────────────────────
// Delete takes the entry (and the recording kept with it) off the disk at
// once; what it removed stays in Waffler's memory for a few seconds, and the
// card's place shows "Entry deleted. Undo" until then. Redo this take sends
// the kept recording through speech to text and the clean-up again and puts
// the new words in the same place, with an Undo line above it. Neither
// pastes anything.
const UNDO_MS = 10000;
const _pageApi = () => (window.pywebview && window.pywebview.api) || null;

function _cardSay(card, text) {
  const el = card.querySelector('.j-status');
  if (el) { el.textContent = text || ''; el.hidden = !text; }
}

// "Entry deleted. Undo", as a row in the feed. `undo` runs on the click and
// answers true when the entry is back; `gone` runs when the time is up.
function _makeUndoRow(kind, label, undo, gone) {
  const row = document.createElement('div');
  row.className = 'j-undo';
  row.setAttribute('role', 'status');
  row.innerHTML = `<span>${escHtml(WL.undoLine(kind))}</span> <button type="button" class="link-btn" aria-label="${escHtml(label)}">Undo</button>`;
  const btn = row.querySelector('button');
  const timer = setTimeout(gone, UNDO_MS);
  btn.addEventListener('click', async () => {
    clearTimeout(timer);
    btn.disabled = true;
    // Could not be put back (too late, or it failed): the line goes.
    if (!(await undo())) gone();
  });
  return row;
}

async function deleteEntry(item, card) {
  const api = _pageApi();
  if (!api || !api.delete_history_item) return;
  // Disabling a button takes its focus away, so note it first.
  const hadFocus = card.contains(document.activeElement);
  const buttons = card.querySelectorAll('button');
  buttons.forEach((b) => { b.disabled = true; });
  let r = null;
  try { r = await api.delete_history_item(...WL.entryRef(item)); } catch (e) { r = null; }
  if (!r || !r.ok) {
    buttons.forEach((b) => { b.disabled = false; });
    if (hadFocus) card.querySelector('.card-del').focus();
    showToast(WL.deleteFailedMessage(r && r.reason), 'error');
    return;
  }
  const at = history.indexOf(item);
  if (at >= 0) history.splice(at, 1);
  const when = formatTime(item.timestamp);
  const row = _makeUndoRow('deleted', when ? `Undo deleting the dictation from ${when}` : 'Undo deleting this dictation', async () => {
    let res = null;
    try { res = await api.undo_history_change(r.undo); } catch (e) { res = null; }
    if (!res || !res.ok || !res.item) { showToast(WL.undoFailedMessage(res && res.reason), 'error'); return false; }
    history.splice(WL.insertIndex(history, res.item), 0, res.item);
    const back = makeCard(res.item, false);
    row.replaceWith(back);
    const del = back.querySelector('.card-del');
    if (del) del.focus();
    _refreshStats();
    return true;
  }, () => {
    if (row.isConnected) _removeCard(row);
    _refreshStats().then(() => { if (!history.length) drawFeed(); });
  });
  card.replaceWith(row);
  // Focus goes where the card was: onto Undo, so a keyboard user can undo at
  // once and Tab on to the next entry.
  if (hadFocus) row.querySelector('button').focus();
  _refreshStats();
}

async function redoEntry(item, card) {
  const api = _pageApi();
  if (!api || !api.redo_history_item) return;
  const hadFocus = card.contains(document.activeElement);
  const buttons = card.querySelectorAll('button');
  const btn = card.querySelector('.card-redo');
  buttons.forEach((b) => { b.disabled = true; });
  if (btn) btn.innerHTML = WI.icon('retry') + '<span>Redoing…</span>';
  _cardSay(card, '');
  let r = null;
  try { r = await api.redo_history_item(...WL.entryRef(item)); } catch (e) { r = null; }
  if (!r || !r.ok || !r.item) {
    buttons.forEach((b) => { b.disabled = false; });
    if (btn) btn.innerHTML = WI.icon('retry') + '<span>Redo this take</span>';
    _cardSay(card, WL.redoFailedMessage(r && r.reason));
    if (r && r.reason === 'missing') {
      // The recording has rotated out since the page was drawn: nothing to redo.
      delete item.can_redo;
      if (btn) btn.remove();
      if (hadFocus) card.querySelector('.card-del').focus();
      return;
    }
    if (hadFocus && btn) btn.focus();
    return;
  }
  const at = history.indexOf(item);
  if (at >= 0) history[at] = r.item;
  const fresh = makeCard(r.item, false);
  const when = formatTime(item.timestamp);
  const row = _makeUndoRow('replaced', when ? `Undo redoing the dictation from ${when}` : 'Undo redoing this take', async () => {
    let res = null;
    try { res = await api.undo_history_change(r.undo); } catch (e) { res = null; }
    if (!res || !res.ok || !res.item) { showToast(WL.undoFailedMessage(res && res.reason), 'error'); return false; }
    const i = history.indexOf(r.item);
    if (i >= 0) history[i] = res.item;
    const back = makeCard(res.item, false);
    fresh.replaceWith(back);
    row.remove();
    const redoAgain = back.querySelector('.card-redo');
    (redoAgain || back.querySelector('.card-del')).focus();
    _refreshStats();
    return true;
  }, () => { row.remove(); });
  card.replaceWith(fresh);
  fresh.before(row);
  if (hadFocus) row.querySelector('button').focus();
  _refreshStats();
}

// The clean text in its paragraphs, with the words the Vocabulary put in
// marked.
function _markedHtml(text, changes) {
  const used = (changes || []).map((c) => c.used);
  return String(text || '').split(/\n\s*\n/).map((para) => {
    const segs = WL.markWords(para, used);
    return `<p>${segs.map((sg) => (sg.mark ? `<span class="word-hl">${escHtml(sg.text)}</span>` : escHtml(sg.text))).join('')}</p>`;
  }).join('');
}

// "21:31": the day is in the heading above.
function _clock(ts) {
  const d = new Date(ts);
  if (!ts || isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

// One name, "What you said", and aria-pressed says whether it is shown (a
// name that changed with the state was read as "Show clean, pressed").
function toggleRawHandler(toggleEl, textEl, rawText, styledText) {
  const showingStyled = textEl.classList.contains('styled');
  if (showingStyled) {
    textEl.textContent = rawText;
    textEl.classList.replace('styled', 'raw');
    toggleEl.setAttribute('aria-pressed', 'true');
  } else {
    textEl.textContent = styledText;
    textEl.classList.replace('raw', 'styled');
    toggleEl.setAttribute('aria-pressed', 'false');
  }
}

function escHtml(str) {
  str = String(str ?? '');
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const timeStr = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (isToday) return `Today, ${timeStr}`;
  const y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `Yesterday, ${timeStr}`;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) + ', ' + timeStr;
}

// ── Toast ───────────────────────────────────────────────────────────────
// An ink pill at the bottom centre, with an icon and one sentence. The
// timer pauses while the pointer is over it, and a click dismisses it.
let _toastHoverBound = false;
let _toastTimeoutMs = 2500;
const _TOAST_ICONS = { success: 'check', error: 'alert-circle', info: 'info' };

function showToast(msg, type, ms) {
  clearTimeout(toastTimer);
  $toast.innerHTML = WI.icon(_TOAST_ICONS[type] || 'info') + `<span>${escHtml(msg)}</span>`;
  // Over the setup wizard a message goes to the top centre: at the bottom it
  // sat on the wizard's Next and Finish Setup buttons, so the first click
  // only closed the message (and hovering there kept it up).
  const overWizard = typeof _wizardVisible === 'function' && _wizardVisible();
  $toast.className = `toast visible ${type || ''}${overWizard ? ' over-wizard' : ''}`;
  _toastTimeoutMs = (typeof ms === 'number' && ms > 0) ? ms : (type === 'error' ? 5000 : 2500);
  toastTimer = setTimeout(dismissToast, _toastTimeoutMs);

  // Bind hover behaviour once; same listener is reused for every toast.
  if (!_toastHoverBound) {
    _toastHoverBound = true;
    $toast.addEventListener('mouseenter', () => {
      clearTimeout(toastTimer);
    });
    $toast.addEventListener('mouseleave', () => {
      // Resume the dismiss timer when the cursor leaves. Use a shorter
      // window than the initial: the user has already read it.
      clearTimeout(toastTimer);
      toastTimer = setTimeout(dismissToast, 1200);
    });
    $toast.addEventListener('click', dismissToast);
  }
}

function dismissToast() {
  clearTimeout(toastTimer);
  $toast.classList.remove('visible');
}

// ── Microphone (Settings, General) ────────────────────────────────────
// The microphones in the order the person prefers them (src/mic_ranking.py).
// Waffler uses the first that is plugged in; with none, it follows the
// computer's own. get_mic_setup() says what is in the list and what is
// plugged in; every edit (add, move, remove) is worked out in logic.js and
// saved as the whole list with set_mic_ranking. The page looks again every
// few seconds while it is open, so a microphone plugged in or out shows up.
let _micView = null;          // the last get_mic_setup() answer
let _micSig = '';             // ...as text, to draw only when it changed
let _micShowOther = false;    // "Show other devices" (virtual ones) is open
let _micSaving = false;

async function loadMicrophones(force) {
  if (!(window.pywebview && pywebview.api && pywebview.api.get_mic_setup)) return;
  if (_micSaving) return;
  let view;
  try { view = await pywebview.api.get_mic_setup(); } catch (e) { console.warn('get_mic_setup error:', e); return; }
  if (!view || _micSaving) return;
  const sig = JSON.stringify(view);
  if (!force && sig === _micSig) return;
  // Not under an open menu: redrawing it would close it.
  const sel = document.getElementById('micSelect');
  if (!force && sel && document.activeElement === sel) return;
  _micSig = sig;
  _micView = view;
  renderMicrophones();
}

// Settings opens: the old name stays for loadSettings.
async function loadAudioDevices() { await loadMicrophones(true); }

// focus: {act: 'up' | 'down' | 'remove' | 'add' | 'other', name} says where
// the keyboard goes after the list is drawn again.
function renderMicrophones(focus) {
  const view = _micView;
  const status = WL.micStatus(view);
  const title = document.getElementById('micNowTitle');
  const desc = document.getElementById('micNowDesc');
  if (title) title.textContent = status.title;
  if (desc) desc.textContent = status.desc;
  const host = document.getElementById('micRank');
  if (host) {
    host.innerHTML = WL.micRows(view).map((r) => `
      <li class="row mic-item${r.dim ? ' is-dim' : ''}" data-i="${r.rank - 1}">
        <span class="mic-n" aria-hidden="true">${r.rank}</span>
        <div class="row-main">
          <div class="row-t"><span class="mic-name">${escHtml(r.name)}</span>${r.chips.map((c) => `<span class="chip${c.cls ? ' ' + c.cls : ''}">${escHtml(c.text)}</span>`).join('')}</div>
          ${r.note ? `<div class="row-d">${escHtml(r.note)}</div>` : ''}
        </div>
        <div class="mic-acts">
          <button type="button" class="rbtn" data-act="up" aria-label="${escHtml(r.upLabel)}"${r.canUp ? '' : ' disabled'} onclick="moveMic(${r.rank - 1}, -1)">${WI.icon('chevron-up')}</button>
          <button type="button" class="rbtn" data-act="down" aria-label="${escHtml(r.downLabel)}"${r.canDown ? '' : ' disabled'} onclick="moveMic(${r.rank - 1}, 1)">${WI.icon('chevron-down')}</button>
          <button type="button" class="rbtn" data-act="remove" aria-label="${escHtml(r.removeLabel)}" onclick="removeMic(${r.rank - 1})">${WI.icon('x')}</button>
        </div>
      </li>`).join('');
  }
  const add = WL.micAddOptions(view, _micShowOther);
  const sel = document.getElementById('micSelect');
  if (sel) {
    sel.innerHTML = '';
    let group = null;
    add.options.forEach((o) => {
      const opt = document.createElement('option');
      opt.value = o.value;
      opt.textContent = o.label;
      if (!o.group) { sel.appendChild(opt); return; }
      if (!group || group.label !== o.group) { group = document.createElement('optgroup'); group.label = o.group; sel.appendChild(group); }
      group.appendChild(opt);
    });
    sel.value = '';
    sel.disabled = add.disabled;
  }
  const other = document.getElementById('micOtherToggle');
  if (other) {
    other.hidden = add.otherCount === 0;
    other.textContent = add.otherLabel;
    other.setAttribute('aria-expanded', String(_micShowOther));
  }
  if (focus) _focusMic(focus);
}

function _focusMic(f) {
  const host = document.getElementById('micRank');
  const row = host && host.querySelector(`.mic-item[data-i="${f.index}"]`);
  const btn = (act) => row && row.querySelector(`button[data-act="${act}"]:not(:disabled)`);
  let target = null;
  if (f.act === 'up' || f.act === 'down') target = btn(f.act) || btn(f.act === 'up' ? 'down' : 'up') || btn('remove');
  else if (f.act === 'remove') {
    const rows = host ? host.querySelectorAll('.mic-item') : [];
    const near = rows[Math.min(f.index, rows.length - 1)];
    target = (near && near.querySelector('button[data-act="remove"]')) || document.getElementById('micSelect');
  } else if (f.act === 'other') target = document.getElementById('micOtherToggle');
  else target = document.getElementById('micSelect');
  if (target && typeof target.focus === 'function') target.focus();
}

async function saveMicRanking(list, focus, toast, said) {
  if (_micSaving) return false;
  _micSaving = true;
  try {
    const r = await pywebview.api.set_mic_ranking(list);
    if (r && r.ok) {
      _micView = r;
      _micSig = JSON.stringify(r);
      renderMicrophones(focus);
      const live = document.getElementById('micLive');
      if (live) live.textContent = said;
      if (toast) showToast(said, 'success');
      return true;
    }
    showToast((r && r.error) || "Couldn't save your microphones. Try again.", 'error');
  } catch (e) {
    console.warn('set_mic_ranking error:', e);
    showToast("Couldn't save your microphones. Try again.", 'error');
  } finally {
    _micSaving = false;
  }
  return false;
}

async function onMicAdd(sel) {
  const device = WL.micPick(_micView, sel.value);
  sel.value = '';
  if (!device) return;
  const list = WL.micAdd(WL.micList(_micView), device, _micView.max);
  await saveMicRanking(list, { act: 'add', index: 0 }, true, WL.micSaved('add', device.name));
}

async function moveMic(index, delta) {
  const list = WL.micList(_micView);
  if (!list[index]) return;
  const name = list[index].name;
  const next = WL.micMove(list, index, delta);
  const to = index + delta;
  await saveMicRanking(next, { act: delta < 0 ? 'up' : 'down', index: to }, false,
    WL.micSaved('move', name, to + 1, list.length));
}

async function removeMic(index) {
  const list = WL.micList(_micView);
  if (!list[index]) return;
  await saveMicRanking(WL.micRemove(list, index), { act: 'remove', index }, true,
    WL.micSaved('remove', list[index].name));
}

function toggleMicOther() {
  _micShowOther = !_micShowOther;
  renderMicrophones({ act: 'other', index: 0 });
}

// "Choose microphone" on a toast (app.py _on_toast_action): Settings, General,
// at the Microphone heading.
window.waffler_show_microphone = function() {
  showPage('settings');
  showSettingsSection('general');
  const h = document.getElementById('micHeading');
  if (h) {
    h.scrollIntoView({ block: 'start' });
    h.focus({ preventScroll: true });
  }
};

// ── Vocabulary ─────────────────────────────────────────────────────────
// The Atelier design (3.15): a table of the words, busiest first, each with
// what it sounds like (optional; the matcher uses it instead of guessing),
// how many dictations it corrected and when it last did. The counts are the
// corrections recorded with each Journal entry (get_vocab_book), so they
// are real or zero. Beside it, Try a sentence runs the same step a
// dictation runs (try_vocab), and the newest corrections. Every change saves
// the words and their spellings together (set_vocab), and a respelling or a
// removal can be undone. With none yet, the page explains what it's for
// with the website's checked examples (decisions.md, "Vocabulary
// examples").
let _vocabWords = [];
let _vocabBook = { entries: [], total: 0, recent: [] };
let _vocabAsk = null;      // { existing, word, sounds }: Replace PostHog with POSTHOG?
let _vocabEditing = '';    // the word whose "sounds like" is open for editing
let _vocabUndoTimer = null;
// S10: search, order, select. The order is remembered (a convenience only: the
// page works without it).
let _vocabQuery = '';
let _vocabSort = 'used';
let _vocabSelecting = false;
let _vocabSelected = new Set();
try { _vocabSort = WL.vocabSortId(window.localStorage.getItem('waffler.vocabSort')); } catch (_) { _vocabSort = 'used'; }

const VOCAB_EXAMPLES = [
  ['Isabel', 'Isobel'], ['Caitlin', 'Caitlyn'], ['Sinead', 'Sinéad'], ['Hayley', 'Hailey'], ['club card', 'Clubcard'],
];

function _vocabRows(pairs) {
  return pairs.map(([heard, pasted]) => `<div class="vrow"><span class="heard">${escHtml(heard)}</span>${WI.icon('arrow')}<span class="pasted">${escHtml(pasted)}</span></div>`).join('');
}

function _vocabError(msg) {
  const el = document.getElementById('vocabError');
  if (el) { el.textContent = msg || ''; el.hidden = !msg; }
}

// The words and spellings as set_vocab takes them.
function _vocabPayload(entries) {
  const sounds = {};
  entries.forEach((e) => { if (e.sounds_like && e.sounds_like.length) sounds[e.word] = e.sounds_like.slice(); });
  return { words: entries.map((e) => e.word), sounds, starred: entries.filter((e) => e.starred).map((e) => e.word) };
}

// After a save: the saved words, with the counts they already had (a
// respelt word keeps its own: the Journal records it in any capitals).
function _vocabTakeSaved(res, entries) {
  const before = {};
  (_vocabBook.entries || []).concat(entries || []).forEach((e) => { before[e.word.toLowerCase()] = e; });
  const sounds = (res && res.sounds_like) || {};
  // The stars the app saved (set_vocab answers them), else the ones the page held.
  const stars = new Set((res && Array.isArray(res.starred)
    ? res.starred
    : (entries || _vocabBook.entries || []).filter((e) => e.starred).map((e) => e.word)).map((w) => w.toLowerCase()));
  _vocabBook.entries = res.words.map((w) => {
    const old = before[w.toLowerCase()] || {};
    return { word: w, sounds_like: sounds[w] || [], count: old.count || 0, last: old.last || '', starred: stars.has(w.toLowerCase()) };
  });
  _vocabBook.total = _vocabBook.entries.reduce((n, e) => n + (Number(e.count) || 0), 0);
  _vocabWords = res.words.slice();
}

function _vocabShowUndo(text, undo) {
  const box = document.getElementById('vocabUndo');
  const label = document.getElementById('vocabUndoText');
  const btn = document.getElementById('vocabUndoBtn');
  if (!box || !label || !btn) return;
  clearTimeout(_vocabUndoTimer);
  label.textContent = text;
  box.hidden = false;
  btn.onclick = async () => {
    box.hidden = true;
    await undo();
  };
  _vocabUndoTimer = setTimeout(() => { box.hidden = true; }, 10000);
}

function _vocabHideUndo() {
  const box = document.getElementById('vocabUndo');
  if (box) box.hidden = true;
  clearTimeout(_vocabUndoTimer);
}

async function loadVocabPage() {
  const listEl = document.getElementById('vocabList');
  const inputEl = document.getElementById('vocabInput');
  if (!listEl) return;

  // Always start empty: an old build once filled this box with every word
  // joined together, and WebView's form restore could bring that back.
  if (inputEl) inputEl.value = '';
  const soundsEl = document.getElementById('vocabSoundsInput');
  if (soundsEl) soundsEl.value = '';
  _vocabError('');
  _vocabAsk = null;
  _vocabEditing = '';
  // Search and selection start fresh each visit; the order is kept.
  _vocabQuery = '';
  _vocabSelecting = false;
  _vocabSelected = new Set();
  const searchEl = document.getElementById('vocabSearch');
  if (searchEl) searchEl.value = '';
  const sortEl = document.getElementById('vocabSort');
  if (sortEl) sortEl.value = _vocabSort;

  _vocabImport.close(false);
  await _vocabReloadBook();
  renderVocab();
  _vocabRunTry();
  loadSnippets();
  loadLearnSwitch();
}

// The words from the app, each with its counts, spellings and star.
async function _vocabReloadBook() {
  try {
    const book = pywebview.api.get_vocab_book ? await pywebview.api.get_vocab_book() : null;
    if (book && book.ok) {
      const stars = new Set((book.starred || []).map((w) => String(w).toLowerCase()));
      _vocabBook = {
        entries: (book.entries || []).map((e) => Object.assign({}, e, { starred: stars.has(String(e.word).toLowerCase()) })),
        total: book.total || 0, recent: book.recent || [],
      };
    } else {
      const words = (await pywebview.api.get_vocab()) || [];
      _vocabBook = { entries: words.map((w) => ({ word: w, sounds_like: [], count: 0, last: '', starred: false })), total: 0, recent: [] };
    }
    _vocabWords = _vocabBook.entries.map((e) => e.word);
  } catch (e) {
    console.warn('loadVocabPage error:', e);
  }
}

function renderVocab() {
  const listEl = document.getElementById('vocabList');
  const inputEl = document.getElementById('vocabInput');
  if (!listEl) return;
  const entries = _vocabBook.entries || [];
  if (inputEl) inputEl.placeholder = entries.length ? 'Word, as it should be written' : 'Word, as it should be written, like Sinéad';

  const sum = WL.vocabSummary(entries.length, _vocabBook.total);
  const sumEl = document.getElementById('vocabSummary');
  if (sumEl) {
    sumEl.innerHTML = entries.length
      ? `<b>${escHtml(sum.words)}</b>${sum.corrections ? ` &middot; <b>${escHtml(sum.corrections)}</b> ${escHtml(sum.correctionsLabel)}` : ''}`
      : '';
  }
  const side = document.getElementById('vocabSide');
  const tryBox = document.getElementById('vocabTryBox');
  const recentBox = document.getElementById('vocabRecentBox');
  if (tryBox) tryBox.hidden = !entries.length;
  if (recentBox) recentBox.hidden = !entries.length;
  if (side) side.classList.toggle('is-empty', !entries.length);
  _renderVocabRecent();

  // Search, Sort and Select are for a list; Export needs a word to save.
  const find = document.getElementById('vocabFind');
  if (find) find.hidden = !entries.length;
  const exportBtn = document.getElementById('vocabExportBtn');
  if (exportBtn) exportBtn.disabled = !entries.length;

  if (!entries.length) {
    _vocabSelecting = false;
    _vocabSelected = new Set();
    _renderVocabBulk([]);
    const status = document.getElementById('vocabSearchStatus');
    if (status) status.textContent = '';
    listEl.innerHTML = `
      <div class="group"><div class="panel v-first">
        <div class="v-first-top">
          <div>
            <h2 class="v-first-title">Teach it the names it gets wrong</h2>
            <p class="v-first-text">Speech to text guesses how names are spelled. Add yours once and Waffler spells them your way from then on. Or import a list you already have.</p>
          </div>
          <span data-waffle="logo" data-size="44"></span>
        </div>
        <div class="vtable">
          <div class="vrow vh"><span class="eyebrow">Heard</span><span></span><span class="eyebrow">Pasted, once it's in your list</span></div>
          ${_vocabRows(VOCAB_EXAMPLES)}
        </div>
        <p class="v-foot">Examples, checked with Waffler's own speech step on a recorded voice.</p>
      </div></div>`;
    if (window.WafflerIcons) WafflerIcons.mount(listEl);
    return;
  }

  const rows = WL.vocabRows(entries, new Date(), { sort: _vocabSort, query: _vocabQuery });
  const shown = rows.map((r) => r.word);
  _vocabSelected = new Set(WL.vocabKeepSelected([..._vocabSelected], shown));
  const status = document.getElementById('vocabSearchStatus');
  if (status) status.textContent = WL.vocabSearchAnnouncement(_vocabQuery, rows.length, entries.length);
  _renderVocabBulk(shown);

  if (!rows.length) {
    listEl.innerHTML = `<p class="vt-empty">No words match &ldquo;${escHtml(_vocabQuery.trim())}&rdquo;. <button type="button" class="link-btn" onclick="clearVocabSearch()">Clear search</button></p>`;
    return;
  }

  const pick = _vocabSelecting;
  const head = `<div class="vt-h" role="row">${pick ? '<span role="columnheader"><span class="sr-only">Select</span></span>' : ''}<span role="columnheader">Word</span><span role="columnheader">Sounds like</span><span role="columnheader">Corrections</span><span role="columnheader">Last used</span><span role="columnheader"><span class="sr-only">Change or remove</span></span></div>`;
  const body = rows.map((r) => {
    const w = escHtml(r.word);
    if (_vocabAsk && _vocabAsk.existing === r.word) {
      const kept = r.count
        ? `Its ${WL.formatCount(r.count)} ${r.count === 1 ? 'correction stays' : 'corrections stay'} with it.`
        : (r.sounds.length ? 'What it sounds like stays with it.' : '');
      return `<div class="vt-r vt-ask" role="row"><div class="vt-q" role="cell">Replace <b>${w}</b> with <b>${escHtml(_vocabAsk.word)}</b>?<span>It's already in your list in other capitals.${kept ? ' ' + escHtml(kept) : ''}</span></div>
        <div class="vt-acts" role="cell"><button type="button" class="btn btn-sec btn-sm" data-act="keep">Keep ${w}</button><button type="button" class="btn btn-pri btn-sm" data-act="replace">Replace</button></div></div>`;
    }
    if (_vocabEditing === r.word) {
      return `<form class="vt-r vt-edit" role="row" data-word="${w}"><span class="vt-w" role="cell">${w}</span>
        <span class="vt-ed" role="cell"><input type="text" class="input vt-ed-in" value="${escHtml(r.sounds.join(', '))}" placeholder="What speech to text writes, like ${escHtml(r.word.toLowerCase())}" aria-label="What ${w} sounds like, separated by commas" autocomplete="off" spellcheck="false"></span>
        <span class="vt-acts" role="cell"><button type="button" class="btn btn-quiet btn-sm" data-act="cancel">Cancel</button><button type="submit" class="btn btn-pri btn-sm">Save</button></span></form>`;
    }
    const sl = r.sounds.length
      ? r.sounds.map((s) => `<span class="vt-chip">${escHtml(s)}</span>`).join('')
      : '<span class="vt-none">Matched loosely</span>';
    const check = pick
      ? `<span class="vt-c" role="cell"><input type="checkbox" class="vt-sel" data-word="${w}" aria-label="Select ${w}"${_vocabSelected.has(r.word) ? ' checked' : ''}></span>`
      : '';
    return `<div class="vt-r${r.starred ? ' is-starred' : ''}" role="row" data-word="${w}">
      ${check}
      <span class="vt-w" role="cell"><button type="button" class="rbtn vt-star" data-word="${w}" aria-pressed="${r.starred ? 'true' : 'false'}" aria-label="Star ${w}" title="${r.starred ? 'Starred: kept first when a list is long' : 'Star it'}">${WI.icon('star')}</button><span class="vt-wt">${w}</span></span>
      <span class="vt-sl" role="cell">${sl}</span>
      <span class="vt-u" role="cell"><span class="vt-n">${WL.formatCount(r.count)}</span><span class="vt-b" aria-hidden="true"><i style="width:${r.bar}%"></i></span></span>
      <span class="vt-l" role="cell">${escHtml(r.last)}</span>
      <span class="vt-x" role="cell"><button type="button" class="rbtn vt-edit-btn" data-word="${w}" aria-label="Change what ${w} sounds like" title="Change what it sounds like">${WI.icon('pencil')}</button><button type="button" class="rbtn vt-del" data-word="${w}" aria-label="Remove ${w}" title="Remove">${WI.icon('x')}</button></span>
    </div>`;
  }).join('');
  listEl.innerHTML = `<div class="vt${pick ? ' is-select' : ''}" role="table" aria-label="Your words">${head}${body}</div>`;

  listEl.querySelectorAll('.vt-del').forEach((b) => {
    b.addEventListener('click', () => deleteVocabWord(b.getAttribute('data-word')));
  });
  listEl.querySelectorAll('.vt-edit-btn').forEach((b) => {
    b.addEventListener('click', () => openVocabSounds(b.getAttribute('data-word')));
  });
  listEl.querySelectorAll('.vt-star').forEach((b) => {
    b.addEventListener('click', () => toggleVocabStar(b.getAttribute('data-word')));
  });
  listEl.querySelectorAll('.vt-sel').forEach((b) => {
    b.addEventListener('change', () => onVocabSelect(b.getAttribute('data-word'), b.checked));
  });
  const ask = listEl.querySelector('.vt-ask');
  if (ask) {
    ask.querySelector('[data-act="keep"]').addEventListener('click', () => {
      _vocabAsk = null;
      renderVocab();
      const i = document.getElementById('vocabInput');
      if (i) { i.focus(); i.select(); }
    });
    ask.querySelector('[data-act="replace"]').addEventListener('click', () => respellVocabWord());
  }
  const edit = listEl.querySelector('.vt-edit');
  if (edit) {
    const inp = edit.querySelector('.vt-ed-in');
    edit.addEventListener('submit', (e) => { e.preventDefault(); saveVocabSounds(edit.getAttribute('data-word'), inp.value); });
    edit.querySelector('[data-act="cancel"]').addEventListener('click', () => closeVocabSounds());
    inp.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); closeVocabSounds(); } });
  }
}

function _renderVocabRecent() {
  const ul = document.getElementById('vocabRecent');
  if (!ul) return;
  const recent = _vocabBook.recent || [];
  if (!recent.length) {
    ul.innerHTML = '<li class="vrc-none">Nothing yet. When your Vocabulary changes a word in a dictation, it shows here.</li>';
    return;
  }
  const now = new Date();
  ul.innerHTML = recent.map((r) => `<li><span><span class="vrc-h">${escHtml(r.heard)}</span><span class="vrc-a" aria-label="to">&rarr;</span>${escHtml(r.used)}</span><span class="vrc-t">${escHtml(WL.vocabRecentWhen(r.timestamp, now))}</span></li>`).join('');
}

// ── Try a sentence ──
let _vocabTrySeq = 0;
const _vocabTrySoon = WL.debounce(() => _vocabRunTry(), 200);
function onVocabTryInput() { _vocabTrySoon(); }

async function _vocabRunTry() {
  const inp = document.getElementById('vocabTryInput');
  const out = document.getElementById('vocabTryText');
  const box = document.getElementById('vocabTryOut');
  if (!inp || !out || !box) return;
  const text = inp.value;
  const seq = ++_vocabTrySeq;
  if (!text.trim()) {
    box.classList.add('is-empty');
    out.textContent = 'Your spelling shows here.';
    return;
  }
  let r = null;
  try { r = pywebview.api.try_vocab ? await pywebview.api.try_vocab(text) : null; } catch (_) {}
  if (seq !== _vocabTrySeq) return;
  box.classList.remove('is-empty');
  if (!r || !r.ok) {
    out.textContent = "Couldn't try that just now.";
    return;
  }
  const segs = WL.markSegments(r.text, r.marks);
  out.innerHTML = segs.map((s) => (s.mark ? `<mark class="hl">${escHtml(s.text)}</mark>` : escHtml(s.text))).join('');
  if (!r.changes || !r.changes.length) out.insertAdjacentHTML('beforeend', '<span class="vtry-same">No changes.</span>');
}

// ── "Sounds like" for a word already listed ──
function openVocabSounds(word) {
  _vocabAsk = null;
  _vocabEditing = word;
  _vocabError('');
  renderVocab();
  const inp = document.querySelector('#vocabList .vt-ed-in');
  if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
}

function closeVocabSounds() {
  const word = _vocabEditing;
  _vocabEditing = '';
  renderVocab();
  const b = [...document.querySelectorAll('#vocabList .vt-edit-btn')].find((x) => x.getAttribute('data-word') === word);
  if (b) b.focus();
}

async function saveVocabSounds(word, input) {
  const entries = _vocabBook.entries.map((e) => Object.assign({}, e));
  const entry = entries.find((e) => e.word === word);
  if (!entry) return;
  const spellings = WL.vocabSoundsParse(input);
  const check = WL.vocabSoundsCheck(entries, word, spellings, WL.VOCAB_MAX_ENTRY_LEN);
  if (!check.ok) { _vocabError(check.error); return; }
  const before = entry.sounds_like.slice();
  entry.sounds_like = spellings;
  const ok = await _vocabSave(entries);
  if (!ok) return;
  _vocabError('');
  _vocabEditing = '';
  renderVocab();
  _vocabRunTry();
  const b = [...document.querySelectorAll('#vocabList .vt-edit-btn')].find((x) => x.getAttribute('data-word') === word);
  if (b) b.focus();
  if (before.join('|') !== spellings.join('|')) {
    _vocabShowUndo(spellings.length ? `${word} now matches ${spellings.join(', ')}.` : `${word} is matched loosely again.`, async () => {
      const back = _vocabBook.entries.map((e) => Object.assign({}, e, e.word === word ? { sounds_like: before } : {}));
      if (await _vocabSave(back)) { renderVocab(); _vocabRunTry(); }
    });
  }
}

// One save of the whole list with its spellings. False (with the reason on
// the page) when it didn't save.
async function _vocabSave(entries) {
  const p = _vocabPayload(entries);
  try {
    const res = await pywebview.api.set_vocab(p.words, p.sounds, p.starred);
    if (!res || !res.ok) {
      _vocabError((res && res.error) || "Couldn't save your list. Try again.");
      return false;
    }
    _vocabTakeSaved(res, entries);
    return true;
  } catch (e) {
    console.warn('set_vocab error:', e);
    _vocabError("Couldn't save your list. Try again.");
    return false;
  }
}

// Replace PostHog with POSTHOG: the confirmed respelling. Its counts and
// spellings stay with it (the Journal records the word in any capitals).
async function respellVocabWord() {
  const ask = _vocabAsk;
  if (!ask) return;
  const entries = _vocabBook.entries.map((e) => Object.assign({}, e));
  const i = entries.findIndex((e) => e.word === ask.existing);
  if (i < 0) { _vocabAsk = null; renderVocab(); return; }
  entries[i].word = ask.word;
  if (ask.sounds.length) entries[i].sounds_like = entries[i].sounds_like.concat(ask.sounds);
  const before = _vocabBook.entries.map((e) => Object.assign({}, e));
  _vocabAsk = null;
  if (!(await _vocabSave(entries))) { renderVocab(); return; }
  const inputEl = document.getElementById('vocabInput');
  const soundsEl = document.getElementById('vocabSoundsInput');
  if (inputEl) inputEl.value = '';
  if (soundsEl) soundsEl.value = '';
  renderVocab();
  _vocabRunTry();
  if (inputEl) inputEl.focus();
  _vocabShowUndo(`Changed ${ask.existing} to ${ask.word}.`, async () => {
    if (await _vocabSave(before)) { renderVocab(); _vocabRunTry(); }
  });
}

async function addVocabWord() {
  const inputEl = document.getElementById('vocabInput');
  const soundsEl = document.getElementById('vocabSoundsInput');
  if (!inputEl) return;

  const r = WL.vocabAdd(_vocabWords, inputEl.value, WL.VOCAB_MAX_ENTRY_LEN);
  if (r.action === 'empty') {
    _vocabError('Type a name or word first.');
    inputEl.focus();
    return;
  }
  if (r.action === 'too_long') {
    _vocabError(`Keep each entry to ${WL.VOCAB_MAX_ENTRY_LEN} characters or fewer: a name, a word or a short phrase.`);
    inputEl.focus();
    return;
  }
  const entries = _vocabBook.entries.map((e) => Object.assign({}, e, { sounds_like: (e.sounds_like || []).slice() }));
  const target = r.action === 'add' ? r.word : r.existing;
  const current = (entries.find((e) => e.word === target) || { sounds_like: [] }).sounds_like;
  const fresh = WL.vocabSoundsParse(soundsEl ? soundsEl.value : '')
    .filter((s) => !current.some((c) => c.toLowerCase() === s.toLowerCase()));
  const check = WL.vocabSoundsCheck(entries, r.word, current.concat(fresh), WL.VOCAB_MAX_ENTRY_LEN);
  if (!check.ok) {
    _vocabError(check.error);
    if (soundsEl) soundsEl.focus();
    return;
  }
  if (r.action === 'same' && !fresh.length) {
    _vocabError(`"${r.word}" is already in your list.`);
    return;
  }
  if (r.action === 'respell') {
    // The same word in other capitals: ask before changing it.
    _vocabError('');
    _vocabEditing = '';
    _vocabAsk = { existing: r.existing, word: r.word, sounds: fresh };
    renderVocab();
    const keep = document.querySelector('#vocabList .vt-ask [data-act="keep"]');
    if (keep) { keep.scrollIntoView({ block: 'nearest' }); keep.focus(); }
    return;
  }
  _vocabError('');
  _vocabAsk = null;
  if (r.action === 'add') entries.push({ word: r.word, sounds_like: fresh, count: 0, last: '' });
  else entries.find((e) => e.word === r.existing).sounds_like = current.concat(fresh);
  const p = _vocabPayload(entries);
  try {
    // set_vocab answers {ok, words, sounds_like} or {ok: false, error};
    // before 3.15 a failed save still said "Added".
    const res = await pywebview.api.set_vocab(p.words, p.sounds);
    if (!res || !res.ok) {
      _vocabError((res && res.error) || "Couldn't save your list. Try again.");
      inputEl.focus();
      return;
    }
    _vocabTakeSaved({ words: Array.isArray(res.words) ? res.words : p.words, sounds_like: res.sounds_like || p.sounds, starred: res.starred }, entries);
    inputEl.value = '';
    if (soundsEl) soundsEl.value = '';
    renderVocab();
    _vocabRunTry();
    const row = [...document.querySelectorAll('#vocabList .vt-r')].find((x) => x.getAttribute('data-word') === r.word);
    if (row) { row.classList.add('new'); row.scrollIntoView({ block: 'nearest' }); }
    showToast(r.action === 'same' ? `${r.word} now also matches ${fresh.join(', ')}.` : `Added "${r.word}".`, 'success');
  } catch(e) {
    console.warn('addVocabWord error:', e);
    showToast("Couldn't add that word. Try again.", 'error');
  }
  inputEl.focus();
}

async function deleteVocabWord(word) {
  const i = _vocabWords.indexOf(word);
  if (i < 0) return;
  const before = _vocabBook.entries.map((e) => Object.assign({}, e));
  const entries = before.filter((e) => e.word !== word);
  const next = entries.map((e) => e.word);
  // Where the removed row was among the rows on screen.
  const btns = [...document.querySelectorAll('#vocabList .vt-del')];
  const at = btns.findIndex((b) => b.getAttribute('data-word') === word);
  const hadFocus = btns.includes(document.activeElement);
  try {
    const p = _vocabPayload(entries);
    const res = await pywebview.api.set_vocab(next, p.sounds);
    if (!res || !res.ok) {
      showToast((res && res.error) || "Couldn't remove that word. Try again.", 'error');
      return;
    }
    _vocabTakeSaved({ words: Array.isArray(res.words) ? res.words : next, sounds_like: res.sounds_like || p.sounds, starred: res.starred }, entries);
    if (_vocabEditing === word) _vocabEditing = '';
    renderVocab();
    _vocabRunTry();
    // Focus went with the row; put it on the next word's remove button,
    // the previous one, or the box when the list is empty.
    if (hadFocus || document.activeElement === document.body) {
      const left = [...document.querySelectorAll('#vocabList .vt-del')];
      const to = WL.focusAfterRemove(at, left.length);
      const target = to >= 0 ? left[to] : document.getElementById('vocabInput');
      if (target) target.focus();
    }
    // Undo puts it back where it was, with what it sounded like.
    _vocabShowUndo(`Removed ${word}.`, async () => {
      if (await _vocabSave(before)) { renderVocab(); _vocabRunTry(); }
    });
  } catch(e) {
    console.warn('deleteVocabWord error:', e);
    showToast("Couldn't remove that word. Try again.", 'error');
  }
}

// ── Vocabulary: find, order and select words (S10) ─────────────────────
// Search matches the word and what it sounds like, as you type. The order is
// one of four (logic.js VOCAB_SORTS) and is remembered. Select shows a box on
// each row and a bar to remove or star many at once; Remove can be undone with
// the same line as removing one. A star changes the order, and which words are
// kept when a long list is sent as a hint to OpenAI or the local model.

function _vocabShown() {
  return [...document.querySelectorAll('#vocabList .vt-sel, #vocabList .vt-star')]
    .map((b) => b.getAttribute('data-word'))
    .filter((w, i, all) => all.indexOf(w) === i);
}

// The bar above the table: how many are selected and what can be done.
function _renderVocabBulk(shown) {
  const btn = document.getElementById('vocabSelectBtn');
  if (btn) btn.setAttribute('aria-pressed', _vocabSelecting ? 'true' : 'false');
  const bar = document.getElementById('vocabBulk');
  if (!bar) return;
  bar.hidden = !_vocabSelecting;
  const n = _vocabSelected.size;
  const count = document.getElementById('vocabBulkCount');
  if (count) count.textContent = WL.vocabSelectedLabel(n);
  ['vocabBulkStar', 'vocabBulkUnstar', 'vocabBulkRemove'].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = !n;
  });
  const all = document.getElementById('vocabSelectAll');
  if (all) {
    const everything = shown.length > 0 && shown.every((w) => _vocabSelected.has(w));
    all.textContent = everything ? 'Select none' : 'Select all shown';
    all.disabled = !shown.length;
  }
}

function onVocabSearch(value) {
  _vocabQuery = String(value || '');
  renderVocab();
}

function clearVocabSearch() {
  const box = document.getElementById('vocabSearch');
  if (box) box.value = '';
  _vocabQuery = '';
  renderVocab();
  if (box) box.focus();
}

// Esc clears the search; the Down arrow goes to the first row.
function onVocabSearchKey(e) {
  if (e.key === 'Escape') {
    if (_vocabQuery) { e.preventDefault(); e.stopPropagation(); clearVocabSearch(); }
    return;
  }
  if (e.key === 'ArrowDown') {
    const first = document.querySelector('#vocabList .vt-sel, #vocabList .vt-star');
    if (first) { e.preventDefault(); first.focus(); }
  }
}

function onVocabSort(value) {
  _vocabSort = WL.vocabSortId(value);
  try { window.localStorage.setItem('waffler.vocabSort', _vocabSort); } catch (_) { /* a convenience only */ }
  renderVocab();
}

async function toggleVocabStar(word) {
  const entries = _vocabBook.entries.map((e) => Object.assign({}, e));
  const entry = entries.find((e) => e.word === word);
  if (!entry) return;
  entry.starred = !entry.starred;
  if (!(await _vocabSave(entries))) return;
  _vocabError('');
  renderVocab();
  // The row may have moved with the order; focus follows the word.
  const star = [...document.querySelectorAll('#vocabList .vt-star')].find((b) => b.getAttribute('data-word') === word);
  if (star) star.focus();
}

function toggleVocabSelect() {
  _vocabSelecting = !_vocabSelecting;
  if (!_vocabSelecting) _vocabSelected = new Set();
  renderVocab();
  const btn = document.getElementById('vocabSelectBtn');
  if (btn) btn.focus();
}

function onVocabSelect(word, checked) {
  if (checked) _vocabSelected.add(word); else _vocabSelected.delete(word);
  _renderVocabBulk(_vocabShown());
}

function selectAllVocab() {
  const shown = _vocabShown();
  const everything = shown.length > 0 && shown.every((w) => _vocabSelected.has(w));
  _vocabSelected = new Set(everything ? [] : shown);
  renderVocab();
}

// Esc leaves Select, from the table or from the bar.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !_vocabSelecting) return;
  const t = e.target;
  if (!t || !t.closest || !(t.closest('#vocabList') || t.closest('#vocabBulk'))) return;
  e.preventDefault();
  toggleVocabSelect();
});

async function bulkRemoveVocab() {
  const words = _vocabBook.entries.map((e) => e.word).filter((w) => _vocabSelected.has(w));
  if (!words.length) return;
  const before = _vocabBook.entries.map((e) => Object.assign({}, e));
  const gone = new Set(words);
  const entries = before.filter((e) => !gone.has(e.word));
  const boxes = [...document.querySelectorAll('#vocabList .vt-sel')];
  const at = boxes.findIndex((b) => gone.has(b.getAttribute('data-word')));
  if (!(await _vocabSave(entries))) return;
  _vocabError('');
  _vocabSelected = new Set();
  if (_vocabEditing && gone.has(_vocabEditing)) _vocabEditing = '';
  renderVocab();
  _vocabRunTry();
  // Focus went with the rows: to the row that took their place, else the Select button.
  const left = [...document.querySelectorAll('#vocabList .vt-sel')];
  const to = WL.focusAfterRemove(at, left.length);
  const target = to >= 0 ? left[to] : (document.getElementById('vocabSelectBtn') || document.getElementById('vocabInput'));
  if (target) target.focus();
  _vocabShowUndo(WL.vocabBulkDone('remove', words), async () => {
    if (await _vocabSave(before)) { renderVocab(); _vocabRunTry(); }
  });
}

async function bulkStarVocab(on) {
  const words = _vocabBook.entries.map((e) => e.word).filter((w) => _vocabSelected.has(w));
  if (!words.length) return;
  const before = _vocabBook.entries.map((e) => Object.assign({}, e));
  const picked = new Set(words);
  const entries = before.map((e) => (picked.has(e.word) ? Object.assign({}, e, { starred: !!on }) : e));
  if (!(await _vocabSave(entries))) return;
  _vocabError('');
  renderVocab();
  _vocabShowUndo(WL.vocabBulkDone(on ? 'star' : 'unstar', words), async () => {
    if (await _vocabSave(before)) renderVocab();
  });
}

// ── Import: choose a file or paste rows, look, then add (S10) ──────────
// One panel for the words and one for the snippets. The app decides what an
// import does (preview_vocab_import / import_vocabulary, and the snippets'
// own); this shows it. A file is read here as bytes and sent as base64, so its
// encoding (UTF-16 from Excel, say) is decoded by the app and not guessed at.
// A file picker may not be offered by every web view: pasting always works.
const IMPORT_MAX_BYTES = 2000000;

function _importPreviewHtml(v, kind) {
  const snip = kind === 'snippets';
  const view = snip ? WL.snippetImportView(v) : WL.vocabImportView(v);
  const chips = view.parts.map((p) => `<span class="vi-chip vi-${p.kind}">${escHtml(p.text)}</span>`).join('');
  const more = (n) => (n ? ` and ${WL.formatCount(n)} more` : '');
  let h = `<p class="vi-head">${escHtml(view.headline)}</p>`;
  if (chips) h += `<div class="vi-chips">${chips}</div>`;
  if (view.header) h += '<p class="vi-note">The first row is used as the column names.</p>';
  if ((v.new || []).length) {
    h += `<p class="vi-k">${snip ? 'New snippets' : 'New words'}</p><p class="vi-list">${v.new.map(escHtml).join(', ')}${more(v.new_more)}</p>`;
  }
  if (!snip && (v.updated || []).length) {
    h += `<p class="vi-k">Already there, gaining a spelling or a star</p><p class="vi-list">${v.updated.map(escHtml).join(', ')}${more(v.updated_more)}</p>`;
  }
  const shorten = (t) => (t.length > 40 ? `${t.slice(0, 40)}\u2026` : t);
  const problem = (s) => `<li><span class="vi-row">Row ${WL.formatCount(s.row)}</span>${(s.word || s.trigger) ? ` <b>${escHtml(shorten(String(s.word || s.trigger)))}</b>` : ''} ${escHtml(s.message)}</li>`;
  if ((v.skipped || []).length) {
    h += `<p class="vi-k">Left out</p><ul class="vi-skip">${v.skipped.map(problem).join('')}</ul>${v.skipped_more ? `<p class="vi-list">${more(v.skipped_more).trim()}.</p>` : ''}`;
  }
  if (!snip && (v.notes || []).length) {
    h += `<p class="vi-k">Spellings left out</p><ul class="vi-skip">${v.notes.map(problem).join('')}</ul>${v.notes_more ? `<p class="vi-list">${more(v.notes_more).trim()}.</p>` : ''}`;
  }
  return { html: h, view };
}

function makeImportPanel(c) {
  let payload = null;          // { content, base64 }
  let seq = 0;
  let ready = false;           // a preview that can be added is showing
  const el = (id) => document.getElementById(id);
  const say = (msg) => { const e = el(c.error); if (e) { e.textContent = msg || ''; e.hidden = !msg; } };
  const clearPreview = () => {
    ready = false;
    const box = el(c.preview);
    if (box) { box.hidden = true; box.innerHTML = ''; }
    const go = el(c.go);
    if (go) go.disabled = true;
  };
  const reset = () => {
    payload = null;
    seq += 1;
    const t = el(c.text); if (t) t.value = '';
    const n = el(c.name); if (n) n.textContent = '';
    say('');
    clearPreview();
  };
  const soon = WL.debounce(() => preview(), 250);

  async function preview() {
    const my = ++seq;
    const api = _pageApi();
    say('');
    if (!payload || !String(payload.content).trim()) { clearPreview(); return; }
    if (!api || !api[c.previewApi]) { clearPreview(); say("Couldn't read that just now. Try again."); return; }
    let r = null;
    try { r = await api[c.previewApi](payload.content, payload.base64); } catch (e) { r = null; }
    if (my !== seq) return;
    if (!r || !r.ok) {
      clearPreview();
      say((r && r.error) || "Couldn't read that just now. Try again.");
      return;
    }
    const out = _importPreviewHtml(r, c.kind);
    const box = el(c.preview);
    if (box) { box.innerHTML = out.html; box.hidden = false; }
    const go = el(c.go);
    if (go) { go.disabled = !out.view.canApply; go.textContent = out.view.button; }
    ready = out.view.canApply;
  }

  async function apply() {
    if (!ready || !payload) return;
    const api = _pageApi();
    const go = el(c.go);
    if (!api || !api[c.applyApi]) return;
    if (go) go.disabled = true;
    let r = null;
    try { r = await api[c.applyApi](payload.content, payload.base64); } catch (e) { r = null; }
    if (!r || !r.ok) {
      say((r && r.error) || "Couldn't save that. Nothing was changed.");
      if (go) go.disabled = false;
      return;
    }
    close(false);
    await c.onApplied(r);
  }

  function open() {
    const root = el(c.root);
    if (!root) return;
    root.hidden = false;
    const b = el(c.toggle);
    if (b) b.setAttribute('aria-expanded', 'true');
    const t = el(c.text);
    if (t) t.focus();
  }

  function close(refocus) {
    const root = el(c.root);
    if (root) root.hidden = true;
    reset();
    const b = el(c.toggle);
    if (b) { b.setAttribute('aria-expanded', 'false'); if (refocus) b.focus(); }
  }

  function toggle() {
    const root = el(c.root);
    if (root && !root.hidden) close(true); else open();
  }

  async function onFile(input) {
    const f = input && input.files && input.files[0];
    if (input) input.value = '';
    if (!f) return;
    if (f.size > IMPORT_MAX_BYTES) { say('That file is too big to be a list.'); return; }
    let buf;
    try {
      buf = f.arrayBuffer ? await f.arrayBuffer() : await new Promise((ok, no) => {
        const fr = new FileReader();
        fr.onload = () => ok(fr.result);
        fr.onerror = () => no(fr.error);
        fr.readAsArrayBuffer(f);
      });
    } catch (e) {
      clearPreview();
      say("Waffler couldn't read that file. Paste its rows below instead.");
      return;
    }
    const t = el(c.text); if (t) t.value = '';
    const n = el(c.name); if (n) n.textContent = f.name;
    payload = { content: WL.bytesToBase64(new Uint8Array(buf)), base64: true };
    await preview();
  }

  function onText() {
    const t = el(c.text);
    const n = el(c.name); if (n) n.textContent = '';
    payload = { content: t ? t.value : '', base64: false };
    soon();
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); apply(); }
  }

  function pick() {
    const f = el(c.file);
    if (f) f.click();
  }

  return { open, close, toggle, onFile, onText, onKey, pick, apply, preview };
}

async function _vocabImported(r) {
  await _vocabReloadBook();
  renderVocab();
  _vocabRunTry();
  const btn = document.getElementById('vocabImportBtn');
  if (btn) btn.focus();
  if (!r.changed) { showToast(WL.vocabImportDone(r), 'success'); return; }
  const before = r.before || { words: [], sounds_like: {}, starred: [] };
  _vocabShowUndo(WL.vocabImportDone(r), async () => {
    try {
      const res = await pywebview.api.set_vocab(before.words, before.sounds_like, before.starred);
      if (!res || !res.ok) { _vocabError((res && res.error) || "Couldn't put your list back. Try again."); return; }
    } catch (e) {
      _vocabError("Couldn't put your list back. Try again.");
      return;
    }
    await _vocabReloadBook();
    renderVocab();
    _vocabRunTry();
  });
}

const _vocabImport = makeImportPanel({
  kind: 'vocab', root: 'vocabImport', toggle: 'vocabImportBtn', text: 'vocabImportText', file: 'vocabImportFile',
  name: 'vocabImportName', error: 'vocabImportError', preview: 'vocabImportPreview', go: 'vocabImportGo',
  previewApi: 'preview_vocab_import', applyApi: 'import_vocabulary', onApplied: _vocabImported,
});
function toggleVocabImport() { _vocabImport.toggle(); }
function closeVocabImport(refocus) { _vocabImport.close(refocus); }
function pickVocabFile() { _vocabImport.pick(); }
function onVocabFile(input) { return _vocabImport.onFile(input); }
function onVocabImportText() { _vocabImport.onText(); }
function onVocabImportKey(e) { _vocabImport.onKey(e); }
function applyVocabImport() { return _vocabImport.apply(); }

// Export: a CSV on the Desktop, shown in its folder (export_vocabulary).
async function exportVocab(btn) {
  const api = _pageApi();
  if (!api || !api.export_vocabulary) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api.export_vocabulary();
    if (r && r.ok) showToast(WL.listExportDone(r, 'words'), 'success', 5000);
    else showToast((r && r.error) || "Couldn't save the file. Try again.", 'error', 6000);
  } catch (e) {
    console.error('exportVocab error:', e);
    showToast("Couldn't save the file. Try again.", 'error');
  } finally {
    if (btn) btn.disabled = !(_vocabBook.entries || []).length;
  }
}

// ── Suggest words from my edits (S10): the switch on this page ─────────
async function loadLearnSwitch() {
  const t = document.getElementById('learnToggle');
  if (!t) return;
  try {
    // Just this one switch, not every page's settings and the key list.
    const s = await pywebview.api.get_learn_from_edits();
    t.checked = !(s && s.on === false);
  } catch (_) {
    t.checked = true;
  }
}

async function setLearnFromEdits(on) {
  const ok = await saveSetting('learn_from_edits', !!on);
  if (ok) {
    if (_lastSettings) _lastSettings.learn_from_edits = !!on;
  } else {
    const t = document.getElementById('learnToggle');
    if (t) t.checked = !on;
  }
}

// ── Snippets ───────────────────────────────────────────────────────────
// Below the words: a phrase you say and the text Waffler pastes for it
// (src/snippets.py). Said on its own the phrase pastes exactly the text and
// skips the clean-up; said inside a sentence it is swapped in. Every change
// saves the whole list (set_snippets, which tidies it and answers with what
// it kept); a removal or a change can be undone, as on the words. With
// none yet, the section says what it is for. Rows are found by their place
// in the list, so a phrase with odd characters never has to be matched by
// its text.
let _snips = [];
let _snipEditing = -1;      // the row being changed
let _snipUndoTimer = null;

const SNIPPET_EXAMPLES = [
  ['my email', 'jane@example.com'], ['my address', '12 Acacia Avenue, London'], ['sign off', 'Best wishes, Jane'],
];

function _snipError(msg, field) {
  const el = document.getElementById('snipError');
  if (el) { el.textContent = msg || ''; el.hidden = !msg; }
  const trig = document.getElementById('snipTrigger');
  const text = document.getElementById('snipText');
  if (trig) trig.setAttribute('aria-invalid', msg && field === 'trigger' ? 'true' : 'false');
  if (text) text.setAttribute('aria-invalid', msg && field === 'text' ? 'true' : 'false');
}

function _snipShowUndo(text, undo) {
  const box = document.getElementById('snipUndo');
  const label = document.getElementById('snipUndoText');
  const btn = document.getElementById('snipUndoBtn');
  if (!box || !label || !btn) return;
  clearTimeout(_snipUndoTimer);
  label.textContent = text;
  box.hidden = false;
  btn.onclick = async () => {
    box.hidden = true;
    await undo();
  };
  _snipUndoTimer = setTimeout(() => { box.hidden = true; }, 10000);
}

function _snipHideUndo() {
  const box = document.getElementById('snipUndo');
  if (box) box.hidden = true;
  clearTimeout(_snipUndoTimer);
}

async function loadSnippets() {
  _snipError('');
  _snipEditing = -1;
  _snipImport.close(false);
  try {
    const list = pywebview.api.get_snippets ? await pywebview.api.get_snippets() : [];
    _snips = Array.isArray(list) ? list.filter((x) => x && typeof x.trigger === 'string' && typeof x.expansion === 'string') : [];
  } catch (e) {
    console.warn('loadSnippets error:', e);
    _snips = [];
  }
  renderSnippets();
}

function _snipCounterText(box) {
  return WL.snippetCounter(box ? box.value.length : 0);
}

function onSnipTextInput() {
  const c = document.getElementById('snipCounter');
  if (c) c.textContent = _snipCounterText(document.getElementById('snipText'));
}

function renderSnippets() {
  const listEl = document.getElementById('snipList');
  if (!listEl) return;
  const sum = document.getElementById('snipSummary');
  if (sum) sum.textContent = _snips.length ? WL.snippetSummary(_snips.length) : '';

  if (!_snips.length) {
    listEl.innerHTML = `
      <div class="group"><div class="panel v-first">
        <div class="v-first-top">
          <div>
            <h3 class="v-first-title">Say a short phrase, get longer text</h3>
            <p class="v-first-text">Add the phrase and what it should paste. Your address, your email, a sign-off, a link you send all the time: say the phrase and it's there.</p>
          </div>
        </div>
        <div class="vtable">
          <div class="vrow vh"><span class="eyebrow">You say</span><span></span><span class="eyebrow">Waffler pastes</span></div>
          ${_vocabRows(SNIPPET_EXAMPLES)}
        </div>
        <p class="v-foot">Examples. A snippet can be up to ${escHtml(WL.formatCount(WL.SNIPPET_MAX_EXPANSION))} characters, and more than one line.</p>
      </div></div>`;
    if (window.WafflerIcons) WafflerIcons.mount(listEl);
    return;
  }

  const rows = _snips.map((s, i) => {
    const t = escHtml(s.trigger);
    if (_snipEditing === i) {
      return `<form class="sn-r sn-edit" data-i="${i}">
        <div class="sn-form">
          <input type="text" class="input sn-ed-t" value="${t}" aria-label="When you say" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
          <textarea class="input sn-ed-x" rows="3" aria-label="Waffler pastes" autocomplete="off" spellcheck="false">${escHtml(s.expansion)}</textarea>
          <div class="sn-acts"><span class="snip-counter" data-role="counter"></span><button type="button" class="btn btn-quiet btn-sm" data-act="cancel">Cancel</button><button type="submit" class="btn btn-pri btn-sm">Save</button></div>
        </div>
      </form>`;
    }
    return `<div class="sn-r" data-i="${i}">
      <span class="sn-t">${t}</span>
      <span class="sn-a" aria-hidden="true">${WI.icon('arrow')}</span>
      <span class="sn-x">${escHtml(s.expansion)}</span>
      <span class="sn-b"><button type="button" class="rbtn sn-edit-btn" data-i="${i}" aria-label="Change ${t}" title="Change">${WI.icon('pencil')}</button><button type="button" class="rbtn sn-del" data-i="${i}" aria-label="Remove ${t}" title="Remove">${WI.icon('x')}</button></span>
    </div>`;
  }).join('');
  listEl.innerHTML = `<div class="sn-rows" role="list" aria-label="Your snippets">${rows}</div>`;
  listEl.querySelectorAll('.sn-rows > .sn-r').forEach((r) => r.setAttribute('role', 'listitem'));

  listEl.querySelectorAll('.sn-del').forEach((b) => {
    b.addEventListener('click', () => deleteSnippet(Number(b.getAttribute('data-i'))));
  });
  listEl.querySelectorAll('.sn-edit-btn').forEach((b) => {
    b.addEventListener('click', () => openSnippetEdit(Number(b.getAttribute('data-i'))));
  });
  const edit = listEl.querySelector('.sn-edit');
  if (edit) {
    const i = Number(edit.getAttribute('data-i'));
    const trig = edit.querySelector('.sn-ed-t');
    const text = edit.querySelector('.sn-ed-x');
    const counter = edit.querySelector('[data-role="counter"]');
    const save = () => saveSnippetEdit(i, trig.value, text.value);
    edit.addEventListener('submit', (e) => { e.preventDefault(); save(); });
    edit.querySelector('[data-act="cancel"]').addEventListener('click', () => closeSnippetEdit());
    [trig, text].forEach((el) => el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); closeSnippetEdit(); }
      else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    }));
    const count = () => { if (counter) counter.textContent = _snipCounterText(text); };
    text.addEventListener('input', count);
    count();
  }
}

// What a save came back with, or null after saying why it could not.
async function _snipSave(list) {
  try {
    const res = await pywebview.api.set_snippets(list);
    if (!res || !res.ok) {
      _snipError((res && res.error) || "Couldn't save your snippets. Try again.");
      return null;
    }
    _snips = Array.isArray(res.snippets) ? res.snippets : list;
    return _snips;
  } catch (e) {
    console.warn('set_snippets error:', e);
    _snipError("Couldn't save your snippets. Try again.");
    return null;
  }
}

async function addSnippet() {
  const trig = document.getElementById('snipTrigger');
  const text = document.getElementById('snipText');
  if (!trig || !text) return;
  const check = WL.snippetCheck(_snips, trig.value, text.value, -1);
  if (!check.ok) {
    _snipError(check.error, check.field);
    (check.field === 'text' ? text : trig).focus();
    return;
  }
  _snipError('');
  _snipEditing = -1;
  const before = _snips.map((x) => Object.assign({}, x));
  const saved = await _snipSave(before.concat([{ trigger: check.trigger, expansion: check.expansion }]));
  if (!saved) { trig.focus(); return; }
  trig.value = '';
  text.value = '';
  onSnipTextInput();
  renderSnippets();
  const at = saved.findIndex((x) => x.trigger.toLowerCase() === check.trigger.toLowerCase());
  const row = document.querySelector(`#snipList .sn-r[data-i="${at}"]`);
  if (row) { row.classList.add('new'); row.scrollIntoView({ block: 'nearest' }); }
  showToast(`Added "${check.trigger}".`, 'success');
  trig.focus();
}

function openSnippetEdit(i) {
  _snipEditing = i;
  _snipError('');
  renderSnippets();
  const inp = document.querySelector('#snipList .sn-ed-t');
  if (inp) { inp.focus(); inp.select(); }
}

function closeSnippetEdit() {
  const i = _snipEditing;
  _snipEditing = -1;
  renderSnippets();
  const b = document.querySelector(`#snipList .sn-edit-btn[data-i="${i}"]`);
  if (b) b.focus();
}

async function saveSnippetEdit(i, trigger, expansion) {
  const check = WL.snippetCheck(_snips, trigger, expansion, i);
  const edit = document.querySelector('#snipList .sn-edit');
  if (!check.ok) {
    _snipError(check.error, check.field);
    const box = edit && edit.querySelector(check.field === 'text' ? '.sn-ed-x' : '.sn-ed-t');
    if (box) box.focus();
    return;
  }
  _snipError('');
  const before = _snips.map((x) => Object.assign({}, x));
  const next = before.map((x, n) => (n === i ? { trigger: check.trigger, expansion: check.expansion } : x));
  if (!(await _snipSave(next))) return;
  _snipEditing = -1;
  renderSnippets();
  const b = document.querySelector(`#snipList .sn-edit-btn[data-i="${i}"]`);
  if (b) b.focus();
  _snipShowUndo(WL.snippetDone('edit', check.trigger), async () => {
    if (await _snipSave(before)) renderSnippets();
  });
}

async function deleteSnippet(i) {
  const gone = _snips[i];
  if (!gone) return;
  _snipError('');
  const before = _snips.map((x) => Object.assign({}, x));
  const hadFocus = document.activeElement && document.activeElement.closest && document.activeElement.closest('#snipList');
  if (!(await _snipSave(before.filter((_x, n) => n !== i)))) return;
  _snipEditing = -1;
  renderSnippets();
  // Focus went with the row: the remove button of the next one, the
  // previous one, or the box when the list is empty.
  if (hadFocus || document.activeElement === document.body) {
    const left = [...document.querySelectorAll('#snipList .sn-del')];
    const to = WL.focusAfterRemove(i, left.length);
    const target = to >= 0 ? left[to] : document.getElementById('snipTrigger');
    if (target) target.focus();
  }
  _snipShowUndo(WL.snippetDone('remove', gone.trigger), async () => {
    if (await _snipSave(before)) renderSnippets();
  });
}

// ── Snippets: import and export (S10) ───────────────────────────────────
// The same panel as the words' (makeImportPanel): a CSV or JSON file, or rows
// pasted, a preview, then Add. A phrase that is already a snippet keeps its
// text and the row is listed as left out. The Undo puts the list back.
async function _snipImported(r) {
  if (Array.isArray(r.snippets)) {
    _snips = r.snippets.filter((x) => x && typeof x.trigger === 'string' && typeof x.expansion === 'string');
  }
  _snipEditing = -1;
  renderSnippets();
  const btn = document.getElementById('snipImportBtn');
  if (btn) btn.focus();
  if (!r.changed) { showToast(WL.snippetImportDone(r), 'success'); return; }
  const before = Array.isArray(r.before) ? r.before : [];
  _snipShowUndo(WL.snippetImportDone(r), async () => {
    if (await _snipSave(before)) renderSnippets();
  });
}

const _snipImport = makeImportPanel({
  kind: 'snippets', root: 'snipImport', toggle: 'snipImportBtn', text: 'snipImportText', file: 'snipImportFile',
  name: 'snipImportName', error: 'snipImportError', preview: 'snipImportPreview', go: 'snipImportGo',
  previewApi: 'preview_snippets_import', applyApi: 'import_snippets', onApplied: _snipImported,
});
function toggleSnipImport() { _snipImport.toggle(); }
function closeSnipImport(refocus) { _snipImport.close(refocus); }
function pickSnipFile() { _snipImport.pick(); }
function onSnipFile(input) { return _snipImport.onFile(input); }
function onSnipImportText() { _snipImport.onText(); }
function onSnipImportKey(e) { _snipImport.onKey(e); }
function applySnipImport() { return _snipImport.apply(); }

async function exportSnippets(btn) {
  const api = _pageApi();
  if (!api || !api.export_snippets) return;
  const sel = document.getElementById('snipExportFormat');
  const fmt = sel && sel.value === 'json' ? 'json' : 'csv';
  if (btn) btn.disabled = true;
  try {
    const r = await api.export_snippets(fmt);
    if (r && r.ok) showToast(WL.listExportDone(r, 'snippets'), 'success', 5000);
    else showToast((r && r.error) || "Couldn't save the file. Try again.", 'error', 6000);
  } catch (e) {
    console.error('exportSnippets error:', e);
    showToast("Couldn't save the file. Try again.", 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Ctrl+Enter (Cmd+Enter on a Mac) adds from the text box, where Enter is a
// new line.
document.addEventListener('DOMContentLoaded', () => {
  const text = document.getElementById('snipText');
  if (!text) return;
  text.addEventListener('input', onSnipTextInput);
  text.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addSnippet(); }
  });
});

// The empty Journal's "Open Notepad and try it" (TextEdit on a Mac).
async function openPracticeEditor(btn) {
  const err = document.getElementById('emptyEditorErr');
  if (err) err.hidden = true;
  if (btn) btn.disabled = true;
  try {
    const r = await pywebview.api.open_practice_editor();
    if (r && !r.ok && err) { err.textContent = r.error || "Couldn't open it. Open any app you type in instead."; err.hidden = false; }
  } catch (e) {
    if (err) { err.textContent = "Couldn't open it. Open any app you type in instead."; err.hidden = false; }
  }
  if (btn) btn.disabled = false;
}

// ── Page Navigation ──────────────────────────────────────────────────────
let _currentPage = 'home';

function showPage(page) {
  _currentPage = page;
  // CSS reads it: on the Journal the live strip shows the seconds, so the
  // top bar does not repeat them.
  document.body.dataset.page = page;

  [['navHome', 'home'], ['navVocab', 'vocabulary'], ['navSettings', 'settings']].forEach(([id, p]) => {
    const tab = document.getElementById(id);
    if (!tab) return;
    tab.classList.toggle('active', page === p);
    if (page === p) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  });

  const sp = document.getElementById('settingsPanel');
  const vp = document.getElementById('vocabularyPanel');
  if ($main) $main.style.display = page === 'home' ? 'flex' : 'none';
  if (sp) sp.style.display = page === 'settings' ? 'grid' : 'none';
  if (vp) vp.style.display = page === 'vocabulary' ? 'flex' : 'none';

  if (page === 'settings') {
    showSettingsSection(_settingsSection);
    loadSettings();
    refreshThemePicker();
  } else if (page === 'vocabulary') {
    loadVocabPage();
  }
  _syncMicMeter();
}

// Settings' side nav: one section at a time.
const SETTINGS_SECTIONS = ['general', 'style', 'transforms', 'command', 'keys', 'hotkey', 'usage', 'privacy', 'about'];
let _settingsSection = 'general';

function showSettingsSection(sec) {
  if (!SETTINGS_SECTIONS.includes(sec)) sec = 'general';
  _settingsSection = sec;
  document.querySelectorAll('#settingsPanel .s-sec').forEach((el) => { el.hidden = el.dataset.sec !== sec; });
  document.querySelectorAll('#settingsPanel .snav-item').forEach((el) => {
    if (el.dataset.sec === sec) el.setAttribute('aria-current', 'true');
    else el.removeAttribute('aria-current');
  });
  const scroll = document.getElementById('settingsScroll');
  if (scroll) scroll.scrollTop = 0;
  // In a very narrow window the whole panel scrolls (style.css, 560 px).
  const panel = document.getElementById('settingsPanel');
  if (panel && panel.scrollTop) panel.scrollTop = 0;
  if (sec !== 'keys') closeKeyEditor();
  // The hotkey test starts fresh each time Hotkey is opened.
  if (sec === 'hotkey') { _hotkeyHeard = false; _renderHotkeyTest(); loadShortcuts(); }
  // Who is plugged in may have changed since the list was last drawn.
  if (sec === 'general') loadMicrophones(false);
  _syncMicMeter();
}

// ── The microphone meter (Settings, General) ─────────────────────────────
// While General is on screen (and the window is), the level of the stream
// the dictations use, a few times a second (app.py get_mic_level). Nothing
// is recorded. Hidden when there is no stream yet; a microphone picked a
// moment ago is used from the next dictation, and the meter says so rather
// than show the old one's level.
const MIC_METER_MS = 120;
let _micTimer = null;
let _micBusy = false;

function _micMeterWanted() {
  return _currentPage === 'settings' && _settingsSection === 'general' && !_windowHidden()
    && !!(window.pywebview && window.pywebview.api && window.pywebview.api.get_mic_level);
}

function _syncMicMeter() {
  const want = _micMeterWanted();
  if (want && !_micTimer) {
    _micTimer = setInterval(_tickMicMeter, MIC_METER_MS);
    _tickMicMeter();
  } else if (!want && _micTimer) {
    clearInterval(_micTimer);
    _micTimer = null;
  }
}

function renderMicMeter(r) {
  const box = document.getElementById('micLevel');
  const meter = document.getElementById('micMeter');
  const label = document.getElementById('micLevelLabel');
  if (!box || !meter) return;
  const live = !!(r && r.live);
  box.hidden = !live;
  if (!live) return;
  const current = r.current !== false;
  box.classList.toggle('is-pending', !current);
  if (label) label.textContent = current ? 'Say something' : 'Used from your next dictation';
  const cells = WL.micMeter(current ? r.level : 0);
  if (meter.children.length !== cells.length) meter.innerHTML = '<i></i>'.repeat(cells.length);
  cells.forEach((c, i) => { meter.children[i].className = c; });
}

let _micTicks = 0;
async function _tickMicMeter() {
  if (_micBusy) return;
  if (!_micMeterWanted()) { _syncMicMeter(); return; }
  _micBusy = true;
  try { renderMicMeter(await pywebview.api.get_mic_level()); } catch (_) { renderMicMeter(null); }
  _micBusy = false;
  // About every three seconds, look at which microphones are plugged in.
  if (++_micTicks % 25 === 0) loadMicrophones(false);
}

// The meter stops while the window is hidden and starts again when it is back.
document.addEventListener('visibilitychange', _syncMicMeter);
['focus', 'pointerdown', 'keydown'].forEach((type) => window.addEventListener(type, _syncMicMeter, true));
(function () {
  const shown = window.waffler_window_visible;
  window.waffler_window_visible = function(visible) { shown(visible); _syncMicMeter(); };
})();

// Settings, General: a few words in the chosen spelling.
function renderSpellingSpecimen(dialect) {
  const el = document.getElementById('spellingSpecimen');
  if (!el) return;
  // British or American only applies to English: in another language the
  // engine puts the language rule in its place, and the row says so.
  const note = WL.spellingNote(_language);
  const v = note ? null : WL.spellingSpecimen(dialect);
  el.classList.toggle('specimen', !!v);
  el.innerHTML = v
    ? v.words.map((w) => `<span>${escHtml(w)}</span>`).join('') + `<s>${escHtml(v.not)}</s>`
    : escHtml(note || 'British or American, whichever you speak.');
}

// Settings, General: Language. The options come from the app
// (get_languages), the choice is saved at once (save_settings checks it)
// and goes to speech to text and to the clean-up from the next dictation.
let _language = 'en';
let _languageList = [];

function renderLanguageRow() {
  const sel = document.getElementById('languageSelect');
  if (!sel) return;
  if (!sel.options.length || sel.options.length !== _languageList.length) {
    sel.innerHTML = _languageList.map((l) => `<option value="${escHtml(l.code)}">${escHtml(l.name)}</option>`).join('');
  }
  sel.value = _language;
  const known = _languageList.find((l) => l.code === _language);
  const desc = document.getElementById('languageDesc');
  if (desc) desc.textContent = WL.languageDesc(_language, known ? known.name : '');
}

async function loadLanguages() {
  try {
    if (!_languageList.length && pywebview.api.get_languages) {
      _languageList = WL.languageOptions(await pywebview.api.get_languages());
    }
  } catch (e) {
    console.warn('get_languages error:', e);
  }
  if (!_languageList.length) _languageList = [{ code: 'en', name: 'English' }];
}

async function onLanguageChange(sel) {
  const previous = _language;
  const code = sel.value;
  const ok = await saveSetting('language', code);
  if (ok) {
    _language = code;
    renderLanguageRow();
    const dialectSel = document.getElementById('dialectSelect');
    renderSpellingSpecimen(dialectSel ? dialectSel.value : 'auto');
  } else {
    sel.value = previous;
  }
}

// Settings, General: Run setup again. Keys and history stay; setup opens at
// "Groq is connected" and carries on from there.
function runSetupAgain() {
  showWizard({ has_key: true, resume_step: '' });
}

// ── Keys and providers ────────────────────────────────────────────────
// The keys and the order are one list (they were two sections). The rows
// are drawn in the order Waffler tries them (logic.js keyRows); the order
// is saved with save_settings({provider_order}) and applies on the next
// dictation. A provider without a key is quieter: Waffler skips it.
// The starting order is the engine's own (logic.js DEFAULT_PROVIDER_ORDER).
let _providerOrder = WL.DEFAULT_PROVIDER_ORDER.slice();
// The last get_settings() answer: which keys are set, and what is in use.
let _lastSettings = null;
let _openKeyEditor = '';

const _KEY_EDITORS = { groq: 'groqKeyEditor', openai: 'apiKeyEditor', cerebras: 'cerebrasKeyEditor' };
const _KEY_PAGES = {
  groq: 'https://console.groq.com/keys',
  openai: 'https://platform.openai.com/api-keys',
  cerebras: 'https://cloud.cerebras.ai/platform/api-keys',
};

function openKeyPage(provider) {
  try { pywebview.api.open_url(_KEY_PAGES[provider] || _KEY_PAGES.groq); } catch (_) {}
}

function renderProviderOrder() {
  const host = document.getElementById('providerOrderList');
  if (!host) return;
  // Park the key editors before the rows are redrawn.
  const park = document.querySelector('.key-editors');
  Object.values(_KEY_EDITORS).forEach((id) => { const el = document.getElementById(id); if (el && park) park.appendChild(el); });
  const rows = WL.keyRows(_providerOrder, _lastSettings);
  host.innerHTML = rows.map((r) => {
    const p = WL.providerPill(r);
    return `
      <div class="row prov provider-order-item${r.hasKey ? '' : ' po-nokey'}" data-provider="${r.id}">
        <button type="button" class="grip" data-provider="${r.id}" aria-label="Move ${escHtml(r.name)}, number ${r.rank} of ${rows.length}. Use the up and down arrow keys."></button>
        <span class="po-rank" aria-hidden="true">${r.rank}</span>
        <span class="pmark pmark-${r.id}" aria-hidden="true">${escHtml(WL.PROVIDER_MARKS[r.id] || '')}</span>
        <div class="row-main">
          <div class="row-t">${escHtml(r.name)}<span class="pill ${p.pillCls}"><i></i>${escHtml(p.pill)}</span></div>
          <div class="row-d">${escHtml(r.desc)}</div>
        </div>
        ${r.masked ? `<span class="mkey">${escHtml(r.masked)}</span>` : ''}
        <button type="button" class="btn btn-sec btn-sm po-btn-key" data-provider="${r.id}" aria-label="${escHtml(_keyButtonName(r))}" aria-controls="${_KEY_EDITORS[r.id]}" aria-expanded="${_openKeyEditor === r.id}" onclick="openKeyEditor('${r.id}')">${r.hasKey ? '' : WI.icon('plus', 'ic-sm')}${escHtml(r.button)}</button>
      </div>`;
  }).join('');
  host.querySelectorAll('.grip').forEach((g) => {
    g.addEventListener('keydown', _onGripKey);
    g.addEventListener('pointerdown', _onGripDown);
  });
  if (_openKeyEditor) openKeyEditor(_openKeyEditor, true);
}

// The handle and the arrow keys: one place up or down, saved at once.
async function _onGripKey(e) {
  const id = e.currentTarget.dataset.provider;
  const delta = e.key === 'ArrowUp' ? -1 : (e.key === 'ArrowDown' ? 1 : 0);
  if (!delta) return;
  e.preventDefault();
  await moveProvider(id, delta);
  const g = document.querySelector(`#providerOrderList .grip[data-provider="${id}"]`);
  if (g) g.focus();
}

// Dragging a row by its handle: the rows reorder under the pointer, and
// the new order is saved when it is let go.
let _drag = null;
function _onGripDown(e) {
  if (e.button !== 0) return;
  const row = e.currentTarget.closest('.prov');
  const host = document.getElementById('providerOrderList');
  if (!row || !host) return;
  e.preventDefault();
  closeKeyEditor();
  _drag = { row, host, start: _providerOrder.slice() };
  row.classList.add('is-dragging');
  // On the window, not captured by the handle: moving the row in the page
  // would drop a pointer capture.
  window.addEventListener('pointermove', _onGripMove);
  window.addEventListener('pointerup', _onGripUp);
  window.addEventListener('pointercancel', _onGripUp);
}
function _onGripMove(e) {
  if (!_drag) return;
  const others = [..._drag.host.querySelectorAll('.prov')].filter((r) => r !== _drag.row);
  const before = others.find((r) => { const b = r.getBoundingClientRect(); return e.clientY < b.top + b.height / 2; });
  if (before) { if (before !== _drag.row.nextElementSibling) _drag.host.insertBefore(_drag.row, before); }
  else if (_drag.row !== _drag.host.lastElementChild) _drag.host.appendChild(_drag.row);
}
async function _onGripUp() {
  window.removeEventListener('pointermove', _onGripMove);
  window.removeEventListener('pointerup', _onGripUp);
  window.removeEventListener('pointercancel', _onGripUp);
  if (!_drag) return;
  const d = _drag;
  _drag = null;
  d.row.classList.remove('is-dragging');
  const order = [...d.host.querySelectorAll('.prov')].map((r) => r.dataset.provider);
  if (order.join() === d.start.join()) return;
  await saveProviderOrder(order);
}

// "Replace the Groq key", "Add an OpenAI key": each row's button says
// whose key it is (three buttons all called "Replace" told a screen reader
// nothing).
function _keyButtonName(r) {
  if (r.hasKey) return `Replace the ${r.name} key`;
  return `Add ${/^[AEIOU]/.test(r.name) ? 'an' : 'a'} ${r.name} key`;
}

function _keyButton(provider) {
  return document.querySelector(`#providerOrderList .po-btn-key[data-provider="${provider}"]`);
}

function _syncKeyButtons() {
  document.querySelectorAll('#providerOrderList .po-btn-key').forEach((b) => {
    b.setAttribute('aria-expanded', String(b.dataset.provider === _openKeyEditor));
  });
}

// Esc in an open key box closes it and goes back to its button, as Cancel
// does.
document.addEventListener('DOMContentLoaded', () => {
  Object.values(_KEY_EDITORS).forEach((id) => {
    const ed = document.getElementById(id);
    if (!ed) return;
    ed.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      closeKeyEditor(true);
    });
  });
});

// Replace or Add key: that provider's box opens under its row.
function openKeyEditor(provider, keep) {
  const ed = document.getElementById(_KEY_EDITORS[provider]);
  const row = document.querySelector(`#providerOrderList [data-provider="${provider}"]`);
  if (!ed || !row) return;
  if (_openKeyEditor === provider && !keep) { closeKeyEditor(); return; }
  if (_openKeyEditor && _openKeyEditor !== provider) closeKeyEditor();
  row.after(ed);
  _openKeyEditor = provider;
  _syncKeyButtons();
  const input = ed.querySelector('input');
  if (input && !keep) { input.value = ''; input.focus(); }
}

// refocus: Esc or Cancel, so the focus goes back to the row's button.
function closeKeyEditor(refocus) {
  const was = _openKeyEditor;
  const park = document.querySelector('.key-editors');
  Object.values(_KEY_EDITORS).forEach((id) => {
    const el = document.getElementById(id);
    if (el && park && el.parentElement !== park) park.appendChild(el);
    const input = el && el.querySelector('input');
    if (input) input.value = '';
  });
  _openKeyEditor = '';
  _syncKeyButtons();
  if (refocus === true && was) { const b = _keyButton(was); if (b) b.focus(); }
}

async function moveProvider(name, delta) {
  const i = _providerOrder.indexOf(name);
  if (i < 0) return;
  const j = i + delta;
  if (j < 0 || j >= _providerOrder.length) return;
  const order = _providerOrder.slice();
  [order[i], order[j]] = [order[j], order[i]];
  await saveProviderOrder(order);
}

async function saveProviderOrder(order) {
  const before = _providerOrder.slice();
  _providerOrder = WL.normalizeProviderOrder(order);
  renderProviderOrder();
  const said = 'Waffler now tries ' + _providerOrder.map((p) => WL.PROVIDER_NAMES[p] || p).join(', then ') + '.';
  try {
    const r = await pywebview.api.save_settings({ provider_order: _providerOrder });
    if (r && r.ok) {
      showToast(said, 'success');
      const live = document.getElementById('providerOrderLive');
      if (live) live.textContent = said;
      // The new order applies at once, so what is in use may change.
      try { _lastSettings = await pywebview.api.get_settings(); } catch (_) {}
      renderProviderOrder();
      _renderBackendInfo();
      return;
    }
  } catch (_) {}
  _providerOrder = before;
  renderProviderOrder();
  showToast("Couldn't save the order. Try again.", 'error');
}

// ── Settings Load ────────────────────────────────────────────────────────
// "Speech to text: Groq · Clean-up: Groq": what each stage uses first.
function _renderBackendInfo() {
  // Keys and providers: You, Speech to text, Clean-up, Out.
  const flow = document.getElementById('keysFlow');
  if (flow) {
    const f = WL.flowView(_lastSettings);
    const node = (k, v) => `<div class="fnode"><span class="fk">${k}</span><span class="fv">${v}</span></div>`;
    const stage = (x) => (x ? `${escHtml(x.by)} <small>${escHtml(x.model)}</small>` : '<small>Add a key</small>');
    flow.innerHTML = [
      node('You', `${WI.icon('mic')}Hold and talk`),
      node('Speech to text', stage(f.speech)),
      node('Clean-up', stage(f.cleanup)),
      node('Out', `${WI.icon('text-cursor')}${f.out}`),
    ].join('<span class="fline" aria-hidden="true"></span>');
    flow.setAttribute('aria-label', WL.backendsLine(_lastSettings));
  }
  // Privacy and data: where your voice goes, if anywhere.
  const to = document.getElementById('privTo');
  const link = document.getElementById('privLink');
  if (to) {
    const p = WL.privacyFlow(_lastSettings);
    to.hidden = !p;
    if (link) link.hidden = !p;
    to.innerHTML = p ? `<span class="pmark pmark-${p.id}">${escHtml(p.mark)}</span><span class="pv-to-n">${escHtml(p.name)}</span><span class="pv-to-w">${escHtml(p.what)}</span>` : '';
  }
  // About: what each job uses right now.
  const uses = WL.usesView(_lastSettings);
  const sp = document.getElementById('aboutSpeech');
  const cl = document.getElementById('aboutCleanup');
  if (sp) sp.textContent = uses.speech;
  if (cl) cl.textContent = uses.cleanup;
  const line = document.getElementById('aboutUses');
  if (line) line.hidden = uses.speech === 'No key yet' && uses.cleanup === 'No key yet';
}

async function loadSettings() {
  try {
    const s = await pywebview.api.get_settings();
    _lastSettings = s;

    // Keys and the order, as one list. Providers without a key are shown
    // as such, so the list says what Waffler will really try.
    _providerOrder = WL.normalizeProviderOrder(s.provider_order);
    renderProviderOrder();
    _renderBackendInfo();

    // Language, then Dialect / Spelling (which depends on it)
    _language = s.language || 'en';
    await loadLanguages();
    renderLanguageRow();
    const dialectSel = document.getElementById('dialectSelect');
    if (dialectSel) dialectSel.value = s.dialect || 'auto';
    renderSpellingSpecimen(s.dialect || 'auto');

    // Auto-paste, and (Mac) what happens to the clipboard after it
    const apToggle = document.getElementById('autoPasteToggle');
    if (apToggle) apToggle.checked = s.auto_paste !== false;
    renderClipboardRow();
    const reads = document.getElementById('privPasteReads');
    if (reads) reads.hidden = !isMacPlatform;
    renderFeedbackRows();
    const typing = document.getElementById('typingWpm');
    if (typing && document.activeElement !== typing && s.typing_wpm) typing.value = String(s.typing_wpm);
    renderStyleSection();
    renderTransformsSection();
    renderCommandSection();
  } catch(e) {
    console.warn('loadSettings error:', e);
  }
}

// ── Settings, Style ──────────────────────────────────────────────────────
// Clean-up On or Off, a style for each kind of app, and the email signature
// (src/style_layer.py). Everything is saved at once, read again for the next
// dictation, and drawn from the last get_settings() answer. The example
// under each choice comes from the app (preview_style), which runs the real
// rules on a sample sentence, so there is only one copy of them.
let _sigTimer = null;

function renderCleanupRow() {
  const c = WL.cleanupView(_lastSettings);
  const tog = document.getElementById('cleanupToggle');
  const desc = document.getElementById('cleanupDesc');
  const note = document.getElementById('styleOffNote');
  if (tog) tog.checked = c.on;
  if (desc) desc.textContent = c.desc;
  if (note) { note.textContent = c.note; note.hidden = !c.note; }
}

function renderStyleSection() {
  renderCleanupRow();
  WL.styleRows(_lastSettings).forEach((r) => {
    const sel = document.getElementById(`styleSelect-${r.kind}`);
    if (!sel) return;
    if (sel.options.length !== r.choices.length) {
      sel.innerHTML = r.choices.map((c) => `<option value="${escHtml(c.value)}">${escHtml(c.label)}</option>`).join('');
    }
    sel.value = r.value;
    showStyleChoice(r.kind, r.value);
  });
  const sig = (_lastSettings && _lastSettings.email_signature) || {};
  const tog = document.getElementById('signatureToggle');
  const box = document.getElementById('signatureText');
  if (tog) tog.checked = sig.enabled === true;
  if (box && document.activeElement !== box) box.value = sig.text || '';
  renderSignatureCount();
}

// What the choice does, and an example of it.
function showStyleChoice(kind, style) {
  const about = document.getElementById(`styleAbout-${kind}`);
  if (about) about.textContent = WL.styleAbout(style);
  refreshStyleExample(kind, style);
}

async function refreshStyleExample(kind, style) {
  const el = document.getElementById(`styleEg-${kind}`);
  if (!el) return;
  try {
    if (!(window.pywebview && pywebview.api && pywebview.api.preview_style)) { el.textContent = ''; return; }
    const r = await pywebview.api.preview_style(style, kind);
    const sel = document.getElementById(`styleSelect-${kind}`);
    if (sel && sel.value !== style) return;      // a newer choice is on its way
    el.innerHTML = r && r.ok ? `<span class="eg-k">Example</span>${escHtml(r.text)}` : '';
  } catch (e) {
    console.warn('preview_style error:', e);
    el.textContent = '';
  }
}

async function onStyleChange(kind, sel) {
  const row = WL.styleRows(_lastSettings).find((r) => r.kind === kind);
  const previous = row ? row.value : 'auto';
  const value = sel.value;
  const ok = await saveSetting('style_by_category', { [kind]: value });
  if (ok) {
    if (_lastSettings) {
      _lastSettings.style_by_category = Object.assign({}, _lastSettings.style_by_category, { [kind]: value });
    }
    showStyleChoice(kind, value);
  } else {
    sel.value = previous;
  }
}

async function setCleanup(on) {
  const ok = await saveSetting('cleanup', on ? 'on' : 'off');
  if (ok) {
    if (_lastSettings) _lastSettings.cleanup = on ? 'on' : 'off';
    renderCleanupRow();
  } else {
    const tog = document.getElementById('cleanupToggle');
    if (tog) tog.checked = !on;
  }
}

// The signature: the switch saves at once, the text a moment after typing
// stops (and when the box loses focus). Too long is said, not cut.
function renderSignatureCount(saved) {
  const box = document.getElementById('signatureText');
  const tog = document.getElementById('signatureToggle');
  const counter = document.getElementById('signatureCounter');
  const hint = document.getElementById('signatureHint');
  if (!box) return;
  const v = WL.signatureView({ enabled: !!(tog && tog.checked), text: '' }, box.value);
  if (counter) { counter.textContent = v.counter; counter.classList.toggle('is-over', v.over); }
  box.setAttribute('aria-invalid', v.over ? 'true' : 'false');
  if (hint) hint.textContent = v.over ? 'Shorten it to save it.' : (saved ? 'Saved.' : v.hint);
}

function onSignatureInput() {
  renderSignatureCount();
  clearTimeout(_sigTimer);
  _sigTimer = setTimeout(commitSignature, 800);
}

async function commitSignature() {
  clearTimeout(_sigTimer);
  const box = document.getElementById('signatureText');
  if (!box) return;
  const kept = (_lastSettings && _lastSettings.email_signature) || {};
  const v = WL.signatureView(kept, box.value);
  if (v.over || v.text === (kept.text || '')) return;
  const ok = await saveSetting('email_signature', { text: v.text });
  if (ok) {
    if (_lastSettings) _lastSettings.email_signature = Object.assign({}, kept, { text: v.text });
    renderSignatureCount(true);
  }
}

async function setSignatureEnabled(on) {
  await commitSignature();
  const ok = await saveSetting('email_signature', { enabled: !!on });
  if (ok) {
    if (_lastSettings) {
      _lastSettings.email_signature = Object.assign({}, _lastSettings.email_signature, { enabled: !!on });
    }
    renderSignatureCount();
  } else {
    const tog = document.getElementById('signatureToggle');
    if (tog) tog.checked = !on;
  }
}

// ── Settings, Command mode (beta) ────────────────────────────────────────
// Hold a shortcut, say what you want, let go (src/command_mode.py). Everything
// here saves at once through save_settings and is drawn from the last
// get_settings() answer; the app checks every value again and says why in one
// sentence when it refuses one (shown under the sheet). Switching it on or off
// and the shortcut apply at once, with no restart. The shortcut is chosen in
// the same dialog as the dictation hotkey.
const _cmView = () => WL.commandView(_lastSettings, isMacPlatform);

function _cmError(msg) {
  const el = document.getElementById('commandError');
  if (!el) return;
  el.textContent = msg || '';
  el.hidden = !msg;
}

// Save part of the Command mode settings. True when saved; when refused, the
// app's sentence is shown and the controls go back to what is saved.
async function saveCommandMode(part) {
  _cmError('');
  let r = null;
  try {
    r = await pywebview.api.save_settings({ command_mode: part });
  } catch (e) {
    console.warn('save command mode error:', e);
  }
  if (!r || !r.ok) {
    _cmError((r && r.error) || "Couldn't save that. Try again.");
    renderCommandSection();
    return false;
  }
  await refreshCommandSettings();
  return true;
}

async function refreshCommandSettings() {
  try {
    _lastSettings = (await pywebview.api.get_settings()) || _lastSettings;
  } catch (e) {
    console.warn('get_settings error:', e);
  }
  renderCommandSection();
  loadShortcuts();            // the Shortcuts page shows the same shortcut
}

function renderCommandSection() {
  const v = _cmView();
  const byId = (id) => document.getElementById(id);
  const tog = byId('commandToggle');
  if (tog) tog.checked = v.enabled;
  const off = byId('commandOffNote');
  if (off) off.hidden = v.enabled;

  const caps = byId('commandCaps');
  if (caps) {
    caps.hidden = !v.hotkey.length;
    caps.innerHTML = v.hotkey.length ? _capsHtml(v.hotkey) : '';
    caps.setAttribute('aria-label', v.hotkey.length ? `Command shortcut: ${v.hotkeyShown}` : '');
  }
  const desc = byId('commandShortcutDesc');
  if (desc) desc.textContent = WL.commandShortcutLine(v);
  const remove = byId('commandRemove');
  if (remove) remove.hidden = !v.hotkey.length;

  const engine = byId('commandEngine');
  if (engine) {
    engine.innerHTML = v.engines.map((e) => `<option value="${escHtml(e.value)}">${escHtml(e.label)}</option>`).join('');
    engine.value = v.engine;
  }

  const examples = byId('commandExamples');
  if (examples) {
    examples.innerHTML = v.examples.map((x) => `
      <div class="row cmd-example" role="listitem">
        <div class="row-main"><div class="row-t"><q>${escHtml(x.say)}</q></div><div class="row-d">${escHtml(x.does)}</div></div>
      </div>`).join('');
  }
}

async function setCommandEnabled(on) {
  const ok = await saveCommandMode({ enabled: !!on });
  if (ok) showToast(WL.commandSaved('enabled', !!on), 'success', 3500);
}

async function onCommandEngineChange(sel) {
  const label = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : '';
  const ok = await saveCommandMode({ web_engine: sel.value });
  if (ok) showToast(WL.commandSaved('engine', label), 'success', 3500);
}

async function saveCommandHotkey(keys, fail) {
  let result;
  try {
    result = await window.pywebview.api.save_command_hotkey(JSON.stringify(keys));
  } catch (e) {
    console.error('saveCommandHotkey error:', e);
    result = { ok: false, error: "Couldn't save the shortcut. Try again." };
  }
  if (!result || !result.ok) { fail((result && result.error) || "Couldn't save the shortcut. Try again."); return; }
  closeHotkeyCapture();
  _cmError('');
  await refreshCommandSettings();
  showToast(WL.commandSaved('hotkey', result.display), 'success', 3500);
}

async function clearCommandHotkey() {
  let result = null;
  try { result = await window.pywebview.api.save_command_hotkey('[]'); } catch (e) { result = null; }
  if (!result || !result.ok) { _cmError((result && result.error) || "Couldn't remove the shortcut. Try again."); return; }
  _cmError('');
  await refreshCommandSettings();
  showToast(WL.commandSaved('hotkey', ''), 'success', 3500);
  const change = document.getElementById('commandChange');
  if (change) change.focus();
}

// ── Settings, Transforms (beta) ──────────────────────────────────────────
// Select text, press a shortcut, and it is rewritten where it is
// (src/transforms.py). Everything here saves at once through save_settings
// and is drawn from the last get_settings() answer; the app checks every
// value again and says why in one sentence when it refuses one (shown under
// the sheet). Switching it on or off, the shortcut and the menu bar item
// apply at once, with no restart. The shortcut is chosen in the same dialog
// as the dictation hotkey.
let _txEditing = -1;       // the index of the transform being changed, or -1
let _txDraft = null;       // what was typed in that form, kept when a save is refused
let _txUndoTimer = null;
let _outlineTimer = null;

const _txView = () => WL.transformView(_lastSettings, isMacPlatform);

function _txError(msg, id) {
  const el = document.getElementById(id || 'transformsError');
  if (!el) return;
  el.textContent = msg || '';
  el.hidden = !msg;
}

// Save part of the Transforms settings. True when saved; when refused, the
// app's sentence is shown and the controls go back to what is saved.
async function saveTransforms(part, errorId) {
  _txError('', errorId);
  let r = null;
  try {
    r = await pywebview.api.save_settings({ transforms: part });
  } catch (e) {
    console.warn('save transforms error:', e);
  }
  if (!r || !r.ok) {
    _txError((r && r.error) || "Couldn't save that. Try again.", errorId);
    renderTransformsSection();
    return false;
  }
  await refreshTransformSettings();
  return true;
}

async function refreshTransformSettings() {
  try {
    _lastSettings = (await pywebview.api.get_settings()) || _lastSettings;
  } catch (e) {
    console.warn('get_settings error:', e);
  }
  renderTransformsSection();
  loadShortcuts();            // the Shortcuts page shows the same shortcut
}

function renderTransformsSection() {
  const v = _txView();
  const byId = (id) => document.getElementById(id);
  const tog = byId('transformsToggle');
  if (tog) tog.checked = v.enabled;
  const off = byId('transformsOffNote');
  if (off) off.hidden = v.enabled;

  // The shortcut.
  const caps = byId('transformsCaps');
  if (caps) {
    caps.hidden = !v.hotkey.length;
    caps.innerHTML = v.hotkey.length ? _capsHtml(v.hotkey) : '';
    caps.setAttribute('aria-label', v.hotkey.length ? `Transform shortcut: ${v.hotkeyShown}` : '');
  }
  const desc = byId('transformsShortcutDesc');
  if (desc) desc.textContent = WL.transformShortcutLine(v);
  const remove = byId('transformsRemove');
  if (remove) remove.hidden = !v.hotkey.length;

  // Which transform the shortcut runs.
  const run = byId('transformsRun');
  if (run) {
    run.innerHTML = v.choices.map((c) => `<option value="${escHtml(c.value)}">${escHtml(c.label)}</option>`).join('');
    run.value = v.defaultId;
  }

  // Polish: the rules, and the person's own.
  v.rules.forEach((r) => {
    const box = byId(`polishRule-${r.key}`);
    if (box) box.checked = r.on;
  });
  renderOwnRules(v);

  // Prompt Engineer: the outline.
  const outline = byId('peOutline');
  if (outline && document.activeElement !== outline) outline.value = v.outline;
  const reset = byId('peReset');
  if (reset) reset.disabled = v.outlineIsDefault;
  renderOutlineCount();

  renderCustomTransforms(v);
}

function renderOwnRules(v) {
  const list = document.getElementById('polishOwnRules');
  if (list) {
    list.hidden = !v.ownRules.length;
    list.innerHTML = v.ownRules.map((r, i) => `
      <div class="row tx-own" role="listitem">
        <div class="row-main"><div class="row-t">${escHtml(r)}</div></div>
        <button type="button" class="rbtn" data-i="${i}" aria-label="Remove the rule ${escHtml(r)}" title="Remove">${WI.icon('x')}</button>
      </div>`).join('');
    list.querySelectorAll('button[data-i]').forEach((b) => {
      b.addEventListener('click', () => removePolishRule(Number(b.getAttribute('data-i'))));
    });
  }
  const form = document.getElementById('polishRuleForm');
  if (form) form.hidden = !v.canAddRule;
  const title = document.getElementById('polishOwnTitle');
  if (title) title.hidden = !v.ownRules.length && !v.canAddRule;
}

function renderCustomTransforms(v) {
  const list = document.getElementById('transformsList');
  const count = document.getElementById('transformsCount');
  if (count) count.textContent = v.custom.length ? `${v.custom.length} of ${WL.TRANSFORM_LIMITS.custom}` : '';
  const form = document.getElementById('transformsForm');
  if (form) form.hidden = !v.canAddCustom;
  const full = document.getElementById('transformsFull');
  if (full) full.hidden = v.canAddCustom;
  if (!list) return;
  list.hidden = !v.custom.length;
  list.innerHTML = v.custom.map((c, i) => {
    const n = escHtml(c.name);
    if (_txEditing === i) {
      const d = _txDraft && _txDraft.i === i ? _txDraft : null;
      return `<form class="row tx-edit" role="listitem" data-i="${i}">
        <div class="row-main">
          <input type="text" class="input tx-ed-n" value="${escHtml(d ? d.name : c.name)}" aria-label="Name" maxlength="40" autocomplete="off" spellcheck="false">
          <textarea class="input tx-prompt tx-ed-p" rows="3" aria-label="What it should do" autocomplete="off" spellcheck="false">${escHtml(d ? d.prompt : c.prompt)}</textarea>
          <p class="s-error tx-ed-err" role="alert" hidden></p>
          <div class="tx-new-foot"><button type="button" class="btn btn-quiet btn-sm" data-act="cancel">Cancel</button><button type="submit" class="btn btn-pri btn-sm">Save</button></div>
        </div>
      </form>`;
    }
    const preview = c.prompt.length > 150 ? `${c.prompt.slice(0, 150).trimEnd()}…` : c.prompt;
    return `<div class="row tx-row" role="listitem" data-i="${i}">
      <div class="row-main"><div class="row-t">${n}</div><div class="row-d">${escHtml(preview)}</div></div>
      <button type="button" class="btn btn-quiet btn-sm tx-edit-btn" data-i="${i}" aria-label="Edit ${n}">Edit</button>
      <button type="button" class="btn btn-quiet btn-sm tx-del" data-i="${i}" data-id="${escHtml(c.id)}" aria-label="Delete ${n}">Delete</button>
    </div>`;
  }).join('');
  list.querySelectorAll('.tx-edit-btn').forEach((b) => b.addEventListener('click', () => openTransformEdit(Number(b.getAttribute('data-i')))));
  list.querySelectorAll('.tx-del').forEach((b) => b.addEventListener('click', () => deleteTransform(b.getAttribute('data-id'))));
  const edit = list.querySelector('.tx-edit');
  if (edit) {
    const i = Number(edit.getAttribute('data-i'));
    const name = edit.querySelector('.tx-ed-n');
    const prompt = edit.querySelector('.tx-ed-p');
    edit.addEventListener('submit', (e) => { e.preventDefault(); saveTransformEdit(i, name.value, prompt.value, edit); });
    edit.querySelector('[data-act="cancel"]').addEventListener('click', () => closeTransformEdit());
    [name, prompt].forEach((el) => el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); closeTransformEdit(); }
    }));
  }
}

async function setTransformsEnabled(on) {
  const ok = await saveTransforms({ enabled: !!on });
  if (ok) showToast(WL.transformSaved('enabled', !!on), 'success', 3500);
}

async function onTransformRunChange(sel) {
  const label = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : '';
  const ok = await saveTransforms({ default: sel.value });
  if (ok) showToast(WL.transformSaved('default', label), 'success', 3500);
}

async function saveTransformHotkey(keys, fail) {
  let result;
  try {
    result = await window.pywebview.api.save_transform_hotkey(JSON.stringify(keys));
  } catch (e) {
    console.error('saveTransformHotkey error:', e);
    result = { ok: false, error: "Couldn't save the shortcut. Try again." };
  }
  if (!result || !result.ok) { fail((result && result.error) || "Couldn't save the shortcut. Try again."); return; }
  closeHotkeyCapture();
  _txError('');
  await refreshTransformSettings();
  showToast(WL.transformSaved('hotkey', result.display), 'success', 3500);
}

async function clearTransformHotkey() {
  let result = null;
  try { result = await window.pywebview.api.save_transform_hotkey('[]'); } catch (e) { result = null; }
  if (!result || !result.ok) { _txError((result && result.error) || "Couldn't remove the shortcut. Try again."); return; }
  _txError('');
  await refreshTransformSettings();
  showToast(WL.transformSaved('hotkey', ''), 'success', 3500);
  const change = document.getElementById('transformsChange');
  if (change) change.focus();
}

// Polish: a rule switched on or off, and rules of the person's own.
async function setPolishRule(key, on) {
  await saveTransforms({ polish: { rules: { [key]: !!on } } });
}

async function addPolishRule() {
  const input = document.getElementById('polishRuleInput');
  if (!input) return;
  const v = _txView();
  const check = WL.transformRuleCheck(v.ownRules, input.value);
  if (!check.ok) { _txError(check.error, 'polishRuleError'); input.focus(); return; }
  _txError('', 'polishRuleError');
  if (await saveTransforms({ polish: { custom_rules: v.ownRules.concat([check.rule]) } }, 'polishRuleError')) {
    input.value = '';
    const again = document.getElementById('polishRuleInput');
    if (again) again.focus();
  }
}

async function removePolishRule(i) {
  const v = _txView();
  const gone = v.ownRules[i];
  if (gone === undefined) return;
  const ok = await saveTransforms({ polish: { custom_rules: v.ownRules.filter((_r, n) => n !== i) } }, 'polishRuleError');
  if (ok) {
    const left = [...document.querySelectorAll('#polishOwnRules button[data-i]')];
    const to = WL.focusAfterRemove(i, left.length);
    const target = to >= 0 ? left[to] : document.getElementById('polishRuleInput');
    if (target) target.focus();
  }
}

// Prompt Engineer's outline: saved a moment after typing stops, never when
// too long. Reset puts back the built-in one.
function renderOutlineCount(saved) {
  const box = document.getElementById('peOutline');
  const counter = document.getElementById('peCounter');
  const hint = document.getElementById('peHint');
  if (!box) return;
  const o = WL.transformOutlineView(box.value);
  if (counter) { counter.textContent = o.counter; counter.classList.toggle('is-over', o.over); }
  box.setAttribute('aria-invalid', o.over ? 'true' : 'false');
  if (hint) hint.textContent = o.over ? 'Shorten it to save it.' : (saved ? 'Saved.' : '');
}

function onOutlineInput() {
  renderOutlineCount();
  clearTimeout(_outlineTimer);
  _outlineTimer = setTimeout(commitOutline, 800);
}

async function commitOutline() {
  clearTimeout(_outlineTimer);
  const box = document.getElementById('peOutline');
  if (!box) return;
  const v = _txView();
  const o = WL.transformOutlineView(box.value);
  if (o.over || o.text === v.outline.trim()) return;
  if (await saveTransforms({ prompt_engineer: { skeleton: o.text } })) renderOutlineCount(true);
}

async function resetOutline() {
  clearTimeout(_outlineTimer);
  const v = _txView();
  const box = document.getElementById('peOutline');
  if (await saveTransforms({ prompt_engineer: { skeleton: '' } })) {
    if (box) box.value = v.outlineDefault;
    renderOutlineCount(true);
    showToast(WL.transformSaved('outline'), 'success', 3500);
  }
}

// Your transforms: add, change, delete (up to five).
function onTransformPromptInput() {
  const box = document.getElementById('transformPrompt');
  const counter = document.getElementById('transformsFormCount');
  if (!box || !counter) return;
  const n = Array.from(box.value.trim()).length;
  counter.textContent = n >= WL.TRANSFORM_LIMITS.prompt * 0.8
    ? `${WL.formatCount(n)} of ${WL.formatCount(WL.TRANSFORM_LIMITS.prompt)}` : '';
}

async function addTransform() {
  const name = document.getElementById('transformName');
  const prompt = document.getElementById('transformPrompt');
  if (!name || !prompt) return;
  const v = _txView();
  const check = WL.transformCheck(v.custom, name.value, prompt.value, -1);
  if (!check.ok) {
    _txError(check.error, 'transformFormError');
    (check.field === 'prompt' ? prompt : name).focus();
    return;
  }
  _txError('', 'transformFormError');
  const next = v.custom.map((c) => ({ id: c.id, name: c.name, prompt: c.prompt })).concat([{ name: check.name, prompt: check.prompt }]);
  if (await saveTransforms({ custom: next }, 'transformFormError')) {
    name.value = '';
    prompt.value = '';
    onTransformPromptInput();
    showToast(WL.transformSaved('added', check.name), 'success', 3500);
    name.focus();
  }
}

function openTransformEdit(i) {
  _txEditing = i;
  _txDraft = null;
  _txError('', 'transformFormError');
  renderCustomTransforms(_txView());
  const box = document.querySelector('#transformsList .tx-ed-n');
  if (box) { box.focus(); box.select(); }
}

function closeTransformEdit() {
  const i = _txEditing;
  _txEditing = -1;
  _txDraft = null;
  renderCustomTransforms(_txView());
  const b = document.querySelector(`#transformsList .tx-edit-btn[data-i="${i}"]`);
  if (b) b.focus();
}

async function saveTransformEdit(i, name, prompt, form) {
  const v = _txView();
  const check = WL.transformCheck(v.custom, name, prompt, i);
  const err = form && form.querySelector('.tx-ed-err');
  if (!check.ok) {
    if (err) { err.textContent = check.error; err.hidden = false; }
    const box = form && form.querySelector(check.field === 'prompt' ? '.tx-ed-p' : '.tx-ed-n');
    if (box) box.focus();
    return;
  }
  const next = v.custom.map((c, n) => (n === i ? { id: c.id, name: check.name, prompt: check.prompt }
    : { id: c.id, name: c.name, prompt: c.prompt }));
  const draft = { i, name, prompt };
  _txEditing = -1;
  if (await saveTransforms({ custom: next }, 'transformFormError')) {
    _txDraft = null;
    const b = document.querySelector(`#transformsList .tx-edit-btn[data-i="${i}"]`);
    if (b) b.focus();
    return;
  }
  // Refused: the form stays, with what was typed and the app's sentence in it.
  _txEditing = i;
  _txDraft = draft;
  renderCustomTransforms(_txView());
  const shown = document.querySelector('#transformsList .tx-ed-err');
  const said = document.getElementById('transformFormError');
  if (shown && said && said.textContent) {
    shown.textContent = said.textContent;
    shown.hidden = false;
    _txError('', 'transformFormError');
  }
}

// "Removed "Friendly". Undo", for ten seconds, as for the words and snippets.
function _txShowUndo(text, undo) {
  const row = document.getElementById('transformsUndo');
  const label = document.getElementById('transformsUndoText');
  const btn = document.getElementById('transformsUndoBtn');
  if (!row || !label || !btn) return;
  label.textContent = text;
  btn.onclick = async () => { _txHideUndo(); await undo(); };
  row.hidden = false;
  clearTimeout(_txUndoTimer);
  _txUndoTimer = setTimeout(_txHideUndo, 10000);
}

function _txHideUndo() {
  clearTimeout(_txUndoTimer);
  const row = document.getElementById('transformsUndo');
  if (row) row.hidden = true;
}

// By id, not by place in the list: the list may have been drawn again since
// the click that meant this one.
async function deleteTransform(id) {
  const before = _txView().custom.map((c) => ({ id: c.id, name: c.name, prompt: c.prompt }));
  const gone = before.find((c) => c.id === id);
  if (!gone) return;
  const i = before.indexOf(gone);
  _txEditing = -1;
  _txDraft = null;
  if (await saveTransforms({ custom: before.filter((c) => c.id !== id) }, 'transformFormError')) {
    const left = [...document.querySelectorAll('#transformsList .tx-del')];
    const to = WL.focusAfterRemove(i, left.length);
    const target = to >= 0 ? left[to] : document.getElementById('transformName');
    if (target) target.focus();
    _txShowUndo(WL.transformSaved('removed', gone.name), async () => {
      if (await saveTransforms({ custom: before }, 'transformFormError')) showToast(WL.transformSaved('added', gone.name), 'success', 3500);
    });
  }
}

// Settings, General: "Keep what I dictated on the clipboard" (Mac only), from
// the last get_settings() answer. With paste off the text is only copied, so
// the switch is fixed and says so.
function renderClipboardRow() {
  const row = document.getElementById('clipboardAfterPasteRow');
  const tog = document.getElementById('keepClipboardToggle');
  const desc = document.getElementById('clipboardAfterPasteDesc');
  if (!row || !tog) return;
  const s = _lastSettings || {};
  const v = WL.clipboardAfterPasteView(s.auto_paste !== false, s.clipboard_after_paste, isMacPlatform);
  row.hidden = !v.visible;
  tog.checked = v.checked;
  tog.disabled = v.disabled;
  if (desc && v.desc) desc.textContent = v.desc;
}

// Settings, General: the Sounds and Show tips switches, from the last
// get_settings() answer.
function renderFeedbackRows() {
  const v = WL.feedbackView(_lastSettings);
  const sounds = document.getElementById('soundsToggle');
  const tips = document.getElementById('tipsToggle');
  if (sounds) sounds.checked = v.sounds;
  if (tips) tips.checked = v.tips;
}

// Save one of the two switches; a refused save puts the switch back.
async function setFeedback(key, on) {
  const ok = await saveSetting(key, !!on);
  if (ok) {
    if (_lastSettings) _lastSettings[key] = !!on;
  } else {
    renderFeedbackRows();
  }
}

async function setClipboardAfterPaste(keep) {
  const mode = keep ? 'keep' : 'restore';
  const ok = await saveSetting('clipboard_after_paste', mode);
  if (ok) {
    if (_lastSettings) _lastSettings.clipboard_after_paste = mode;
  } else {
    const tog = document.getElementById('keepClipboardToggle');
    if (tog) tog.checked = !keep;
  }
}

// ── Restart dialog ───────────────────────────────────────────────────────
// After a key is saved the app needs a fresh start to use it. The one
// modal: Restart now is the primary; Later is a quiet link. Esc
// dismisses; Enter restarts.
function showRestartBanner(reason) {
  const prior = document.getElementById('restartRequiredModal');
  if (prior) prior.remove();

  const overlay = document.createElement('div');
  overlay.id = 'restartRequiredModal';
  overlay.className = 'modal-overlay restart-modal-overlay';
  overlay.innerHTML = `
    <div class="modal restart-modal-card" role="dialog" aria-modal="true" aria-labelledby="restartModalTitle" tabindex="-1">
      <span class="itile">${WI.icon('refresh')}</span>
      <h2 class="modal-title" id="restartModalTitle">Restart Waffler to use it</h2>
      <p class="modal-sub">${escHtml(reason || 'Your change needs a fresh start to take effect.')}</p>
      <div class="modal-acts">
        <button type="button" class="btn btn-quiet" id="restartModalLater">Later</button>
        <span class="modal-sp"></span>
        <button type="button" class="btn btn-pri" id="restartModalNow">Restart now</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  modalOpened(overlay);

  const dismiss = () => {
    overlay.classList.add('restart-modal-closing');
    setTimeout(() => { overlay.remove(); modalClosed(overlay); }, 180);
    document.removeEventListener('keydown', onKey);
  };

  const doRestart = async () => {
    const btn = document.getElementById('restartModalNow');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Restarting…';
    }
    try {
      if (window.pywebview?.api?.restart_app) {
        await pywebview.api.restart_app();
      } else {
        showToast('Quit and reopen Waffler to use it.', 'info');
        dismiss();
      }
    } catch (_e) {
      showToast("Couldn't restart. Quit and reopen Waffler.", 'error');
      dismiss();
    }
  };

  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); dismiss(); }
    else if (e.key === 'Enter') { e.preventDefault(); doRestart(); }
  };

  document.getElementById('restartModalNow').onclick = doRestart;
  document.getElementById('restartModalLater').onclick = dismiss;
  // Clicking the dimmed backdrop (but not the card) also dismisses.
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) dismiss();
  });
  document.addEventListener('keydown', onKey);

  requestAnimationFrame(() => {
    const btn = document.getElementById('restartModalNow');
    if (btn) btn.focus();
  });
}

function _keyError(inp, msg) {
  if (inp) { inp.setAttribute('aria-invalid', 'true'); inp.focus(); }
  showToast(msg, 'error', 5000);
}

async function _afterKeySaved(inp, name) {
  if (inp) { inp.value = ''; inp.removeAttribute('aria-invalid'); }
  closeKeyEditor();
  showToast(`${name} key saved.`, 'success');
  showRestartBanner(`Waffler starts using the new ${name} key after a restart.`);
  await loadSettings();
}

async function saveGroqKey() {
  const inp = document.getElementById('groqKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { _keyError(inp, 'Paste your Groq key first.'); return; }
  if (!val.startsWith('gsk_')) { _keyError(inp, "That isn't a Groq key. Groq keys start with gsk_."); return; }
  try {
    const r = await pywebview.api.save_settings({ groq_key: val });
    if (r.ok) await _afterKeySaved(inp, 'Groq');
    else {
      console.warn('save_settings failed:', r.error);
      showToast("Couldn't save the key. Try again.", 'error');
    }
  } catch(e) {
    showToast("Couldn't save the key. Try again.", 'error');
  }
}

async function saveCerebrasKey() {
  const inp = document.getElementById('cerebrasKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { _keyError(inp, 'Paste your Cerebras key first.'); return; }
  if (!val.startsWith('csk-')) { _keyError(inp, "That isn't a Cerebras key. Cerebras keys start with csk-."); return; }
  try {
    // validate_cerebras_key persists on success.
    const r = await pywebview.api.validate_cerebras_key(val);
    if (r.ok) await _afterKeySaved(inp, 'Cerebras');
    else showToast(r.error || "Couldn't check that key with Cerebras. Try again in a moment.", 'error', 6000);
  } catch(e) {
    showToast("Couldn't save the key. Try again.", 'error');
  }
}

async function saveApiKey() {
  const inp = document.getElementById('apiKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { _keyError(inp, 'Paste your OpenAI key first.'); return; }
  if (!val.startsWith('sk-')) { _keyError(inp, "That isn't an OpenAI key. OpenAI keys start with sk-."); return; }
  try {
    const r = await pywebview.api.save_settings({ api_key: val });
    if (r.ok) await _afterKeySaved(inp, 'OpenAI');
    else {
      console.warn('save_settings failed:', r.error);
      showToast("Couldn't save the key. Try again.", 'error');
    }
  } catch(e) {
    showToast("Couldn't save the key. Try again.", 'error');
  }
}

// True when the setting was saved.
async function saveSetting(key, value) {
  try {
    const r = await pywebview.api.save_settings({ [key]: value });
    if (r.ok) {
      if (key === 'auto_paste') {
        showToast(value ? 'Waffler pastes when you let go.' : 'Waffler puts the text on the clipboard. Paste it yourself.', 'success', 3500);
        if (_lastSettings) _lastSettings.auto_paste = !!value;
        renderClipboardRow();
      } else if (key === 'clipboard_after_paste') {
        showToast(WL.clipboardAfterPasteSaved(value), 'success', 3500);
      } else if (key === 'sounds' || key === 'tips') {
        showToast(WL.feedbackSaved(key, value), 'success', 3500);
      } else if (key === 'cleanup') {
        showToast(WL.cleanupSaved(value === 'on'), 'success', 3500);
      } else if (key === 'style_by_category') {
        const kind = Object.keys(value)[0];
        showToast(WL.styleSaved(kind, value[kind]), 'success', 3500);
      } else if (key === 'email_signature' && 'enabled' in value) {
        showToast(WL.signatureSaved(value.enabled), 'success', 3500);
      } else if (key === 'typing_wpm') {
        showToast(WL.typingSpeedSaved(value), 'success', 3500);
      } else if (key === 'learn_from_edits') {
        showToast(WL.learnSaved(value), 'success', 3500);
      } else if (key === 'double_tap_handsfree') {
        showToast(WL.doubleTapSaved(value), 'success', 3500);
      } else if (key === 'language') {
        const known = _languageList.find((l) => l.code === value);
        showToast(WL.languageSaved(value, known ? known.name : ''), 'success');
      } else if (key === 'dialect') {
        const labels = { 'auto': 'Spelling matches how you speak.', 'en-GB': 'Spelling: British English.', 'en-US': 'Spelling: American English.' };
        showToast(labels[value] || 'Spelling saved.', 'success');
      }
      return true;
    }
    showToast(WL.saveFailedMessage(r), 'error');       // the app's own sentence when it wrote one
  } catch(e) {
    console.warn('saveSetting error:', e);
    showToast(WL.SAVE_FAILED, 'error');
  }
  return false;
}

// ── Search ────────────────────────────────────────────────────────────────
let _searchQuery = '';
let _searchText = '';  // as typed, for "No entries match "…""

// The search runs once typing pauses (logic.js SEARCH_DEBOUNCE_MS), not on
// every keystroke, and asks the backend for the first page of matches.
const _renderFeedSoon = WL.debounce(() => renderFeed(), WL.SEARCH_DEBOUNCE_MS);

function onSearchInput(q) {
  _searchText = String(q || '').trim();
  _searchQuery = _searchText.toLowerCase();
  _renderFeedSoon();
}

// "Clear search" on the no-matches state: empties the box and shows every
// entry straight away.
function clearSearch() {
  const input = document.getElementById('searchInput');
  if (input) { input.value = ''; input.focus(); }
  _searchText = '';
  _searchQuery = '';
  _renderFeedSoon.cancel();
  renderFeed();
}

// ============================================================
// ── First-run setup (3.15) ───────────────────────────────────
// ============================================================
// Three steps on Windows, four on a Mac (logic.js setupSteps):
//   connect      Connect your free Groq account
//   permissions  (Mac) Let Waffler listen and type for you
//   try          Hold <hotkey> and talk: a full practice dictation
//   anywhere     Now use it anywhere: Notepad or TextEdit, start at sign-in
// Each step is a <section class="ob-step"> in index.html. A step's state
// lives in its data-state, and elements marked data-when="a b" show only in
// those states (wizSetState), so the markup holds every sentence and this
// code only switches between them.
// Existing users never see this: it opens only when setup is needed.

const WIZ_STEPS = WL.setupSteps(isMacPlatform);
const WIZ_LABELS = { connect: 'Connect Groq', permissions: 'Permissions', try: 'Try it', anywhere: 'Use it anywhere' };
const WIZ_SECTIONS = { connect: 'obStepConnect', permissions: 'obStepPermissions', try: 'obStepTry', anywhere: 'obStepAnywhere' };
let _wizardStep = '';
let _wizardShown = false;
let _wizKeyOk = false;          // a Groq key passed the check
let _wizardMicTested = false;   // a practice dictation came back
let _wizardHotkeyTestActive = false;
let _wizDictationLive = false;  // the real hotkey is listening (Notepad/TextEdit)
let _wizLastWrote = '';
let _wizLoginChoice = true;     // start at sign-in: on by default (owner decision D6)
let _currentWizardHotkey = isMacPlatform ? ['fn'] : ['win', 'ctrl'];

async function checkOnboarding() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    if (_wizardShown) return;
    const status = await pywebview.api.get_onboarding_status();
    if (status.needs_setup) {
      showWizard(status);
    } else {
      const main = document.getElementById('mainArea');
      if (main) main.style.display = '';
      refreshAll();
    }
  } catch(e) {
    console.warn('checkOnboarding error:', e);
  }
}

function showWizard(status) {
  const overlay = document.getElementById('wizardOverlay');
  if (!overlay || _wizardShown) return;
  _wizardShown = true;
  overlay.style.display = 'flex';
  // Hide main app UI
  const main = document.getElementById('mainArea');
  if (main) main.style.display = 'none';
  const settings = document.getElementById('settingsPanel');
  if (settings) settings.style.display = 'none';
  const vocab = document.getElementById('vocabularyPanel');
  if (vocab) vocab.style.display = 'none';
  // Setup covers the top bar: keep it out of Tab and out of screen readers
  // until setup closes.
  _setTopbarInert(true);
  document.body.dataset.platform = isMacPlatform ? 'mac' : 'win';
  if (window.WafflerIcons) WafflerIcons.mount(document);
  wizRenderHotkey();
  wizRefreshHotkey();

  // A key already saved (setup left part-way, or a Mac "Quit & Reopen")
  // carries on where it was.
  let first = 'connect';
  if (status && status.has_key) {
    _wizKeyOk = true;
    wizSetState('connect', 'connected');
    wizRenderServices(null);
    if (WIZ_STEPS.includes(status.resume_step)) first = status.resume_step;
  }
  wizShowStep(first);
}

function hideWizard() {
  wizStopPractice();
  stopWizClipboardWatch();
  wizStopPermissionPoll();
  wizStopRecTimer();
  const overlay = document.getElementById('wizardOverlay');
  if (!overlay) return;
  overlay.classList.add('hiding');
  setTimeout(() => {
    overlay.style.display = 'none';
    overlay.classList.remove('hiding');
    _wizardShown = false;
    _setTopbarInert(false);
    showPage('home');
    // Focus would otherwise fall to the page itself: start at the Journal.
    const title = document.getElementById('journalTitle');
    if (title) title.focus();
    refreshAll();
    loadAudioDevices();
  }, 400);
}

function _setTopbarInert(on) {
  const bar = document.getElementById('topbar');
  if (!bar) return;
  bar.inert = !!on;
  if (on) bar.setAttribute('aria-hidden', 'true');
  else bar.removeAttribute('aria-hidden');
}

function _wizardVisible() {
  const o = document.getElementById('wizardOverlay');
  return !!o && o.style.display !== 'none';
}

// ── Setup: where focus goes ───────────────────────────────────────────────
// A new step puts focus on its title, so a screen reader reads it. When a
// state change hides the focused control (Get my free Groq key, the key box
// once the key works, an Allow that became "Allowed") or disables it, focus
// moves to the first control the new state shows, or else to the title.
// Focus that is still on something visible is left alone, so typing a key
// is never interrupted.
function _wizVisibleTitle(sec) {
  if (!sec) return null;
  return [...sec.querySelectorAll('.ob-title')].find((h) => !h.hidden && h.getClientRects().length) || null;
}

function _wizFocusLost() {
  const overlay = document.getElementById('wizardOverlay');
  const a = document.activeElement;
  if (!a || a === document.body || !overlay || !overlay.contains(a)) return true;
  return !!a.disabled || !a.getClientRects().length || !!a.closest('[hidden]');
}

function _wizFocus(el) {
  if (!el) return;
  try { el.focus({ preventScroll: false }); } catch (_) { el.focus(); }
}

// The Try step says what is happening, for screen readers: progress in a
// status region, and silence, an error or no microphone as an alert. The
// words are the ones on screen (the callouts), so there is one copy.
const _WIZ_TRY_PROBLEMS = ['silent', 'error', 'nomic'];
const _WIZ_TRY_PROGRESS = { rec: "Recording. Let go when you're done.", clean: 'Tidying it up.' };

function _wizSay(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  // Emptied first, so the same sentence twice is still read twice.
  el.textContent = '';
  if (text) setTimeout(() => { el.textContent = text; }, 60);
}

function _wizAnnounceTry(sec, state) {
  const words = (sel) => {
    const el = sec.querySelector(sel);
    return el ? (el.innerText || el.textContent).replace(/\s+/g, ' ').trim() : '';
  };
  if (_WIZ_TRY_PROBLEMS.includes(state)) {
    _wizSay('obTryLive', '');
    _wizSay('obTryAlert', words(`.ob-callout[data-when="${state}"]`));
    return;
  }
  _wizSay('obTryAlert', '');
  if (state === 'done') {
    const wrote = words('#obWrote');
    _wizSay('obTryLive', words('.ob-callout[data-when="done"]') + (wrote ? ` Waffler wrote: ${wrote}` : ''));
    return;
  }
  _wizSay('obTryLive', _WIZ_TRY_PROGRESS[state] || '');
}

function _wizLabelDialog(sec) {
  const h = _wizVisibleTitle(sec);
  const overlay = document.getElementById('wizardOverlay');
  if (h && h.id && overlay) overlay.setAttribute('aria-labelledby', h.id);
}

function _wizRehomeFocus(sec, state) {
  if (!_wizardVisible() || !sec || !_wizFocusLost()) return;
  // Another dialog (the hotkey one) is open over setup: leave it be.
  if (typeof _openModalOverlay === 'function' && _openModalOverlay()) return;
  const shown = [...sec.querySelectorAll('[data-when]')]
    .filter((el) => el.dataset.when.split(' ').includes(state));
  for (const el of shown) {
    const f = _focusables(el);
    if (f.length) { _wizFocus(f[0]); return; }
  }
  _wizFocus(_wizVisibleTitle(sec));
}

function _wizSection(step) {
  return document.getElementById(WIZ_SECTIONS[step || _wizardStep]);
}

// Show the parts of a step meant for this state and hide the rest.
function wizSetState(step, state) {
  const sec = _wizSection(step);
  if (!sec) return;
  const changed = sec.dataset.state !== state;
  sec.dataset.state = state;
  sec.querySelectorAll('[data-when]').forEach((el) => {
    el.hidden = !el.dataset.when.split(' ').includes(state);
  });
  if (step === _wizardStep) {
    wizUpdateNextButton();
    _wizLabelDialog(sec);
    if (step === 'try' && (changed || _WIZ_TRY_PROBLEMS.includes(state))) _wizAnnounceTry(sec, state);
    _wizRehomeFocus(sec, state);
  }
}

function wizRenderStepper() {
  const host = document.getElementById('obStepper');
  if (!host) return;
  const cur = WIZ_STEPS.indexOf(_wizardStep);
  host.innerHTML = WIZ_STEPS.map((s, i) => {
    const cls = i < cur ? 'is-done' : i === cur ? 'is-on' : '';
    const n = i < cur ? '<svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>' : String(i + 1);
    return `<li class="${cls}"${i === cur ? ' aria-current="step"' : ''}><span class="ob-sn">${n}</span><span class="ob-slabel">${WIZ_LABELS[s]}</span></li>`;
  }).join('');
  // "Step one of three" (four on a Mac), above Connect's title.
  const kick = document.getElementById('obKick');
  const words = ['one', 'two', 'three', 'four', 'five'];
  if (kick) kick.textContent = `Step one of ${words[WIZ_STEPS.length - 1] || WIZ_STEPS.length}`;
  wizRenderHotkey();
}

function wizShowStep(step) {
  if (!WIZ_STEPS.includes(step)) step = WIZ_STEPS[0];
  const prev = _wizardStep;
  // Leaving a step stops what it started.
  if (prev === 'try' && step !== 'try') wizStopPractice();
  if (prev === 'permissions' && step !== 'permissions') wizStopPermissionPoll();
  if (step !== 'connect') stopWizClipboardWatch();

  _wizardStep = step;
  document.body.setAttribute('data-wiz-step', step);
  // The shown step gets no inline display at all, so its stylesheet layout
  // (the two-column grid) applies.
  Object.entries(WIZ_SECTIONS).forEach(([s, id]) => {
    const con = document.getElementById(id);
    if (!con) return;
    if (s === step) con.style.removeProperty('display');
    else con.style.display = 'none';
  });
  // Every step's parts start in the right state.
  const sec = _wizSection(step);
  if (sec) wizSetState(step, sec.dataset.state);
  wizRenderStepper();
  wizUpdateNextButton();
  // A new step: its title takes focus (Continue may now be disabled, and
  // the button that moved here may be hidden).
  if (step !== prev && _wizardVisible()) _wizFocus(_wizVisibleTitle(sec));
  try { pywebview.api.save_setup_step(step); } catch (_) {}

  if (step === 'connect') wizInitConnect();
  if (step === 'permissions') wizStartPermissionPoll();
  if (step === 'try') wizInitTryItStep();
  if (step === 'anywhere') wizInitAnywhere();
}

function wizUpdateNextButton() {
  const btn = document.getElementById('wizBtnNext');
  const label = document.getElementById('wizBtnNextLabel');
  const back = document.getElementById('wizBtnBack');
  const skip = document.getElementById('wizBtnSkip');
  const left = document.getElementById('obFootLeft');
  if (!btn) return;
  const step = _wizardStep;
  const i = WIZ_STEPS.indexOf(step);
  const sec = _wizSection(step);
  const state = sec ? sec.dataset.state : '';
  let enabled = false;
  if (step === 'connect') enabled = _wizKeyOk;
  if (step === 'permissions') enabled = _wizPermsAll();
  if (step === 'try') enabled = _wizardMicTested;
  if (step === 'anywhere') enabled = true;
  btn.disabled = !enabled;
  if (label) label.textContent = step === 'anywhere' ? 'Done' : 'Continue';
  btn.title = !enabled && step === 'try' ? 'Hold the keys and talk once, or skip for now.' : '';
  // Back once the real hotkey is on would start a second listener.
  if (back) back.hidden = i <= 0 || (step === 'anywhere' && _wizDictationLive);
  // "Skip for now" is there until a practice dictation works, so a
  // microphone problem never strands anyone.
  if (skip) skip.hidden = !(step === 'try' && !_wizardMicTested);
  if (left) left.hidden = !(step === 'connect' && state === 'start');
}

async function wizNext() {
  const btn = document.getElementById('wizBtnNext');
  if (btn && btn.disabled) return;
  const i = WIZ_STEPS.indexOf(_wizardStep);
  if (_wizardStep === 'anywhere') { await wizCompleteSetup(); return; }
  if (i >= 0 && i < WIZ_STEPS.length - 1) wizShowStep(WIZ_STEPS[i + 1]);
}

function wizBack() {
  const i = WIZ_STEPS.indexOf(_wizardStep);
  if (i > 0) wizShowStep(WIZ_STEPS[i - 1]);
}

// "Skip for now" skips the practice, not the rest of setup: the last step
// still offers Notepad or TextEdit and start at sign-in.
async function wizSkipTryIt() {
  wizShowStep('anywhere');
}

// ── Clipboard key pickup (Connect) ────────────────────────────────────────
// Setup's most annoying moment is the hand-off: create a key on Groq's
// site, copy it, come back, find the field, paste. The copy has already
// happened, so the app can just notice.
//
// The backend only ever returns text matching a known key shape, so ordinary
// clipboard contents are never read into the UI. Filling the field is not
// irreversible either: the user can clear or overwrite it.
//
// Check the clipboard ONCE, on demand. Never on a timer. This used to poll
// every 1200 ms while the key step was open. Windows Defender's behavioural
// model started flagging the app as Behavior:Win32/CredentialAccess.A!ml on
// 2026-09-22 and deleting Waffler.exe mid-install, and a process repeatedly
// reading the clipboard and regex-matching it for `sk-` / `gsk_` secrets is
// the most credential-stealer-shaped thing in the codebase. The only moment
// the poll ever caught anything was the alt-tab back from the provider's
// website. That moment IS a window focus event, so the clipboard is read
// then: once on arriving, once per return to the window, and when Paste is
// clicked.
let _wizClipTimer = null;        // legacy poll handle, kept so an in-flight
                                 // timer from a previous build is cleared
let _wizClipLastSeen = '';
let _wizClipLastCheck = 0;
let _wizClipOnFocus = null;

async function _wizCheckClipboardOnce(fromClick) {
  if (!(window.pywebview && pywebview.api && pywebview.api.peek_clipboard_key)) return false;
  // Debounce: a focus flap must not turn back into a poll.
  const now = Date.now();
  if (!fromClick && now - _wizClipLastCheck < 400) return false;
  _wizClipLastCheck = now;
  if (_wizKeyOk && !fromClick) return false;
  try {
    const r = await pywebview.api.peek_clipboard_key();
    if (!r || !r.found || !r.key || r.provider !== 'groq') return false;
    if (r.key === _wizClipLastSeen && !fromClick) return false;   // already handled this one
    const el = document.getElementById('wizGroqKeyInput');
    if (!el) return false;
    _wizClipLastSeen = r.key;
    el.value = r.key;
    wizValidateGroqKey(r.key, true);
    return true;
  } catch (e) { return false; }  // clipboard unavailable: the user can still paste by hand
}

function startWizClipboardWatch() {
  stopWizClipboardWatch();
  if (!(window.pywebview && pywebview.api && pywebview.api.peek_clipboard_key)) return;
  _wizClipOnFocus = () => { _wizCheckClipboardOnce(false); };
  window.addEventListener('focus', _wizClipOnFocus);
  // Coming back to the window does not always fire focus in WebView2 (a
  // click on the title bar or the taskbar), so the page becoming visible
  // counts too. Both are events, never a timer, and the check is debounced.
  document.addEventListener('visibilitychange', _wizClipOnVisible);
  _wizCheckClipboardOnce(false);
}

function _wizClipOnVisible() {
  if (document.visibilityState === 'visible' && _wizClipOnFocus) _wizClipOnFocus();
}

function stopWizClipboardWatch() {
  if (_wizClipTimer) { clearInterval(_wizClipTimer); _wizClipTimer = null; }
  if (_wizClipOnFocus) {
    window.removeEventListener('focus', _wizClipOnFocus);
    document.removeEventListener('visibilitychange', _wizClipOnVisible);
    _wizClipOnFocus = null;
  }
}

// The Paste button: the same one-off check, started by the click.
async function wizPasteKey() {
  const found = await _wizCheckClipboardOnce(true);
  if (!found) {
    wizKeyMessage('alert', "There's no Groq key on the clipboard yet.", 'In Groq, click Copy, then try again. Or paste it with Ctrl+V.'.replace('Ctrl+V', isMacPlatform ? 'Cmd+V' : 'Ctrl+V'));
  }
}

// ── Step: Connect your free Groq account ─────────────────────────────────

let _wizKeyListenerAttached = false;
let _wizKeyRetries = 0;

function wizInitConnect() {
  startWizClipboardWatch();
  if (_wizKeyListenerAttached) return;
  _wizKeyListenerAttached = true;
  const inp = document.getElementById('wizGroqKeyInput');
  if (!inp) return;
  const check = WL.debounce(() => {
    const v = WL.keyInputView(inp.value);
    if (v.kind === 'ok') wizValidateGroqKey(inp.value.trim(), false);
  }, 600);
  // A key that isn't one yet ("too short") is only pointed out once typing
  // pauses, not on every character.
  const complainNow = () => {
    const v = WL.keyInputView(inp.value);
    if (v.kind === 'ok' || v.kind === 'empty') return;
    wizSetState('connect', 'error');
    wizKeyMessage('alert', v.message, '');
    inp.setAttribute('aria-invalid', 'true');
  };
  const complain = WL.debounce(complainNow, 600);
  inp.addEventListener('input', () => {
    _wizKeyOk = false;
    inp.removeAttribute('aria-invalid');
    const v = WL.keyInputView(inp.value);
    if (v.kind === 'empty') { wizKeyWaiting(); check.cancel(); complain.cancel(); return; }
    if (v.kind !== 'ok') {
      check.cancel();
      complain();
      return;
    }
    complain.cancel();
    check();
  });
  inp.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    check.cancel();
    complain.cancel();
    if (WL.keyInputView(inp.value).kind === 'ok') wizValidateGroqKey(inp.value.trim(), false);
    else complainNow();
  });
}

// "Get my free Groq key": Groq's key page in the browser, and this screen
// switches to waiting for the key to come back.
function wizOpenGroq() {
  try { pywebview.api.open_url('https://console.groq.com/keys'); } catch (_) {}
  if (!_wizKeyOk) wizKeyWaiting();
}

// "Already have a key? Paste it here"
function wizPasteInstead() {
  wizKeyWaiting();
  const inp = document.getElementById('wizGroqKeyInput');
  if (inp) setTimeout(() => inp.focus(), 50);
}

function wizKeyWaiting() {
  wizSetState('connect', 'waiting');
  _wizKeyStateHtml('ob-keystate', '<span class="ob-pulse" aria-hidden="true"></span><span>Waiting for your key. Waffler picks it up when you come back.</span>');
}

// #obKeyState is a live region: it is only rewritten when the message
// changes, so a screen reader hears each message once (it used to be
// rewritten on every keystroke).
function _wizKeyStateHtml(cls, html) {
  const box = document.getElementById('obKeyState');
  if (!box || (box.dataset.msg === html && box.className === cls)) return;
  box.className = cls;
  box.innerHTML = html;
  box.dataset.msg = html;
}

// A line in the key box: an icon tile, a sentence, and a second line.
function wizKeyMessage(icon, title, detail) {
  const box = document.getElementById('obKeyState');
  if (!box) return;
  const tile = icon === 'loader'
    ? '<span class="ob-pulse" aria-hidden="true"></span>'
    : `<span class="itile ${icon === 'alert' ? 'is-err' : 'is-ok'}"><svg class="ic" aria-hidden="true"><use href="#i-${icon}"/></svg></span>`;
  _wizKeyStateHtml('ob-keystate' + (icon === 'alert' ? ' is-err' : ''),
    `${tile}<span><b class="ob-keystate-t">${escHtml(title)}</b>`
    + (detail ? `<span class="ob-keystate-d">${escHtml(detail)}</span>` : '') + '</span>');
}

async function wizValidateGroqKey(key, fromClipboard) {
  const inp = document.getElementById('wizGroqKeyInput');
  wizSetState('connect', 'checking');
  wizKeyMessage('loader', fromClipboard ? 'Got your key. Checking it with Groq…' : 'Checking your key with Groq…', '');
  let r;
  try {
    r = await pywebview.api.validate_groq_key(key);
  } catch (e) {
    r = { ok: false, error: "Couldn't check that key. Check you're online and try again." };
  }
  if (inp && inp.value.trim() !== key) return;   // the field changed meanwhile
  if (r && r.ok) {
    _wizKeyOk = true;
    _wizKeyRetries = 0;
    if (inp) inp.removeAttribute('aria-invalid');
    const tail = document.getElementById('obKeyTail');
    if (tail) tail.textContent = key.slice(0, 8) + '…' + key.slice(-4);
    wizRenderServices(r.services);
    wizSetState('connect', 'connected');
    stopWizClipboardWatch();
    return;
  }
  _wizKeyOk = false;
  // A busy moment at Groq: say so and try again by itself.
  if (r && r.kind === 'rate_limited' && _wizKeyRetries < 3) {
    _wizKeyRetries += 1;
    wizKeyMessage('loader', 'Groq is busy for a moment.', 'Trying again in a few seconds.');
    wizRetryKeyCheckSoon(key);
    return;
  }
  _wizKeyRetries = 0;
  const m = WL.splitMessage((r && r.error) || "Couldn't check that key. Try again.");
  wizSetState('connect', 'error');
  wizKeyMessage('alert', m.title + '.', m.subtitle);
  if (inp && r && r.kind === 'unauthorized') inp.setAttribute('aria-invalid', 'true');
  wizUpdateNextButton();
}

function wizRetryKeyCheckSoon(key) {
  setTimeout(() => {
    const inp = document.getElementById('wizGroqKeyInput');
    if (_wizardStep === 'connect' && inp && inp.value.trim() === key) wizValidateGroqKey(key, false);
  }, 5000);
}

function wizRenderServices(services) {
  const host = document.getElementById('obServices');
  if (!host) return;
  const rows = WL.serviceRows(services).map((v) => `
    <div class="row">
      <span class="itile ${v.tile}"><svg class="ic" aria-hidden="true"><use href="#i-${v.icon}"/></svg></span>
      <div class="row-main"><div class="row-t">${escHtml(v.title)}</div><div class="row-d">${escHtml(v.desc)}</div></div>
      <span class="chip ${v.chipCls}">${escHtml(v.chip)}</span>
    </div>`).join('');
  host.innerHTML = rows + `
    <div class="row">
      <span class="itile"><svg class="ic" aria-hidden="true"><use href="#i-plus"/></svg></span>
      <div class="row-main"><div class="row-t">Backup <span class="chip">Optional</span></div><div class="row-d">Add an OpenAI key later in Settings.</div></div>
    </div>`;
}

// ── Step: Mac permissions ────────────────────────────────────────────────
// Each Allow calls the prompt macOS provides (app.py request_permission,
// src/mac_permissions.py), so Waffler is already in each list and the user
// flips one switch. A check every second ticks the rows; when all three are
// allowed, setup moves on by itself.
let _wizPerms = { microphone: false, input_monitoring: false, accessibility: false };
let _wizPermPollTimer = null;
let _wizPermsWereMissing = false;
const _WIZ_PERM_ROWS = { microphone: 'obPermMic', input_monitoring: 'obPermKeys', accessibility: 'obPermTyping' };
const _WIZ_PERM_ALERTS = {
  microphone: { title: '“Waffler” would like to access the microphone.', body: 'Waffler needs microphone access for voice transcription.',
    buttons: ["Don't Allow", 'Allow'], row: true, cap: 'Click Allow. Waffler notices by itself.' },
  input_monitoring: { title: '“Waffler” would like to receive keystrokes from any application.', body: 'Grant access to this application in Privacy & Security settings, located in System Settings.',
    buttons: ['Open System Settings', 'Deny'], row: false, cap: 'Turn Waffler on, then come back here.' },
  accessibility: { title: '“Waffler” would like to control this computer using accessibility features.', body: 'Grant access to this application in Privacy & Security settings, located in System Settings.',
    buttons: ['Open System Settings', 'Deny'], row: false, cap: 'Turn Waffler on, then come back here.' },
};

function _wizPermsAll() {
  return !isMacPlatform || (_wizPerms.microphone && _wizPerms.input_monitoring && _wizPerms.accessibility);
}

// Each Allow is named for what it allows, for screen readers and voice
// control ("Allow" three times said nothing).
const _WIZ_PERM_NAMES = { microphone: 'microphone', input_monitoring: 'keyboard monitoring', accessibility: 'typing for you' };
let _wizPermsShown = null;

function wizRenderPermissions() {
  const order = ['microphone', 'input_monitoring', 'accessibility'];
  const next = order.find((p) => !_wizPerms[p]);
  const newlyAllowed = _wizPermsShown ? order.filter((p) => _wizPerms[p] && !_wizPermsShown[p]) : [];
  let redrawn = false;
  order.forEach((p) => {
    const row = document.getElementById(_WIZ_PERM_ROWS[p]);
    const ctl = row && row.querySelector('.ob-perm-ctl');
    if (!ctl) return;
    // Redraw only what changed, so a focused Allow isn't replaced every second.
    const want = _wizPerms[p] ? 'ok' : (p === next ? 'next' : 'later');
    if (ctl.dataset.view === want) return;
    ctl.dataset.view = want;
    redrawn = true;
    ctl.innerHTML = _wizPerms[p]
      ? '<span class="chip chip-ok"><svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>Allowed</span>'
      : `<button class="btn btn-sm ${p === next ? 'btn-pri' : 'btn-sec'}" aria-label="Allow ${_WIZ_PERM_NAMES[p]}" onclick="wizAllow('${p}')">Allow</button>`;
  });
  _wizPermsShown = Object.assign({}, _wizPerms);
  const live = document.getElementById('obPermLive');
  if (live && newlyAllowed.length) {
    const said = newlyAllowed.map((p) => _WIZ_PERM_NAMES[p]).join(' and ');
    live.textContent = said.charAt(0).toUpperCase() + said.slice(1) + ' allowed.';
  }
  // The Allow that had focus became "Allowed": move on to the next Allow.
  if (redrawn && _wizardStep === 'permissions' && _wizardVisible() && _wizFocusLost()) {
    const sec = _wizSection('permissions');
    const btn = next && document.querySelector(`#${_WIZ_PERM_ROWS[next]} .ob-perm-ctl button`);
    _wizFocus(btn || _wizVisibleTitle(sec));
  }
  // The picture shows the macOS dialog the next Allow brings up.
  const a = _WIZ_PERM_ALERTS[next || 'accessibility'];
  const put = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  put('obMacAlertTitle', a.title);
  put('obMacAlertBody', a.body);
  put('obMacCap', next ? a.cap : 'All three are allowed. Setup carries on by itself.');
  const btns = document.getElementById('obMacAlertBtns');
  if (btns) {
    btns.classList.toggle('is-row', a.row);
    btns.innerHTML = a.row
      ? `<span class="ob-mac-btn">${a.buttons[0]}</span><span class="ob-mac-btn ob-mac-btn-pri ob-hl">${a.buttons[1]}</span>`
      : `<span class="ob-mac-btn ob-mac-btn-pri ob-hl">${a.buttons[0]}</span><span class="ob-mac-btn">${a.buttons[1]}</span>`;
  }
  const sw = document.getElementById('obMacSwitch');
  if (sw) sw.hidden = a.row;
}

async function wizAllow(name) {
  try { await pywebview.api.request_permission(name); } catch (_) {}
  wizPermTick();
}

async function wizPermTick() {
  try {
    const r = await pywebview.api.check_permissions();
    const before = _wizPermsAll();
    _wizPerms = {
      microphone: !!r.mic_granted,
      input_monitoring: !!r.input_monitoring_granted,
      accessibility: !!r.accessibility_granted,
    };
    if (!_wizPermsAll()) _wizPermsWereMissing = true;
    wizRenderPermissions();
    if (_wizardStep === 'permissions') {
      wizUpdateNextButton();
      if (!before && _wizPermsAll() && _wizPermsWereMissing) wizNext();
    }
  } catch (e) { /* poll errors are silent */ }
}

function wizStartPermissionPoll() {
  if (_wizPermPollTimer) return;
  _wizPermsWereMissing = false;
  wizPermTick();
  _wizPermPollTimer = setInterval(wizPermTick, 1000);
}

function wizStopPermissionPoll() {
  if (_wizPermPollTimer) { clearInterval(_wizPermPollTimer); _wizPermPollTimer = null; }
}

// ── Step: Hold <hotkey> and talk ─────────────────────────────────────────

async function wizLoadMicDevices() {
  const sel = document.getElementById('wizMicSelect');
  if (!sel) return;
  try {
    const devices = await pywebview.api.get_audio_devices();
    const current = await pywebview.api.get_selected_device();
    sel.innerHTML = '';
    if (!devices || !devices.length) {
      sel.innerHTML = '<option value="">No microphone found</option>';
      return;
    }
    devices.forEach((d) => {
      const opt = document.createElement('option');
      opt.value = d.index;
      opt.textContent = d.name;
      if ((current && current.index === d.index) || (current && current.index === null && d.is_default)) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (e) {
    console.warn('wizLoadMicDevices error:', e);
  }
}

async function wizInitTryItStep() {
  wizRenderHotkey();
  wizRefreshHotkey();
  wizCheckFnKey();
  wizSetMeter(0);
  const sec = _wizSection('try');
  if (_wizardMicTested && sec && sec.dataset.state === 'done') wizSetState('try', 'done');
  else wizSetState('try', 'wait');
  await wizLoadMicDevices();
  await wizStartPractice();
}

// The practice records from the microphone Waffler would use (the ranking, or
// the computer's own); only one the person picks here is passed on, and
// remembered. The list shows the computer's microphone selected, which is not
// a choice.
let _wizMicPicked = false;

async function wizStartPractice() {
  const sel = document.getElementById('wizMicSelect');
  const idx = _wizMicPicked && sel && sel.value !== '' ? Number(sel.value) : null;
  try {
    const r = await pywebview.api.wizard_start_hotkey_test(idx);
    if (r && r.ok) {
      _wizardHotkeyTestActive = true;
      return;
    }
    _wizardHotkeyTestActive = false;
    if (r && r.mic === 'denied') { wizSetState('try', 'nomic'); return; }
    wizPracticeError((r && r.error) || "Couldn't start the practice. Try again, or skip for now.");
  } catch (e) {
    console.warn('wizStartPractice error:', e);
  }
}

function wizStopPractice() {
  wizStopRecTimer();
  if (_wizardHotkeyTestActive) {
    try { pywebview.api.wizard_stop_hotkey_test(); } catch (_) {}
    _wizardHotkeyTestActive = false;
  }
}

async function wizMicChanged(value) {
  if (value === '') return;
  _wizMicPicked = true;
  try { await pywebview.api.set_audio_device(Number(value)); } catch (_) {}
  if (_wizardStep === 'try') wizStartPractice();
}

// The Fn (Globe) key's own job on a Mac (src/mac_permissions.py). Shown,
// never changed.
async function wizCheckFnKey() {
  const box = document.getElementById('obFnWarn');
  if (!box || !isMacPlatform) return;
  try {
    const r = await pywebview.api.get_fn_key_conflict();
    box.hidden = !(r && r.conflict);
    if (r && r.conflict) {
      document.getElementById('obFnWarnTitle').textContent = r.title;
      document.getElementById('obFnWarnDetail').innerHTML = escHtml(r.detail)
        .replace('\u{1F310}', '<svg class="ic ob-inl-globe" aria-label="Globe"><use href="#i-globe"/></svg>');
    }
  } catch (_) { box.hidden = true; }
}

function wizOpenKeyboardSettings() {
  try { pywebview.api.open_keyboard_settings(); } catch (_) {}
}

// "Doesn't work on this keyboard? Pick another key": this platform's
// choices (logic.js hotkeyPresets). Windows used to be offered
// "Ctrl + Alt + Space", which it never accepted.
function wizShowKeyPicker() {
  const panel = document.getElementById('obKeyPicker');
  const list = document.getElementById('wizHotkeyPresetList');
  if (!panel || !list) return;
  const cur = WL.hotkeyName(_currentHotkeyKeys, isMacPlatform);
  list.textContent = '';
  WL.hotkeyPresets(isMacPlatform).forEach((p) => {
    const b = document.createElement('button');
    b.className = 'btn btn-sec btn-sm';
    b.textContent = p.label;
    b.title = p.hint || '';
    if (!p.custom && p.label === cur) b.classList.add('is-current');
    b.addEventListener('click', () => (p.custom ? openHotkeyCapture() : selectHotkeyPreset(p.keys)));
    list.appendChild(b);
  });
  const err = document.getElementById('obKeyPickerErr');
  if (err) err.hidden = true;
  panel.hidden = !panel.hidden;
}

async function selectHotkeyPreset(keys) {
  if (!_hotkeySaving.enter()) return;
  _hotkeyButtonsBusy(true);
  try {
    let result;
    try {
      result = await pywebview.api.save_hotkey_config(keys);
    } catch (e) {
      console.error('Failed to save hotkey:', e);
      result = { ok: false, error: "Couldn't change the hotkey. Try again." };
    }
    if (!result || !result.ok) {
      // Nothing changed, so the keys stay as they are and the picker says why.
      const err = document.getElementById('obKeyPickerErr');
      if (err) { err.textContent = (result && result.error) || "Couldn't change the hotkey. Try again."; err.hidden = false; }
      return;
    }
    await _onHotkeySaved(result);
    wizHotkeyChanged(result);
  } finally {
    _hotkeySaving.leave();
    _hotkeyButtonsBusy(false);
  }
}

// A new hotkey from the picker or the capture dialog: the practice listens
// for it from now on.
function wizHotkeyChanged(result) {
  const panel = document.getElementById('obKeyPicker');
  if (panel) panel.hidden = true;
  showToast(`Hotkey is now ${result.display || WL.hotkeyName(result.keys, isMacPlatform)}`, 'success');
  wizCheckFnKey();
  if (_wizardStep === 'try') wizStartPractice();
}

// Fetch the saved hotkey and redraw from it.
async function wizRefreshHotkey() {
  try {
    const config = await pywebview.api.get_hotkey_config();
    if (config && config.ok && Array.isArray(config.keys) && config.keys.length) {
      _currentWizardHotkey = config.keys.slice();
      wizRenderHotkey(config.keys);
    }
  } catch (e) {
    console.warn('wizRefreshHotkey error:', e);
  }
}

// Every place setup names the hotkey is drawn from the keys actually saved,
// so choosing another hotkey redraws them all. Text only ever goes into the
// label parts, never over a keycap's icon.
const _KEYCAP_ICONS = { globe: '<svg class="ic" aria-hidden="true"><use href="#i-globe"/></svg>' };

function wizRenderHotkey(keys) {
  if (Array.isArray(keys) && keys.length) _currentHotkeyKeys = keys.slice();
  const caps = WL.keycaps(_currentHotkeyKeys, isMacPlatform);
  const name = WL.hotkeyName(_currentHotkeyKeys, isMacPlatform);
  document.querySelectorAll('#wizardOverlay .ob-hk-name').forEach((el) => { el.textContent = name; });
  const hold = document.getElementById('obHoldKey');
  if (hold) {
    hold.innerHTML = caps.map((c) => (c.icon && _KEYCAP_ICONS[c.icon] ? _KEYCAP_ICONS[c.icon] : '') + escHtml(c.label))
      .join(' + ');
  }
  const any = document.getElementById('obAnyKeys');
  if (any) any.innerHTML = caps.map((c) => `<kbd class="kc kc-md">${escHtml(c.label)}</kbd>`).join('<span class="plus">+</span>');
  // Connect's example: the keys this computer will use.
  const ex = document.getElementById('obExampleKeys');
  if (ex) ex.innerHTML = _capsHtml(_currentHotkeyKeys, 'kc-md');
}

// The live level, drawn as the waffle's 4 x 4 cells: rows fill from the
// bottom as the voice gets louder, like the overlay.
function _wizLevels(level) {
  const l = Math.max(0, Math.min(1, Number(level) || 0));
  const p = Math.pow(l, 0.5);
  const out = [];
  for (let row = 0; row < 4; row++) {
    const threshold = (3 - row) / 4;
    const cell = p <= threshold ? 0 : Math.min(1, (p - threshold) / 0.25);
    for (let col = 0; col < 4; col++) out.push(cell);
  }
  return out;
}

function wizSetMeter(level) {
  if (!window.WafflerIcons) return;
  const meter = document.getElementById('obMeter');
  if (meter) meter.innerHTML = WafflerIcons.waffle({ levels: level ? _wizLevels(level) : 'empty', size: 24 });
  const big = document.getElementById('obBigWaffle');
  if (big) big.innerHTML = WafflerIcons.waffle({ levels: level ? _wizLevels(level) : 'empty', size: 118 });
}

let _wizRecTimer = null;
function wizStopRecTimer() {
  if (_wizRecTimer) { clearInterval(_wizRecTimer); _wizRecTimer = null; }
}

// Called from Python (app.py _wizard_on_press) when the keys go down.
window.wizOnRecordingStart = function() {
  const hold = document.getElementById('obHoldKey');
  if (hold) hold.classList.add('is-down');
  const cap = document.getElementById('obHoldCap');
  if (cap) cap.textContent = 'Holding';
  const panel = document.getElementById('obKeyPicker');
  if (panel) panel.hidden = true;
  wizSetMeter(0);
  wizSetState('try', 'rec');
  const t0 = Date.now();
  const time = document.getElementById('obRecTime');
  wizStopRecTimer();
  if (time) time.textContent = WL.recordingTime(0);
  _wizRecTimer = setInterval(() => {
    if (time) time.textContent = WL.recordingTime((Date.now() - t0) / 1000);
  }, 250);
};

// The microphone level while recording (app.py _wizard_level_loop).
window.wizOnLevel = function(level) {
  if (_wizardStep === 'try') wizSetMeter(level);
};

// The keys came up: the words are on their way to Groq.
window.wizOnRecordingStop = function() {
  const hold = document.getElementById('obHoldKey');
  if (hold) hold.classList.remove('is-down');
  const cap = document.getElementById('obHoldCap');
  if (cap) cap.textContent = 'Hold to talk';
  wizStopRecTimer();
  wizSetMeter(0);
  const said = document.getElementById('obSaid');
  if (said) said.innerHTML = '<span class="ob-skel"><i></i><i></i></span>';
  wizSetState('try', 'clean');
};

// The words came back; the clean-up is running.
window.wizOnCleaning = function(said) {
  const el = document.getElementById('obSaid');
  if (el) el.textContent = said || '';
  wizSetState('try', 'clean');
};

// The practice worked: "You said" next to "Waffler wrote".
window.wizOnPracticeResult = function(r) {
  r = r || {};
  const saidEl = document.getElementById('obSaid');
  if (saidEl) {
    saidEl.innerHTML = WL.saidDiff(r.said || '', r.cleaned ? (r.wrote || '') : (r.said || ''))
      .map((p) => (p.cut ? `<del>${escHtml(p.text)}</del>` : escHtml(p.text))).join('');
  }
  const wroteEl = document.getElementById('obWrote');
  if (wroteEl) wroteEl.textContent = r.wrote || '';
  const savedLine = document.getElementById('obSavedLine');
  if (savedLine) savedLine.hidden = !r.saved;
  const savedChip = document.getElementById('obSavedChip');
  const note = document.getElementById('obPracticeNote');
  _wizLastWrote = r.wrote || '';
  _wizardMicTested = true;
  wizSetState('try', 'done');
  if (savedChip) savedChip.hidden = !r.saved;
  if (note) { note.textContent = r.note || ''; note.hidden = !r.note; }
};

// Nothing was heard: the microphone may be muted, or the wrong one.
window.wizOnSilentRecording = function() {
  const hold = document.getElementById('obHoldKey');
  if (hold) hold.classList.remove('is-down');
  wizStopRecTimer();
  wizSetMeter(0);
  wizSetState('try', 'silent');
};

window.wizOnPracticeError = function(message) { wizPracticeError(message); };

function wizPracticeError(message) {
  const el = document.getElementById('obPracticeError');
  if (el) el.textContent = message || 'Something went wrong with that one. Hold the keys and try again.';
  wizSetState('try', 'error');
}

// ── Step: Now use it anywhere ────────────────────────────────────────────

async function wizInitAnywhere() {
  wizRenderHotkey();
  const put = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  put('obEditorLabel', isMacPlatform ? 'Open TextEdit and try it' : 'Open Notepad and try it');
  put('obTrayTitle', isMacPlatform ? 'Waffler waits in the menu bar' : 'Waffler waits in the tray');
  put('obTrayDesc', isMacPlatform ? "Look for the waffle at the top right. Closing this window doesn't stop it."
                                  : "Under the ^ by the clock. Closing this window doesn't stop it.");
  if (_wizLastWrote) document.querySelectorAll('#obDesk .ob-desk-text').forEach((el) => { el.textContent = _wizLastWrote; });
  const tog = document.getElementById('wizStartAtLogin');
  const desc = document.getElementById('obLoginDesc');
  try {
    const s = await pywebview.api.get_start_at_login();
    if (tog) {
      tog.disabled = !s.supported;
      tog.checked = s.supported ? (s.enabled || _wizLoginChoice) : false;
    }
    if (desc) desc.textContent = s.supported ? 'So the hotkey works after a restart.' : s.reason;
  } catch (_) {
    if (tog) { tog.disabled = true; tog.checked = false; }
  }
}

async function wizLoginToggled(on) {
  _wizLoginChoice = !!on;
  try {
    const r = await pywebview.api.set_start_at_login(!!on);
    const tog = document.getElementById('wizStartAtLogin');
    if (r && !r.ok) {
      if (tog) tog.checked = !!r.enabled;
      showToast(r.error || "Couldn't change starting at sign-in. Try again.", 'error');
    }
  } catch (_) {}
}

// Notepad or TextEdit, with the real hotkey listening: the first real paste
// happens during setup.
async function wizOpenEditor() {
  _wizDictationLive = true;
  wizUpdateNextButton();
  const err = document.getElementById('obEditorErr');
  if (err) err.hidden = true;
  try {
    const r = await pywebview.api.open_practice_editor();
    if (r && !r.ok && err) { err.textContent = r.error; err.hidden = false; }
  } catch (_) {}
}

// Done: start at sign-in as chosen (on unless switched off), then the
// pipeline starts and the Journal opens with the practice as its first
// entry.
async function wizCompleteSetup() {
  const btn = document.getElementById('wizBtnNext');
  const label = document.getElementById('wizBtnNextLabel');
  btn.disabled = true;
  if (label) label.textContent = 'Setting up…';
  try {
    wizStopPractice();
    const tog = document.getElementById('wizStartAtLogin');
    if (tog && !tog.disabled) {
      try { await pywebview.api.set_start_at_login(tog.checked); } catch (_) {}
    }
    const r = await pywebview.api.complete_setup();
    if (r.ok) {
      showToast('Waffler is ready!', 'success');
      hideWizard();
      return;
    }
    console.warn('complete_setup failed:', r.error);
    showToast("Couldn't finish setup. Try again.", 'error');
  } catch(e) {
    console.warn('complete_setup failed:', e);
    showToast("Couldn't finish setup. Try again.", 'error');
  }
  wizUpdateNextButton();
}

// Opening Settings also loads Usage, the version, Privacy and data, start
// at sign-in and the Fn key note.
const _origLoadSettings = loadSettings;
loadSettings = async function() {
  await _origLoadSettings();
  await loadUsageStats();
  await loadAppVersion();
  await loadUnsentSummary();
  await loadRecentAudio();
  await loadHistoryKeep();
  await loadStartAtLogin();
  await loadSettingsFnWarning();
};

// ── Start at sign-in (Settings, General) ─────────────────────────────────
// The switch shows what the operating system has, read each time Settings
// opens: the user can also turn it off in Task Manager or Login Items,
// and the switch then shows Off (src/login_item.py reads Windows' record).
async function loadStartAtLogin() {
  const tog = document.getElementById('startAtLoginToggle');
  const desc = document.getElementById('startAtLoginDesc');
  if (!tog || !window.pywebview || !pywebview.api || !pywebview.api.get_start_at_login) return;
  try {
    const s = await pywebview.api.get_start_at_login();
    tog.checked = !!s.enabled;
    tog.disabled = !s.supported && !s.enabled;
    if (desc) desc.textContent = s.supported
      ? `So the hotkey works after a restart. Waffler waits in the ${isMacPlatform ? 'menu bar' : 'tray'}.`
      : s.reason;
  } catch (e) {
    console.warn('get_start_at_login failed:', e);
  }
}

async function setStartAtLogin(on) {
  const tog = document.getElementById('startAtLoginToggle');
  try {
    const r = await pywebview.api.set_start_at_login(!!on);
    if (tog) tog.checked = !!(r && r.enabled);
    if (r && !r.ok) showToast(r.error || "Couldn't change starting at sign-in. Try again.", 'error');
  } catch (e) {
    if (tog) tog.checked = !on;
    showToast("Couldn't change starting at sign-in. Try again.", 'error');
  }
}

// ── The Fn key's own job (Settings, Hotkey; Mac) ─────────────────────────
async function loadSettingsFnWarning() {
  const box = document.getElementById('settingsFnWarn');
  if (!box || !isMacPlatform || !window.pywebview || !pywebview.api.get_fn_key_conflict) return;
  try {
    const r = await pywebview.api.get_fn_key_conflict();
    box.hidden = !(r && r.conflict);
    if (r && r.conflict) {
      document.getElementById('settingsFnWarnTitle').textContent = r.title;
      document.getElementById('settingsFnWarnDetail').innerHTML = escHtml(r.detail)
        .replace('\u{1F310}', '<svg class="ic ob-inl-globe" aria-label="Globe"><use href="#i-globe"/></svg>');
    }
  } catch (_) { box.hidden = true; }
}

// ── Recordings not sent (Settings, Privacy and data) ─────────────────────
async function loadUnsentSummary() {
  const desc = document.getElementById('unsentSummary');
  const btn = document.getElementById('unsentSendNow');
  const del = document.getElementById('unsentDelete');
  if (!desc || !window.pywebview || !window.pywebview.api || !pywebview.api.get_unsent_summary) return;
  try {
    const v = WL.unsentSummary(await pywebview.api.get_unsent_summary());
    desc.textContent = v.label;
    const title = document.getElementById('unsentTitle');
    if (title) title.textContent = v.canSend ? 'Waiting to be sent' : 'Nothing waiting to be sent';
    const icon = document.getElementById('unsentIcon');
    if (icon) {
      icon.classList.toggle('is-wait', v.canSend);
      icon.innerHTML = WI.icon(v.canSend ? 'wifi-off' : 'check');
    }
    if (btn) btn.hidden = !v.canSend;
    if (del) del.hidden = !v.canSend;
    const confirmTitle = document.getElementById('unsentDeleteTitle');
    if (confirmTitle && v.confirm) confirmTitle.textContent = v.confirm;
    if (!v.canSend) askDeleteUnsent(false);
  } catch (e) {
    console.warn('get_unsent_summary failed:', e);
  }
}

// Delete, next to Try again: asks first, in the panel.
function askDeleteUnsent(ask) {
  const row = document.getElementById('unsentDeleteConfirm');
  if (!row) return;
  row.hidden = !ask;
  if (ask) document.getElementById('unsentDeleteNo').focus();
}

async function deleteAllUnsent(btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await pywebview.api.delete_all_unsent();
    const n = (r && r.deleted) || 0;
    if (r && r.ok) {
      showToast(n === 1 ? '1 recording deleted.' : `${WL.formatCount(n)} recordings deleted.`, 'success');
    } else {
      showToast(n ? `${n} of ${r.total} deleted. Try again in a moment.` : "Couldn't delete them. Try again in a moment.", 'error');
    }
    await refreshAll();
  } catch (e) {
    showToast("Couldn't delete them. Try again in a moment.", 'error');
  } finally {
    if (btn) btn.disabled = false;
    askDeleteUnsent(false);
    await loadUnsentSummary();
  }
}

async function sendUnsentNow(btn) {
  if (btn) { btn.disabled = true; btn.lastChild.textContent = 'Sending…'; }
  try {
    const r = await pywebview.api.retry_all_unsent();
    if (r && r.total) {
      showToast(r.sent === r.total
        ? (r.total === 1 ? 'Sent. The words are in the Journal.' : `All ${r.total} sent. The words are in the Journal.`)
        : `${r.sent} of ${r.total} sent. Waffler tries the rest again later.`,
        r.sent ? 'success' : 'error');
    }
  } catch (e) {
    showToast("Couldn't send them. Try again in a moment.", 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.lastChild.textContent = 'Try again'; }
    await loadUnsentSummary();
  }
}

// ── Recent recordings (Settings, Privacy and data; owner decision D9) ────
// Waffler keeps the audio of the last 10 dictations on this computer, to
// help look into a problem. Said here, with a switch and "Delete now".
function _renderRecentAudio(s) {
  if (!s) return;
  const tog = document.getElementById('recentAudioToggle');
  const desc = document.getElementById('recentAudioDesc');
  const del = document.getElementById('recentAudioDelete');
  if (tog) tog.checked = !!s.enabled;
  const n = Number(s.count) || 0;
  const now = n ? ` ${n === 1 ? '1 is' : `${n} are`} kept now.` : ' None are kept now.';
  if (desc) {
    desc.textContent = (s.enabled
      ? `The audio of your last ${s.keep || 10} dictations (fewer if they are very long, about 300 MB at most), so you can redo a take, get one back if Waffler stops, or look into a problem.`
      : "Off: new recordings are not kept, so a take can't be redone or recovered.") + now;
  }
  if (del) { del.disabled = !n; del.hidden = !n; }
}

async function loadRecentAudio() {
  if (!window.pywebview || !pywebview.api.get_recent_audio) return;
  try { _renderRecentAudio(await pywebview.api.get_recent_audio()); } catch (_) {}
  // History: how many dictations the Journal holds.
  const h = document.getElementById('privHistoryDesc');
  const n = Number(stats.total_count) || 0;
  if (h) h.textContent = `${WL.formatCount(n)} ${n === 1 ? 'dictation' : 'dictations'}, searchable in your Journal. Each notes the app you were in, the language and which services handled it. The app is kept with its kind (email, work chat, personal chat or other), on this computer only. Each also notes how loud your speech arrived, so Waffler can tell you if your microphone is set too low.`;
}

async function setRecentAudio(on) {
  const tog = document.getElementById('recentAudioToggle');
  try {
    const r = await pywebview.api.set_recent_audio(!!on);
    if (r && r.ok) {
      _renderRecentAudio(r);
      showToast(on ? 'Waffler keeps your recent recordings.' : 'Waffler stops keeping recordings. Delete now removes the ones kept.', 'success', 4000);
    } else {
      if (tog) tog.checked = !on;
      showToast((r && r.error) || "Couldn't change that setting. Try again.", 'error');
    }
  } catch (e) {
    if (tog) tog.checked = !on;
    showToast("Couldn't change that setting. Try again.", 'error');
  }
}

async function deleteRecentAudio(btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await pywebview.api.delete_recent_audio();
    if (r && r.ok) {
      _renderRecentAudio(r);
      showToast(r.deleted === 1 ? '1 recording deleted.' : `${WL.formatCount(r.deleted)} recordings deleted.`, 'success');
      return;
    }
    showToast((r && r.error) || "Couldn't delete them. Try again.", 'error');
  } catch (e) {
    showToast("Couldn't delete them. Try again.", 'error');
  }
  if (btn) btn.disabled = false;
}

// ── History retention (Settings, Privacy and data) ───────────────────────
// Keep forever (the default), a year, 90 or 30 days. A choice that would
// delete dictations says how many and asks first.
let _historyKeep = 0;
let _historyKeepPending = null;

async function loadHistoryKeep() {
  const sel = document.getElementById('historyKeep');
  if (!sel || !window.pywebview || !pywebview.api.get_history_retention) return;
  try {
    const r = await pywebview.api.get_history_retention();
    _historyKeep = Number(r && r.keep_days) || 0;
    sel.value = String(_historyKeep);
  } catch (_) {}
  _showHistoryKeepConfirm(null);
}

function _showHistoryKeepConfirm(days, count) {
  const row = document.getElementById('historyKeepConfirm');
  if (!row) return;
  _historyKeepPending = days;
  row.hidden = days == null;
  if (days != null) {
    document.getElementById('historyKeepConfirmTitle').textContent = WL.historyKeepConfirm(days, count);
    document.getElementById('historyKeepNo').focus();
  }
}

async function onHistoryKeepChange(sel) {
  const days = Number(sel.value) || 0;
  let count = 0;
  try { count = Number((await pywebview.api.preview_history_retention(days)).would_delete) || 0; } catch (_) {}
  if (count > 0) { _showHistoryKeepConfirm(days, count); return; }
  _showHistoryKeepConfirm(null);
  await _applyHistoryKeep(days);
}

function cancelHistoryKeep() {
  const sel = document.getElementById('historyKeep');
  if (sel) sel.value = String(_historyKeep);
  _showHistoryKeepConfirm(null);
}

async function confirmHistoryKeep() {
  const days = _historyKeepPending;
  _showHistoryKeepConfirm(null);
  if (days != null) await _applyHistoryKeep(days);
}

async function _applyHistoryKeep(days) {
  const sel = document.getElementById('historyKeep');
  try {
    const r = await pywebview.api.set_history_retention(days);
    if (r && r.ok) {
      _historyKeep = Number(r.keep_days) || 0;
      showToast(WL.historyKeepDone(_historyKeep, r.deleted), 'success', 4500);
      if (r.deleted) { await refreshAll(); await loadRecentAudio(); }
    } else {
      showToast((r && r.error) || "Couldn't change that setting. Try again.", 'error');
    }
  } catch (e) {
    showToast("Couldn't change that setting. Try again.", 'error');
  }
  if (sel) sel.value = String(_historyKeep);
}

// ── Usage (Settings) ──────────────────────────────────────────────────────
// Counts first, from the Journal; then what it would have cost at each
// provider's published paid rates, labelled as an estimate. Waffler can't
// see anyone's bill, and Groq's free plan is free.
async function loadUsageStats() {
  try {
    const usage = await pywebview.api.get_usage_stats();
    // Counts and the chart come from the Journal, as in the stats strip.
    let words = null;
    try { words = await pywebview.api.get_stats(); } catch (_) {}
    if (words) stats = words;
    const st = words || stats || {};
    const v = WL.usageView(usage, st);
    const put = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

    // Nothing dictated yet: one card that says how to start.
    const none = !(Number(st.total_count) > 0);
    const hero = document.getElementById('usageHero');
    const empty = document.getElementById('usageEmpty');
    const detail = document.getElementById('usageDetail');
    if (hero) hero.hidden = none;
    if (empty) empty.hidden = !none;
    if (detail) detail.hidden = none;

    put('usageBigWords', WL.formatCount(st.total_words));
    const f = WL.usageFacts(st);
    put('usageFactCount', f.dictations);
    put('usageFactCountLabel', f.dictationsLabel);
    put('usageFactPer', f.perDictation);
    _renderUsageChart(st);
    _renderInsights(st);

    const ids = { today: 'Today', week: 'Week', month: 'Month', all: 'All' };
    const wordKeys = { today: 'today_words', week: 'week_words', month: 'month_words', all: 'total_words' };
    v.periods.forEach((p) => {
      const n = Math.round(Number(st[wordKeys[p.id]]) || 0);
      put(`usage${ids[p.id]}Words`, WL.formatCount(n));
      put(`usage${ids[p.id]}Count`, p.count);
      const count = document.getElementById(`usage${ids[p.id]}Count`);
      if (count && count.nextElementSibling) count.nextElementSibling.textContent = p.countLabel;
      const label = count && count.parentElement.querySelector('.u-wl');
      if (label) label.textContent = n === 1 ? 'word' : 'words';
    });
    put('usageEstimateNote', v.note);
    put('usageAvgCost', (usage && usage.transcription_count) ? `About ${v.costs.perDictation} a dictation.` : '');
    put('usageTodayCost', v.costs.today);
    put('usageWeekCost', v.costs.week);
    put('usageMonthCost', v.costs.month);
    put('usageTotalCost', v.costs.total);
    const free = document.getElementById('usageFreeNote');
    if (free) free.hidden = !(usage && usage.by_provider && usage.by_provider.groq);

    // One bar per provider. A provider priced at an unpublished rate
    // (Cerebras publishes no per-token price) shows "~" before its cost,
    // so its figure is not read as exact (estimated_count).
    const rows = document.getElementById('usageProviderRows');
    if (rows) {
      const list = WL.usageProviderRows((usage && usage.by_provider) || {});
      rows.innerHTML = list.length ? list.map((r) => `
        <div class="pbar" title="${escHtml(r.calls)}${r.estimate ? '. This provider publishes no per-token price, so its cost is an estimate.' : ''}">
          <span class="pbar-n">${escHtml(r.name)}</span>
          <span class="pbar-tr"><i class="${r.top ? 'is-top' : ''}" style="width:${r.pct}%"></i></span>
          <span class="pbar-c">${r.estimate ? '<span class="usage-provider-est">estimate</span>' : ''}${escHtml(r.cost)}</span>
        </div>`).join('')
        : '<div class="usage-provider-empty">No usage yet. Make your first dictation to see it here.</div>';
    }
  } catch(e) {
    console.warn('loadUsageStats error:', e);
  }
}

// The last 30 days as bars (journal_data.py daily_words), with guide lines
// at round numbers. Each bar says its day and words to a pointer.
function _renderUsageChart(st) {
  const host = document.getElementById('usageChart');
  if (!host) return;
  const c = WL.usageChart(st.daily_words, st.daily_start);
  const start = document.getElementById('usageChartStart');
  if (start) start.textContent = c.start;
  host.innerHTML = c.grid.map((g) => `<span class="grid" style="bottom:${g.pos}%"></span><span class="glab" style="bottom:${g.pos}%">${escHtml(g.label)}</span>`).join('')
    + `<span class="bars">${c.bars.map((b) => `<i class="${b.cls}" style="height:${b.h}%" title="${escHtml(b.label)}"></i>`).join('')}</span>`;
  const total = c.bars.reduce((n, b) => n + b.words, 0);
  host.setAttribute('aria-label', `Words a day, the last 30 days: ${WL.formatCount(total)} words in all. ${c.bars.length ? c.bars[c.bars.length - 1].label : ''}`);
}

// ── Usage: weeks, time saved, the map of days, busiest hour, top apps ──────
// Drawn from the same get_stats answer as the totals above (computed on this
// computer by src/stats_insights.py). The sentences and the buckets are in
// logic.js (weekView, minutesSavedView, heatmapView, ...) where they are
// tested; this only puts them on the page.
function _put(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _renderInsights(st) {
  const streak = WL.weekStreakView(st);
  _put('insStreakValue', streak.value);
  _put('insStreakSub', streak.sub);
  const wk = WL.weekView(st);
  _put('insWeekValue', wk.words);
  _put('insWeekUnit', wk.wordsLabel);
  _put('insWeekLine', wk.line);
  _put('insWeekLast', wk.last);
  const wpm = WL.wpmView(st);
  _put('insWpmValue', wpm.value);
  _put('insWpmSub', wpm.sub);
  _renderSaved(st);
  _renderHeat(st);
  _renderPeakHour(st);
  _renderTopApps(st);
}

function _renderSaved(st) {
  const v = WL.minutesSavedView(st);
  _put('insSavedValue', v.value);
  _put('insSavedSub', v.sub);
  const how = document.getElementById('insSavedHow');
  if (how) how.innerHTML = v.how.map((t) => `<p>${escHtml(t)}</p>`).join('');
  // The box shows the speed the estimate used; it is left alone while it is
  // being typed in.
  const input = document.getElementById('typingWpm');
  if (input && document.activeElement !== input) {
    input.value = String(v.typing);
    input.removeAttribute('aria-invalid');
    const err = document.getElementById('typingWpmErr');
    if (err) { err.textContent = ''; err.hidden = true; }
  }
}

function toggleSavedHow() {
  const btn = document.getElementById('insSavedHowBtn');
  const box = document.getElementById('insSavedHow');
  if (!btn || !box) return;
  const open = box.hidden;
  box.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
}

// Settings, Usage: the typing speed behind the estimate. Checked here, then
// by the app, which refuses anything but a whole number from 10 to 200.
async function onTypingSpeedChange(input) {
  const err = document.getElementById('typingWpmErr');
  const check = WL.typingSpeedCheck(input.value);
  if (!check.ok) {
    input.setAttribute('aria-invalid', 'true');
    if (err) { err.textContent = check.message; err.hidden = false; }
    return;
  }
  input.removeAttribute('aria-invalid');
  if (err) { err.textContent = ''; err.hidden = true; }
  const ok = await saveSetting('typing_wpm', check.value);
  if (ok) {
    if (_lastSettings) _lastSettings.typing_wpm = check.value;
    await loadUsageStats();
  } else if (_lastSettings && _lastSettings.typing_wpm) {
    input.value = String(_lastSettings.typing_wpm);
  }
}

// The map of days: a column a week, Monday at the top, one square a day. It is
// a grid with one Tab stop; the arrow keys move between days (WL.heatMove) and
// each square says its day and words to a screen reader and in a tip on focus
// or hover.
let _heat = null;

function _renderHeat(st) {
  const card = document.getElementById('usageHeat');
  const grid = document.getElementById('heatGrid');
  if (!card || !grid) return;
  const v = WL.heatmapView(st.heatmap, st.today);
  _heat = v;
  card.hidden = !v.cells.length;
  // A row for each weekday, a cell for each week (days still to come have no
  // cell). The cells are not buttons: they do nothing when pressed.
  const rows = [[], [], [], [], [], [], []];
  v.cells.filter((c) => !c.future).forEach((c) => {
    rows[c.dow].push(`<div role="gridcell" class="hm-c hm-l${c.level}${c.iso === st.today ? ' is-today' : ''}" data-i="${c.i}" tabindex="${c.i === v.last ? 0 : -1}" aria-label="${escHtml(c.label)}"></div>`);
  });
  grid.innerHTML = rows.map((r) => `<div role="row" class="heat-row">${r.join('')}</div>`).join('');
  const months = document.getElementById('heatMonths');
  if (months) months.innerHTML = v.months.map((m) => `<span style="grid-column:${m.col + 1} / span 3">${escHtml(m.label)}</span>`).join('');
  _put('heatSummary', v.summary);
  _heatHideTip();
  if (!grid.dataset.wired) {
    grid.dataset.wired = '1';
    grid.addEventListener('keydown', _heatKey);
    grid.addEventListener('focusin', (e) => {
      const cell = e.target.closest && e.target.closest('.hm-c[data-i]');
      if (!cell) return;
      grid.querySelectorAll('.hm-c[tabindex="0"]').forEach((x) => { x.tabIndex = -1; });
      cell.tabIndex = 0;
      _heatShowTip(cell);
    });
    grid.addEventListener('focusout', _heatHideTip);
    grid.addEventListener('mouseover', (e) => {
      const cell = e.target.closest && e.target.closest('.hm-c[data-i]');
      if (cell) _heatShowTip(cell);
    });
    grid.addEventListener('mouseleave', _heatHideTip);
  }
}

function _heatKey(e) {
  if (e.key === 'Escape') { _heatHideTip(); return; }
  const cell = e.target.closest && e.target.closest('.hm-c[data-i]');
  if (!cell || !_heat || e.altKey || e.ctrlKey || e.metaKey) return;
  const to = WL.heatMove(Number(cell.dataset.i), e.key, _heat.last);
  if (to === null) return;
  e.preventDefault();
  const next = document.querySelector(`#heatGrid .hm-c[data-i="${to}"]`);
  if (next && next !== cell) next.focus();
}

function _heatShowTip(cell) {
  const tip = document.getElementById('heatTip');
  const wrap = document.getElementById('heatWrap');
  if (!tip || !wrap) return;
  tip.textContent = cell.getAttribute('aria-label') || '';
  tip.hidden = false;
  const w = wrap.getBoundingClientRect();
  const r = cell.getBoundingClientRect();
  const half = tip.offsetWidth / 2;
  tip.style.left = Math.min(Math.max(r.left - w.left + r.width / 2, half), w.width - half) + 'px';
  tip.style.top = (r.top - w.top) + 'px';
}

function _heatHideTip() {
  const tip = document.getElementById('heatTip');
  if (tip) tip.hidden = true;
}

// The busiest hour: 24 bars, the peak in honey, and a table for screen readers.
function _renderPeakHour(st) {
  const v = WL.peakHourView(st);
  _put('peakTitle', v.known ? v.title : '');
  const bars = document.getElementById('peakBars');
  const axis = document.getElementById('peakAxis');
  if (bars) {
    bars.hidden = !v.known;
    bars.innerHTML = v.bars.map((b) => `<i class="${b.peak ? 'is-peak' : (b.count ? '' : 'is-zero')}" style="height:${b.height}%" title="${escHtml(b.label)}"></i>`).join('');
  }
  if (axis) axis.hidden = !v.known;
  _put('peakSub', v.known ? v.sub : v.teach);
  const table = document.getElementById('peakTable');
  if (table) {
    table.innerHTML = v.known
      ? '<caption>Dictations by hour of the day</caption>'
        + v.bars.filter((b) => b.count).map((b) => `<tr><th scope="row">${escHtml(WL.hourRange(b.hour))}</th><td>${b.count}</td></tr>`).join('')
      : '';
  }
}

// The apps dictated into most, as ranked bars; the dictations that never
// said which app are counted apart, so older history is not misread.
function _renderTopApps(st) {
  const v = WL.topAppsView(st);
  const rows = document.getElementById('appsRows');
  if (rows) {
    rows.innerHTML = v.rows.map((r) => `
      <div class="abar" title="${escHtml(r.name)}: ${r.countText} ${r.count === 1 ? 'dictation' : 'dictations'}">
        <span class="abar-n">${escHtml(r.name)}</span>
        <span class="pbar-tr" aria-hidden="true"><i class="${r.top ? 'is-top' : ''}" style="width:${r.pct}%"></i></span>
        <span class="abar-c">${r.countText}</span>
      </div>`).join('')
      + (v.missing ? `<div class="abar abar-none"><span class="abar-n">${escHtml(v.missing.label)}</span><span></span><span class="abar-c">${v.missing.countText}</span></div>` : '');
  }
  _put('appsNote', v.empty ? v.teach : [v.more, v.missing ? v.missing.note : ''].filter(Boolean).join(' '));
}

async function loadAppVersion() {
  try {
    const ver = await pywebview.api.get_app_version();
    const el = document.getElementById('aboutVersion');
    if (el) el.textContent = `Version ${ver}`;
    const nav = document.getElementById('snavVersion');
    if (nav) nav.textContent = `Version ${ver}`;
    // About: what is new in this version.
    const news = WL.whatsNew(ver);
    const box = document.getElementById('aboutNew');
    if (box) box.hidden = !news;
    if (news) {
      const t = document.getElementById('aboutNewTitle');
      if (t) t.textContent = news.title;
      const list = document.getElementById('aboutNewList');
      if (list) list.innerHTML = news.items.map((x, i) => `<li><b aria-hidden="true">${i + 1}</b>${escHtml(x)}</li>`).join('');
    }
    // The models in use, as a tooltip on the version too.
    if (el) el.title = WL.aboutLine(ver, _lastSettings);
  } catch(e) {
    console.warn('loadAppVersion error:', e);
  }
}

// Settings, About: the project's pages on GitHub.
const _ABOUT_LINKS = {
  source: 'https://github.com/jbf-tars/Waffler',
  issues: 'https://github.com/jbf-tars/Waffler/issues',
  releases: 'https://github.com/jbf-tars/Waffler/releases',
};
function openAboutLink(which) {
  try { pywebview.api.open_url(_ABOUT_LINKS[which] || _ABOUT_LINKS.source); } catch (_) {}
}
