/* Natter — Frontend Logic */

// ── State ──────────────────────────────────────────────────────────────
let history = [];
let stats = { today_words: 0, today_count: 0, total_words: 0 };
let toastTimer = null;

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
  refreshAll();
  updateDateLabel();
});

// Fallback: also try on DOMContentLoaded in case pywebview event fires early
document.addEventListener('DOMContentLoaded', () => {
  updateDateLabel();
  updateHotkeyHint();
  setTimeout(refreshAll, 300);
});

function updateDateLabel() {
  const now = new Date();
  const opts = { weekday: 'long', month: 'long', day: 'numeric' };
  $dateLabel.textContent = now.toLocaleDateString('en-GB', opts);
}

function updateHotkeyHint() {
  // Detect platform from user agent
  const isWin = navigator.userAgent.includes('Windows');
  const badge = document.getElementById('hotkeyBadge');
  const label = document.getElementById('hotkeyLabel');
  const emptyHint = document.getElementById('emptyHint');
  if (isWin) {
    if (badge) badge.textContent = 'Ctrl + Space';
    if (label) label.textContent = 'Toggle recording';
    if (emptyHint) emptyHint.innerHTML = 'Press <strong>Ctrl + Space</strong> and speak';
  } else {
    if (badge) badge.textContent = '⌥ Right';
    if (label) label.textContent = 'Hold to record';
    if (emptyHint) emptyHint.innerHTML = 'Hold <strong>Right ⌥</strong> and speak';
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
    btnEl.innerHTML = '✅ Copied';
    showToast('Copied to clipboard!', 'success');
    setTimeout(() => {
      btnEl.classList.remove('copied');
      btnEl.innerHTML = '📋 Copy';
    }, 2000);
  } catch (e) {
    showToast('Failed to copy', 'error');
  }
}

// ── Called by Python after each transcription ─────────────────────────
window.natter_refresh = function(newItem) {
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
window.natter_status = function(status) {
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
      <button class="btn-copy" onclick="copyText(this, '${encodeText(displayText)}')">📋 Copy</button>
      ${hasStyled ? `<span class="text-toggle" onclick="toggleRaw(this, '${item.timestamp}', '${encodeText(rawText)}', '${encodeText(displayText)}')">Show original</span>` : ''}
    </div>
  `;

  if (isNew) {
    setTimeout(() => div.classList.remove('new'), 3000);
  }

  return div;
}

function copyText(btn, encoded) {
  const text = decodeText(encoded);
  copyItem(text, btn);
}

function toggleRaw(toggleEl, ts, rawEncoded, styledEncoded) {
  const textEl = document.getElementById(`text-${ts}`);
  const showingStyled = textEl.classList.contains('styled');
  if (showingStyled) {
    textEl.textContent = decodeText(rawEncoded);
    textEl.classList.replace('styled', 'raw');
    toggleEl.textContent = 'Show clean';
  } else {
    textEl.textContent = decodeText(styledEncoded);
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
  smart:  'Concise — classifies and trims filler',
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

async function loadVocab() {
  const ta = document.getElementById('vocabInput');
  if (!ta) return;
  try {
    const words = await pywebview.api.get_vocab();
    ta.value = (words || []).join('\n');
  } catch(e) { console.warn('loadVocab error:', e); }
}

async function onVocabSave() {
  const ta = document.getElementById('vocabInput');
  if (!ta) return;
  const words = ta.value.split('\n').map(w => w.trim()).filter(Boolean);
  try {
    await pywebview.api.set_vocab(words);
    showToast(`📖 Vocabulary saved (${words.length} words)`, 'success');
  } catch(e) { console.warn('onVocabSave error:', e); }
}

// ── Vocabulary Page ───────────────────────────────────────────────────
let _vocabWords = [];

async function loadVocabPage() {
  const listEl = document.getElementById('vocabList');
  const emptyEl = document.getElementById('vocabEmpty');
  const countEl = document.getElementById('vocabCount');
  
  if (!listEl) return;
  
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

// Load devices + mode once pywebview is ready
window.addEventListener('pywebviewready', () => {
  loadAudioDevices();
  loadMode();
  loadVocab();
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
  } else if (page === 'vocabulary') {
    loadVocabPage();
  }
}

// ── Settings Load ────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await pywebview.api.get_settings();

    // Groq key
    const groqInput = document.getElementById('groqKeyInput');
    const groqDesc = document.getElementById('groqKeyDesc');
    if (groqInput) {
      groqInput.placeholder = s.groq_key_set ? s.groq_key_masked : 'gsk_…';
    }
    if (groqDesc) {
      groqDesc.textContent = s.groq_key_set
        ? ('Active: ' + s.groq_key_masked)
        : 'Free & 10x faster — console.groq.com';
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
    if (langSel) langSel.value = s.language || 'auto';

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
    a.download = `natter-history-${new Date().toISOString().slice(0,10)}.txt`;
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
let _wizardGroqKeyValidated = false;
let _wizardApiKeyValidated = false;
let _wizardMicTested = false;
let _wizardMicDeviceIndex = null;

async function checkOnboarding() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const status = await pywebview.api.get_onboarding_status();
    if (status.needs_setup) {
      showWizard();
    }
  } catch(e) {
    console.warn('checkOnboarding error:', e);
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
  wizShowStep(1);
  setTimeout(() => {
    const inp = document.getElementById('wizApiKeyInput');
    if (inp) inp.focus();
  }, 200);
}

function hideWizard() {
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

function wizShowStep(step) {
  // Clean up wizard hotkey test when leaving step 3
  if (_wizardStep === 3 && step !== 3 && _wizardHotkeyTestActive) {
    pywebview.api.wizard_stop_hotkey_test().catch(() => {});
    _wizardHotkeyTestActive = false;
  }
  _wizardStep = step;
  for (let i = 1; i <= 3; i++) {
    const ind = document.getElementById('wizStep' + i);
    const con = document.getElementById('wizContent' + i);
    ind.classList.remove('active', 'done');
    if (i < step) ind.classList.add('done');
    if (i === step) ind.classList.add('active');
    con.style.display = (i === step) ? 'block' : 'none';
  }
  const connectors = document.querySelectorAll('.wizard-step-connector');
  connectors.forEach((c, i) => c.classList.toggle('done', i < step - 1));

  const backBtn = document.getElementById('wizBtnBack');
  const nextBtn = document.getElementById('wizBtnNext');
  backBtn.style.display = step > 1 ? 'inline-block' : 'none';
  if (step === 3) {
    nextBtn.textContent = 'Finish Setup';
    nextBtn.classList.add('finish');
  } else {
    nextBtn.textContent = 'Next';
    nextBtn.classList.remove('finish');
  }
  wizUpdateNextButton();
  if (step === 2) wizLoadHotkeyInfo();
  if (step === 3) wizLoadMicDevices();
}

function wizUpdateNextButton() {
  const btn = document.getElementById('wizBtnNext');
  switch (_wizardStep) {
    case 1: btn.disabled = !(_wizardGroqKeyValidated || _wizardApiKeyValidated); break;
    case 2: btn.disabled = false; break;
    case 3: btn.disabled = !_wizardMicTested; break;
  }
}

async function wizNext() {
  if (_wizardStep < 3) {
    wizShowStep(_wizardStep + 1);
  } else {
    await wizCompleteSetup();
  }
}

function wizBack() {
  if (_wizardStep > 1) wizShowStep(_wizardStep - 1);
}

// ── Step 1: API Key ──────────────────────────────────────────

let _wizGroqTimer = null;
let _wizApiTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  // ── Groq key input ──
  const groqInp = document.getElementById('wizGroqKeyInput');
  if (groqInp) {
    groqInp.addEventListener('input', () => {
      _wizardGroqKeyValidated = false;
      wizUpdateNextButton();
      const val = groqInp.value.trim();
      const v = document.getElementById('wizGroqValidation');
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
  const inp = document.getElementById('wizApiKeyInput');
  if (inp) {
    inp.addEventListener('input', () => {
      _wizardApiKeyValidated = false;
      wizUpdateNextButton();
      const val = inp.value.trim();
      const v = document.getElementById('wizApiValidation');
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
});

async function wizValidateGroqKey(key) {
  const v = document.getElementById('wizGroqValidation');
  v.textContent = 'Validating with Groq...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_groq_key(key);
    if (r.ok) {
      v.textContent = 'Groq key is valid! 10x speed enabled.';
      v.className = 'wizard-validation success';
      _wizardGroqKeyValidated = true;
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation error';
      _wizardGroqKeyValidated = false;
    }
  } catch(e) {
    v.textContent = 'Failed to validate — check your internet connection';
    v.className = 'wizard-validation error';
    _wizardGroqKeyValidated = false;
  }
  wizUpdateNextButton();
}

async function wizValidateApiKey(key) {
  const v = document.getElementById('wizApiValidation');
  v.textContent = 'Validating with OpenAI...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_api_key(key);
    if (r.ok) {
      v.textContent = 'API key is valid!';
      v.className = 'wizard-validation success';
      _wizardApiKeyValidated = true;
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation error';
      _wizardApiKeyValidated = false;
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

// ── Step 2: Hotkey Info ──────────────────────────────────────

async function wizLoadHotkeyInfo() {
  try {
    const info = await pywebview.api.test_hotkey();
    document.getElementById('wizHotkeyBadge').textContent = info.hotkey;
    document.getElementById('wizHotkeyMode').textContent = info.mode === 'toggle'
      ? 'Toggle mode: press once to start, press again to stop'
      : 'Hold mode: hold key to record, release to stop';
    document.getElementById('wizHotkeyDesc').textContent = info.description;
    // Show macOS Accessibility hint if on Mac
    const macHint = document.getElementById('wizMacAccessibility');
    if (macHint && info.platform === 'Darwin') {
      macHint.style.display = 'block';
    }
  } catch(e) {
    console.warn('wizLoadHotkeyInfo error:', e);
  }
}

// ── Step 3: Microphone ──────────────────────────────────────

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
  _wizardMicTested = false;
  wizUpdateNextButton();
  // Reset hotkey test area
  const testArea = document.getElementById('wizHotkeyTestArea');
  const btn = document.getElementById('wizTestMicBtn');
  if (testArea) testArea.style.display = 'none';
  if (btn) { btn.style.display = 'block'; btn.disabled = false; btn.textContent = '🎤 Enable Hotkey Test'; }
  const valid = document.getElementById('wizMicValidation');
  if (valid) valid.textContent = '';
  // Stop any running hotkey test
  if (window.pywebview && window.pywebview.api) {
    pywebview.api.wizard_stop_hotkey_test().catch(() => {});
  }
}

let _wizardHotkeyTestActive = false;

async function wizStartHotkeyTest() {
  if (_wizardMicDeviceIndex === null) return;
  const btn = document.getElementById('wizTestMicBtn');
  const testArea = document.getElementById('wizHotkeyTestArea');
  const valid = document.getElementById('wizMicValidation');

  btn.disabled = true;
  btn.textContent = 'Starting hotkey listener...';

  try {
    await pywebview.api.set_audio_device(_wizardMicDeviceIndex);
    const r = await pywebview.api.wizard_start_hotkey_test(_wizardMicDeviceIndex);
    if (r.ok) {
      btn.style.display = 'none';
      testArea.style.display = 'block';
      valid.textContent = '';
      _wizardHotkeyTestActive = true;
    } else {
      valid.textContent = r.error || 'Failed to start hotkey test';
      valid.className = 'wizard-validation error';
      btn.disabled = false;
      btn.textContent = '🎤 Enable Hotkey Test';
    }
  } catch(e) {
    valid.textContent = 'Error: ' + e;
    valid.className = 'wizard-validation error';
    btn.disabled = false;
    btn.textContent = '🎤 Enable Hotkey Test';
  }
}

// Called from Python via evaluate_js when recording starts
window.wizOnRecordingStart = function() {
  const status = document.getElementById('wizRecordingStatus');
  const hint = document.getElementById('wizRecordingHint');
  if (status) {
    status.textContent = 'Recording... Press Ctrl+Space to stop.';
    status.className = 'wizard-recording-status active';
  }
  if (hint) hint.style.display = 'none';
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
  const resultArea = document.getElementById('wizTranscriptionResult');
  const resultText = document.getElementById('wizResultText');
  const valid = document.getElementById('wizMicValidation');
  const hint = document.getElementById('wizRecordingHint');

  if (status) {
    status.textContent = '';
    status.className = 'wizard-recording-status';
  }

  if (resultArea) resultArea.style.display = 'block';
  if (resultText) resultText.value = text;
  if (hint) {
    hint.style.display = 'block';
    hint.innerHTML = 'Try again with <strong>Ctrl + Space</strong>, or click Finish Setup.';
  }

  // Check if transcription is meaningful (not an error)
  const isError = text.startsWith('(') && text.endsWith(')');
  if (!isError && text.trim().length > 0) {
    valid.textContent = 'Pipeline test passed! Transcription working.';
    valid.className = 'wizard-validation success';
    _wizardMicTested = true;
  } else {
    valid.textContent = 'No speech detected. Try again.';
    valid.className = 'wizard-validation error';
    _wizardMicTested = false;
  }
  wizUpdateNextButton();
};

// ── Complete Setup ──────────────────────────────────────────

async function wizCompleteSetup() {
  const btn = document.getElementById('wizBtnNext');
  btn.disabled = true;
  btn.textContent = 'Setting up...';
  try {
    // Stop wizard hotkey before starting real pipeline
    if (_wizardHotkeyTestActive) {
      await pywebview.api.wizard_stop_hotkey_test();
      _wizardHotkeyTestActive = false;
    }
    const r = await pywebview.api.complete_setup();
    if (r.ok) {
      showToast('Natter is ready!', 'success');
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
};

// ── Usage Stats ───────────────────────────────────────────────────────────
async function loadUsageStats() {
  try {
    const stats = await pywebview.api.get_usage_stats();
    
    document.getElementById('usageMonthCost').textContent = '$' + (stats.month_cost_usd || 0).toFixed(2);
    document.getElementById('usageTotalCost').textContent = '$' + (stats.total_cost_usd || 0).toFixed(2);
    document.getElementById('usageTranscriptions').textContent = stats.transcription_count || 0;
    document.getElementById('usageAvgCost').textContent = '$' + (stats.avg_cost_per_transcription || 0).toFixed(3);
  } catch(e) {
    console.warn('loadUsageStats error:', e);
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
