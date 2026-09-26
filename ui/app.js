/* Waffler — Frontend Logic */

// Pure helpers (labels, presets, formatting) live in logic.js, which
// index.html loads first, so the tests can run them without a page.
const WL = window.WafflerLogic;
// Line icons and the waffle (icons.js, loaded before this file).
const WI = window.WafflerIcons;

// ── Theme (v3.14.3+) ──────────────────────────────────────────────────
// Apply the saved theme as early as possible so the page doesn't flash
// in the wrong colours. Default for new installs is "cream".
//
// "auto" (Settings: System) is resolved here to "cream" or "dark" from the
// OS setting, and followed live. It used to be set on <body> as-is, and the
// stylesheet only had a dark branch for it, so on a light OS "System" showed
// the dark colours. Resolving it means every cream- and dark-specific rule
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
  window.addEventListener('pywebviewready', () => _syncThemeToApp(_themePref));
})();

function setAppTheme(theme) {
  if (!THEME_CHOICES.includes(theme)) return;
  _applyTheme(theme);
  try { localStorage.setItem('waffler_theme', theme); } catch (_) {}
  _syncThemeToApp(theme);
  refreshThemePicker();
}

// Mark the current theme button as active when settings opens
function refreshThemePicker() {
  const cur = _themePref || 'cream';
  document.querySelectorAll('.theme-option').forEach((el) => {
    el.classList.toggle('active', el.getAttribute('data-theme') === cur);
  });
}

// ── State ──────────────────────────────────────────────────────────────
let history = [];
let stats = { today_words: 0, today_count: 0, total_words: 0 };
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
const $feedScroll    = document.getElementById('feedScroll');
const $feed          = document.getElementById('transcriptFeed');
const $empty         = document.getElementById('emptyState');
const $feedCount     = document.getElementById('feedCount');
const $statusInd     = document.getElementById('statusIndicator');
const $statusText    = document.getElementById('statusText');
const $statusTime    = document.getElementById('statusTime');
const $hotkeyCaps    = document.getElementById('hotkeyHint');
const $toast         = document.getElementById('toast');
const $statWords     = document.getElementById('statWords');
const $statCount     = document.getElementById('statCount');
const $statTotal     = document.getElementById('statTotal');
const $dateLabel     = document.getElementById('dateLabel');

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
window.waffler_window_visible = function(visible) { _setWindowHidden(!visible); };
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

// ── Init ─────────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', () => {
  checkOnboarding();  // Check if wizard needed or show main app
  refreshAll();
  loadHotkeyConfig();
  updateDateLabel();
  // Check for updates after a short delay (don't block startup)
  setTimeout(checkForUpdates, 3000);
});

// Fallback: also try on DOMContentLoaded in case pywebview event fires early
document.addEventListener('DOMContentLoaded', () => {
  updateDateLabel();
  updateHotkeyHint();
  renderSettingsHotkeyPresets();

  // Prevent Mac error sound when space is pressed in the app
  // (Space monitor observes at OS level, but we need to handle it in UI to avoid "bonk" sound)
  document.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.code === 'Space') {
      // Only prevent default if NOT in an input field (allow typing in text fields)
      const target = e.target;
      if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA' && !target.isContentEditable) {
        e.preventDefault();
      }
    }
  });

  setTimeout(() => {
    checkOnboarding();  // Fallback check if pywebviewready hasn't fired
    refreshAll();
  }, 300);
});

function updateDateLabel() {
  const now = new Date();
  // The Journal layout uses two spans inside #dateLabel — month is the
  // weekday name, day is the date. Fall back to plain textContent if
  // the spans aren't present (e.g. older HTML).
  const monthEl = $dateLabel ? $dateLabel.querySelector('.j-date-month') : null;
  const dayEl   = $dateLabel ? $dateLabel.querySelector('.j-date-day')   : null;
  if (monthEl && dayEl) {
    monthEl.textContent = now.toLocaleDateString('en-GB', { weekday: 'long' });
    dayEl.textContent   = now.toLocaleDateString('en-GB', { day: 'numeric', month: 'long' });
  } else if ($dateLabel) {
    const opts = { weekday: 'long', month: 'long', day: 'numeric' };
    $dateLabel.textContent = now.toLocaleDateString('en-GB', opts);
  }
}

// A notice card (components.css .notice): an icon tile, a title, a line of
// detail, actions and a close button. Used for the update notices.
function makeNotice({ icon, tone, title, desc, actions }) {
  const box = document.createElement('div');
  box.className = 'notice update-banner' + (tone === 'honey' || tone === 'warn' ? ' notice-honey' : '');
  box.setAttribute('role', 'status');
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

async function checkForUpdates() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const r = await pywebview.api.check_for_updates();
    // A previous update that silently did nothing used to leave no trace at
    // all — the app just restarted on the old version. Say so plainly.
    if (r.last_update_failed && r.last_update_failed.message) {
      const host0 = document.querySelector('.journal');
      if (host0) {
        const m = WL.splitMessage(r.last_update_failed.message);
        const warn = makeNotice({ icon: 'alert', tone: 'warn', title: m.title, desc: m.subtitle });
        host0.prepend(warn);
      }
    }
    if (r.update_available) {
      // The update notice goes at the top of the Journal, the first thing
      // you see. Download opens the same in-app download-and-install dialog
      // as Settings, About (v3.14.34), which falls back to the download page.
      const host = document.querySelector('.journal');
      if (!host) return;
      const download = document.createElement('button');
      download.className = 'btn btn-pri btn-sm';
      download.textContent = 'Download';
      const banner = makeNotice({ icon: 'circle-up', tone: 'honey', title: `Update v${r.latest_version} available`, actions: [download] });
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

// ── Manual update flow (Settings → About → Check for Update) ──────────
let _updatePollTimer = null;
let _downloadedPath = null;
let _lastUpdateInfo = null;

function showUpdateModal() {
  const m = document.getElementById('updateModal');
  if (m) m.style.display = 'flex';
}
function closeUpdateModal(ev) {
  if (ev && ev.target && ev.target.id !== 'updateModal') return;
  const m = document.getElementById('updateModal');
  if (m) m.style.display = 'none';
  stopProgressPolling();
}
function setUpdateModal({ icon, title, subtitle, showProgress, primaryLabel, primaryHandler, cancelLabel, browserUrl, browserLabel }) {
  // icon is a name from icons.js ('circle-up', 'download', 'alert'...).
  document.getElementById('updateModalIcon').innerHTML = WI.icon(icon || 'circle-up', 'ic-lg');
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
  document.getElementById('updateCancelBtn').textContent = cancelLabel || 'Close';
}

async function checkForUpdatesManual() {
  showUpdateModal();
  setUpdateModal({ icon: 'refresh', title: 'Checking for updates…', subtitle: 'Contacting GitHub…' });
  let r;
  try {
    r = await pywebview.api.check_for_updates();
  } catch(e) {
    console.warn('check_for_updates failed:', e);
    r = { error: WL.UPDATE_TEXT.checkFailed };
  }
  if (r && r.update_available) { openUpdateModalFromCheck(r); return; }
  // The backend's sentence as it is ("Couldn't check for updates. Try
  // again later."), never "GitHub API returned HTTP 403".
  setUpdateModal(WL.updateCheckView(r));
}

function openUpdateModalFromCheck(r) {
  showUpdateModal();
  _lastUpdateInfo = r;

  // v3.14.3+: Mac in-app downloads now work properly via /usr/bin/curl
  // (no more PyInstaller-bundled-requests SSL hang). Both platforms get
  // the same "Download & Install" experience: stream the installer
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
    title: 'Downloading update…',
    subtitle: 'Please keep Waffler open.',
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
      document.getElementById('updateProgressText').textContent = `${pct}% — ${mb} / ${totalMb} MB`;
    } else {
      document.getElementById('updateProgressText').textContent = 'Starting…';
    }
    if (p.done) {
      stopProgressPolling();
      _downloadedPath = p.path;
      setUpdateModal({
        icon: 'check-circle',
        title: 'Ready to install',
        subtitle: 'Waffler will close, install the update, and relaunch.',
        primaryLabel: 'Install Now',
        primaryHandler: () => installDownloadedUpdate(),
        cancelLabel: 'Later',
      });
    }
  } catch(e) {
    console.warn('progress poll failed', e);
  }
}

async function installDownloadedUpdate() {
  if (!_downloadedPath) return;
  setUpdateModal({ icon: 'settings', title: 'Installing…', subtitle: 'Waffler is closing to apply the update.' });
  let r;
  try {
    r = await pywebview.api.install_update_and_restart(_downloadedPath);
  } catch(e) {
    console.warn('install_update_and_restart failed:', e);
    r = { ok: false };
  }
  // Success usually never answers (Waffler quits); a refusal used to be
  // ignored and left "Installing…" on screen.
  if (r && r.ok === false) setUpdateModal(WL.updateFailureView(r, WL.UPDATE_TEXT.installFailed));
}

function updateHotkeyHint() {
  // Always reflect the user's actual configured hotkey (Mac users can
  // pick Cmd+Shift / Option+Shift via the wizard or Settings, not just
  // Fn). Previous version hardcoded 'Fn' on Mac, so customised users
  // saw 'Press Fn to start recording' on the home page no matter what
  // they'd actually configured.
  //
  // We still set a platform-appropriate default IMMEDIATELY so the
  // badges aren't blank during the async API round-trip to fetch the
  // saved config — loadHotkeyConfig() overwrites them once it returns.
  renderHotkeyCaps(_currentHotkeyKeys);
  const emptyHint = document.getElementById('emptyHint');
  if (emptyHint) emptyHint.innerHTML = `Hold <strong>${escHtml(hotkeyDisplayStr(_currentHotkeyKeys))}</strong> to record`;
  loadHotkeyConfig();
}

// The top bar's keycaps: the saved hotkey, one key per cap ("Win" + "Ctrl").
// The pill keeps one width whatever it says; if the keycaps don't fit beside
// "Ready", it takes its wider size, decided here when the hotkey changes and
// never during a dictation.
function renderHotkeyCaps(keys) {
  if (!$hotkeyCaps) return;
  $hotkeyCaps.innerHTML = WL.keycaps(keys, isMacPlatform)
    .map((c) => `<kbd class="kc">${escHtml(c.label)}</kbd>`)
    .join('<span class="plus" aria-hidden="true">+</span>');
  $hotkeyCaps.setAttribute('aria-label', 'Hotkey: ' + hotkeyDisplayStr(keys));
  _fitStatusPill();
}

function _fitStatusPill() {
  if (!$statusInd || !$statusText || !$statusInd.classList.contains('idle')) return;
  $statusInd.classList.remove('is-wide');
  // At its normal width, "Ready" is cut short when the keycaps need more room.
  if ($statusText.scrollWidth > $statusText.clientWidth + 1) $statusInd.classList.add('is-wide');
}

// ── Permissions (Step 1) ─────────────────────────────────────────────

async function openAccessibilitySettings() {
  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  try {
    const result = await pywebview.api.open_accessibility_settings();
    if (result.ok) {
      showToast("Opening System Settings...", "success");
      // Permission status is managed manually by the user — no auto-recheck.
    } else {
      console.warn('open settings failed:', result.error);
      showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
    }
  } catch (e) {
    console.error("openAccessibilitySettings error:", e);
    showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
  }
}

async function openInputMonitoringSettings() {
  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  try {
    const result = await pywebview.api.open_input_monitoring_settings();
    if (result.ok) {
      showToast("Opening System Settings...", "success");
      // Permission status is managed manually by the user — no auto-recheck.
    } else {
      console.warn('open settings failed:', result.error);
      showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
    }
  } catch (e) {
    console.error("openInputMonitoringSettings error:", e);
    showToast("Couldn't open System Settings. Open it from the Apple menu instead.", "error");
  }
}

async function downloadLogs(btn) {
  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  // Disable + show progress on the button so the user knows we're working.
  let originalText = null;
  if (btn) {
    originalText = btn.textContent;
    btn.disabled = true;
    btn.style.opacity = "0.6";
    btn.style.cursor = "wait";
    btn.textContent = "Bundling…";
  }

  try {
    const result = await pywebview.api.download_logs();
    if (result && result.ok) {
      showToast(`Logs saved to ${result.path}`, "success", 6000);
    } else {
      console.warn('download_logs failed:', result && result.error);
      showToast("Couldn't save the logs. Try again.", "error", 6000);
    }
  } catch (e) {
    console.error("downloadLogs error:", e);
    showToast("Couldn't save the logs. Try again.", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = "";
      btn.style.cursor = "";
      btn.textContent = originalText || "Download Logs";
    }
  }
}

async function factoryReset() {
  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  // Show confirmation dialog
  const confirmed = confirm(
    "Factory Reset\n\n" +
    "This will delete ALL Waffler data including:\n\n" +
    "• Recording history\n" +
    "• Configuration settings\n" +
    "• Usage statistics\n" +
    "• Logs\n\n" +
    "The app will quit and restart from setup on next launch.\n\n" +
    "This cannot be undone.\n\n" +
    "Are you sure?"
  );

  if (!confirmed) {
    return;
  }

  try {
    const result = await pywebview.api.factory_reset();
    if (result.ok) {
      showToast("Resetting all data...", "success");
      // App will quit automatically
    } else {
      console.warn('factory_reset failed:', result.error);
      showToast("Couldn't reset Waffler. Try again.", "error");
    }
  } catch (e) {
    console.error("factoryReset error:", e);
    showToast("Couldn't reset Waffler. Try again.", "error");
  }
}

// ── Hotkey Config ────────────────────────────────────────────────────

async function loadHotkeyConfig() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const config = await window.pywebview.api.get_hotkey_config();
    if (config.ok) {
      _currentHotkeyKeys = config.keys;
      const display = config.display;
      const settingsBadge = document.getElementById("settingsHotkeyBadge");
      if (settingsBadge) settingsBadge.textContent = display;
      renderHotkeyCaps(config.keys);
      const emptyHint = document.getElementById("emptyHint");
      if (emptyHint) emptyHint.innerHTML = `Hold <strong>${escHtml(display)}</strong> to record`;
    }
  } catch (e) {
    console.error("loadHotkeyConfig error:", e);
  }
}

// Settings offers this platform's hotkeys only (logic.js hotkeyPresets).
// It used to show the Mac keys on Windows too; the backend refused them,
// the screen never checked, and the badge flashed green anyway.
function renderSettingsHotkeyPresets() {
  const host = document.getElementById('settingsHotkeyPresets');
  if (!host) return;
  host.textContent = '';
  WL.hotkeyPresets(isMacPlatform).forEach((p) => {
    const b = document.createElement('button');
    b.className = 'settings-btn';
    b.textContent = p.label;
    b.title = p.hint;
    b.addEventListener('click', () => (p.custom ? openHotkeyCapture() : changeSettingsHotkey(p.keys)));
    host.appendChild(b);
  });
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

async function changeSettingsHotkey(keys) {
  const settingsBadge = document.getElementById("settingsHotkeyBadge");
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
    showToast(msg, 'error', 6000);
    return;
  }
  _showSettingsHotkeyError('');
  await _onHotkeySaved(result);
  showToast(`Hotkey is now ${result.display || WL.hotkeyName(result.keys, isMacPlatform)}`, 'success');
  // Brief green on the badge, only when the save really worked.
  if (settingsBadge) {
    settingsBadge.style.color = '#4CAF50';
    setTimeout(() => { settingsBadge.style.color = '#C8A256'; }, 1000);
  }
}

function openHotkeyCapture() {
  _capturedKeys.clear();
  _lastCapturedKeys = [..._currentHotkeyKeys];
  document.getElementById("hotkeyCaptureKeys").textContent = hotkeyDisplayStr(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
  document.getElementById("hotkeyModal").style.display = "flex";
  document.addEventListener("keydown", _onCaptureKeyDown);
  document.addEventListener("keyup", _onCaptureKeyUp);
}

function closeHotkeyCapture() {
  document.getElementById("hotkeyModal").style.display = "none";
  document.removeEventListener("keydown", _onCaptureKeyDown);
  document.removeEventListener("keyup", _onCaptureKeyUp);
  _capturedKeys.clear();
}

function _onCaptureKeyDown(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (!id) return;
  _capturedKeys.add(id);
  _lastCapturedKeys = [..._capturedKeys];
  document.getElementById("hotkeyCaptureKeys").textContent = hotkeyDisplayStr(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
}

function _onCaptureKeyUp(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (id) _capturedKeys.delete(id);
}

function resetHotkeyDefault() {
  _lastCapturedKeys = isMacPlatform ? ["fn"] : ["win", "ctrl"];
  _capturedKeys.clear();
  document.getElementById("hotkeyCaptureKeys").textContent = isMacPlatform ? "Fn" : "Win + Ctrl";
  document.getElementById("hotkeyError").style.display = "none";
}

async function saveHotkeyCapture() {
  const keys = _lastCapturedKeys;
  const errEl = document.getElementById("hotkeyError");
  const fail = (msg) => { errEl.textContent = msg; errEl.style.display = "block"; };
  if (!keys.length) { fail("Hold the keys you want, then press Save."); return; }
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

// ── API Calls ─────────────────────────────────────────────────────────
async function refreshAll() {
  try {
    if (window.pywebview && window.pywebview.api) {
      const [h, s] = await Promise.all([
        window.pywebview.api.get_history(),
        window.pywebview.api.get_stats()
      ]);
      history = h || [];
      stats   = s || stats;
      renderFeed();
      renderStats();
    }
  } catch (e) {
    console.warn('API not ready yet:', e);
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
    setTimeout(() => {
      btnEl.classList.remove('copied', 'is-done');
      btnEl.innerHTML = WI.icon('copy') + '<span>Copy</span>';
    }, 2500);
  } catch (e) {
    showToast('Failed to copy', 'error');
  }
}

// ── Called by Python after each transcription ─────────────────────────
window.waffler_refresh = function(newItem) {
  if (newItem) {
    history.unshift(newItem);
  }
  if (window.pywebview && window.pywebview.api) {
    Promise.all([
      window.pywebview.api.get_stats()
    ]).then(([s]) => {
      stats = s || stats;
      renderStats();
    });
  }
  renderFeed(newItem ? newItem.timestamp : null);
  if (newItem) {
    // A Not sent card is not a finished transcription.
    if (newItem.failed) showToast('Not sent. The recording is saved in the Journal.', 'error');
    else showToast('Transcription complete', 'success');
  }
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
  if (view.cls === 'listening') {
    // A new recording starts the clock; coming back from a pause carries on.
    if (!_recordingStarted) _recordingStarted = Date.now();
    else if (_recordingPausedAt) _recordingStarted += Date.now() - _recordingPausedAt;
    _recordingPausedAt = 0;
    const tick = () => { if ($statusTime) $statusTime.textContent = WL.recordingTime(_recordingSeconds()); };
    tick();
    _statusTimer = setInterval(tick, 1000);
  } else if (view.cls === 'paused') {
    if (_recordingStarted && !_recordingPausedAt) _recordingPausedAt = Date.now();
    if ($statusTime) $statusTime.textContent = WL.recordingTime(_recordingSeconds());
  } else {
    _recordingStarted = 0;
    _recordingPausedAt = 0;
  }
  if (view.cls === 'processing') {
    const started = Date.now();
    _statusTimer = setInterval(() => {
      const secs = (Date.now() - started) / 1000;
      if ($statusTime) $statusTime.textContent = WL.workingTime(secs);
      $statusInd.title = WL.workingLabel(view.label, secs);
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

// ── Render ─────────────────────────────────────────────────────────────
function renderStats() {
  $statWords.textContent = fmt(stats.today_words);
  $statCount.textContent = fmt(stats.today_count);
  $statTotal.textContent = fmt(stats.total_words);

  // Stack streak — hide the chip entirely when the streak is 0 so we
  // don't show a "0 stack streak" eyesore on the first day of use.
  const streakChip = document.getElementById('streakChip');
  const streakNum  = document.getElementById('streakNum');
  if (streakChip && streakNum) {
    const days = (stats.streak_days || 0);
    streakNum.textContent = String(days);
    streakChip.classList.toggle('j-streak-empty', days <= 0);
  }
}

function fmt(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function renderFeed(newTimestamp) {
  // Filter by search query if present
  const filtered = _searchQuery
    ? history.filter(item => {
        const haystack = ((item.styled || '') + ' ' + (item.text || '')).toLowerCase();
        return haystack.includes(_searchQuery);
      })
    : history;

  // First run, a search with no matches, or the list (logic.js feedView).
  // A search with no matches used to show "Your journal is empty."
  const view = WL.feedView(history.length, _searchText, filtered.length);
  $empty.style.display = view.kind === 'empty' ? 'flex' : 'none';
  const $noMatch = document.getElementById('noMatchState');
  if ($noMatch) $noMatch.style.display = view.kind === 'no_match' ? 'flex' : 'none';
  if (view.kind !== 'list') {
    const label = document.getElementById('noMatchLabel');
    if (label && view.label) label.textContent = view.label;
    $feed.innerHTML = '';
    $feedCount.textContent = history.length
      ? `0 of ${history.length} (filtered)`
      : '0 entries';
    return;
  }

  $feedCount.textContent = _searchQuery
    ? `${filtered.length} of ${history.length}`
    : `${filtered.length} ${filtered.length === 1 ? 'entry' : 'entries'}`;

  // Also drive the journal search placeholder so the count is always
  // visible without needing a separate badge.
  const searchEl = document.getElementById('searchInput');
  if (searchEl && searchEl.classList.contains('j-search-input')) {
    const total = history.length;
    searchEl.placeholder = total
      ? `Search ${total} ${total === 1 ? 'entry' : 'entries'}…`
      : 'Search…';
  }

  $feed.innerHTML = '';
  filtered.forEach((item, idx) => {
    const card = makeCard(item, item.timestamp === newTimestamp && idx === 0);
    $feed.appendChild(card);
  });
}

// Quality badge. The pipeline attaches item.quality only when a recording
// looks suspect, so a clean dictation shows nothing at all - if ordinary
// recordings lit up, the badge would become noise and get ignored.
// It reports; it never blocks or alters the text.
function qualityBadge(item) {
  const q = item && item.quality;
  if (!q || !q.level || q.level === 'ok') return '';
  const labels = {
    low_word_rate:        'far fewer words than the audio length suggests',
    styled_dropped_words: 'cleanup removed an unusual amount of text',
    styling_fallback:     'cleanup did not run - this is the raw transcript',
    truncated_midsentence:'ends mid-sentence - speech may be missing',
    unterminated_ending:  'ends without punctuation',
    asr_filter_edited:    'the transcript filter altered the result',
    retry_used:           'the first provider returned too little; it was retried',
    retry_rejected:       'looks incomplete, and the retry disagreed with it too much to trust',
    styling_deadline:     'cleanup ran out of time; raw text was kept',
  };
  const why = (q.flags || []).map(f => labels[f] || f).join('; ');
  const low = q.level === 'low';
  const cls = low ? 'q-low chip-err' : 'q-check chip-warn';
  const mark = low ? WI.icon('alert') + 'Check this one' : WI.icon('eye') + 'Worth a look';
  return ` <span class="q-badge chip ${cls}" title="${escHtml(why)}">${mark}</span>`;
}

// ── Not sent cards ──────────────────────────────────────────────────────
// A recording that was not turned into text. The card used to show the raw
// note and a Copy button for text that did not exist, although it promised
// the audio was "saved so you can retry": nothing in the app could reach it.
// Now: a plain sentence (logic.js notSentView), Try again, Show the file and
// Delete. Messages from the last try are kept per recording, because a
// card is rebuilt when its entry changes.
const _unsentMessages = {};

function makeNotSentCard(item, isNew) {
  const v = WL.notSentView(item);
  const div = document.createElement('div');
  div.className = 'transcript-card not-sent-card' + (isNew ? ' new' : '');
  if (v.id) div.dataset.unsentId = v.id;
  const msg = v.id ? (_unsentMessages[v.id] || '') : '';
  div.innerHTML = `
    <div class="card-meta">
      <div class="card-time">${escHtml(formatTime(item.timestamp))}</div>
      <div class="ns-badge">${escHtml(v.badge)}</div>
    </div>
    <p class="ns-text">${escHtml(v.text)}</p>
    ${v.next ? `<p class="ns-next">${escHtml(v.next)}</p>` : ''}
    <p class="ns-status" role="status" aria-live="polite"${msg ? '' : ' hidden'}>${escHtml(msg)}</p>
    <div class="card-actions ns-actions">
      ${v.canRetry ? '<button class="btn btn-sec btn-sm btn-copy ns-retry">Try again</button>' : ''}
      ${v.canReveal ? '<button class="btn btn-quiet btn-sm ns-reveal">Show the file</button>' : ''}
      ${v.canDelete ? '<button class="btn btn-quiet btn-sm ns-delete">Delete</button>' : ''}
    </div>
    <div class="ns-confirm" hidden>
      <span>Delete this recording? This can't be undone.</span>
      <button class="btn btn-danger btn-sm ns-confirm-yes">Delete</button>
      <button class="btn btn-sec btn-sm btn-copy ns-confirm-no">Keep it</button>
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
      retry.textContent = 'Sending…';
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
      retry.textContent = 'Try again';
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
        div.remove();
        $feedCount.textContent = `${history.length} ${history.length === 1 ? 'entry' : 'entries'}`;
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
  else renderFeed();
  if (!item.failed) {
    showToast('Sent. The words are in the Journal.', 'success');
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.get_stats().then((s) => { stats = s || stats; renderStats(); }).catch(() => {});
    }
  }
  return true;
}

// Called by Python when a Not sent recording changes: an automatic try
// failed again, or it went through and the card is now a normal entry.
window.waffler_item_updated = function(unsentId, item) {
  _applyItemUpdate(unsentId, item);
  if (_currentPage === 'settings') loadUnsentSummary();
};

function makeCard(item, isNew) {
  if (item && item.failed) return makeNotSentCard(item, isNew);
  const div = document.createElement('div');
  div.className = 'transcript-card' + (isNew ? ' new' : '');

  const displayText = item.styled || item.text || '';
  const rawText     = item.text  || '';
  const hasStyled   = item.styled && item.styled !== item.text;
  const words       = (displayText.split(/\s+/).filter(Boolean)).length;
  const timeStr     = formatTime(item.timestamp);

  div.innerHTML = `
    <div class="card-meta">
      <div class="card-time">${escHtml(timeStr)}</div>
      <div class="card-words">${words} words${qualityBadge(item)}</div>
    </div>
    <div class="card-text styled" id="text-${escHtml(String(item.timestamp))}">${escHtml(displayText)}</div>
    <div class="card-actions">
      <button class="btn btn-sec btn-sm btn-copy" data-text="${escHtml(displayText)}">${WI.icon('copy')}<span>Copy</span></button>
      ${hasStyled ? `<span class="text-toggle" data-timestamp="${escHtml(String(item.timestamp))}" data-raw="${escHtml(rawText)}" data-styled="${escHtml(displayText)}">Show transcript</span>` : ''}
    </div>
  `;

  // Attach event listeners to the buttons
  const copyBtn = div.querySelector('.btn-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', function() {
      const text = this.getAttribute('data-text');
      copyItem(text, this);
    });
  }

  const toggleBtn = div.querySelector('.text-toggle');
  if (toggleBtn && hasStyled) {
    toggleBtn.addEventListener('click', function() {
      const ts = this.getAttribute('data-timestamp');
      const rawText = this.getAttribute('data-raw');
      const styledText = this.getAttribute('data-styled');
      toggleRawHandler(this, ts, rawText, styledText);
    });
  }

  if (isNew) {
    setTimeout(() => div.classList.remove('new'), 3000);
  }

  return div;
}

function toggleRawHandler(toggleEl, ts, rawText, styledText) {
  const textEl = document.getElementById(`text-${ts}`);
  const showingStyled = textEl.classList.contains('styled');
  if (showingStyled) {
    textEl.textContent = rawText;
    textEl.classList.replace('styled', 'raw');
    toggleEl.textContent = 'Show clean';
  } else {
    textEl.textContent = styledText;
    textEl.classList.replace('raw', 'styled');
    toggleEl.textContent = 'Show transcript';
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
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) + ', ' + timeStr;
}

// ── Toast ───────────────────────────────────────────────────────────────
// v3.14.28 — hover-to-keep. The toast still auto-dismisses on a timer,
// but if the user is hovering over it the timer pauses. This way a
// slow reader (or someone reaching for the mouse to click an action
// inside the toast) doesn't get robbed of the message half-way through.
let _toastHoverBound = false;
let _toastTimeoutMs = 2500;

function showToast(msg, type, ms) {
  clearTimeout(toastTimer);
  $toast.textContent = msg;
  // Over the setup wizard a message goes to the top centre: bottom right it
  // sat on the wizard's Next and Finish Setup buttons, so the first click
  // only closed the message (and hovering there kept it up).
  const overWizard = typeof _wizardVisible === 'function' && _wizardVisible();
  $toast.className = `toast visible ${type || ''}${overWizard ? ' over-wizard' : ''}`;
  _toastTimeoutMs = (typeof ms === 'number' && ms > 0) ? ms : 2500;
  toastTimer = setTimeout(dismissToast, _toastTimeoutMs);

  // Bind hover behaviour once; same listener is reused for every toast.
  if (!_toastHoverBound) {
    _toastHoverBound = true;
    $toast.addEventListener('mouseenter', () => {
      clearTimeout(toastTimer);
    });
    $toast.addEventListener('mouseleave', () => {
      // Resume the dismiss timer when the cursor leaves. Use a shorter
      // window than the initial — the user has already read it.
      clearTimeout(toastTimer);
      toastTimer = setTimeout(dismissToast, 1200);
    });
    // Clicking the toast dismisses it immediately (in case the user
    // wants to keep going without waiting for the timer).
    $toast.addEventListener('click', dismissToast);
  }
}

function dismissToast() {
  clearTimeout(toastTimer);
  $toast.classList.remove('visible');
}

// ── Audio Device Selector ─────────────────────────────────────────────

async function loadAudioDevices() {
  try {
    const devices = await pywebview.api.get_audio_devices();
    const current = await pywebview.api.get_selected_device();

    // Fill the microphone lists (the top bar keeps one, not shown yet).
    const selectors = ['deviceSelect', 'micSelect'].map(id => document.getElementById(id)).filter(Boolean);
    if (!selectors.length) return;

    selectors.forEach(sel => {
      sel.innerHTML = '';
      if (!devices || devices.length === 0) {
        sel.innerHTML = '<option value="">No devices found</option>';
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
      // Keep settings page dropdown in sync
      const devSel = document.getElementById('deviceSelect');
      if (devSel) devSel.value = idx;
      showToast(`Microphone: ${result.name}`, 'success');
    }
  } catch(e) {
    console.warn('setAudioDevice error:', e);
  }
}

// The top bar's "Normal" mode menu is gone: Normal was its only real
// choice, so it took space and did nothing. The backend keeps its mode
// calls (get_current_mode, set_mode) for when more modes exist.

// ── Custom Vocabulary ─────────────────────────────────────────────────

// v3.14.23 — legacy loadVocab() / onVocabSave() removed.
// They were leftovers from an older vocabulary UI where #vocabInput
// was a multi-line <textarea> the user could edit directly. The current
// UI is a single-line "Add word" input + a separate list with delete
// buttons, all driven by loadVocabPage() / addVocabWord() / deleteVocabWord().
// The old loadVocab() ran on pywebviewready and dumped every existing
// vocab word joined by '\n' into #vocabInput — so the "Add word" box
// showed up pre-filled with "WafflerAshkanGroqMLXkubernetes..." (all
// the words concatenated, because newlines collapse in a text input).
// Removing it leaves the input clean on every app load.

// ── Vocabulary Page ───────────────────────────────────────────────────
let _vocabWords = [];

async function loadVocabPage() {
  const listEl = document.getElementById('vocabList');
  const emptyEl = document.getElementById('vocabEmpty');
  const countEl = document.getElementById('vocabCount');
  const inputEl = document.getElementById('vocabInput');

  if (!listEl) return;

  // v3.14.24 — belt-and-suspenders: explicitly wipe the "Add word" input
  // every time we render the vocab page. The legacy loadVocab() (removed
  // in v3.14.23) used to dump every word joined by \n into this field on
  // startup; some users on older builds still saw the pre-filled string
  // after upgrading because WebView's form-restoration cached the value.
  // Setting `.value = ''` here is unconditional, cheap, and means the
  // box is guaranteed empty whenever the user lands on the page.
  if (inputEl) inputEl.value = '';

  try {
    _vocabWords = await pywebview.api.get_vocab() || [];
    
    // Update count
    if (countEl) countEl.textContent = _vocabWords.length;
    
    // Clear current list
    listEl.innerHTML = '';
    
    if (_vocabWords.length === 0) {
      listEl.innerHTML = `
        <div class="vocab-empty" id="vocabEmpty">
          <div class="vocab-empty-icon">${WI.icon('book', 'ic-lg')}</div>
          <div class="vocab-empty-label">No words added yet</div>
          <div class="vocab-empty-sub">
            Add tricky words once — names, acronyms, jargon — and Waffler will spell them right every time.
          </div>
          <div class="vocab-empty-examples">
            <span class="vocab-empty-example"><strong>Siobhan</strong> &nbsp;<span style="opacity:.55">(catches "Shavon")</span></span>
            <span class="vocab-empty-example"><strong>JSON</strong> &nbsp;<span style="opacity:.55">(catches "Jason")</span></span>
            <span class="vocab-empty-example"><strong>Postgres</strong> &nbsp;<span style="opacity:.55">(catches "post grass")</span></span>
            <span class="vocab-empty-example"><strong>macOS</strong> &nbsp;<span style="opacity:.55">(catches "Mac OS")</span></span>
          </div>
          <div class="vocab-empty-tip">
            One word or short phrase per entry. No special syntax — just type it the way you want it written.
          </div>
        </div>
      `;
      return;
    }
    
    // Render words
    _vocabWords.forEach((word, idx) => {
      const row = document.createElement('div');
      row.className = 'vocab-word-row';
      row.innerHTML = `
        <span class="vocab-word-text">${escHtml(word)}</span>
        <button class="vocab-word-delete rbtn" onclick="deleteVocabWord(${idx})" title="Delete" aria-label="Delete ${escHtml(word)}">${WI.icon('trash')}</button>
      `;
      listEl.appendChild(row);
    });
    
  } catch(e) {
    console.warn('loadVocabPage error:', e);
  }
}

async function addVocabWord() {
  const inputEl = document.getElementById('vocabInput');
  if (!inputEl) return;
  
  const word = inputEl.value.trim();
  if (!word) {
    showToast('Enter a word first', 'error');
    return;
  }
  
  if (_vocabWords.includes(word)) {
    showToast('Word already exists', 'error');
    return;
  }
  
  _vocabWords.push(word);
  
  try {
    await pywebview.api.set_vocab(_vocabWords);
    inputEl.value = '';
    await loadVocabPage();
    showToast(`Added "${word}"`, 'success');
  } catch(e) {
    console.warn('addVocabWord error:', e);
    showToast('Failed to add word', 'error');
  }
}

async function deleteVocabWord(idx) {
  if (idx < 0 || idx >= _vocabWords.length) return;
  
  const word = _vocabWords[idx];
  _vocabWords.splice(idx, 1);
  
  try {
    await pywebview.api.set_vocab(_vocabWords);
    await loadVocabPage();
    showToast(`Removed "${word}"`, 'success');
  } catch(e) {
    console.warn('deleteVocabWord error:', e);
    showToast('Failed to delete word', 'error');
  }
}

// Load devices once pywebview is ready.
// Vocab list is loaded lazily by loadVocabPage() when the user
// navigates to the Vocabulary tab — no longer pre-loaded on startup
// (which was the cause of the "Add word" box being pre-filled with
// every existing word jammed together).
window.addEventListener('pywebviewready', () => {
  loadAudioDevices();
});

// ── Page Navigation ──────────────────────────────────────────────────────
let _currentPage = 'home';

function showPage(page) {
  _currentPage = page;

  // Update nav
  [['navHome', 'home'], ['navVocab', 'vocabulary'], ['navSettings', 'settings']].forEach(([id, p]) => {
    const tab = document.getElementById(id);
    if (!tab) return;
    tab.classList.toggle('active', page === p);
    tab.setAttribute('aria-selected', String(page === p));
  });

  // Toggle panels - use direct style manipulation
  const mainArea = document.getElementById('mainArea');
  const sp = document.getElementById('settingsPanel');
  const vp = document.getElementById('vocabularyPanel');

  // Hide all panels first
  if (mainArea) mainArea.style.display = 'none';
  if (sp) sp.style.display = 'none';
  if (vp) vp.style.display = 'none';

  // Show the selected panel
  if (page === 'home' && mainArea) mainArea.style.display = 'flex';
  if (page === 'settings' && sp) sp.style.display = 'flex';
  if (page === 'vocabulary' && vp) vp.style.display = 'flex';

  if (page === 'settings') {
    loadSettings();
    refreshThemePicker();
  } else if (page === 'vocabulary') {
    loadVocabPage();
  }
}

// ── Provider fallback order ──────────────────────────────────────────────
// Reorderable list of cleanup/transcription providers. The user sets the
// order; Waffler tries them top-to-bottom. Persisted + applied live via
// save_settings({provider_order}). Cerebras is tagged "cleanup only" because
// it has no speech-to-text endpoint (it's skipped for the transcription step).
// The starting order is the engine's own (logic.js DEFAULT_PROVIDER_ORDER).
let _providerOrder = WL.DEFAULT_PROVIDER_ORDER.slice();
// The last get_settings() answer: which keys are set, and what is in use.
let _lastSettings = null;

const _PROVIDER_META = {
  groq:     { label: 'Groq',     tag: 'recommended',  note: 'Speech + cleanup · free tier' },
  openai:   { label: 'OpenAI',   tag: 'backup',       note: 'Speech + cleanup · pay as you go' },
  cerebras: { label: 'Cerebras', tag: 'cleanup only', note: 'No speech-to-text' },
};

function renderProviderOrder() {
  const host = document.getElementById('providerOrderList');
  if (!host) return;
  const rows = WL.providerOrderRows(_providerOrder, _lastSettings);
  host.innerHTML = rows.map((r, i) => {
    const p = r.id;
    const m = _PROVIDER_META[p] || { label: p, tag: '', note: '' };
    const tag = m.tag ? `<span class="po-tag">${m.tag}</span>` : '';
    const up = i === 0 ? 'disabled' : '';
    const down = i === rows.length - 1 ? 'disabled' : '';
    // No key: greyed out, because Waffler skips it. It can still be moved.
    const note = r.hasKey ? m.note : 'No key yet, so Waffler skips it';
    return `
      <div class="provider-order-item${r.hasKey ? '' : ' po-nokey'}">
        <span class="po-rank">${r.rank}</span>
        <span class="po-name">${m.label} ${tag}<span class="po-note">${note}</span></span>
        <span class="po-controls">
          <button class="po-btn" ${up} onclick="moveProvider('${p}', -1)" title="Move up">&#9650;</button>
          <button class="po-btn" ${down} onclick="moveProvider('${p}', 1)" title="Move down">&#9660;</button>
        </span>
      </div>`;
  }).join('');
}

async function moveProvider(name, delta) {
  const i = _providerOrder.indexOf(name);
  if (i < 0) return;
  const j = i + delta;
  if (j < 0 || j >= _providerOrder.length) return;
  [_providerOrder[i], _providerOrder[j]] = [_providerOrder[j], _providerOrder[i]];
  renderProviderOrder();
  try {
    const r = await pywebview.api.save_settings({ provider_order: _providerOrder });
    if (r && r.ok) {
      showToast('Provider order: ' + _providerOrder.map(p => (_PROVIDER_META[p] || {label:p}).label).join(' → '), 'success');
      // The new order applies at once, so "Speech to text / Clean-up" may change.
      try { _lastSettings = await pywebview.api.get_settings(); } catch (_) {}
      _renderBackendInfo();
    } else {
      showToast('Could not save provider order', 'error');
    }
  } catch (e) {
    showToast('Could not save provider order', 'error');
  }
}

// ── Settings Load ────────────────────────────────────────────────────────
function _renderBackendInfo() {
  const backendInfo = document.getElementById('backendInfo');
  if (backendInfo) backendInfo.textContent = WL.backendsLine(_lastSettings);
}

async function loadSettings() {
  try {
    const s = await pywebview.api.get_settings();
    _lastSettings = s;

    // Keys are listed Groq, OpenAI, then Cerebras (optional), as in setup.
    // Cerebras key (optional, cleanup only)
    const cerebrasInput = document.getElementById('cerebrasKeyInput');
    const cerebrasDesc = document.getElementById('cerebrasKeyDesc');
    if (cerebrasInput) {
      cerebrasInput.placeholder = s.cerebras_key_set ? s.cerebras_key_masked : 'csk-…';
    }
    if (cerebrasDesc) {
      cerebrasDesc.textContent = s.cerebras_key_set
        ? ('Active: ' + s.cerebras_key_masked)
        : 'Optional. Clean-up only, no speech to text. cloud.cerebras.ai/platform/api-keys';
    }

    // Groq key
    const groqInput = document.getElementById('groqKeyInput');
    const groqDesc = document.getElementById('groqKeyDesc');
    if (groqInput) {
      groqInput.placeholder = s.groq_key_set ? s.groq_key_masked : 'gsk_…';
    }
    if (groqDesc) {
      groqDesc.textContent = s.groq_key_set
        ? ('Active: ' + s.groq_key_masked)
        : 'Recommended. Very fast, with a free plan. console.groq.com';
    }

    // OpenAI key
    const apiInput = document.getElementById('apiKeyInput');
    const apiStatus = document.getElementById('apiKeyStatus');
    if (apiInput) {
      apiInput.placeholder = s.api_key_set ? s.api_key_masked : 'sk-…';
    }
    if (apiStatus) {
      if (s.api_key_set) {
        apiStatus.textContent = 'Key set: ' + s.api_key_masked;
        apiStatus.className = 'api-key-status ok';
      } else {
        apiStatus.textContent = 'No API key set';
        apiStatus.className = 'api-key-status err';
      }
    }

    // "Speech to text: Groq · Clean-up: Groq": what each stage uses first.
    _renderBackendInfo();

    // Provider fallback order (reorderable list). Providers without a key
    // are greyed out, so the list shows what Waffler will really try.
    _providerOrder = WL.normalizeProviderOrder(s.provider_order);
    renderProviderOrder();

    // Local Whisper
    const lwToggle = document.getElementById('localWhisperToggle');
    const lwLabel  = document.getElementById('localWhisperLabel');
    if (lwToggle) lwToggle.checked = s.local_whisper;
    if (lwLabel)  lwLabel.textContent = s.local_whisper ? (s.local_whisper_active ? 'On (active)' : 'On (restart required)') : 'Off';

    // Language
    const langSel = document.getElementById('languageSelect');
    if (langSel) langSel.value = s.language || 'en';

    // Dialect / Spelling
    const dialectSel = document.getElementById('dialectSelect');
    if (dialectSel) dialectSel.value = s.dialect || 'auto';

    // Auto-paste
    const apToggle = document.getElementById('autoPasteToggle');
    const apLabel  = document.getElementById('autoPasteLabel');
    if (apToggle) apToggle.checked = s.auto_paste !== false;
    if (apLabel)  apLabel.textContent = (s.auto_paste !== false) ? 'On' : 'Off';

  } catch(e) {
    console.warn('loadSettings error:', e);
  }
}

// ── Settings Save ────────────────────────────────────────────────────────
// v3.14.30 — after any API-key save, show a centered modal popup (was a
// top-of-page banner in v3.14.28-29). User reported the top banner was
// easy to miss; a centered modal with a soft backdrop is more obviously
// "you need to do something here". Escape dismisses; Enter triggers
// restart. The "Restart now" button is autofocused so the keyboard path
// is one tap.

function showRestartBanner(reason) {
  // Tear down any prior modal so we don't stack them.
  const prior = document.getElementById('restartRequiredModal');
  if (prior) prior.remove();

  const overlay = document.createElement('div');
  overlay.id = 'restartRequiredModal';
  overlay.className = 'restart-modal-overlay';
  overlay.innerHTML = `
    <div class="restart-modal-card" role="dialog" aria-modal="true"
         aria-labelledby="restartModalTitle">
      <div class="restart-modal-icon">${WI.icon('refresh', 'ic-lg')}</div>
      <h2 class="restart-modal-title" id="restartModalTitle">Restart required</h2>
      <p class="restart-modal-body">${reason || 'Your changes need a fresh app start to take effect.'}</p>
      <div class="restart-modal-actions">
        <button class="restart-modal-btn-secondary" id="restartModalLater">Later</button>
        <button class="restart-modal-btn-primary" id="restartModalNow">Restart now</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const dismiss = () => {
    overlay.classList.add('restart-modal-closing');
    setTimeout(() => overlay.remove(), 180);
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
        showToast('Restart Waffler manually to apply changes', 'info');
        dismiss();
      }
    } catch (_e) {
      showToast('Couldn\'t auto-restart — please quit and reopen Waffler', 'error');
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

  // Focus the primary CTA after the entry animation settles so keyboard
  // users can hit Enter immediately.
  requestAnimationFrame(() => {
    const btn = document.getElementById('restartModalNow');
    if (btn) btn.focus();
  });
}

async function saveGroqKey() {
  const inp = document.getElementById('groqKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { showToast('Enter a Groq API key first', 'error'); return; }
  if (!val.startsWith('gsk_')) { showToast("That isn't a Groq key. Groq keys start with gsk_", 'error'); return; }
  try {
    const r = await pywebview.api.save_settings({ groq_key: val });
    if (r.ok) {
      inp.value = '';
      showToast('Groq key saved', 'success');
      showRestartBanner('Restart Waffler to start using the new Groq key.');
      await loadSettings();
    } else {
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
  if (!val) { showToast('Enter a Cerebras API key first', 'error'); return; }
  if (!val.startsWith('csk-')) { showToast("That isn't a Cerebras key. Cerebras keys start with csk-", 'error'); return; }
  try {
    // validate_cerebras_key persists on success.
    const r = await pywebview.api.validate_cerebras_key(val);
    if (r.ok) {
      inp.value = '';
      showToast(r.message || 'Cerebras key saved', 'success');
      showRestartBanner('Restart Waffler to start using the new Cerebras key.');
      await loadSettings();
    } else {
      showToast(r.error || "Couldn't check that key with Cerebras. Try again in a moment.", 'error', 6000);
    }
  } catch(e) {
    showToast("Couldn't save the key. Try again.", 'error');
  }
}

async function saveApiKey() {
  const inp = document.getElementById('apiKeyInput');
  const statusEl = document.getElementById('apiKeyStatus');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { showToast('Enter an API key first', 'error'); return; }
  if (!val.startsWith('sk-')) { showToast("That isn't an OpenAI key. OpenAI keys start with sk-", 'error'); return; }
  try {
    const r = await pywebview.api.save_settings({ api_key: val });
    if (r.ok) {
      inp.value = '';
      if (statusEl) {
        statusEl.textContent = 'Key saved and active';
        statusEl.className = 'api-key-status ok';
      }
      showToast('OpenAI key saved', 'success');
      showRestartBanner('Restart Waffler to start using the new OpenAI key.');
    } else {
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
      // Update toggle labels
      if (key === 'auto_paste') {
        const lbl = document.getElementById('autoPasteLabel');
        if (lbl) lbl.textContent = value ? 'On' : 'Off';
        showToast('Auto-paste ' + (value ? 'enabled' : 'disabled'), 'success');
      } else if (key === 'language') {
        showToast('Language saved', 'success');
      } else if (key === 'dialect') {
        const labels = {'auto': 'Auto', 'en-GB': 'British English', 'en-US': 'American English'};
        showToast('Spelling: ' + (labels[value] || value), 'success');
      }
    }
  } catch(e) {
    console.warn('saveSetting error:', e);
  }
}

// ── Search ────────────────────────────────────────────────────────────────
let _searchQuery = '';
let _searchText = '';  // as typed, for "No entries match "…""

// The feed is rebuilt once typing pauses (logic.js SEARCH_DEBOUNCE_MS), not
// on every keystroke: each rebuild took 340 to 713 ms with 3,300 entries.
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

window.addEventListener('pywebviewready', checkOnboarding);

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
    showPage('home');
    refreshAll();
    loadAudioDevices();
  }, 400);
}

function _wizardVisible() {
  const o = document.getElementById('wizardOverlay');
  return !!o && o.style.display !== 'none';
}

function _wizSection(step) {
  return document.getElementById(WIZ_SECTIONS[step || _wizardStep]);
}

// Show the parts of a step meant for this state and hide the rest.
function wizSetState(step, state) {
  const sec = _wizSection(step);
  if (!sec) return;
  sec.dataset.state = state;
  sec.querySelectorAll('[data-when]').forEach((el) => {
    el.hidden = !el.dataset.when.split(' ').includes(state);
  });
  if (step === _wizardStep) wizUpdateNextButton();
}

function wizRenderStepper() {
  const host = document.getElementById('obStepper');
  if (!host) return;
  const cur = WIZ_STEPS.indexOf(_wizardStep);
  host.innerHTML = WIZ_STEPS.map((s, i) => {
    const cls = i < cur ? 'is-done' : i === cur ? 'is-on' : '';
    const n = i < cur ? '<svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>' : String(i + 1);
    const sep = i ? '<li class="ob-ssep" aria-hidden="true"></li>' : '';
    return `${sep}<li class="${cls}"${i === cur ? ' aria-current="step"' : ''}><span class="ob-sn">${n}</span>${WIZ_LABELS[s]}</li>`;
  }).join('');
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
  _wizCheckClipboardOnce(false);
}

function stopWizClipboardWatch() {
  if (_wizClipTimer) { clearInterval(_wizClipTimer); _wizClipTimer = null; }
  if (_wizClipOnFocus) {
    window.removeEventListener('focus', _wizClipOnFocus);
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
  inp.addEventListener('input', () => {
    _wizKeyOk = false;
    inp.removeAttribute('aria-invalid');
    const v = WL.keyInputView(inp.value);
    if (v.kind === 'empty') { wizKeyWaiting(); check.cancel(); return; }
    if (v.kind !== 'ok') {
      wizSetState('connect', 'error');
      wizKeyMessage('alert', v.message, '');
      inp.setAttribute('aria-invalid', 'true');
      check.cancel();
      return;
    }
    check();
  });
  inp.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    check.cancel();
    if (WL.keyInputView(inp.value).kind === 'ok') wizValidateGroqKey(inp.value.trim(), false);
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
  const box = document.getElementById('obKeyState');
  if (box) {
    box.className = 'ob-keystate';
    box.innerHTML = '<span class="ob-pulse" aria-hidden="true"></span><span>Waiting for your key. Waffler picks it up when you come back.</span>';
  }
}

// A line in the key box: an icon tile, a sentence, and a second line.
function wizKeyMessage(icon, title, detail) {
  const box = document.getElementById('obKeyState');
  if (!box) return;
  const tile = icon === 'loader'
    ? '<span class="ob-pulse" aria-hidden="true"></span>'
    : `<span class="itile ${icon === 'alert' ? 'is-err' : 'is-ok'}"><svg class="ic" aria-hidden="true"><use href="#i-${icon}"/></svg></span>`;
  box.className = 'ob-keystate' + (icon === 'alert' ? ' is-err' : '');
  box.innerHTML = `${tile}<span><b class="ob-keystate-t">${escHtml(title)}</b>`
    + (detail ? `<span class="ob-keystate-d">${escHtml(detail)}</span>` : '') + '</span>';
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

function wizRenderPermissions() {
  const order = ['microphone', 'input_monitoring', 'accessibility'];
  const next = order.find((p) => !_wizPerms[p]);
  order.forEach((p) => {
    const row = document.getElementById(_WIZ_PERM_ROWS[p]);
    const ctl = row && row.querySelector('.ob-perm-ctl');
    if (!ctl) return;
    ctl.innerHTML = _wizPerms[p]
      ? '<span class="chip chip-ok"><svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>Allowed</span>'
      : `<button class="btn btn-sm ${p === next ? 'btn-pri' : 'btn-sec'}" onclick="wizAllow('${p}')">Allow</button>`;
  });
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

// Opening Settings also loads Usage, the version and the Not sent count.
const _origLoadSettings = loadSettings;
loadSettings = async function() {
  await _origLoadSettings();
  await loadUsageStats();
  await loadAppVersion();
  await loadUnsentSummary();
  await loadStartAtLogin();
  await loadSettingsFnWarning();
};

// ── Start at sign-in (Settings, Preferences) ─────────────────────────────
// The switch shows what the operating system has, read each time Settings
// opens: the user can also remove it in Task Manager or Login Items.
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

// ── Recordings not sent (Settings, Data) ─────────────────────────────────
async function loadUnsentSummary() {
  const desc = document.getElementById('unsentSummary');
  const btn = document.getElementById('unsentSendNow');
  if (!desc || !window.pywebview || !window.pywebview.api || !pywebview.api.get_unsent_summary) return;
  try {
    const v = WL.unsentSummary(await pywebview.api.get_unsent_summary());
    desc.textContent = v.label;
    if (btn) btn.style.display = v.canSend ? '' : 'none';
  } catch (e) {
    console.warn('get_unsent_summary failed:', e);
  }
}

async function sendUnsentNow(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
  try {
    const r = await pywebview.api.retry_all_unsent();
    if (r && r.total) {
      showToast(r.sent === r.total
        ? (r.total === 1 ? 'Sent. The words are in the Journal.' : `All ${r.total} sent. The words are in the Journal.`)
        : `${r.sent} of ${r.total} sent. The rest will be tried again later.`,
        r.sent ? 'success' : 'error');
    }
  } catch (e) {
    showToast("Couldn't send them. Try again in a moment.", 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Send now'; }
    await loadUnsentSummary();
  }
}

// ── Usage Stats ───────────────────────────────────────────────────────────
// Provider display metadata — gold dot per provider for the breakdown rows.
const PROVIDER_META = {
  // Model names shown in the Usage panel. These are labels only, but a
  // stale one is a lie about what you are being billed for: both Groq and
  // Cerebras were still advertised as models that had been retired.
  groq:     { name: 'Groq',     accent: '#f55036', desc: 'gpt-oss-120b' },
  cerebras: { name: 'Cerebras', accent: '#C8A256', desc: 'gpt-oss-120b' },
  openai:   { name: 'OpenAI',   accent: '#10a37f', desc: 'gpt-4.1-mini' },
  local:    { name: 'Local',    accent: '#6B6560', desc: 'On-device' },
  unknown:  { name: 'Unknown',  accent: '#A09890', desc: '' },
};

function _fmtUsd(n, digits = 2) {
  return '$' + (Number(n) || 0).toFixed(digits);
}

async function loadUsageStats() {
  try {
    const stats = await pywebview.api.get_usage_stats();
    // Words come from the Journal, as in the stats strip.
    let words = null;
    try { words = await pywebview.api.get_stats(); } catch (_) {}
    const v = WL.usageView(stats, words);
    const put = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

    // Counts first, then the cost estimate at published paid rates, which
    // is labelled as such: Waffler can't see anyone's bill or plan.
    put('usageTranscriptions', v.dictations);
    put('usageWords', v.words);
    put('usageEstimateNote', v.note);
    put('usageAvgCost', `${v.costs.perDictation} a dictation`);
    put('usageTodayCost', v.costs.today);
    put('usageWeekCost', v.costs.week);
    put('usageMonthCost', v.costs.month);
    put('usageTotalCost', v.costs.total);

    // Per-provider breakdown
    const rows = document.getElementById('usageProviderRows');
    if (rows) {
      const byProv = stats.by_provider || {};
      // The engine's order (groq, openai, cerebras), then anything else.
      const order = WL.DEFAULT_PROVIDER_ORDER;
      const sorted = order.filter((p) => byProv[p])
        .concat(Object.keys(byProv).filter((p) => !order.includes(p)));

      if (!sorted.length) {
        rows.innerHTML = '<div class="usage-provider-empty">No usage yet. Make your first dictation to see the breakdown.</div>';
      } else {
        const totalAll = sorted.reduce((s, p) => s + (byProv[p].cost_usd || 0), 0) || 1;
        rows.innerHTML = sorted.map((p) => {
          const b = byProv[p];
          const meta = PROVIDER_META[p] || PROVIDER_META.unknown;
          const pct = ((b.cost_usd / totalAll) * 100).toFixed(1);
          // Calls priced at an unpublished rate (Cerebras publishes no
          // per-token price) are labelled, so the figure is not read as exact.
          const est = (b.estimated_count || 0) > 0;
          return `
            <div class="usage-provider-row">
              <div class="usage-provider-row-head">
                <span class="usage-provider-dot" style="background:${meta.accent}"></span>
                <span class="usage-provider-name">${meta.name}</span>
                ${meta.desc ? `<span class="usage-provider-desc">${meta.desc}</span>` : ''}
                ${est ? `<span class="usage-provider-est" title="Estimated cost: this provider does not publish a per-token price.">estimate</span>` : ''}
                <span class="usage-provider-cost">${est ? '~' : ''}${_fmtUsd(b.cost_usd, 4)}</span>
                <span class="usage-provider-count">${b.count} call${b.count === 1 ? '' : 's'}</span>
              </div>
              <div class="usage-provider-bar"><div class="usage-provider-bar-fill" style="width:${pct}%;background:${meta.accent}"></div></div>
            </div>
          `;
        }).join('');
      }
    }
  } catch(e) {
    console.warn('loadUsageStats error:', e);
  }
}

async function loadAppVersion() {
  try {
    const ver = await pywebview.api.get_app_version();
    const el = document.getElementById('aboutVersion');
    // The models in use ("Powered by Whisper large v3 and gpt-oss-120b").
    if (el) el.textContent = WL.aboutLine(ver, _lastSettings);
  } catch(e) {
    console.warn('loadAppVersion error:', e);
  }
}


