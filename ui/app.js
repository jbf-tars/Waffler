/* Waffler — Frontend Logic */

// Pure helpers (labels, presets, formatting) live in logic.js, which
// index.html loads first, so the tests can run them without a page.
const WL = window.WafflerLogic;

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
let _fnKeyPressed = false;
let _fnKeyCheckInterval = null;

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
  initializeProviderSelection();
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

async function checkForUpdates() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const r = await pywebview.api.check_for_updates();
    // A previous update that silently did nothing used to leave no trace at
    // all — the app just restarted on the old version. Say so plainly.
    if (r.last_update_failed && r.last_update_failed.message) {
      const host0 = document.querySelector('.sidebar') || document.querySelector('.journal');
      if (host0) {
        const warn = document.createElement('div');
        warn.className = 'update-banner';
        const w = document.createElement('span');
        w.textContent = r.last_update_failed.message;
        const x = document.createElement('button');
        x.className = 'dismiss';
        x.textContent = '✕';
        x.addEventListener('click', () => warn.remove());
        warn.appendChild(w); warn.appendChild(x);
        host0.prepend(warn);
      }
    }
    if (r.update_available) {
      // Show update banner — sidebar in legacy layout, top of `.journal`
      // in the v3.14.16+ Journal layout. Prepend so it's the very first
      // child the user sees.
      const host = document.querySelector('.sidebar') || document.querySelector('.journal');
      if (!host) return;
      const banner = document.createElement('div');
      banner.className = 'update-banner';
      const span = document.createElement('span');
      span.textContent = `Update v${r.latest_version} available`;

      const downloadBtn = document.createElement('button');
      downloadBtn.textContent = 'Download';
      downloadBtn.addEventListener('click', () => {
        // v3.14.34 — open the same in-app download-and-install modal
        // that Settings → About uses, instead of bouncing the user out
        // to the website. The modal streams the installer with a
        // progress bar and runs `install_update_and_restart` on
        // completion, so the user never has to leave the app. Falls
        // back to opening wafflerai.com/download/ via the modal's
        // "Open download page" button if the in-app fetch fails.
        openUpdateModalFromCheck(r);
        banner.remove();
      });

      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'dismiss';
      dismissBtn.textContent = '✕';
      dismissBtn.addEventListener('click', () => {
        banner.remove();
      });

      banner.append(span, downloadBtn, dismissBtn);
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
  document.getElementById('updateModalIcon').textContent = icon || '⬆️';
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
  setUpdateModal({ icon: '🔄', title: 'Checking for updates…', subtitle: 'Contacting GitHub…' });
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
    icon: '⬇️',
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
        icon: '✓',
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
  setUpdateModal({ icon: '⚙️', title: 'Installing…', subtitle: 'Waffler is closing to apply the update.' });
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
  const isWin = navigator.userAgent.includes('Windows');
  if (!isWin) {
    const badge = document.getElementById('hotkeyBadge');
    const sidebarBadge = document.getElementById('hotkeyHint');
    const label = document.getElementById('hotkeyLabel');
    const emptyHint = document.getElementById('emptyHint');
    if (badge) badge.textContent = 'Fn';
    if (sidebarBadge) sidebarBadge.textContent = 'Fn';
    if (label) label.textContent = 'Tap to start/stop';
    if (emptyHint) emptyHint.innerHTML = 'Press <strong>Fn</strong> to start recording';
  }
  loadHotkeyConfig();
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

function updatePermissionUI(permissionType, isGranted) {
  const card = document.getElementById(`${permissionType}Card`);
  const badge = document.getElementById(`${permissionType}Badge`);
  const button = document.getElementById(`${permissionType}Btn`);
  const status = document.getElementById(`${permissionType}Status`);

  if (!card || !badge || !button || !status) return;

  if (isGranted) {
    // Update card
    card.classList.add('granted');

    // Update badge
    badge.textContent = '✓';
    badge.classList.add('granted');

    // Update button
    button.textContent = '✓ Granted';
    button.classList.add('granted');
    button.disabled = true;

    // Update status
    status.textContent = 'Granted';
    status.classList.add('granted');
  } else {
    // Reset to default state
    card.classList.remove('granted');
    badge.textContent = '○';
    badge.classList.remove('granted');
    button.textContent = 'Open System Settings';
    button.classList.remove('granted');
    button.disabled = false;
    status.textContent = 'Not granted';
    status.classList.remove('granted');
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
      const sidebarBadge = document.getElementById("hotkeyHint");
      if (sidebarBadge) sidebarBadge.textContent = display;
      const hintDesc = document.getElementById("hotkeyHintDesc");
      if (hintDesc) hintDesc.textContent = `Hold to record \u2022 Space = sticky \u2022 Esc = cancel`;
      const badge = document.getElementById("hotkeyBadge");
      if (badge) badge.textContent = display;
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
  // Opened from the wizard's hotkey step: say so there too.
  if (_wizardStep === 2 && _wizardVisible()) wizHotkeyChanged(result);
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
    btnEl.classList.add('copied');
    btnEl.innerHTML = '<span style="font-size:1.1em">✅</span> Copied!';
    btnEl.style.background = 'rgba(34, 197, 94, 0.25)';
    btnEl.style.borderColor = '#22c55e';
    btnEl.style.color = '#22c55e';
    btnEl.style.fontWeight = '600';
    setTimeout(() => {
      btnEl.classList.remove('copied');
      btnEl.innerHTML = '📋 Copy';
      btnEl.style.background = '';
      btnEl.style.borderColor = '';
      btnEl.style.color = '';
      btnEl.style.fontWeight = '';
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
    else showToast('Transcription complete ✨', 'success');
  }
};

// ── Called by Python for status updates ──────────────────────────────
// Only the state class changes: the pill keeps its own j-listen class,
// which the old `className = ...` assignment wiped, so the pill lost its
// styling while recording. That version also threw on a #recordingOverlay
// element that no longer exists, before the "Done" to "Ready" reset was
// scheduled, so after the first dictation the label stuck on "Done".
let _statusResetTimer = null;
// While a dictation is processed the pill counts the seconds ("Cleaning up
// · 4 s"), so working and stuck no longer look the same. One timer, only
// while processing; it stops on the next status.
let _workingTimer = null;

function _showStatus(view) {
  if (!$statusInd || !$statusText) return;
  $statusInd.classList.remove(...WL.STATUS_CLASSES);
  $statusInd.classList.add(view.cls);
  $statusText.textContent = view.label;
}

window.waffler_status = function(status) {
  clearTimeout(_statusResetTimer);
  _statusResetTimer = null;
  clearInterval(_workingTimer);
  _workingTimer = null;
  const view = WL.statusView(status);
  _showStatus(view);
  if (view.cls === 'processing') {
    const started = Date.now();
    _workingTimer = setInterval(() => {
      if ($statusText) $statusText.textContent = WL.workingLabel(view.label, (Date.now() - started) / 1000);
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
  const cls = q.level === 'low' ? 'q-low' : 'q-check';
  const mark = q.level === 'low' ? '⚠ check this one' : 'ℹ worth a look';
  return ` <span class="q-badge ${cls}" title="${escHtml(why)}">${mark}</span>`;
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
      ${v.canRetry ? '<button class="btn-copy ns-retry">Try again</button>' : ''}
      ${v.canReveal ? '<button class="btn-copy ns-reveal">Show the file</button>' : ''}
      ${v.canDelete ? '<button class="ns-delete">Delete</button>' : ''}
    </div>
    <div class="ns-confirm" hidden>
      <span>Delete this recording? This can't be undone.</span>
      <button class="ns-confirm-yes">Delete</button>
      <button class="btn-copy ns-confirm-no">Keep it</button>
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
      <button class="btn-copy" data-text="${escHtml(displayText)}">📋 Copy</button>
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

    // Populate both the settings page dropdown and sidebar dropdown
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

async function onDeviceChange(indexStr) {
  const idx = parseInt(indexStr, 10);
  try {
    const result = await pywebview.api.set_audio_device(idx);
    if (result && result.ok) {
      // Keep sidebar mic dropdown in sync
      const micSel = document.getElementById('micSelect');
      if (micSel) micSel.value = idx;
      showToast(`🎙️ Mic: ${result.name}`, 'success');
    }
  } catch(e) {
    console.warn('setAudioDevice error:', e);
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
      showToast(`🎙️ Mic: ${result.name}`, 'success');
    }
  } catch(e) {
    console.warn('setAudioDevice error:', e);
  }
}

// The top bar's "✨ Normal" mode menu is gone: Normal was its only real
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
          <div class="vocab-empty-icon">📝</div>
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
        <button class="vocab-word-delete" onclick="deleteVocabWord(${idx})" title="Delete">🗑️</button>
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
  document.getElementById('navHome').classList.toggle('active', page === 'home');
  document.getElementById('navVocab').classList.toggle('active', page === 'vocabulary');
  document.getElementById('navSettings').classList.toggle('active', page === 'settings');

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
      <div class="restart-modal-icon">🔄</div>
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
      if (key === 'local_whisper') {
        const lbl = document.getElementById('localWhisperLabel');
        if (lbl) lbl.textContent = value ? 'On (restart required)' : 'Off';
        if (value) showToast('⚡ Local Whisper enabled — restart to activate', 'success');
        else       showToast('Local Whisper off', 'success');
      } else if (key === 'auto_paste') {
        const lbl = document.getElementById('autoPasteLabel');
        if (lbl) lbl.textContent = value ? 'On' : 'Off';
        showToast('Auto-paste ' + (value ? 'enabled' : 'disabled'), 'success');
      } else if (key === 'language') {
        showToast('🌐 Language saved', 'success');
      } else if (key === 'dialect') {
        const labels = {'auto': 'Auto', 'en-GB': 'British English', 'en-US': 'American English'};
        showToast('Spelling: ' + (labels[value] || value), 'success');
      }
    }
  } catch(e) {
    console.warn('saveSetting error:', e);
  }
}

function toggleApiReveal() {
  const inp = document.getElementById('apiKeyInput');
  if (!inp) return;
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

function openApiLink() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) {
    window.pywebview.api.open_url('https://platform.openai.com/api-keys');
  }
}

// ── Export / Clear History ────────────────────────────────────────────────
async function exportHistory() {
  try {
    const r = await pywebview.api.export_history();
    if (!r.ok) { showToast(r.error || 'Nothing to export', 'error'); return; }
    // Trigger file download via data URI
    const blob = new Blob([r.content], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `waffler-history-${new Date().toISOString().slice(0,10)}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast(`📥 Exported ${r.count} transcriptions`, 'success');
  } catch(e) {
    showToast('Export failed', 'error');
  }
}

async function clearHistory() {
  if (!confirm('Delete all transcription history? This cannot be undone.')) return;
  try {
    const r = await pywebview.api.clear_history();
    if (r.ok) {
      history = [];
      renderFeed();
      showToast('🗑️ History cleared', 'success');
    } else {
      console.warn('clear_history failed:', r.error);
      showToast("Couldn't clear History. Try again.", 'error');
    }
  } catch(e) {
    showToast("Couldn't clear History. Try again.", 'error');
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
// ── Setup Wizard ─────────────────────────────────────────────
// ============================================================

let _wizardStep = 1;
const WIZARD_TOTAL_STEPS = isMacPlatform ? 4 : 3;
const WIZARD_FIRST_STEP = isMacPlatform ? 1 : 2;  // Skip permissions on Windows
let _wizardGroqKeyValidated = false;
let _wizardApiKeyValidated = false;
let _wizardCerebrasKeyValidated = false;
let _wizardMicTested = false;
let _wizardMicDeviceIndex = null;
let _wizardPermissionsGranted = false;
let _wizardPermCheckInterval = null;

async function checkOnboarding() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;

    const status = await pywebview.api.get_onboarding_status();
    if (status.needs_setup) {
      showWizard();
    } else {
      const sidebar = document.querySelector('.sidebar');
      if (sidebar) sidebar.style.display = '';
      const main = document.getElementById('mainArea');
      if (main) main.style.display = '';
      refreshAll();
    }
  } catch(e) {
    console.warn('checkOnboarding error:', e);
  }
}

async function checkOnboardingAfterAuth() {
  try {
    const status = await pywebview.api.get_onboarding_status();
    if (status.needs_setup) {
      showWizard();
    } else {
      // Show main app
      const sidebar = document.querySelector('.sidebar');
      if (sidebar) sidebar.style.display = '';
      const main = document.getElementById('mainArea');
      if (main) main.style.display = '';
      refreshAll();
    }
  } catch(e) {
    console.warn('checkOnboardingAfterAuth error:', e);
  }
}

window.addEventListener('pywebviewready', checkOnboarding);

function showWizard() {
  const overlay = document.getElementById('wizardOverlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  // Hide main app UI
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) sidebar.style.display = 'none';
  const main = document.getElementById('mainArea');
  if (main) main.style.display = 'none';
  const settings = document.getElementById('settingsPanel');
  if (settings) settings.style.display = 'none';
  const vocab = document.getElementById('vocabularyPanel');
  if (vocab) vocab.style.display = 'none';
  // Initialize progress bar — skip permissions step on Windows
  const wizSub = document.getElementById('wizSubtitle');
  if (wizSub) wizSub.textContent = `Let's get you set up in ${WIZARD_TOTAL_STEPS} quick steps.`;
  updateWizardProgress(WIZARD_FIRST_STEP);
  wizShowStep(WIZARD_FIRST_STEP);
  setTimeout(() => {
    const inp = document.getElementById('wizApiKeyInput3');
    if (inp) inp.focus();
  }, 200);
}

function hideWizard() {
  stopFnKeyPolling();
  stopWizClipboardWatch();
  wizStopExplainerWaffle();
  const overlay = document.getElementById('wizardOverlay');
  if (!overlay) return;
  overlay.classList.add('hiding');
  setTimeout(() => {
    overlay.style.display = 'none';
    overlay.classList.remove('hiding');
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.style.display = 'flex';
    showPage('home');
    refreshAll();
    loadAudioDevices();
  }, 400);
}

// ── Step Navigation ──────────────────────────────────────────

async function triggerMacOSPermissions() {
  try {
    await pywebview.api.trigger_permission_requests();
  } catch (error) {
    console.warn('[Wizard] Permission trigger failed:', error);
    // Fail silently - users can still use "Open System Settings" buttons
  }
}

function updateWizardProgress(step) {
  // Update step text — offset display number on Windows (no permissions step)
  const displayStep = step - WIZARD_FIRST_STEP + 1;
  const stepText = document.getElementById('wizStepText');
  if (stepText) {
    stepText.textContent = `Step ${displayStep} of ${WIZARD_TOTAL_STEPS}`;
  }

  // Update progress segments — hide segment 1 on Windows
  for (let i = 1; i <= 4; i++) {
    const segment = document.getElementById(`wizProgress${i}`);
    if (!segment) continue;
    if (i < WIZARD_FIRST_STEP) {
      segment.style.display = 'none';
    } else {
      segment.classList.toggle('active', i === step);
    }
  }
}

// ── Clipboard key pickup (wizard step 3) ─────────────────────────────────────
// Setup's most annoying moment is the hand-off: create a key on the provider's
// site, copy it, come back, find the field, paste. The copy has already
// happened, so the app can just notice. While the key step is open we poll for
// a key-shaped clipboard entry and fill it in.
//
// The backend only ever returns text matching a known key shape, so ordinary
// clipboard contents are never read into the UI. Filling the field is not
// irreversible either: the user can clear or overwrite it.
let _wizClipTimer = null;        // legacy poll handle, kept so an in-flight
                                 // timer from a previous build is cleared
let _wizClipLastSeen = '';
let _wizClipLastCheck = 0;
let _wizClipOnFocus = null;

const _WIZ_KEY_FIELDS = {
  groq:     { input: 'wizGroqKeyInput3',     validate: (k) => wizValidateGroqKey(k) },
  openai:   { input: 'wizApiKeyInput3',      validate: (k) => wizValidateApiKey(k) },
  cerebras: { input: 'wizCerebrasKeyInput3', validate: (k) => (typeof wizValidateCerebrasKey === 'function' ? wizValidateCerebrasKey(k) : null) },
};

// Check the clipboard ONCE, on demand. Never on a timer.
//
// This used to poll every 1200 ms while step 3 was open. Windows Defender's
// behavioural model started flagging the app as
// Behavior:Win32/CredentialAccess.A!ml on 2026-09-22 and deleting
// Waffler.exe mid-install, and a process repeatedly reading the clipboard
// and regex-matching it for `sk-` / `gsk_` secrets is the single most
// credential-stealer-shaped thing in the codebase — roughly 50 scans a
// minute, for a key that arrives once.
//
// The user experience is unchanged, because the only moment the poll ever
// caught anything was the alt-tab back from the provider's website. That
// moment IS a window focus event, so we read the clipboard then: once per
// return to the app instead of continuously. Same pickup, ~1/50th of the
// reads, and no standing clipboard surveillance.
async function _wizCheckClipboardOnce() {
  if (!(window.pywebview && pywebview.api && pywebview.api.peek_clipboard_key)) return;
  // Debounce: a focus flap must not turn back into a poll.
  const now = Date.now();
  if (now - _wizClipLastCheck < 400) return;
  _wizClipLastCheck = now;
  try {
    const r = await pywebview.api.peek_clipboard_key();
    if (!r || !r.found || !r.key) return;
    if (r.key === _wizClipLastSeen) return;   // already handled this one
    const field = _WIZ_KEY_FIELDS[r.provider];
    if (!field) return;
    const el = document.getElementById(field.input);
    if (!el || el.value.trim() === r.key) return;
    _wizClipLastSeen = r.key;
    el.value = r.key;
    // Switch to that provider's tab so the user sees where it landed.
    const tab = document.querySelector(`.wiz-prov-tab[data-provider="${r.provider}"]`);
    if (tab) tab.click();
    wizNotePickedUpKey(r.provider);
    field.validate(r.key);
  } catch (e) { /* clipboard unavailable: the user can still paste by hand */ }
}

function startWizClipboardWatch() {
  stopWizClipboardWatch();
  if (!(window.pywebview && pywebview.api && pywebview.api.peek_clipboard_key)) return;
  // One check on arrival (the key may already be copied), then one per
  // return to the window.
  _wizClipOnFocus = () => { _wizCheckClipboardOnce(); };
  window.addEventListener('focus', _wizClipOnFocus);
  _wizCheckClipboardOnce();
}

function stopWizClipboardWatch() {
  if (_wizClipTimer) { clearInterval(_wizClipTimer); _wizClipTimer = null; }
  if (_wizClipOnFocus) {
    window.removeEventListener('focus', _wizClipOnFocus);
    _wizClipOnFocus = null;
  }
}

function wizNotePickedUpKey(provider) {
  const v = document.getElementById(
    provider === 'groq' ? 'wizGroqValidation3'
    : provider === 'cerebras' ? 'wizCerebrasValidation3' : 'wizApiValidation3');
  if (!v) return;
  v.textContent = 'Found the key you just copied. Checking it...';
  v.className = 'wizard-validation loading';
}

function wizShowStep(step) {
  // Clean up Step 2 hotkey monitor when leaving step 2
  if (_wizardStep === 2 && step !== 2) {
    stopFnKeyPolling();
    pywebview.api.wizard_cleanup_step2().catch(() => {});
  }

  // Clean up wizard hotkey test when leaving step 3
  if (_wizardStep === 3 && step !== 3 && _wizardHotkeyTestActive) {
    pywebview.api.wizard_stop_hotkey_test().catch(() => {});
    _wizardHotkeyTestActive = false;
  }

  // Only watch the clipboard while the key step is actually on screen.
  if (step === 3) { startWizClipboardWatch(); } else { stopWizClipboardWatch(); }

  _wizardStep = step;
  updateWizardProgress(step);
  // Tell CSS which step is active so step-specific layout rules
  // (e.g. the side-by-side permission grid for step 1) only apply
  // when that step is genuinely visible — prevents the v3.14.3 bug
  // where step-1 content leaked onto Windows.
  document.body.setAttribute('data-wiz-step', String(step));

  // Show/hide wizard step content (always loop to 4 — the actual number of content divs).
  // The shown step gets no inline display at all, so its stylesheet layout
  // applies: an inline "block" overrode the Mac permissions grid, stacking
  // the two cards into a page about two screens tall.
  for (let i = 1; i <= 4; i++) {
    const con = document.getElementById('wizContent' + i);
    if (!con) continue;
    if (i === step) con.style.removeProperty('display');
    else con.style.display = 'none';
  }

  // Toggle wide container for step 4 (mock app split layout)
  const container = document.querySelector('.wizard-container');
  if (container) container.classList.toggle('wide', step === 4);

  const backBtn = document.getElementById('wizBtnBack');
  const nextBtn = document.getElementById('wizBtnNext');
  backBtn.style.display = step > WIZARD_FIRST_STEP ? 'inline-block' : 'none';
  if (step === 4) {
    nextBtn.textContent = 'Finish Setup';
    nextBtn.classList.add('finish');
  } else {
    nextBtn.textContent = 'Next';
    nextBtn.classList.remove('finish');
  }
  wizUpdateNextButton();

  // Step-specific initialization
  // Permission polling — start when entering step 1, stop when leaving.
  if (step === 1) {
    wizStartPermissionPoll();
  } else {
    wizStopPermissionPoll();
  }
  if (step === 2) { wizResetHotkeyPill(); wizRenderHotkey(); wizLoadHotkeyInfo(); initFnKeyFeedback(); }
  if (step === 3) { wizInitApiKeyStep(); wizInitProviderTabs(); }
  if (step === 4) { wizRenderHotkey(); wizInitTryItStep(); initFnKeyFeedback(); wizStartExplainerWaffle(); }
  else wizStopExplainerWaffle();
}

// The Hotkey step's pill says "Listening" whenever the step is shown. After
// an auto-advance, Back used to bring up a stale "Hotkey detected,
// advancing" that never advanced.
function wizResetHotkeyPill() {
  window._fnKeyDetected = false;
  const status = document.getElementById('wizHotkeyStatus');
  if (!status) return;
  status.classList.remove('detected', 'error');
  status.style.color = '';
  const label = status.querySelector('.wiz-listening-label');
  if (label) label.textContent = 'Listening for hotkey press…';
}

function wizUpdateNextButton() {
  const btn = document.getElementById('wizBtnNext');
  switch (_wizardStep) {
    case 1:
      // v3.14.27 — require BOTH Accessibility and Input Monitoring before
      // letting the user advance. Previously this was `disabled = false`
      // unconditionally, so users could click Next without granting one
      // (most commonly Input Monitoring) — which broke the hotkey
      // detection later in step 4 with no obvious cause. Windows wizard
      // doesn't use step 1, so this branch only runs on macOS.
      btn.disabled = !(_wizardPermsAccessibility && _wizardPermsInputMon);
      btn.title = btn.disabled
        ? 'Grant both Accessibility and Input Monitoring above to continue.'
        : '';
      break;
    case 2: btn.disabled = false; break;  // Hotkeys - always allow
    case 3: btn.disabled = !(_wizardCerebrasKeyValidated || _wizardGroqKeyValidated || _wizardApiKeyValidated); break;
    case 4:  // Try It - finish after one dictation, or skip
      btn.disabled = !_wizardMicTested;
      btn.title = btn.disabled ? 'Dictate once to finish, or skip for now.' : '';
      break;
  }
  // "Skip for now" is there on the last step until a dictation works, so a
  // microphone problem never strands anyone on it.
  const skip = document.getElementById('wizBtnSkip');
  if (skip) skip.hidden = !(_wizardStep === 4 && !_wizardMicTested);
}

async function wizNext() {
  // v3.14.27 — belt-and-suspenders for step 1 on macOS: refuse to
  // advance unless both Accessibility and Input Monitoring are granted,
  // even if the button's disabled state was somehow bypassed.
  if (_wizardStep === 1 && isMacPlatform) {
    if (!_wizardPermsAccessibility || !_wizardPermsInputMon) {
      const missing = [];
      if (!_wizardPermsAccessibility) missing.push('Accessibility');
      if (!_wizardPermsInputMon) missing.push('Input Monitoring');
      showToast(`Grant ${missing.join(' + ')} above to continue`, 'error');
      return;
    }
  }
  if (_wizardStep < 4) {
    wizShowStep(_wizardStep + 1);
  } else {
    await wizCompleteSetup();
  }
}

function wizBack() {
  if (_wizardStep > WIZARD_FIRST_STEP) wizShowStep(_wizardStep - 1);
}

// ── Step 2: Hotkey Configuration ─────────────────────────────

function showWizardHotkeyConfig() {
  // This platform's choices only (logic.js hotkeyPresets). Windows used to
  // be offered "Ctrl + Alt + Space", which it never accepted.
  const list = document.getElementById('wizHotkeyPresetList');
  if (list) {
    list.textContent = '';
    WL.hotkeyPresets(isMacPlatform).forEach((p) => {
      const b = document.createElement('button');
      b.className = 'hotkey-preset-btn';
      const name = document.createElement('span');
      name.className = 'hotkey-preview';
      name.textContent = p.label;
      const hint = document.createElement('span');
      hint.style.cssText = 'opacity:0.6;font-size:13px';
      hint.textContent = p.hint;
      b.append(name, ' ', hint);
      b.addEventListener('click', () => (p.custom ? openHotkeyCapture() : selectHotkeyPreset(p.keys)));
      list.appendChild(b);
    });
  }
  document.getElementById('wizHotkeyConfigPanel').style.display = 'block';
}

function hideWizardHotkeyConfig() {
  document.getElementById('wizHotkeyConfigPanel').style.display = 'none';
}

function _wizardVisible() {
  const o = document.getElementById('wizardOverlay');
  return !!o && o.style.display !== 'none';
}

// The Hotkey step's pill, set through its label so the dot and spacing
// stay (setting the pill's own text wiped them and the dot sat on the
// first letter).
function _wizSetHotkeyPill(text, kind) {
  const status = document.getElementById('wizHotkeyStatus');
  if (!status) return;
  status.classList.remove('detected', 'error');
  if (kind) status.classList.add(kind);
  status.style.color = '';
  const label = status.querySelector('.wiz-listening-label');
  if (label) label.textContent = text;
}

function wizHotkeyChanged(result) {
  window._fnKeyDetected = false;
  _wizSetHotkeyPill(`Hotkey changed to ${result.display || WL.hotkeyName(result.keys, isMacPlatform)}. Hold it to test.`);
  hideWizardHotkeyConfig();
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
    // Nothing changed, so the keycaps stay as they are and the pill says why.
    _wizSetHotkeyPill((result && result.error) || "Couldn't change the hotkey. Try again.", 'error');
    return;
  }
  await _onHotkeySaved(result);
  wizHotkeyChanged(result);
}

// ── Step 3: API Key ──────────────────────────────────────────

let _wizGroqTimer = null;
let _wizApiTimer = null;
let _wizCerebrasTimer = null;
let _apiKeyListenersAttached = false;

function wizInitApiKeyStep() {
  // Only attach listeners once
  if (_apiKeyListenersAttached) return;
  _apiKeyListenersAttached = true;

  // ── Cerebras key input (primary) ──
  const cerebrasInp = document.getElementById('wizCerebrasKeyInput3');
  if (cerebrasInp) {
    cerebrasInp.addEventListener('input', () => {
      _wizardCerebrasKeyValidated = false;
      wizUpdateNextButton();
      const val = cerebrasInp.value.trim();
      const v = document.getElementById('wizCerebrasValidation3');
      if (!val) { v.textContent = ''; v.className = 'wizard-validation'; return; }
      if (!val.startsWith('csk-')) { v.textContent = 'Key should start with csk-'; v.className = 'wizard-validation error'; return; }
      if (val.length < 20) { v.textContent = 'Key seems too short...'; v.className = 'wizard-validation error'; return; }
      clearTimeout(_wizCerebrasTimer);
      v.textContent = 'Validating...';
      v.className = 'wizard-validation loading';
      _wizCerebrasTimer = setTimeout(() => wizValidateCerebrasKey(val), 800);
    });
    cerebrasInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        clearTimeout(_wizCerebrasTimer);
        const val = cerebrasInp.value.trim();
        if (val.startsWith('csk-') && val.length >= 20) wizValidateCerebrasKey(val);
      }
    });
  }

  // ── Groq key input ──
  const groqInp = document.getElementById('wizGroqKeyInput3');
  if (groqInp) {
    groqInp.addEventListener('input', () => {
      _wizardGroqKeyValidated = false;
      wizUpdateNextButton();
      const val = groqInp.value.trim();
      const v = document.getElementById('wizGroqValidation3');
      if (!val) { v.textContent = ''; v.className = 'wizard-validation'; return; }
      if (!val.startsWith('gsk_')) { v.textContent = 'Key should start with gsk_'; v.className = 'wizard-validation error'; return; }
      if (val.length < 20) { v.textContent = 'Key seems too short...'; v.className = 'wizard-validation error'; return; }
      clearTimeout(_wizGroqTimer);
      v.textContent = 'Validating...';
      v.className = 'wizard-validation loading';
      _wizGroqTimer = setTimeout(() => wizValidateGroqKey(val), 800);
    });
    groqInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        clearTimeout(_wizGroqTimer);
        const val = groqInp.value.trim();
        if (val.startsWith('gsk_') && val.length >= 20) wizValidateGroqKey(val);
      }
    });
  }

  // ── OpenAI key input ──
  const inp = document.getElementById('wizApiKeyInput3');
  if (inp) {
    inp.addEventListener('input', () => {
      _wizardApiKeyValidated = false;
      wizUpdateNextButton();
      const val = inp.value.trim();
      const v = document.getElementById('wizApiValidation3');
      if (!val) { v.textContent = ''; v.className = 'wizard-validation'; return; }
      if (!val.startsWith('sk-')) { v.textContent = 'Key should start with sk-'; v.className = 'wizard-validation error'; return; }
      if (val.length < 20) { v.textContent = 'Key seems too short...'; v.className = 'wizard-validation error'; return; }
      clearTimeout(_wizApiTimer);
      v.textContent = 'Validating...';
      v.className = 'wizard-validation loading';
      _wizApiTimer = setTimeout(() => wizValidateApiKey(val), 800);
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        clearTimeout(_wizApiTimer);
        const val = inp.value.trim();
        if (val.startsWith('sk-') && val.length >= 20) wizValidateApiKey(val);
      }
    });
  }

}

async function wizValidateGroqKey(key) {
  const v = document.getElementById('wizGroqValidation3');
  v.textContent = 'Validating with Groq...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_groq_key(key);
    if (r.ok) {
      v.textContent = 'Groq key is valid.';
      v.className = 'wizard-validation wiz-prov-status success';
      _wizardGroqKeyValidated = true;
      wizSetTick('wizGroqTick', true);
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation wiz-prov-status error';
      _wizardGroqKeyValidated = false;
      wizSetTick('wizGroqTick', false);
    }
  } catch(e) {
    v.textContent = "Couldn't check that key. Check you're online and try again.";
    v.className = 'wizard-validation error';
    _wizardGroqKeyValidated = false;
  }
  wizUpdateNextButton();
}

async function wizValidateCerebrasKey(key) {
  const v = document.getElementById('wizCerebrasValidation3');
  v.textContent = 'Validating with Cerebras...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_cerebras_key(key);
    if (r.ok) {
      v.textContent = r.message || 'Cerebras key is valid.';
      v.className = 'wizard-validation wiz-prov-status success';
      _wizardCerebrasKeyValidated = true;
      wizSetTick('wizCerebrasTick', true);
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation wiz-prov-status error';
      _wizardCerebrasKeyValidated = false;
      wizSetTick('wizCerebrasTick', false);
    }
  } catch(e) {
    v.textContent = "Couldn't check that key. Check you're online and try again.";
    v.className = 'wizard-validation error';
    _wizardCerebrasKeyValidated = false;
  }
  wizUpdateNextButton();
}

async function wizValidateApiKey(key) {
  const v = document.getElementById('wizApiValidation3');
  v.textContent = 'Validating with OpenAI...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_api_key(key);
    if (r.ok) {
      v.textContent = 'OpenAI key is valid.';
      v.className = 'wizard-validation wiz-prov-status success';
      _wizardApiKeyValidated = true;
      wizSetTick('wizOpenAITick', true);
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation wiz-prov-status error';
      _wizardApiKeyValidated = false;
      wizSetTick('wizOpenAITick', false);
    }
  } catch(e) {
    v.textContent = "Couldn't check that key. Check you're online and try again.";
    v.className = 'wizard-validation error';
    _wizardApiKeyValidated = false;
  }
  wizUpdateNextButton();
}

function wizToggleVisibility(inputId) {
  const inp = document.getElementById(inputId);
  if (inp) inp.type = inp.type === 'password' ? 'text' : 'password';
}

// Provider switching for API keys
function switchProvider(provider) {
    // Update button states
    document.querySelectorAll('.pill-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.provider === provider);
    });

    // Update field visibility
    const fields = ['groqField', 'openaiField'];
    const providerFieldMap = { groq: 'groqField', openai: 'openaiField' };

    fields.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', id === providerFieldMap[provider]);
    });

    // Save preference to localStorage
    localStorage.setItem('preferredProvider', provider);
}

// Toggle API key visibility
function toggleKeyVisibility(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    if (input.type === 'password') {
        input.type = 'text';
    } else {
        input.type = 'password';
    }
}

// Initialize provider selection
function initializeProviderSelection() {
    // Restore saved preference or default to Groq
    const savedProvider = localStorage.getItem('preferredProvider') || 'groq';
    switchProvider(savedProvider);
}

// ── Step 2: Permissions ──────────────────────────────────────

async function wizCheckPermissions() {
  try {
    const result = await pywebview.api.check_permissions();
    const explanations = await pywebview.api.get_permission_explanations();
    
    // Update microphone permission display
    updatePermissionRow('wizPermMic', {
      granted: result.mic_granted,
      error: result.mic_error,
      explanation: explanations.microphone,
      buttonText: 'Allow Microphone',
      buttonAction: 'wizRequestMicPermission()'
    });

    // Update accessibility permission display (macOS only)
    const accessRow = document.getElementById('wizPermAccessibility');
    const isMac = result.platform === 'Darwin';

    if (isMac && result.accessibility_granted !== undefined) {
      accessRow.style.display = 'flex';
      updatePermissionRow('wizPermAccessibility', {
        granted: result.accessibility_granted,
        error: result.accessibility_error,
        explanation: explanations.accessibility,
        buttonText: 'Open System Settings',
        buttonAction: 'wizRequestAccessibilityPermission()',
        isAccessibility: true
      });
    } else {
      accessRow.style.display = 'none';
    }

    // Check for input monitoring if on macOS
    if (isMac && result.input_monitoring_granted !== undefined) {
      const inputMonitoringRow = document.getElementById('wizPermInputMonitoring');
      if (inputMonitoringRow) {
        inputMonitoringRow.style.display = 'flex';
        updatePermissionRow('wizPermInputMonitoring', {
          granted: result.input_monitoring_granted,
          error: result.input_monitoring_error,
          explanation: explanations.input_monitoring,
          buttonText: 'Open System Settings',
          buttonAction: 'wizRequestInputMonitoringPermission()'
        });
      }
    }

    // Overall state - require microphone as critical, others as optional
    const micOk = result.mic_granted;
    const accessOk = isMac ? (result.accessibility_granted || false) : true;
    _wizardPermissionsGranted = micOk; // Only microphone is critical for basic functionality

    // Update status and recommendations
    updatePermissionStatus(result);

    const recheckBtn = document.getElementById('wizRecheckBtn');
    if (recheckBtn) recheckBtn.style.display = _wizardPermissionsGranted ? 'none' : 'block';

    wizUpdateNextButton();
  } catch(e) {
    console.warn('wizCheckPermissions error:', e);
    // If check fails, allow user through anyway with warning
    _wizardPermissionsGranted = true;
    updatePermissionStatus({all_granted: false, recommendations: ['Could not check permissions - proceeding anyway']});
    wizUpdateNextButton();
  }
}

function updatePermissionRow(rowId, config) {
  const row = document.getElementById(rowId);
  if (!row) return;

  const icon = row.querySelector('.wizard-perm-icon');
  const desc = row.querySelector('.wizard-perm-desc');
  const btn = row.querySelector('.wizard-perm-btn');

  if (config.granted) {
    icon.innerHTML = '<span class="wizard-perm-granted">&#10003;</span>';
    desc.innerHTML = `<strong>${config.explanation.title}</strong><br>${config.explanation.why}<br><em style="color: #28a745;">✓ Permission granted</em>`;
    btn.style.display = 'none';
    row.classList.add('granted');
  } else {
    icon.innerHTML = '<span class="wizard-perm-denied">&#10007;</span>';
    let descHTML = `<strong>${config.explanation.title}</strong><br>${config.explanation.why}`;
    
    if (config.error) {
      descHTML += `<br><em style="color: #dc3545;">⚠ ${config.error}</em>`;
    }
    
    if (config.isAccessibility) {
      descHTML += `<br><br><strong>Steps to grant:</strong><br>1. Click "Open System Settings" below<br>2. Click + button → Applications → Waffler.app<br>3. Toggle switch ON<br>4. Return here and click "Recheck"`;
    }
    
    desc.innerHTML = descHTML;
    btn.style.display = 'inline-block';
    btn.textContent = config.buttonText;
    btn.setAttribute('onclick', config.buttonAction);
    row.classList.remove('granted');
  }
}

function updatePermissionStatus(result) {
  const valid = document.getElementById('wizPermValidation');
  if (!valid) return;

  if (result.all_granted) {
    valid.innerHTML = '<strong style="color: #28a745;">✓ All permissions granted!</strong><br>Waffler has full functionality available.';
    valid.className = 'wizard-validation success';
  } else if (_wizardPermissionsGranted) {
    valid.innerHTML = '<strong style="color: #ffc107;">⚠ Core functionality ready</strong><br>Some optional features may be limited without additional permissions.';
    valid.className = 'wizard-validation warning';
  } else {
    valid.innerHTML = '<strong style="color: #dc3545;">⚠ Required permissions missing</strong><br>Grant microphone access to continue.';
    valid.className = 'wizard-validation error';
  }

  if (result.recommendations && result.recommendations.length > 0) {
    const recDiv = document.getElementById('wizPermRecommendations');
    if (recDiv) {
      recDiv.innerHTML = result.recommendations.map(rec => `<li>${rec}</li>`).join('');
      recDiv.style.display = 'block';
    }
  }
}

function wizStartPermPolling() {
  clearInterval(_wizardPermCheckInterval);
  _wizardPermCheckInterval = setInterval(() => {
    if (_wizardStep === 2 && !_wizardPermissionsGranted) {
      wizCheckPermissions();
    } else if (_wizardPermissionsGranted) {
      clearInterval(_wizardPermCheckInterval);
      _wizardPermCheckInterval = null;
    }
  }, 2000);
}

async function wizRequestMicPermission() {
  try {
    await pywebview.api.request_mic_permission();
    setTimeout(wizCheckPermissions, 1000);
  } catch(e) {
    console.warn('wizRequestMicPermission error:', e);
  }
}

async function wizRequestAccessibilityPermission() {
  try {
    await pywebview.api.open_permission_settings('accessibility');
  } catch(e) {
    console.warn('wizRequestAccessibilityPermission error:', e);
  }
}

async function wizRequestInputMonitoringPermission() {
  try {
    await pywebview.api.request_input_monitoring_permission();
  } catch(e) {
    console.warn('wizRequestInputMonitoringPermission error:', e);
  }
}

// ── Step 3: Hotkey Info ──────────────────────────────────────

async function wizLoadHotkeyInfo() {
  // Reset Fn key detection flag for this step
  window._fnKeyDetected = false;

  try {
    // Start hotkey monitor for Step 2 (provides visual feedback)
    await pywebview.api.wizard_init_step2();
  } catch(e) {
    console.warn('wizLoadHotkeyInfo error:', e);
  }
  await wizRefreshHotkey();
}

// ── Hotkey Visual Feedback (works for any configured hotkey) ──────────────────────────────────

let _currentWizardHotkey = ['fn']; // Track configured hotkey for wizard

function initFnKeyFeedback() {
  // Previously had `if (!document.getElementById('wizHotkeyBadge')) return;`
  // — this early-return broke hotkey detection on Windows after the v3.14.5
  // wizard redesign, because the new Windows keycap layout uses
  // `wizHotkeyBadgeWin` for the Win key and no ID at all on the Ctrl
  // keycap. So `wizHotkeyBadge` (without the Win suffix) didn't exist,
  // initFnKeyFeedback returned early, startFnKeyPolling never ran, and
  // get_fn_key_state was never called regardless of what the user
  // pressed. The wizard's "Listening for hotkey press…" pill sat there
  // forever even though the Python hook was firing PUSH_TO_TALK on every
  // press (confirmed via hotkey.log).
  //
  // The visual-pressed feedback now targets `#wizHotkeyDisplay
  // .wiz-keycap-large` (all keycaps inside the combo container), so we
  // don't need a specific element to anchor on — just always start
  // polling.

  // Get current configured hotkey
  pywebview.api.get_hotkey_config().then(config => {
    _currentWizardHotkey = config.keys || (isMacPlatform ? ['fn'] : ['win', 'ctrl']);
  }).catch(() => {
    _currentWizardHotkey = isMacPlatform ? ['fn'] : ['win', 'ctrl'];
  });

  // Monitor for ANY modifier combination (in-page keydown/keyup helps on
  // platforms where the OS-level hook isn't fast enough)
  document.addEventListener('keydown', checkHotkeyState);
  document.addEventListener('keyup', checkHotkeyState);

  startFnKeyPolling();
}

function checkHotkeyState(event) {
  try {
    const isPressed = isHotkeyPressed(_currentWizardHotkey, event);
    setFnKeyActive(isPressed);
  } catch (e) {
    console.warn('checkHotkeyState error:', e);
  }
}

function isHotkeyPressed(keys, event) {
  // Check if all keys in the hotkey combination are currently pressed
  for (const key of keys) {
    switch(key.toLowerCase()) {
      case 'fn':
        if (!event.getModifierState?.('Fn')) return false;
        break;
      case 'cmd':
      case 'command':
        if (!event.metaKey) return false;
        break;
      case 'shift':
        if (!event.shiftKey) return false;
        break;
      case 'option':
      case 'alt':
        if (!event.altKey) return false;
        break;
      case 'control':
      case 'ctrl':
        if (!event.ctrlKey) return false;
        break;
      default:
        // Regular key - check if it matches
        if (event.key.toLowerCase() !== key.toLowerCase()) return false;
    }
  }
  return true;
}

function startFnKeyPolling() {
  stopFnKeyPolling();
  _fnKeyCheckInterval = setInterval(async () => {
    if (_wizardStep !== 2 && _wizardStep !== 3) return;
    try {
      if (window.pywebview?.api) {
        const state = await window.pywebview.api.get_fn_key_state();
        if (state?.pressed !== undefined) {
          setFnKeyActive(state.pressed);
        }
      }
    } catch (e) {}
  }, 100);
}

function stopFnKeyPolling() {
  if (_fnKeyCheckInterval) {
    clearInterval(_fnKeyCheckInterval);
    _fnKeyCheckInterval = null;
  }
}

function setFnKeyActive(isActive) {
  if (_fnKeyPressed === isActive) return;
  _fnKeyPressed = isActive;

  // Light up ALL keycaps inside the Step-2 hotkey display, not just one,
  // so users see clear feedback whichever key they're focused on.
  document.querySelectorAll('#wizHotkeyDisplay .wiz-keycap-large').forEach((el) => {
    el.classList.toggle('pressed', isActive);
  });

  // On Step 2: Flip the listening pill to "Hotkey detected!" and
  // auto-advance.
  if (_wizardStep === 2 && isActive && !window._fnKeyDetected) {
    window._fnKeyDetected = true;

    // Flip the listening pill to a green "detected" state
    const status = document.getElementById('wizHotkeyStatus');
    if (status) {
      status.classList.add('detected');
      const label = status.querySelector('.wiz-listening-label');
      if (label) label.textContent = '✓ Hotkey detected — advancing…';
    }

    // Auto-advance after 1 second, unless the user has moved on already.
    setTimeout(() => {
      if (_wizardStep === 2 && window._fnKeyDetected) wizNext();
    }, 1000);
  }

  // On Step 3: Show waffle overlay with mic feedback when Fn is held
  if (_wizardStep === 3) {
    if (isActive) {
      // Show overlay with mic sensitivity
      if (window.pywebview?.api) {
        pywebview.api.demo_overlay_show().catch(e => {
          console.warn('demo_overlay_show error:', e);
        });
      }
    } else {
      // Hide overlay when Fn is released
      if (window.pywebview?.api) {
        pywebview.api.demo_overlay_hide().catch(e => {
          console.warn('demo_overlay_hide error:', e);
        });
      }
    }
  }
}

// ── Step 4: Try It Out (Mock App) ────────────────────────────

async function wizLoadMicDevices() {
  const sel = document.getElementById('wizMicSelect');
  if (!sel) return;
  try {
    const devices = await pywebview.api.get_audio_devices();
    const current = await pywebview.api.get_selected_device();
    sel.innerHTML = '';
    if (!devices || !devices.length) {
      sel.innerHTML = '<option value="">No microphones found</option>';
      return;
    }
    devices.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.index;
      opt.textContent = d.name + (d.is_default ? ' (default)' : '');
      if ((current && current.index === d.index) || (current && current.index === null && d.is_default)) {
        opt.selected = true;
        _wizardMicDeviceIndex = d.index;
      }
      sel.appendChild(opt);
    });
    if (_wizardMicDeviceIndex === null && devices.length > 0) {
      _wizardMicDeviceIndex = devices[0].index;
      sel.value = _wizardMicDeviceIndex;
    }
  } catch(e) {
    console.warn('wizLoadMicDevices error:', e);
  }
}

function wizOnMicChange(val) {
  _wizardMicDeviceIndex = parseInt(val, 10);
  // Re-check permissions with new mic
  if (_wizardStep === 2) wizCheckPermissions();
}

let _wizardHotkeyTestActive = false;

async function wizInitTryItStep() {
  if (_wizardMicDeviceIndex === null) _wizardMicDeviceIndex = 0;

  // Keycaps and the mock box's hotkey come from the saved keys.
  wizRefreshHotkey();

  // v3.14.25 — Mock send button now actually moves the dictated text
  // into the chat thread as a sent user-reply bubble. Previously it just
  // showed a toast and cleared the input. This way the user sees the
  // full loop: dictate → text in input → tap send → message appears as
  // their reply in the conversation. Reinforces what the app will do
  // for real in their actual messaging apps.
  const sendBtn = document.getElementById('wizMockSendBtn');
  if (sendBtn) {
    sendBtn.onclick = function() {
      const mockText = document.getElementById('wizMockText');
      const thread = document.querySelector('.wiz-mockapp-thread');
      const placeholder = document.getElementById('wizMockPlaceholder');
      if (!mockText || !thread) return;

      const text = mockText.textContent.trim();
      if (!text) return;

      // Build a "you" reply bubble. Right-aligned, gold-tinted to match
      // sent messages on iMessage / WhatsApp.
      const sentMsg = document.createElement('div');
      sentMsg.className = 'wiz-msg wiz-msg-you';
      const bubble = document.createElement('span');
      bubble.className = 'wiz-msg-bubble';
      bubble.textContent = text;
      const time = document.createElement('span');
      time.className = 'wiz-msg-time';
      const now = new Date();
      const hours = now.getHours().toString().padStart(2, '0');
      const minutes = now.getMinutes().toString().padStart(2, '0');
      time.textContent = `${hours}:${minutes}`;
      sentMsg.appendChild(bubble);
      sentMsg.appendChild(time);
      thread.appendChild(sentMsg);
      // Scroll the new bubble into view inside the thread
      sentMsg.scrollIntoView({ behavior: 'smooth', block: 'end' });

      showToast('✨ Sent! That\'s how dictation works in any app.', 'success');

      // Reset the mock input for another go
      setTimeout(() => {
        mockText.textContent = '';
        sendBtn.disabled = true;
        if (placeholder) {
          placeholder.style.display = 'inline';
          placeholder.innerHTML = 'Try another one — hold the hotkey and speak…';
        }
      }, 500);
    };
  }

  // Auto-start hotkey test
  try {
    await pywebview.api.set_audio_device(_wizardMicDeviceIndex);
    const r = await pywebview.api.wizard_start_hotkey_test(_wizardMicDeviceIndex);
    if (r.ok) {
      _wizardHotkeyTestActive = true;
    } else {
      const valid = document.getElementById('wizMicValidation');
      if (valid) { valid.textContent = r.error || "Couldn't start the test recording. Try again, or skip for now."; valid.className = 'wizard-validation error'; }
    }
  } catch(e) {
    console.warn('wizInitTryItStep error:', e);
  }
}

// Called from Python via evaluate_js when recording starts
window.wizOnRecordingStart = function() {
  const status = document.getElementById('wizRecordingStatus');
  const mockInput = document.getElementById('wizMockInput');
  const placeholder = document.getElementById('wizMockPlaceholder');
  const cursor = document.getElementById('wizMockCursor');
  const mockText = document.getElementById('wizMockText');

  if (status) {
    status.textContent = 'Listening...';
    status.className = 'wizard-recording-status active';
  }
  if (mockInput) mockInput.classList.add('active');
  if (placeholder) placeholder.style.display = 'none';
  if (mockText) mockText.textContent = '';
  if (cursor) cursor.style.display = 'inline-block';
};

// Called from Python via evaluate_js when recording stops
window.wizOnRecordingStop = function() {
  const status = document.getElementById('wizRecordingStatus');
  if (status) {
    status.textContent = 'Transcribing...';
    status.className = 'wizard-recording-status processing';
  }
};

// Called from Python via evaluate_js with transcription result
window.wizOnTranscriptionResult = function(text) {
  const status = document.getElementById('wizRecordingStatus');
  const mockInput = document.getElementById('wizMockInput');
  const placeholder = document.getElementById('wizMockPlaceholder');
  const cursor = document.getElementById('wizMockCursor');
  const mockText = document.getElementById('wizMockText');
  const sendBtn = document.getElementById('wizMockSendBtn');
  const valid = document.getElementById('wizMicValidation');

  if (status) {
    status.textContent = '';
    status.className = 'wizard-recording-status';
  }
  if (mockInput) mockInput.classList.remove('active');
  if (cursor) cursor.style.display = 'none';

  const isError = text.startsWith('(') && text.endsWith(')');
  if (!isError && text.trim().length > 0) {
    if (mockText) wizAnimateText(mockText, text);
    if (sendBtn) sendBtn.disabled = false;
    if (placeholder) placeholder.style.display = 'none';
    if (valid) {
      valid.textContent = 'Waffler is working! Try again or finish setup.';
      valid.className = 'wizard-validation success';
    }
    _wizardMicTested = true;
  } else {
    if (mockText) mockText.textContent = '';
    if (placeholder) { placeholder.style.display = 'inline'; placeholder.textContent = 'No speech detected. Try again!'; }
    if (sendBtn) sendBtn.disabled = true;
    if (valid) {
      valid.textContent = 'No speech detected. Give it another go!';
      valid.className = 'wizard-validation error';
    }
    _wizardMicTested = false;
  }
  wizUpdateNextButton();
};

// Called from Python when wizard recording captured silence / no audio
window.wizOnSilentRecording = function() {
  const status = document.getElementById('wizRecordingStatus');
  const mockInput = document.getElementById('wizMockInput');
  const cursor = document.getElementById('wizMockCursor');
  const placeholder = document.getElementById('wizMockPlaceholder');
  const valid = document.getElementById('wizMicValidation');

  if (status) {
    status.textContent = "We couldn't hear you";
    status.className = 'wizard-recording-status error';
  }
  if (mockInput) mockInput.classList.remove('active');
  if (cursor) cursor.style.display = 'none';
  if (placeholder) {
    placeholder.style.display = 'inline';
    placeholder.textContent = 'Check your mic and try again';
  }
  if (valid) {
    valid.textContent = "No speech detected — make sure your mic isn't muted.";
    valid.className = 'wizard-validation error';
  }
};

function wizAnimateText(el, text) {
  el.textContent = '';
  let i = 0;
  const interval = setInterval(() => {
    if (i < text.length) {
      el.textContent += text[i];
      i++;
    } else {
      clearInterval(interval);
    }
  }, 20);
}

// ── Complete Setup ──────────────────────────────────────────

// ════════════════════════════════════════════════════════════════════
// ── Branded wizard (v3.14.2+) helpers ──────────────────────────────
// New UI uses provider tabs, status pills, and animated walkthrough.
// These helpers bridge to the existing IPC calls.
// ════════════════════════════════════════════════════════════════════

// Update a permission status pill (Step 1 — Accessibility / Input Monitoring).
function wizSetPermPill(pillId, cardId, granted) {
  const pill = document.getElementById(pillId);
  const card = document.getElementById(cardId);
  if (!pill) return;
  const label = pill.querySelector('.wiz-pill-label');
  if (granted) {
    pill.classList.remove('wiz-pill-waiting');
    pill.classList.add('wiz-pill-granted');
    if (label) label.textContent = 'Granted';
    if (card) card.classList.add('granted');
  } else {
    pill.classList.add('wiz-pill-waiting');
    pill.classList.remove('wiz-pill-granted');
    if (label) label.textContent = 'Not granted yet';
    if (card) card.classList.remove('granted');
  }
}

// Background poll for macOS permissions. Runs every 1s while step 1 is open.
// v3.14.27 — track permission state across the whole wizard lifetime
// so wizUpdateNextButton() can gate step 1's Next button on BOTH
// permissions being granted. Without this, the user can advance past
// the permissions step with no Input Monitoring access, which then
// breaks the hotkey detection on step 4 — they hold the key and
// nothing happens, with no clear indicator of why.
let _wizardPermsAccessibility = false;
let _wizardPermsInputMon = false;
let _wizPermPollTimer = null;
async function wizStartPermissionPoll() {
  if (_wizPermPollTimer) return;
  const tick = async () => {
    try {
      const r = await pywebview.api.check_permissions();
      const accOk = !!r.accessibility_granted;
      const inpOk = !!r.input_monitoring_granted;
      wizSetPermPill('wizAccessStatus', 'wizPermAccessibility', accOk);
      wizSetPermPill('wizInputMonStatus', 'wizPermInputMon', inpOk);
      // v3.14.27 — record state globally + recompute Next via the
      // central wizUpdateNextButton() so the button correctly toggles
      // OFF if the user revokes a permission, not just ON when both
      // are granted. Previously the Next button could be left enabled
      // when one of the two permissions had been revoked partway
      // through, letting the user proceed without Input Monitoring —
      // which then broke step 4 because the hotkey never fired.
      _wizardPermsAccessibility = accOk;
      _wizardPermsInputMon = inpOk;
      if (_wizardStep === 1) wizUpdateNextButton();
    } catch (e) { /* poll errors are silent */ }
  };
  tick();  // immediate
  _wizPermPollTimer = setInterval(tick, 1000);
}
function wizStopPermissionPoll() {
  if (_wizPermPollTimer) { clearInterval(_wizPermPollTimer); _wizPermPollTimer = null; }
}

// Flip the green tick on a provider's input row (Step 3).
function wizSetTick(tickId, valid) {
  const t = document.getElementById(tickId);
  if (!t) return;
  if (valid) {
    t.classList.remove('wiz-prov-tick-empty');
    t.classList.add('wiz-prov-tick-valid');
    t.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>';
  } else {
    t.classList.add('wiz-prov-tick-empty');
    t.classList.remove('wiz-prov-tick-valid');
    t.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/></svg>';
  }
}

// Switch between provider tabs (Step 3).
let _provTabsBound = false;
function wizInitProviderTabs() {
  if (_provTabsBound) return;
  _provTabsBound = true;
  const tabs = document.querySelectorAll('.wiz-prov-tab');
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.getAttribute('data-provider');
      tabs.forEach((t) => t.classList.toggle('wiz-prov-tab-active', t === tab));
      const panels = {
        groq: 'wizProvPanelGroq',
        cerebras: 'wizProvPanelCerebras',
        openai: 'wizProvPanelOpenAI',
      };
      Object.entries(panels).forEach(([prov, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = (prov === target) ? 'flex' : 'none';
      });
    });
  });
}

// ── Wizard keycaps, drawn from the saved hotkey ──────────────────────────
// Every keycap, tile and hint on the Hotkey and Try-it steps is drawn from
// the keys actually saved, so choosing another hotkey redraws them all.
// Text only ever goes into the label spans. Setting textContent on a keycap
// itself (as the Hotkey and Try-it steps used to, with the display name)
// wiped its light label and icon and left dark text on a black key; and the
// icons were drawn in near-black, so the Win and fn keys looked blank.
const _KEYCAP_ICONS = {
  windows: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801"/></svg>',
  globe: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke-width="1"/><ellipse cx="8" cy="8" rx="3" ry="6.5" stroke-width="0.8"/><line x1="1.5" y1="5.5" x2="14.5" y2="5.5" stroke-width="0.7"/><line x1="1.5" y1="10.5" x2="14.5" y2="10.5" stroke-width="0.7"/><line x1="8" y1="1.5" x2="8" y2="14.5" stroke-width="0.5"/></svg>',
};

function wizRenderHotkey(keys) {
  if (Array.isArray(keys) && keys.length) _currentHotkeyKeys = keys.slice();
  const caps = WL.keycaps(_currentHotkeyKeys, isMacPlatform);
  const name = WL.hotkeyName(_currentHotkeyKeys, isMacPlatform);

  // Step 2: the big keycaps.
  const combo = document.getElementById('wizHotkeyDisplay');
  if (combo) {
    combo.innerHTML = caps.map((c) => {
      const icon = c.icon ? _KEYCAP_ICONS[c.icon] : '';
      // Long names ("Command", "Option") get a smaller size to fit the key.
      const size = c.label.length > 5 ? ' style="font-size:15px"' : '';
      const label = !icon
        ? `<span class="wiz-keycap-label-large"${size}>${escHtml(c.label)}</span>`
        : c.icon === 'globe'
          ? `<span class="wiz-keycap-label-large" style="font-size:14px;margin-top:2px">${escHtml(c.label)}</span>`
          : `<span class="wiz-keycap-label" style="font-size:11px;margin-top:2px">${escHtml(c.label)}</span>`;
      return `<div class="wiz-keycap-large">${icon}${label}</div>`;
    }).join('<span class="wiz-keycap-plus">+</span>');
  }

  // Step 2: the instruction tiles under the listening pill.
  const host = document.getElementById('wizHotkeyInstructionCards');
  if (host) {
    const plus = '<span class="wiz-mini-plus">+</span>';
    const comboHtml = caps.map((c) => `<span class="wiz-mini-kbd">${escHtml(c.label)}</span>`).join(plus);
    const hint = WL.pressOrderHint(_currentHotkeyKeys, isMacPlatform);
    host.innerHTML = `
    <div class="wiz-hotkey-card">
      <div class="wiz-hotkey-card-keys">${comboHtml}</div>
      <div class="wiz-hotkey-card-title">Hold to record</div>
      ${hint ? `<div class="wiz-hotkey-card-sub">${escHtml(hint)}</div>` : ''}
    </div>
    <div class="wiz-hotkey-card wiz-hotkey-card-nokey">
      <div class="wiz-hotkey-card-title">Release to stop</div>
    </div>
    <div class="wiz-hotkey-card">
      <div class="wiz-hotkey-card-keys"><span class="wiz-mini-kbd">Space</span>${plus}${comboHtml}</div>
      <div class="wiz-hotkey-card-title">Sticky mode</div>
      <div class="wiz-hotkey-card-sub">Press Space to lock recording on. Press the hotkey again to disable.</div>
    </div>
    <div class="wiz-hotkey-card">
      <div class="wiz-hotkey-card-keys"><span class="wiz-mini-kbd">Esc</span></div>
      <div class="wiz-hotkey-card-title">Cancel</div>
      <div class="wiz-hotkey-card-sub">Tap Esc to discard a recording without transcribing or pasting.</div>
    </div>
  `;
  }

  // Step 4: the "Hold this" chips and the mock reply box.
  const mini = document.getElementById('wizTryHotkeyBadge');
  if (mini) {
    mini.innerHTML = caps.map((c) => `<span class="wiz-keycap-mini">${escHtml(c.label)}</span>`)
      .join('<span class="wiz-keycap-plus-mini">+</span>');
  }
  const kbd = document.getElementById('wizMockKbd');
  if (kbd) kbd.textContent = name;
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

// Animate the Step-4 explainer waffle cells the same way the website
// homepage waffle animates — speech wave drives row-by-row darkening so
// users see the cells "listening" before they ever press the hotkey.
let _wizExplainerWaffleRAF = null;
function wizStartExplainerWaffle() {
  const svg = document.getElementById('wizExplainerWaffle');
  if (!svg) return;
  const cells = svg.querySelectorAll('.wiz-wcell-anim');
  if (!cells.length) return;

  const SYRUP_DARK = '#5C2E0E';
  const SYRUP_LIGHT = '#7A3F14';
  const LIGHT_GOLD = '#B89040';
  const cellBars = new Float32Array(16);
  const cellTargets = new Float32Array(16);
  const speechWave = [
    0, 0, 0.1, 0.3, 0.6, 0.8, 0.95, 0.85, 0.7, 0.5, 0.3, 0.1, 0, 0,
    0.2, 0.5, 0.75, 0.9, 1.0, 0.85, 0.7, 0.8, 0.9, 0.75, 0.5, 0.3, 0.1, 0,
    0, 0.15, 0.4, 0.65, 0.8, 0.7, 0.55, 0.4, 0.2, 0,
  ];

  // With reduced motion asked for, the waffle stays still at mid volume.
  if (_prefersReducedMotion()) {
    cells.forEach((cell) => {
      const row = parseInt(cell.getAttribute('data-row') || '0', 10);
      cell.setAttribute('fill', row >= 2 ? SYRUP_LIGHT : LIGHT_GOLD);
    });
    return;
  }

  function animate() {
    // Keep the loop alive but do no work while the window is hidden.
    if (_windowHidden()) {
      _wizExplainerWaffleRAF = requestAnimationFrame(animate);
      return;
    }
    const now = performance.now();
    const progress = (now % 4000) / 4000;
    const total = speechWave.length;
    const exactIdx = progress * (total - 1);
    const idx = Math.floor(exactIdx);
    const frac = exactIdx - idx;
    const level = speechWave[idx] * (1 - frac) + (speechWave[Math.min(idx + 1, total - 1)] || 0) * frac;
    const powLevel = Math.pow(level, 0.4);
    const t = now / 1000;

    cells.forEach((cell, i) => {
      const row = parseInt(cell.getAttribute('data-row') || '0', 10);
      const col = i % 4;
      const invRow = 3 - row;
      const threshold = invRow / 4;
      let cellLevel;
      if (powLevel <= threshold) cellLevel = 0;
      else if (powLevel >= threshold + 0.25) cellLevel = 1;
      else cellLevel = (powLevel - threshold) / 0.25;
      const phase = i * 0.7 + col * 2.3 + row * 1.8;
      const bounce1 = Math.sin(phase + t * 4.5) * 0.25;
      const bounce2 = Math.sin(phase * 1.7 + t * 6.2) * 0.15;
      const jitter = (Math.random() - 0.5) * 0.2;
      const wobble = 1.0 + bounce1 + bounce2 + jitter;
      cellTargets[i] = Math.max(0, Math.min(1, cellLevel * wobble));
      const diff = cellTargets[i] - cellBars[i];
      if (Math.abs(diff) > 0.005) cellBars[i] += diff * 0.35;
      else cellBars[i] = cellTargets[i];
      const lvl = cellBars[i];
      if (lvl < 0.05) cell.setAttribute('fill', LIGHT_GOLD);
      else cell.setAttribute('fill', lvl > 0.6 ? SYRUP_DARK : SYRUP_LIGHT);
    });

    _wizExplainerWaffleRAF = requestAnimationFrame(animate);
  }

  if (_wizExplainerWaffleRAF) cancelAnimationFrame(_wizExplainerWaffleRAF);
  _wizExplainerWaffleRAF = requestAnimationFrame(animate);
}

// The loop used to run for the rest of the session once the Try-it step had
// been shown, redrawing 16 hidden squares every frame behind the Journal.
function wizStopExplainerWaffle() {
  if (_wizExplainerWaffleRAF) cancelAnimationFrame(_wizExplainerWaffleRAF);
  _wizExplainerWaffleRAF = null;
}

// Finish without a test dictation (for example when the microphone isn't
// working yet). Waffler is set up either way; the hotkey works from the
// Journal as soon as the key is saved.
async function wizSkipTryIt() {
  await wizCompleteSetup();
}

async function wizCompleteSetup() {
  const btn = document.getElementById('wizBtnNext');
  const skip = document.getElementById('wizBtnSkip');
  btn.disabled = true;
  btn.textContent = 'Setting up...';
  if (skip) skip.disabled = true;
  try {
    // Clean up
    clearInterval(_wizardPermCheckInterval);
    if (_wizardHotkeyTestActive) {
      await pywebview.api.wizard_stop_hotkey_test();
      _wizardHotkeyTestActive = false;
    }
    const r = await pywebview.api.complete_setup();
    if (r.ok) {
      showToast('Waffler is ready!', 'success');
      hideWizard();
    } else {
      console.warn('complete_setup failed:', r.error);
      showToast("Couldn't finish setup. Try again.", 'error');
      btn.textContent = 'Finish Setup';
      wizUpdateNextButton();
    }
  } catch(e) {
    console.warn('complete_setup failed:', e);
    showToast("Couldn't finish setup. Try again.", 'error');
    btn.textContent = 'Finish Setup';
    wizUpdateNextButton();
  }
  if (skip) skip.disabled = false;
}

// ── Snippets ──────────────────────────────────────────────────────────────
let _snippets = [];

async function loadSnippets() {
  try {
    _snippets = (await pywebview.api.get_snippets()) || [];
    renderSnippets();
  } catch(e) {
    console.warn('loadSnippets error:', e);
  }
}

function renderSnippets() {
  const list = document.getElementById('snippetsList');
  if (!list) return;
  list.innerHTML = '';
  if (!_snippets.length) {
    list.innerHTML = '<div class="snippet-empty">No snippets yet. Click + Add to create one.</div>';
    return;
  }
  _snippets.forEach((s, i) => {
    const row = document.createElement('div');
    row.className = 'snippet-row';
    row.innerHTML = `
      <input class="snippet-trigger" type="text" value="${escHtml(s.trigger || '')}"
        placeholder="trigger phrase" onchange="updateSnippet(${i}, 'trigger', this.value)">
      <span class="snippet-arrow">→</span>
      <textarea class="snippet-expansion" rows="2"
        placeholder="expansion text"
        onchange="updateSnippet(${i}, 'expansion', this.value)">${escHtml(s.expansion || '')}</textarea>
      <button class="icon-btn" onclick="deleteSnippet(${i})" title="Delete">🗑</button>
    `;
    list.appendChild(row);
  });
}

function addSnippetRow() {
  _snippets.push({ trigger: '', expansion: '' });
  renderSnippets();
  // Focus the new trigger input
  const inputs = document.querySelectorAll('.snippet-trigger');
  if (inputs.length) inputs[inputs.length - 1].focus();
}

function updateSnippet(i, field, value) {
  if (_snippets[i]) {
    _snippets[i][field] = value;
    saveSnippets();
  }
}

function deleteSnippet(i) {
  _snippets.splice(i, 1);
  renderSnippets();
  saveSnippets();
}

async function saveSnippets() {
  try {
    const valid = _snippets.filter(s => s.trigger.trim());
    await pywebview.api.set_snippets(valid);
  } catch(e) {
    console.warn('saveSnippets error:', e);
  }
}

// Load snippets when settings page opens
const _origLoadSettings = loadSettings;
loadSettings = async function() {
  await _origLoadSettings();
  await loadSnippets();
  await loadUsageStats();
  await loadAppVersion();
  await loadUnsentSummary();
};

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

async function resetUsage() {
  if (!confirm('Reset all usage statistics? This cannot be undone.')) return;
  try {
    const r = await pywebview.api.reset_usage();
    if (r.ok) {
      await loadUsageStats();
      showToast('🗑️ Usage stats reset', 'success');
    } else {
      console.warn('reset_usage failed:', r.error);
      showToast("Couldn't reset the usage figures. Try again.", 'error');
    }
  } catch(e) {
    showToast("Couldn't reset the usage figures. Try again.", 'error');
  }
}


