// Settings, Claude: one click and the Claude desktop app can read meetings and
// dictations. Python: Api.claude_status / claude_connect / claude_disconnect
// (src/claude_connector.py), and claude_dictations through save_settings.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;

  function draw(s) {
    if (!$('secClaude') || !s) return;
    const state = $('claudeState'), on = $('claudeConnect'), off = $('claudeDisconnect');
    const stale = s.connected && !s.current;
    on.hidden = !s.installed || (s.connected && !stale) ;
    off.hidden = !s.connected;
    on.textContent = stale ? 'Connect again' : 'Connect Claude';
    if (s.error) state.textContent = s.error;
    else if (!s.installed) state.textContent = 'Install the Claude app and open it once.';
    else if (stale) state.textContent = 'Connected to another copy of Waffler.';
    else if (s.connected) state.textContent = 'Connected. Claude reads your meetings' + (s.dictations ? ' and dictations' : '') + '.';
    else state.textContent = 'Not connected.';
    $('claudeDictToggle').checked = s.dictations !== false;
    $('claudeCommand').textContent = s.command || '';
    $('claudeCodeRow').hidden = !s.command;
    $('claudeCodex').textContent = s.codex || '';
    $('claudeCodexRow').hidden = !s.codex;
    $('claudeOther').textContent = s.other || '';
    $('claudeOtherRow').hidden = !s.other;
  }

  async function refresh() {
    const a = api(); if (!a || !a.claude_status) return;
    try { draw(await a.claude_status()); } catch (e) { /* the page stays as it is */ }
  }

  async function act(name, done) {
    const a = api(); if (!a || !a[name]) return;
    try {
      const r = await a[name]();
      draw(r);
      if (r && r.ok) showToast(done, 'success', 5000);
      else showToast((r && r.error) || 'That did not work. Try again.', 'error');
    } catch (e) { showToast('That did not work. Try again.', 'error'); }
  }

  async function saveDictations() {
    const a = api(), tog = $('claudeDictToggle'); if (!a || !tog) return;
    const on = tog.checked;
    try {
      const r = await a.save_settings({ claude_dictations: on });
      if (r && r.ok) { showToast(on ? 'Claude can read your dictations.' : 'Claude reads meetings only now.', 'success', 3500); refresh(); }
      else { tog.checked = !on; showToast('That could not be saved. Try again.', 'error'); }
    } catch (e) { tog.checked = !on; showToast('That could not be saved. Try again.', 'error'); }
  }

  async function copyCommand(from) {
    const a = api(), text = $(typeof from === 'string' ? from : 'claudeCommand').textContent; if (!text) return;
    try {
      if (a && a.copy_item) { if ((await a.copy_item(text)) === false) throw new Error('not copied'); }
      else await navigator.clipboard.writeText(text);
      showToast('Copied.', 'success', 2000);
    } catch (e) { showToast('That could not be copied.', 'error'); }
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!$('secClaude')) return;
    $('claudeConnect').addEventListener('click', () => act('claude_connect', 'Connected. Quit Claude and open it again to finish.'));
    $('claudeDisconnect').addEventListener('click', () => act('claude_disconnect', 'Disconnected. Claude can no longer read Waffler.'));
    $('claudeDictToggle').addEventListener('change', saveDictations);
    $('claudeCopy').addEventListener('click', () => copyCommand('claudeCommand'));
    $('claudeCodexCopy').addEventListener('click', () => copyCommand('claudeCodex'));
    $('claudeOtherCopy').addEventListener('click', () => copyCommand('claudeOther'));
  });

  window.loadClaude = refresh;
})();
