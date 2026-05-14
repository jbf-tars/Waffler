/* Waffler — Frontend Logic */

// ── Theme (v3.14.3+) ──────────────────────────────────────────────────
// Apply the saved theme as early as possible so the page doesn't flash
// in the wrong colours. Default for new installs is "cream".
(function applyStoredTheme() {
  try {
    const t = localStorage.getItem('waffler_theme') || 'cream';
    document.body.setAttribute('data-theme', t);
  } catch (_) {
    document.body.setAttribute('data-theme', 'cream');
  }
})();

function setAppTheme(theme) {
  if (!['cream', 'dark', 'auto'].includes(theme)) return;
  document.body.setAttribute('data-theme', theme);
  try { localStorage.setItem('waffler_theme', theme); } catch (_) {}
  // Update theme-picker active state
  document.querySelectorAll('.theme-option').forEach((el) => {
    el.classList.toggle('active', el.getAttribute('data-theme') === theme);
  });
}

// Mark the current theme button as active when settings opens
function refreshThemePicker() {
  const cur = document.body.getAttribute('data-theme') || 'cream';
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
  // Plain text key names (no symbols)
  const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
  const keyNameMap = isMac ? {
    'alt': 'Option',
    'option': 'Option',
    'ctrl': 'Control',
    'control': 'Control',
    'cmd': 'Command',
    'command': 'Command',
    'win': 'Command',
    'shift': 'Shift',
    'fn': 'Fn',
    'space': 'Space'
  } : {
    'alt': 'Alt',
    'option': 'Alt',
    'ctrl': 'Ctrl',
    'control': 'Ctrl',
    'cmd': 'Win',
    'command': 'Win',
    'win': 'Win',
    'shift': 'Shift',
    'fn': 'Fn',
    'space': 'Space'
  };

  return keys.map(k => {
    const lowerKey = k.toLowerCase();
    return keyNameMap[lowerKey] || k.charAt(0).toUpperCase() + k.slice(1);
  }).join(" + ");
}

// ── DOM refs ────────────────────────────────────────────────────────────
const $feedScroll    = document.getElementById('feedScroll');
const $feed          = document.getElementById('transcriptFeed');
const $empty         = document.getElementById('emptyState');
const $feedCount     = document.getElementById('feedCount');
const $statusInd     = document.getElementById('statusIndicator');
const $statusText    = document.getElementById('statusText');
const $overlay       = document.getElementById('recordingOverlay');
const $toast         = document.getElementById('toast');
const $statWords     = document.getElementById('statWords');
const $statCount     = document.getElementById('statCount');
const $statTotal     = document.getElementById('statTotal');
const $dateLabel     = document.getElementById('dateLabel');

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
        // Always go to our own download page rather than GitHub's release UI.
        pywebview.api.open_url('https://wafflerai.com/download/');
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
function setUpdateModal({ icon, title, subtitle, showProgress, primaryLabel, primaryHandler, cancelLabel, browserUrl }) {
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
  try {
    const r = await pywebview.api.check_for_updates();
    if (r.update_available) {
      openUpdateModalFromCheck(r);
    } else if (r.error) {
      setUpdateModal({
        icon: '⚠️',
        title: 'Couldn\'t check for updates',
        subtitle: `${r.error}${r.current_version ? ` (you're on v${r.current_version})` : ''}`,
      });
    } else {
      const latest = r.latest_version ? ` (latest: v${r.latest_version})` : '';
      setUpdateModal({
        icon: '✓',
        title: 'You\'re up to date',
        subtitle: `Running Waffler v${r.current_version || '?'}${latest}.`,
      });
    }
  } catch(e) {
    setUpdateModal({ icon: '⚠️', title: 'Check failed', subtitle: String(e) });
  }
}

function openUpdateModalFromCheck(r) {
  showUpdateModal();
  _lastUpdateInfo = r;

  // v3.14.3+: Mac in-app downloads now work properly via /usr/bin/curl
  // (no more PyInstaller-bundled-requests SSL hang). Both platforms get
  // the same "Download & Install" experience: stream the installer
  // in-app, show a progress bar, run the platform installer, relaunch.
  setUpdateModal({
    icon: '⬆️',
    title: `Waffler v${r.latest_version} is available`,
    subtitle: `You're on v${r.current_version}. Download and install now?`,
    primaryLabel: 'Download & Install',
    primaryHandler: () => startDownloadFlow(r.download_url),
    browserUrl: 'https://wafflerai.com/download/',
    cancelLabel: 'Later',
  });
}

async function startDownloadFlow(url) {
  _downloadedPath = null;
  // On any failure / "Download in browser" click, send users to the
  // website's download page rather than the GitHub release page —
  // it's the user-friendly entry point we control.
  const fallbackUrl = 'https://wafflerai.com/download/';
  setUpdateModal({
    icon: '⬇️',
    title: 'Downloading update…',
    subtitle: 'Please keep Waffler open.',
    showProgress: true,
    browserUrl: fallbackUrl,
    cancelLabel: 'Cancel',
  });
  try {
    const r = await pywebview.api.start_update_download(url);
    if (!r.ok) throw new Error(r.error || 'Failed to start download');
    startProgressPolling();
  } catch(e) {
    setUpdateModal({
      icon: '⚠️',
      title: 'Download failed',
      subtitle: String(e),
      browserUrl: fallbackUrl,
    });
  }
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
      stopProgressPolling();
      setUpdateModal({
        icon: '⚠️',
        title: 'Download failed',
        subtitle: p.error,
        browserUrl: 'https://wafflerai.com/download/',
      });
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
  try {
    await pywebview.api.install_update_and_restart(_downloadedPath);
  } catch(e) {
    setUpdateModal({ icon: '⚠️', title: 'Install failed', subtitle: String(e) });
  }
}

function updateHotkeyHint() {
  const isWin = navigator.userAgent.includes('Windows');
  if (isWin) {
    loadHotkeyConfig();
  } else {
    const badge = document.getElementById('hotkeyBadge');
    const sidebarBadge = document.getElementById('hotkeyHint');
    const label = document.getElementById('hotkeyLabel');
    const emptyHint = document.getElementById('emptyHint');
    if (badge) badge.textContent = 'Fn';
    if (sidebarBadge) sidebarBadge.textContent = 'Fn';
    if (label) label.textContent = 'Tap to start/stop';
    if (emptyHint) emptyHint.innerHTML = 'Press <strong>Fn</strong> to start recording';
  }
}

// ── Permissions (Step 1) ─────────────────────────────────────────────

async function openAccessibilitySettings() {
  console.log("openAccessibilitySettings called");

  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  try {
    const result = await pywebview.api.open_accessibility_settings();
    console.log("openAccessibilitySettings result:", result);

    if (result.ok) {
      showToast("Opening System Settings...", "success");
      // Check permission status after a moment (disabled - user manages permissions)
      // setTimeout(checkPermissions, 2000);
    } else {
      showToast(result.error || "Failed to open settings", "error");
    }
  } catch (e) {
    console.error("openAccessibilitySettings error:", e);
    showToast("Error opening settings", "error");
  }
}

async function openInputMonitoringSettings() {
  console.log("openInputMonitoringSettings called");

  if (!window.pywebview || !window.pywebview.api) {
    showToast("App not ready yet", "error");
    return;
  }

  try {
    const result = await pywebview.api.open_input_monitoring_settings();
    console.log("openInputMonitoringSettings result:", result);

    if (result.ok) {
      showToast("Opening System Settings...", "success");
      // Check permission status after a moment (disabled - user manages permissions)
      // setTimeout(checkPermissions, 2000);
    } else {
      showToast(result.error || "Failed to open settings", "error");
    }
  } catch (e) {
    console.error("openInputMonitoringSettings error:", e);
    showToast("Error opening settings", "error");
  }
}

async function factoryReset() {
  console.log("factoryReset called");

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
    console.log("factoryReset result:", result);

    if (result.ok) {
      showToast("Resetting all data...", "success");
      // App will quit automatically
    } else {
      showToast(result.error || "Failed to reset", "error");
    }
  } catch (e) {
    console.error("factoryReset error:", e);
    showToast("Error resetting data", "error");
  }
}

// Permission checking disabled - just let users click through
async function checkPermissions() {
  // No automatic checking - users will manually grant permissions
  return;
}

// Permission monitoring disabled
function startPermissionMonitoring() {
  // Disabled - no automatic checking
}

function stopPermissionMonitoring() {
  // Disabled
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
      if (hintDesc) hintDesc.textContent = `Hold to record \u2022 +Space = sticky mode`;
      const badge = document.getElementById("hotkeyBadge");
      if (badge) badge.textContent = display;
      const emptyHint = document.getElementById("emptyHint");
      if (emptyHint) emptyHint.innerHTML = `Hold <strong>${display}</strong> to record`;
    }
  } catch (e) {
    console.error("loadHotkeyConfig error:", e);
  }
}

async function changeSettingsHotkey(keys) {
  try {
    // Save the new hotkey
    await window.pywebview.api.save_hotkey_config(keys);

    // Reload to update all displays
    await loadHotkeyConfig();

    // Show success feedback
    const settingsBadge = document.getElementById("settingsHotkeyBadge");
    if (settingsBadge) {
      const originalColor = settingsBadge.style.color;
      settingsBadge.style.color = '#4CAF50';
      setTimeout(() => {
        settingsBadge.style.color = originalColor;
      }, 1000);
    }
  } catch (e) {
    console.error("Failed to change hotkey:", e);
    alert("Failed to change hotkey. Please try again.");
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
  if (!keys.length) return;
  if (!keys.some(k => MODIFIER_IDS.has(k))) {
    const modList = isMacPlatform ? "Command, Option, Control, Shift, or Fn" : "Ctrl, Alt, Shift, or Win";
    document.getElementById("hotkeyError").textContent = `At least one modifier key required (${modList})`;
    document.getElementById("hotkeyError").style.display = "block";
    return;
  }
  if (keys.length > 3) {
    document.getElementById("hotkeyError").textContent = "Maximum 3 keys allowed";
    document.getElementById("hotkeyError").style.display = "block";
    return;
  }
  try {
    const result = await window.pywebview.api.save_hotkey_config(JSON.stringify(keys));
    if (result.ok) {
      _currentHotkeyKeys = keys;
      closeHotkeyCapture();
      loadHotkeyConfig();
    } else {
      document.getElementById("hotkeyError").textContent = result.error;
      document.getElementById("hotkeyError").style.display = "block";
    }
  } catch (e) {
    document.getElementById("hotkeyError").textContent = "Failed to save";
    document.getElementById("hotkeyError").style.display = "block";
  }
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
    showToast('Transcription complete ✨', 'success');
  }
};

// ── Called by Python for status updates ──────────────────────────────
window.waffler_status = function(status) {
  $statusInd.className = 'status-indicator ' + status;
  const labels = {
    idle:       'Ready',
    listening:  'Listening…',
    processing: 'Processing…',
    done:       'Done'
  };
  $statusText.textContent = labels[status] || status;

  if (status === 'listening') {
    $overlay.classList.add('visible');
  } else {
    $overlay.classList.remove('visible');
  }

  if (status === 'done') {
    setTimeout(() => {
      $statusInd.className = 'status-indicator idle';
      $statusText.textContent = 'Ready';
    }, 3000);
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

  if (!filtered.length) {
    $empty.style.display = 'flex';
    $feed.innerHTML = '';
    $feedCount.textContent = history.length
      ? `0 of ${history.length} (filtered)`
      : '0 entries';
    return;
  }

  $empty.style.display = 'none';
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

function makeCard(item, isNew) {
  const div = document.createElement('div');
  div.className = 'transcript-card' + (isNew ? ' new' : '');

  const displayText = item.styled || item.text || '';
  const rawText     = item.text  || '';
  const hasStyled   = item.styled && item.styled !== item.text;
  const words       = (displayText.split(/\s+/).filter(Boolean)).length;
  const timeStr     = formatTime(item.timestamp);

  div.innerHTML = `
    <div class="card-meta">
      <div class="card-time">${timeStr}</div>
      <div class="card-words">${words} words</div>
    </div>
    <div class="card-text styled" id="text-${item.timestamp}">${escHtml(displayText)}</div>
    <div class="card-actions">
      <button class="btn-copy" data-text="${escHtml(displayText)}">📋 Copy</button>
      ${hasStyled ? `<span class="text-toggle" data-timestamp="${item.timestamp}" data-raw="${escHtml(rawText)}" data-styled="${escHtml(displayText)}">Show original</span>` : ''}
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
    toggleEl.textContent = 'Show original';
  }
}

function encodeText(t) {
  return encodeURIComponent(t);
}

function decodeText(t) {
  return decodeURIComponent(t);
}

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
function showToast(msg, type) {
  clearTimeout(toastTimer);
  $toast.textContent = msg;
  $toast.className = `toast visible ${type || ''}`;
  toastTimer = setTimeout(() => {
    $toast.classList.remove('visible');
  }, 2500);
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

// ── Mode Selector ─────────────────────────────────────────────────────

const MODE_DESCS = {
  normal: 'Keeps everything, cleans grammar',
  email: 'Email body — paragraphed for readability, keeps your greetings/sign-offs as spoken',
};

async function loadMode() {
  const sel = document.getElementById('modeSelect');
  const desc = document.getElementById('modeDesc');
  if (!sel) return;
  try {
    const current = await pywebview.api.get_current_mode();
    sel.value = current;
    if (desc) desc.textContent = MODE_DESCS[current] || '';
  } catch(e) {
    console.warn('loadMode error:', e);
  }
}

async function onModeChange(modeId) {
  const desc = document.getElementById('modeDesc');
  try {
    const result = await pywebview.api.set_mode(modeId);
    if (result && result.ok) {
      if (desc) desc.textContent = MODE_DESCS[modeId] || '';
      showToast(`✨ Mode: ${modeId.replace('_', ' ')}`, 'success');
    } else {
      showToast('Failed to switch mode', 'error');
    }
  } catch(e) {
    console.warn('onModeChange error:', e);
  }
}

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
          <div class="vocab-empty-sub">Add custom words to improve transcription accuracy</div>
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

// Load devices + mode once pywebview is ready.
// Vocab list is loaded lazily by loadVocabPage() when the user
// navigates to the Vocabulary tab — no longer pre-loaded on startup
// (which was the cause of the "Add word" box being pre-filled with
// every existing word jammed together).
window.addEventListener('pywebviewready', () => {
  loadAudioDevices();
  loadMode();
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

// ── Settings Load ────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await pywebview.api.get_settings();

    // Cerebras key (primary tier — fastest)
    const cerebrasInput = document.getElementById('cerebrasKeyInput');
    const cerebrasDesc = document.getElementById('cerebrasKeyDesc');
    if (cerebrasInput) {
      cerebrasInput.placeholder = s.cerebras_key_set ? s.cerebras_key_masked : 'csk-…';
    }
    if (cerebrasDesc) {
      cerebrasDesc.textContent = s.cerebras_key_set
        ? ('Active: ' + s.cerebras_key_masked)
        : 'Fastest. Free tier ~1M tokens/day — cloud.cerebras.ai/platform/api-keys';
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
        : 'Very fast, free — console.groq.com';
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

    // Backend info
    const backendInfo = document.getElementById('backendInfo');
    if (backendInfo) {
      const stt = s.transcription_backend === 'groq' ? 'Groq Whisper' :
                  s.transcription_backend === 'api' ? 'OpenAI Whisper' :
                  s.transcription_backend || 'unknown';
      const llm = s.styling_backend === 'groq' ? 'Groq LLaMA' :
                  s.styling_backend === 'openai' ? 'GPT-4o-mini' :
                  s.styling_backend || 'unknown';
      backendInfo.textContent = `STT: ${stt} · LLM: ${llm}`;
    }

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
async function saveGroqKey() {
  const inp = document.getElementById('groqKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { showToast('Enter a Groq API key first', 'error'); return; }
  if (!val.startsWith('gsk_')) { showToast('Invalid key — should start with gsk_', 'error'); return; }
  try {
    const r = await pywebview.api.save_settings({ groq_key: val });
    if (r.ok) {
      inp.value = '';
      showToast('Groq key saved — restart for speed boost', 'success');
      await loadSettings();
    } else {
      showToast('Error: ' + r.error, 'error');
    }
  } catch(e) {
    showToast('Failed to save key', 'error');
  }
}

async function saveCerebrasKey() {
  const inp = document.getElementById('cerebrasKeyInput');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { showToast('Enter a Cerebras API key first', 'error'); return; }
  try {
    // validate_cerebras_key persists on success.
    const r = await pywebview.api.validate_cerebras_key(val);
    if (r.ok) {
      inp.value = '';
      showToast(r.message || 'Cerebras key saved — restart for speed boost', 'success');
      await loadSettings();
    } else {
      showToast('Error: ' + (r.error || 'invalid'), 'error');
    }
  } catch(e) {
    showToast('Failed to save Cerebras key', 'error');
  }
}

async function saveApiKey() {
  const inp = document.getElementById('apiKeyInput');
  const statusEl = document.getElementById('apiKeyStatus');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) { showToast('Enter an API key first', 'error'); return; }
  if (!val.startsWith('sk-')) { showToast('Invalid key — should start with sk-', 'error'); return; }
  try {
    const r = await pywebview.api.save_settings({ api_key: val });
    if (r.ok) {
      inp.value = '';
      if (statusEl) {
        statusEl.textContent = 'Key saved and active';
        statusEl.className = 'api-key-status ok';
      }
      showToast('API key saved', 'success');
    } else {
      showToast('Error: ' + r.error, 'error');
    }
  } catch(e) {
    showToast('Failed to save key', 'error');
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
      showToast('Error: ' + r.error, 'error');
    }
  } catch(e) {
    showToast('Failed to clear history', 'error');
  }
}

// ── Search ────────────────────────────────────────────────────────────────
let _searchQuery = '';

function onSearchInput(q) {
  _searchQuery = q.trim().toLowerCase();
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
    loadMode();
  }, 400);
}

// ── Step Navigation ──────────────────────────────────────────

async function triggerMacOSPermissions() {
  try {
    const result = await pywebview.api.trigger_permission_requests();
    console.log('[Wizard] Permission triggers:', result);
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

function wizShowStep(step) {
  // Clean up permission monitoring when leaving step 1 (disabled - no monitoring)
  if (_wizardStep === 1 && step !== 1) {
    // stopPermissionMonitoring();
  }

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

  _wizardStep = step;
  updateWizardProgress(step);
  // Tell CSS which step is active so step-specific layout rules
  // (e.g. the side-by-side permission grid for step 1) only apply
  // when that step is genuinely visible — prevents the v3.14.3 bug
  // where step-1 content leaked onto Windows.
  document.body.setAttribute('data-wiz-step', String(step));

  // Show/hide wizard step content (always loop to 4 — the actual number of content divs)
  for (let i = 1; i <= 4; i++) {
    const con = document.getElementById('wizContent' + i);
    if (!con) continue;
    con.style.display = (i === step) ? 'block' : 'none';
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
  if (step === 2) { wizUpdateHotkeyBadge(); wizLoadHotkeyInfo(); initFnKeyFeedback(); }
  if (step === 3) { wizInitApiKeyStep(); wizInitProviderTabs(); }
  if (step === 4) { wizInitTryItStep(); wizUpdateTryItKeycap(); initFnKeyFeedback(); wizStartExplainerWaffle(); }
}

function wizUpdateNextButton() {
  const btn = document.getElementById('wizBtnNext');
  switch (_wizardStep) {
    case 1: btn.disabled = false; break;  // Permissions - always allow
    case 2: btn.disabled = false; break;  // Hotkeys - always allow
    case 3: btn.disabled = !(_wizardCerebrasKeyValidated || _wizardGroqKeyValidated || _wizardApiKeyValidated); break;
    case 4: btn.disabled = !_wizardMicTested; break;  // Try It - require mic test
  }
}

async function wizNext() {
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
  // Render presets based on platform — Fn doesn't exist on Windows.
  const list = document.getElementById('wizHotkeyPresetList');
  if (list) {
    const presets = isMacPlatform ? [
      { keys: ['fn'],               label: 'Fn',             hint: 'Default · works on most MacBooks' },
      { keys: ['cmd', 'shift'],     label: 'Cmd + Shift',    hint: "If Fn doesn't work" },
      { keys: ['option', 'shift'],  label: 'Option + Shift', hint: 'Alternative' },
    ] : [
      { keys: ['win', 'ctrl'],          label: 'Win + Ctrl',          hint: 'Default · most reliable' },
      { keys: ['ctrl', 'shift'],        label: 'Ctrl + Shift',        hint: "If Win + Ctrl doesn't work" },
      { keys: ['ctrl', 'alt', 'space'], label: 'Ctrl + Alt + Space',  hint: 'Three-key combo' },
    ];
    list.innerHTML = presets.map((p) => {
      const keysJson = JSON.stringify(p.keys).replace(/"/g, '&quot;');
      return `<button class="hotkey-preset-btn" onclick='selectHotkeyPreset(${JSON.stringify(p.keys)})'>` +
             `<span class="hotkey-preview">${p.label}</span>` +
             ` <span style="opacity:0.6;font-size:13px">${p.hint}</span>` +
             `</button>`;
    }).join('');
  }
  document.getElementById('wizHotkeyConfigPanel').style.display = 'block';
}

function hideWizardHotkeyConfig() {
  document.getElementById('wizHotkeyConfigPanel').style.display = 'none';
}

async function selectHotkeyPreset(keys) {
  try {
    // Save hotkey configuration
    await pywebview.api.save_hotkey_config(keys);

    // Update display
    const config = await pywebview.api.get_hotkey_config();
    const badge = document.getElementById('wizHotkeyBadge');
    if (badge && config.display) {
      badge.textContent = config.display;
    }

    // Update detection to use new hotkey
    _currentWizardHotkey = keys;
    window._fnKeyDetected = false; // Reset detection flag

    // Update status message
    const statusEl = document.getElementById('wizHotkeyStatus');
    if (statusEl) {
      statusEl.textContent = `Hotkey changed to ${config.display}. Press it to test!`;
      statusEl.style.color = '#C8A256';
    }

    // Hide config panel
    hideWizardHotkeyConfig();
  } catch (e) {
    console.error('Failed to save hotkey:', e);
    const statusEl = document.getElementById('wizHotkeyStatus');
    if (statusEl) {
      statusEl.textContent = 'Failed to save hotkey. Please try again.';
      statusEl.style.color = '#f44336';
    }
  }
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
        if (val.length >= 20) wizValidateCerebrasKey(val);
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
      v.textContent = 'Groq key is valid. Free tier active.';
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
    v.textContent = 'Failed to validate — check your internet connection';
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
      v.textContent = r.message || 'Cerebras key is valid. Fastest tier enabled.';
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
    v.textContent = 'Failed to validate — check your internet connection';
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
    v.textContent = 'Failed to validate — check your internet connection';
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

// ── Permission Status Indicator for Main UI ──────────────────────
async function showPermissionStatusIndicator() {
  try {
    const status = await pywebview.api.get_permission_status();
    const indicator = document.getElementById('permissionStatusIndicator');
    
    if (!indicator) {
      // Create the indicator if it doesn't exist
      const indicatorHTML = `
        <div id="permissionStatusIndicator" class="permission-status-indicator">
          <div class="permission-status-header">
            <span class="permission-status-icon">🔒</span>
            <span class="permission-status-title">Permissions</span>
            <button class="permission-status-toggle" onclick="togglePermissionStatus()">▼</button>
          </div>
          <div id="permissionStatusDetails" class="permission-status-details" style="display:none;">
            <div id="permissionStatusContent"></div>
            <button class="permission-recheck-btn" onclick="recheckPermissions()">Recheck</button>
          </div>
        </div>
      `;
      
      // Insert at top of main content area
      const mainContent = document.querySelector('.main-content') || document.body;
      mainContent.insertAdjacentHTML('afterbegin', indicatorHTML);
    }
    
    updatePermissionStatusIndicator(status);
  } catch(e) {
    console.warn('showPermissionStatusIndicator error:', e);
  }
}

function updatePermissionStatusIndicator(status) {
  const indicator = document.getElementById('permissionStatusIndicator');
  const content = document.getElementById('permissionStatusContent');
  const icon = indicator.querySelector('.permission-status-icon');
  
  if (!indicator || !content) return;
  
  // Update indicator icon and color based on overall status
  if (status.all_granted) {
    icon.textContent = '✅';
    indicator.className = 'permission-status-indicator granted';
  } else if (status.missing_critical.length === 0) {
    icon.textContent = '⚠️';
    indicator.className = 'permission-status-indicator partial';
  } else {
    icon.textContent = '❌';
    indicator.className = 'permission-status-indicator denied';
  }
  
  // Generate detailed status content
  let contentHTML = '';
  
  Object.entries(status.permissions).forEach(([permName, permInfo]) => {
    const statusIcon = permInfo.granted ? '✅' : (permInfo.critical ? '❌' : '⚠️');
    const statusClass = permInfo.granted ? 'granted' : (permInfo.critical ? 'critical' : 'optional');
    
    contentHTML += `
      <div class="permission-item ${statusClass}">
        <span class="permission-item-icon">${statusIcon}</span>
        <div class="permission-item-info">
          <div class="permission-item-title">${permInfo.title}</div>
          <div class="permission-item-desc">${permInfo.explanation || ''}</div>
          ${!permInfo.granted && permInfo.error ? `<div class="permission-item-error">${permInfo.error}</div>` : ''}
          ${!permInfo.granted && permInfo.fallback_available ? `<div class="permission-item-fallback">Fallback: ${permInfo.fallback_message}</div>` : ''}
        </div>
        ${!permInfo.granted ? `<button class="permission-item-btn" onclick="requestPermission('${permName}')">Fix</button>` : ''}
      </div>
    `;
  });
  
  if (status.recommendations.length > 0) {
    contentHTML += `
      <div class="permission-recommendations">
        <strong>Status:</strong> ${status.recommendations.join(' ')}
      </div>
    `;
  }
  
  content.innerHTML = contentHTML;
}

function togglePermissionStatus() {
  const details = document.getElementById('permissionStatusDetails');
  const toggle = document.querySelector('.permission-status-toggle');
  
  if (details.style.display === 'none') {
    details.style.display = 'block';
    toggle.textContent = '▲';
  } else {
    details.style.display = 'none';
    toggle.textContent = '▼';
  }
}

async function recheckPermissions() {
  try {
    const status = await pywebview.api.get_permission_status();
    updatePermissionStatusIndicator(status);
  } catch(e) {
    console.warn('recheckPermissions error:', e);
  }
}

async function requestPermission(permissionType) {
  try {
    switch(permissionType) {
      case 'microphone':
        await pywebview.api.request_mic_permission();
        break;
      case 'accessibility':
        await pywebview.api.request_accessibility_permission();
        break;
      case 'input_monitoring':
        await pywebview.api.request_input_monitoring_permission();
        break;
    }
    // Recheck status after request
    setTimeout(recheckPermissions, 1000);
  } catch(e) {
    console.warn('requestPermission error:', e);
  }
}

// (Permission status indicator removed — not needed for BYOK app)

// ── Step 3: Hotkey Info ──────────────────────────────────────

async function wizLoadHotkeyInfo() {
  // Reset Fn key detection flag for this step
  window._fnKeyDetected = false;

  try {
    // Start hotkey monitor for Step 2 (provides visual feedback)
    await pywebview.api.wizard_init_step2();

    const info = await pywebview.api.test_hotkey();
    const badge = document.getElementById('wizHotkeyBadge');
    if (badge) badge.textContent = info.hotkey;
    const modeEl = document.getElementById('wizHotkeyMode');
    if (modeEl) modeEl.textContent = info.mode === 'toggle'
      ? 'Toggle mode: press once to start, press again to stop'
      : 'Hold mode: hold key to record, release to stop';
    const descEl = document.getElementById('wizHotkeyDesc');
    if (descEl) descEl.textContent = info.description;
  } catch(e) {
    console.warn('wizLoadHotkeyInfo error:', e);
  }
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

    // Auto-advance after 1 second
    setTimeout(() => {
      wizNext();
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

  // Update hotkey badge text
  try {
    const info = await pywebview.api.test_hotkey();
    const badge = document.getElementById('wizTryHotkeyBadge');
    if (badge) badge.textContent = info.hotkey;
    const ph = document.getElementById('wizMockPlaceholder');
    if (ph) ph.innerHTML = 'Press <kbd>' + info.hotkey + '</kbd> and speak...';
  } catch(e) {}

  // Attach event listener to mock send button
  const sendBtn = document.getElementById('wizMockSendBtn');
  if (sendBtn) {
    sendBtn.onclick = function() {
      const mockText = document.getElementById('wizMockText');
      if (mockText && mockText.textContent.trim()) {
        showToast('✨ Pushed to app!', 'success');
        // Reset the mock input after "sending"
        setTimeout(() => {
          mockText.textContent = '';
          sendBtn.disabled = true;
          const placeholder = document.getElementById('wizMockPlaceholder');
          if (placeholder) {
            placeholder.style.display = 'inline';
            placeholder.innerHTML = 'Try again or continue...';
          }
        }, 800);
      }
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
      if (valid) { valid.textContent = r.error || 'Failed to start'; valid.className = 'wizard-validation error'; }
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
let _wizPermPollTimer = null;
async function wizStartPermissionPoll() {
  if (_wizPermPollTimer) return;
  const tick = async () => {
    try {
      const r = await pywebview.api.check_permissions();
      wizSetPermPill('wizAccessStatus', 'wizPermAccessibility', !!r.accessibility_granted);
      wizSetPermPill('wizInputMonStatus', 'wizPermInputMon', !!r.input_monitoring_granted);
      // Auto-advance Next button when both granted
      if (r.accessibility_granted && r.input_monitoring_granted) {
        const next = document.getElementById('wizBtnNext');
        if (next) next.disabled = false;
      }
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

// Update the hotkey badge text in the Try-It step (Step 4) and the mock app
// placeholder, based on platform.
function wizUpdateTryItKeycap() {
  const combo = document.getElementById('wizTryHotkeyBadge');
  const kbd = document.getElementById('wizMockKbd');
  if (!combo) return;
  if (isMacPlatform) {
    combo.innerHTML = '<span class="wiz-keycap-mini">fn</span>';
    if (kbd) kbd.textContent = 'fn';
  } else {
    // Ctrl first, then Win — user preference.
    combo.innerHTML = '<span class="wiz-keycap-mini">Ctrl</span><span class="wiz-keycap-plus-mini">+</span><span class="wiz-keycap-mini">Win</span>';
    if (kbd) kbd.textContent = 'Ctrl+Win';
  }
}

// Update the big hotkey badge on Step 2 based on platform.
// On Windows, Ctrl is displayed first then Win (user preference).
function wizUpdateHotkeyBadge() {
  const combo = document.getElementById('wizHotkeyDisplay');
  if (!combo) return;
  if (isMacPlatform) {
    combo.innerHTML = '<div class="wiz-keycap-large" id="wizHotkeyBadge"><svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.5" stroke="#1A1A1A" stroke-width="1"/><ellipse cx="8" cy="8" rx="3" ry="6.5" stroke="#1A1A1A" stroke-width="0.8"/><line x1="1.5" y1="5.5" x2="14.5" y2="5.5" stroke="#1A1A1A" stroke-width="0.7"/><line x1="1.5" y1="10.5" x2="14.5" y2="10.5" stroke="#1A1A1A" stroke-width="0.7"/><line x1="8" y1="1.5" x2="8" y2="14.5" stroke="#1A1A1A" stroke-width="0.5"/></svg><span class="wiz-keycap-label-large" style="font-size:14px;margin-top:2px">fn</span></div>';
  } else {
    // Ctrl first, then Win — user explicitly asked for this ordering.
    combo.innerHTML =
      '<div class="wiz-keycap-large"><span class="wiz-keycap-label-large">Ctrl</span></div>' +
      '<span class="wiz-keycap-plus">+</span>' +
      '<div class="wiz-keycap-large" id="wizHotkeyBadgeWin"><svg viewBox="0 0 24 24" fill="#1A1A1A"><path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801"/></svg><span class="wiz-keycap-label" style="font-size:11px;margin-top:2px">Win</span></div>';
  }
  // Render the new branded instruction cards underneath
  wizRenderHotkeyInstructions();
}

// Render the three instruction tiles under the listening pill.
// Vertical layout per tile: keys on top, title underneath. Sticky-mode
// tile shows Space + the hotkey combo and includes a short description.
// Release-to-stop has no keycap at all — just the title.
function wizRenderHotkeyInstructions() {
  const host = document.getElementById('wizHotkeyInstructionCards');
  if (!host) return;
  const k1 = isMacPlatform ? 'fn' : 'Ctrl';
  const k2 = isMacPlatform ? null  : 'Win';
  const comboHtml = k2
    ? `<span class="wiz-mini-kbd">${k1}</span><span class="wiz-mini-plus">+</span><span class="wiz-mini-kbd">${k2}</span>`
    : `<span class="wiz-mini-kbd">${k1}</span>`;
  const stickyKeys = k2
    ? `<span class="wiz-mini-kbd">Space</span><span class="wiz-mini-plus">+</span><span class="wiz-mini-kbd">${k1}</span><span class="wiz-mini-plus">+</span><span class="wiz-mini-kbd">${k2}</span>`
    : `<span class="wiz-mini-kbd">Space</span><span class="wiz-mini-plus">+</span><span class="wiz-mini-kbd">${k1}</span>`;
  host.innerHTML = `
    <div class="wiz-hotkey-card">
      <div class="wiz-hotkey-card-keys">${comboHtml}</div>
      <div class="wiz-hotkey-card-title">Hold to record</div>
    </div>
    <div class="wiz-hotkey-card wiz-hotkey-card-nokey">
      <div class="wiz-hotkey-card-title">Release to stop</div>
    </div>
    <div class="wiz-hotkey-card">
      <div class="wiz-hotkey-card-keys">${stickyKeys}</div>
      <div class="wiz-hotkey-card-title">Sticky mode</div>
      <div class="wiz-hotkey-card-sub">Press Space to lock recording on. Press the hotkey again to disable.</div>
    </div>
  `;
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

  function animate() {
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

async function wizCompleteSetup() {
  const btn = document.getElementById('wizBtnNext');
  btn.disabled = true;
  btn.textContent = 'Setting up...';
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
      showToast('Setup error: ' + r.error, 'error');
      btn.disabled = false;
      btn.textContent = 'Finish Setup';
    }
  } catch(e) {
    showToast('Setup failed: ' + e, 'error');
    btn.disabled = false;
    btn.textContent = 'Finish Setup';
  }
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
};

// ── Usage Stats ───────────────────────────────────────────────────────────
// Provider display metadata — gold dot per provider for the breakdown rows.
const PROVIDER_META = {
  groq:     { name: 'Groq',     accent: '#f55036', desc: 'Llama 3.3 70B' },
  cerebras: { name: 'Cerebras', accent: '#C8A256', desc: 'Qwen-3 235B' },
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

    // Time buckets
    const today = document.getElementById('usageTodayCost');
    const week  = document.getElementById('usageWeekCost');
    const month = document.getElementById('usageMonthCost');
    const total = document.getElementById('usageTotalCost');
    if (today) today.textContent = _fmtUsd(stats.today_cost_usd);
    if (week)  week.textContent  = _fmtUsd(stats.week_cost_usd);
    if (month) month.textContent = _fmtUsd(stats.month_cost_usd);
    if (total) total.textContent = _fmtUsd(stats.total_cost_usd);

    // Activity totals
    document.getElementById('usageTranscriptions').textContent = stats.transcription_count || 0;
    document.getElementById('usageAvgCost').textContent = _fmtUsd(stats.avg_cost_per_transcription, 3);

    // Per-provider breakdown
    const rows = document.getElementById('usageProviderRows');
    if (rows) {
      const byProv = stats.by_provider || {};
      // Order: groq, cerebras, openai, anything else
      const order = ['groq', 'cerebras', 'openai'];
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
          return `
            <div class="usage-provider-row">
              <div class="usage-provider-row-head">
                <span class="usage-provider-dot" style="background:${meta.accent}"></span>
                <span class="usage-provider-name">${meta.name}</span>
                ${meta.desc ? `<span class="usage-provider-desc">${meta.desc}</span>` : ''}
                <span class="usage-provider-cost">${_fmtUsd(b.cost_usd, 4)}</span>
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
    if (el) el.textContent = 'v' + ver + ' · Powered by Groq + Whisper + LLaMA';
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
      showToast('Error: ' + r.error, 'error');
    }
  } catch(e) {
    showToast('Failed to reset usage', 'error');
  }
}


