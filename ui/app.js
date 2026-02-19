/* VoiceFlow — Frontend Logic */

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
    if (badge) badge.textContent = 'RCtrl + RAlt';
    if (label) label.textContent = 'Toggle recording';
    if (emptyHint) emptyHint.innerHTML = 'Press <strong>Right Ctrl + Right Alt</strong> and speak';
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
window.voiceflow_refresh = function(newItem) {
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
window.voiceflow_status = function(status) {
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
  const sel = document.getElementById('deviceSelect');
  if (!sel) return;
  try {
    const devices = await pywebview.api.get_audio_devices();
    const current = await pywebview.api.get_selected_device();
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
  } catch(e) {
    console.warn('loadAudioDevices error:', e);
  }
}

async function onDeviceChange(indexStr) {
  const idx = parseInt(indexStr, 10);
  try {
    const result = await pywebview.api.set_audio_device(idx);
    if (result && result.ok) {
      showToast(`🎙️ Mic: ${result.name}`, 'success');
    }
  } catch(e) {
    console.warn('setAudioDevice error:', e);
  }
}

// ── Mode Selector ─────────────────────────────────────────────────────

const MODE_DESCS = {
  smart:               'Auto-detects list / prose / task',
  adhd_ramble:         'Organises long brain dumps',
  agentic_engineering: 'Structured prompts for Claude / Cursor',
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

    // API key
    const apiInput = document.getElementById('apiKeyInput');
    const apiStatus = document.getElementById('apiKeyStatus');
    if (apiInput) {
      apiInput.placeholder = s.api_key_set ? s.api_key_masked : 'sk-…';
    }
    if (apiStatus) {
      if (s.api_key_set) {
        apiStatus.textContent = '✅ Key set: ' + s.api_key_masked;
        apiStatus.className = 'api-key-status ok';
      } else {
        apiStatus.textContent = '⚠️ No API key set';
        apiStatus.className = 'api-key-status err';
      }
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
        statusEl.textContent = '✅ Key saved and active';
        statusEl.className = 'api-key-status ok';
      }
      showToast('✅ API key saved', 'success');
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
    a.download = `voiceflow-history-${new Date().toISOString().slice(0,10)}.txt`;
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

// ── Onboarding ────────────────────────────────────────────────────────────
async function checkOnboarding() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const status = await pywebview.api.get_onboarding_status();
    if (status.needs_setup) {
      showPage('settings');
      const banner = document.getElementById('onboardingBanner');
      if (banner) banner.style.display = 'block';
    }
  } catch(e) {
    console.warn('checkOnboarding error:', e);
  }
}

// Run onboarding check after pywebview ready
window.addEventListener('pywebviewready', checkOnboarding);

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
