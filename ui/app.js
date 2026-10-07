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

// Platform-specific key mapping
const JS_KEY_TO_ID = isMacPlatform ? {
  "Control": "control", "Alt": "option", "Shift": "shift",
  "Meta": "cmd", "OS": "cmd",  // Command key on Mac
} : {
  "Control": "ctrl", "Alt": "alt", "Shift": "shift",
  "Meta": "win", "OS": "win",  // Windows key
};

const MODIFIER_IDS = new Set(isMacPlatform ?
  ["control", "option", "shift", "cmd", "fn"] :
  ["ctrl", "alt", "shift", "win"]
);

function jsKeyToId(e) {
  if (JS_KEY_TO_ID[e.key]) return JS_KEY_TO_ID[e.key];
  if (e.code.startsWith("Key")) return e.code.slice(3).toLowerCase();
  if (e.code.startsWith("Digit")) return e.code.slice(5);
  if (e.code.startsWith("F") && !isNaN(e.code.slice(1))) return e.code.toLowerCase();
  return null;
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
const $strip         = document.getElementById('statStrip');
const $feedMore      = document.getElementById('feedMore');
const $statusInd     = document.getElementById('statusIndicator');
const $statusText    = document.getElementById('statusText');
const $statusTime    = document.getElementById('statusTime');
const $hotkeyCaps    = document.getElementById('hotkeyHint');
const $toast         = document.getElementById('toast');
const $statWords     = document.getElementById('statWords');
const $statCount     = document.getElementById('statCount');
const $rail          = document.getElementById('journalRail');

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

  // Both platforms get the same "Download & Install": stream the installer
  // in-app, show a progress bar, run the platform installer, relaunch.
  // A release with no installer for this computer opens its page instead.
  const v = WL.updateCheckView(r);
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
    icon: 'download',
    title: 'Downloading the update…',
    subtitle: 'Keep Waffler open until it finishes.',
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
      setUpdateModal({
        icon: 'check-circle',
        title: 'Ready to install',
        subtitle: 'Waffler will close, install the update and open again.',
        primaryLabel: 'Install now',
        primaryHandler: () => installDownloadedUpdate(),
        cancelLabel: 'Later',
      });
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
  setUpdateModal({ icon: 'settings', title: 'Installing…', subtitle: 'Waffler is closing to install the update.' });
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
// "Delete my data" keeps the keys, words and settings and Waffler keeps
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
  host.textContent = '';
  presets.forEach((p) => {
    const on = p.custom ? !isPreset : p.label === cur;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'preset' + (on ? ' on' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(on));
    const top = p.custom
      ? (on ? `<span class="keys">${_capsHtml(_currentHotkeyKeys)}</span>` : '<span class="preset-cus">Custom…</span>')
      : `<span class="keys">${_capsHtml(p.keys)}</span>`;
    const hint = p.custom && on ? 'Your own keys. Choose to change them.' : p.hint.replace(' · ', ', ');
    b.innerHTML = `<span class="preset-top">${top}<span class="preset-r" aria-hidden="true"></span></span><span class="preset-h">${escHtml(hint)}</span>`;
    b.setAttribute('aria-label', `${p.custom ? (on ? cur + ', your own keys' : 'Custom') : p.label}. ${hint}`);
    b.addEventListener('click', () => (p.custom ? openHotkeyCapture() : (on ? null : changeSettingsHotkey(p.keys))));
    host.appendChild(b);
  });
}

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
}

// A preset in the hotkey dialog: saved at once, and the dialog closes.
async function changeSettingsHotkey(keys) {
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
}

function _showCaptured(keys) {
  const el = document.getElementById("hotkeyCaptureKeys");
  if (el) el.innerHTML = keys.length ? _capsHtml(keys, 'kc-xl') : '';
}

function openHotkeyCapture() {
  _capturedKeys.clear();
  _lastCapturedKeys = [..._currentHotkeyKeys];
  _showCaptured(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
  // Setup has its own list of choices ("Pick another key").
  const presets = document.getElementById('hotkeyPresetRow');
  if (presets) presets.hidden = typeof _wizardVisible === 'function' && _wizardVisible();
  renderSettingsHotkeyPresets();
  _announceCaptured('');
  const modal = document.getElementById("hotkeyModal");
  modal.style.display = "flex";
  modalOpened(modal);
  document.addEventListener("keydown", _onCaptureKeyDown);
  document.addEventListener("keyup", _onCaptureKeyUp);
}

function closeHotkeyCapture() {
  const modal = document.getElementById("hotkeyModal");
  modal.style.display = "none";
  document.removeEventListener("keydown", _onCaptureKeyDown);
  document.removeEventListener("keyup", _onCaptureKeyUp);
  _capturedKeys.clear();
  modalClosed(modal);
}

// Screen readers hear the keys once they are let go ("Ctrl + Shift"), not
// every key on the way down.
function _announceCaptured(text) {
  const live = document.getElementById('hotkeyCaptureLive');
  if (live) live.textContent = text;
}

function _onCaptureKeyDown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeHotkeyCapture(); return; }
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
}

function _onCaptureKeyUp(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (id) _capturedKeys.delete(id);
  if (!_capturedKeys.size && _lastCapturedKeys.length && id) {
    _announceCaptured(WL.hotkeyName(_lastCapturedKeys, isMacPlatform));
  }
}

function resetHotkeyDefault() {
  _lastCapturedKeys = WL.defaultHotkey(isMacPlatform);
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
  // The backend decides (src/hotkey_rules.py) and says why in one sentence.
  let result;
  try {
    result = await window.pywebview.api.save_hotkey_config(JSON.stringify(keys));
  } catch (e) {
    console.error("saveHotkeyCapture error:", e);
    result = { ok: false, error: "Couldn't change the hotkey. Try again." };
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
  if ($strip) $strip.hidden = view.kind === 'empty';
  // First run: the margin has nothing to count yet.
  if ($rail) $rail.hidden = view.kind === 'empty';
  const layout = document.getElementById('journalLayout');
  if (layout) layout.classList.toggle('is-first', view.kind === 'empty');
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
    page = (await window.pywebview.api.get_history(PAGE_SIZE, history.length, _searchText)) || [];
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
    setTimeout(() => {
      btnEl.classList.remove('copied', 'is-done');
      btnEl.innerHTML = WI.icon('copy') + '<span>Copy</span>';
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
  const first = !history.length && !(Number(stats.entries) > 0);
  stats.entries = (Number(stats.entries) || 0) + 1;
  // During a search, it joins the list only if it matches.
  const shown = !_searchQuery ||
    ((newItem.styled || '') + ' ' + (newItem.text || '')).toLowerCase().includes(_searchQuery);
  if (shown) {
    history.unshift(newItem);
    if (first || !$noMatch.hidden) drawFeed();
    else _prependCard(newItem);
  }
  if (window.pywebview && window.pywebview.api) _refreshStats();
  // A Not sent card is not a finished transcription.
  if (newItem.failed) showToast('Not sent. The recording is saved in the Journal.', 'error');
  else showToast('Added to your Journal.', 'success');
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
  if (esc) esc.hidden = cls === 'processing';
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
  const v = WL.notSentView(item);
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
  const div = document.createElement('article');
  div.className = 'transcript-card j-ent' + (isNew ? ' new' : '');

  const displayText = item.styled || item.text || '';
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
  const qualityLine = (q.chip || q.asSaid || q.reasons.length)
    ? `<p class="j-meta card-reason">${qualityBadge(item)}<span>${escHtml(q.reasons.join(' '))}</span></p>`
    : '';
  div.innerHTML = `
    <div class="j-g"><div class="j-t">${escHtml(_clock(item.timestamp))}</div><div class="j-w">${WL.formatCount(words)} ${words === 1 ? 'word' : 'words'}</div></div>
    <div class="j-body">
      <div class="j-said-k" hidden>What you said</div>
      <div class="card-text styled" id="${textId}">${_markedHtml(displayText, changes)}</div>
      ${vocabLine}${qualityLine}
    </div>
    <div class="j-acts card-actions">
      <button type="button" class="btn btn-sec btn-sm btn-copy" aria-label="${escHtml(when ? `Copy the dictation from ${when}` : 'Copy')}">${WI.icon('copy')}<span>Copy</span></button>
      ${hasStyled ? `<button type="button" class="rbtn text-toggle" aria-controls="${textId}" aria-pressed="false" aria-label="Show what you said" title="Show what you said">${WI.icon('file-text')}</button>` : ''}
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

  if (isNew) {
    setTimeout(() => div.classList.remove('new'), 3000);
  }

  return div;
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

function toggleRawHandler(toggleEl, textEl, rawText, styledText) {
  const showingStyled = textEl.classList.contains('styled');
  if (showingStyled) {
    textEl.textContent = rawText;
    textEl.classList.replace('styled', 'raw');
    toggleEl.setAttribute('aria-pressed', 'true');
    toggleEl.setAttribute('aria-label', 'Show clean');
    toggleEl.title = 'Show clean';
  } else {
    textEl.textContent = styledText;
    textEl.classList.replace('raw', 'styled');
    toggleEl.setAttribute('aria-pressed', 'false');
    toggleEl.setAttribute('aria-label', 'Show what you said');
    toggleEl.title = 'Show what you said';
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

async function loadAudioDevices() {
  try {
    const devices = await pywebview.api.get_audio_devices();
    const current = await pywebview.api.get_selected_device();
    const sel = document.getElementById('micSelect');
    if (!sel) return;
    sel.innerHTML = '';
    if (!devices || devices.length === 0) {
      sel.innerHTML = '<option value="">No microphone found</option>';
      return;
    }
    devices.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.index;
      opt.textContent = d.name + (d.is_default ? ' (default)' : '');
      if (current && current.index === d.index) opt.selected = true;
      else if (current && current.index === null && d.is_default) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch(e) {
    console.warn('loadAudioDevices error:', e);
  }
}

async function onMicChange(indexStr) {
  const idx = parseInt(indexStr, 10);
  try {
    const result = await pywebview.api.set_audio_device(idx);
    if (result && result.ok) {
      showToast(`Microphone: ${result.name}`, 'success');
    }
  } catch(e) {
    console.warn('setAudioDevice error:', e);
  }
}

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
  return { words: entries.map((e) => e.word), sounds };
}

// After a save: the saved words, with the counts they already had (a
// respelt word keeps its own: the Journal records it in any capitals).
function _vocabTakeSaved(res, entries) {
  const before = {};
  (_vocabBook.entries || []).concat(entries || []).forEach((e) => { before[e.word.toLowerCase()] = e; });
  const sounds = (res && res.sounds_like) || {};
  _vocabBook.entries = res.words.map((w) => {
    const old = before[w.toLowerCase()] || {};
    return { word: w, sounds_like: sounds[w] || [], count: old.count || 0, last: old.last || '' };
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

  try {
    const book = pywebview.api.get_vocab_book ? await pywebview.api.get_vocab_book() : null;
    if (book && book.ok) {
      _vocabBook = { entries: book.entries || [], total: book.total || 0, recent: book.recent || [] };
    } else {
      const words = (await pywebview.api.get_vocab()) || [];
      _vocabBook = { entries: words.map((w) => ({ word: w, sounds_like: [], count: 0, last: '' })), total: 0, recent: [] };
    }
    _vocabWords = _vocabBook.entries.map((e) => e.word);
  } catch (e) {
    console.warn('loadVocabPage error:', e);
  }
  renderVocab();
  _vocabRunTry();
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

  if (!entries.length) {
    listEl.innerHTML = `
      <div class="group"><div class="panel v-first">
        <div class="v-first-top">
          <div>
            <h2 class="v-first-title">Teach it the names it gets wrong</h2>
            <p class="v-first-text">Speech to text guesses how names are spelled. Add yours once and Waffler spells them your way from then on.</p>
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

  const rows = WL.vocabRows(entries, new Date());
  const head = `<div class="vt-h" role="row"><span role="columnheader">Word</span><span role="columnheader">Sounds like</span><span role="columnheader">Corrections</span><span role="columnheader">Last used</span><span role="columnheader"><span class="sr-only">Change or remove</span></span></div>`;
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
    return `<div class="vt-r" role="row" data-word="${w}">
      <span class="vt-w" role="cell">${w}</span>
      <span class="vt-sl" role="cell">${sl}</span>
      <span class="vt-u" role="cell"><span class="vt-n">${WL.formatCount(r.count)}</span><span class="vt-b" aria-hidden="true"><i style="width:${r.bar}%"></i></span></span>
      <span class="vt-l" role="cell">${escHtml(r.last)}</span>
      <span class="vt-x" role="cell"><button type="button" class="rbtn vt-edit-btn" data-word="${w}" aria-label="Change what ${w} sounds like" title="Change what it sounds like">${WI.icon('pencil')}</button><button type="button" class="rbtn vt-del" data-word="${w}" aria-label="Remove ${w}" title="Remove">${WI.icon('x')}</button></span>
    </div>`;
  }).join('');
  listEl.innerHTML = `<div class="vt" role="table" aria-label="Your words">${head}${body}</div>`;

  listEl.querySelectorAll('.vt-del').forEach((b) => {
    b.addEventListener('click', () => deleteVocabWord(b.getAttribute('data-word')));
  });
  listEl.querySelectorAll('.vt-edit-btn').forEach((b) => {
    b.addEventListener('click', () => openVocabSounds(b.getAttribute('data-word')));
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
    const res = await pywebview.api.set_vocab(p.words, p.sounds);
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
    _vocabTakeSaved({ words: Array.isArray(res.words) ? res.words : p.words, sounds_like: res.sounds_like || p.sounds }, entries);
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
    _vocabTakeSaved({ words: Array.isArray(res.words) ? res.words : next, sounds_like: res.sounds_like || p.sounds }, entries);
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
const SETTINGS_SECTIONS = ['general', 'keys', 'hotkey', 'usage', 'privacy', 'about'];
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
  if (sec === 'hotkey') { _hotkeyHeard = false; _renderHotkeyTest(); }
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

async function _tickMicMeter() {
  if (_micBusy) return;
  if (!_micMeterWanted()) { _syncMicMeter(); return; }
  _micBusy = true;
  try { renderMicMeter(await pywebview.api.get_mic_level()); } catch (_) { renderMicMeter(null); }
  _micBusy = false;
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
  const v = WL.spellingSpecimen(dialect);
  el.classList.toggle('specimen', !!v);
  el.innerHTML = v
    ? v.words.map((w) => `<span>${escHtml(w)}</span>`).join('') + `<s>${escHtml(v.not)}</s>`
    : 'British or American, whichever you speak.';
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
        <button type="button" class="btn btn-sec btn-sm po-btn-key" onclick="openKeyEditor('${r.id}')">${r.hasKey ? '' : WI.icon('plus', 'ic-sm')}${escHtml(r.button)}</button>
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

// Replace or Add key: that provider's box opens under its row.
function openKeyEditor(provider, keep) {
  const ed = document.getElementById(_KEY_EDITORS[provider]);
  const row = document.querySelector(`#providerOrderList [data-provider="${provider}"]`);
  if (!ed || !row) return;
  if (_openKeyEditor === provider && !keep) { closeKeyEditor(); return; }
  if (_openKeyEditor && _openKeyEditor !== provider) closeKeyEditor();
  row.after(ed);
  _openKeyEditor = provider;
  const input = ed.querySelector('input');
  if (input && !keep) { input.value = ''; input.focus(); }
}

function closeKeyEditor() {
  const park = document.querySelector('.key-editors');
  Object.values(_KEY_EDITORS).forEach((id) => {
    const el = document.getElementById(id);
    if (el && park && el.parentElement !== park) park.appendChild(el);
    const input = el && el.querySelector('input');
    if (input) input.value = '';
  });
  _openKeyEditor = '';
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

    // Dialect / Spelling
    const dialectSel = document.getElementById('dialectSelect');
    if (dialectSel) dialectSel.value = s.dialect || 'auto';
    renderSpellingSpecimen(s.dialect || 'auto');

    // Auto-paste
    const apToggle = document.getElementById('autoPasteToggle');
    if (apToggle) apToggle.checked = s.auto_paste !== false;
  } catch(e) {
    console.warn('loadSettings error:', e);
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

async function saveSetting(key, value) {
  try {
    const r = await pywebview.api.save_settings({ [key]: value });
    if (r.ok) {
      if (key === 'auto_paste') {
        showToast(value ? 'Waffler pastes when you let go.' : 'Waffler puts the text on the clipboard. Paste it yourself.', 'success', 3500);
      } else if (key === 'language') {
        showToast('Language saved.', 'success');
      } else if (key === 'dialect') {
        const labels = { 'auto': 'Spelling matches how you speak.', 'en-GB': 'Spelling: British English.', 'en-US': 'Spelling: American English.' };
        showToast(labels[value] || 'Spelling saved.', 'success');
      }
    } else {
      showToast("Couldn't save that. Try again.", 'error');
    }
  } catch(e) {
    console.warn('saveSetting error:', e);
    showToast("Couldn't save that. Try again.", 'error');
  }
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

async function wizStartPractice() {
  const sel = document.getElementById('wizMicSelect');
  const idx = sel && sel.value !== '' ? Number(sel.value) : null;
  try {
    if (idx !== null) await pywebview.api.set_audio_device(idx);
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
      ? `The audio of your last ${s.keep || 10} dictations, to help look into a problem.`
      : 'Off: new recordings are not kept.') + now;
  }
  if (del) { del.disabled = !n; del.hidden = !n; }
}

async function loadRecentAudio() {
  if (!window.pywebview || !pywebview.api.get_recent_audio) return;
  try { _renderRecentAudio(await pywebview.api.get_recent_audio()); } catch (_) {}
  // History: how many dictations the Journal holds.
  const h = document.getElementById('privHistoryDesc');
  const n = Number(stats.total_count) || 0;
  if (h) h.textContent = `${WL.formatCount(n)} ${n === 1 ? 'dictation' : 'dictations'}, searchable in your Journal.`;
}

async function setRecentAudio(on) {
  const tog = document.getElementById('recentAudioToggle');
  try {
    const r = await pywebview.api.set_recent_audio(!!on);
    if (r && r.ok) {
      _renderRecentAudio(r);
      showToast(on ? 'Waffler keeps your last 10 recordings.' : 'Waffler stops keeping recordings. Delete now removes the ones kept.', 'success', 4000);
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
