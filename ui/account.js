// Settings, Account: sign in with an emailed code, then this computer's
// meetings sync with the account. Signed in, the page shows when it last
// synced, the devices on the account, and the private link that lets Claude
// read the meetings. Python: Api.account_* (app.py, src/account.py). The
// small decisions (what a line says, which buttons show) are in logic.js.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const L = window.WafflerLogic;
  const FAILED = 'That didn’t work. Try again.';

  let status = null;      // the last Api.account_status()
  let email = '';         // the address the code was sent to
  let connector = null;   // the last answer about the Claude link
  let link = '';          // the link made a moment ago: held here only, gone when the window closes
  let ticker = null;

  const open = () => $('secAccount') && !$('secAccount').hidden;
  function say(id, text) { const el = $(id); el.textContent = text || ''; el.hidden = !text; }

  // One call to the app. Always answers an object with `ok`; never throws.
  async function call(name, ...args) {
    const a = api();
    if (!a || !a[name]) return { ok: false, error: FAILED };
    try { return (await a[name](...args)) || { ok: false, error: FAILED }; } catch (e) { return { ok: false, error: FAILED }; }
  }

  // While a button's call runs it cannot be pressed twice.
  async function busy(button, work) {
    if (button.disabled) return;
    button.disabled = true;
    try { await work(); } finally { button.disabled = false; }
  }

  // ── drawing ───────────────────────────────────────────────────────────────
  function draw(s) {
    if (!$('secAccount')) return;
    const was = L.accountView(status), view = L.accountView(s);
    status = s;
    $('acctOff').hidden = view !== 'off';
    $('acctOut').hidden = view !== 'out';
    $('acctIn').hidden = view !== 'in';
    if (view === 'out') {
      say('acctNotice', s.notice);
      if (was === 'in') { link = ''; connector = null; resetSignIn(); }
    }
    if (view === 'in') {
      $('acctWho').textContent = s.email || 'Signed in';
      $('acctDevice').textContent = s.device_name ? 'Signed in on ' + s.device_name + '.' : 'Signed in on this computer.';
      drawSync();
    }
  }

  function drawSync() {
    if (!status || L.accountView(status) !== 'in') return;
    const sync = status.sync || {};
    $('acctSync').textContent = L.accountSyncLine(sync);
    $('acctSyncNow').disabled = !!sync.syncing;
  }

  function drawDevices(devices) {
    const list = $('acctDevices');
    list.textContent = '';
    (devices || []).forEach((d) => {
      const row = document.createElement('div'); row.className = 'row'; row.setAttribute('role', 'listitem');
      const main = document.createElement('div'); main.className = 'row-main';
      const name = document.createElement('div'); name.className = 'row-t'; name.textContent = d.name;
      const sub = document.createElement('div'); sub.className = 'row-d'; sub.textContent = L.accountDeviceLine(d);
      main.append(name, sub); row.append(main);
      if (!d.current) {
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'btn btn-sec btn-sm'; remove.textContent = 'Remove';
        remove.setAttribute('aria-label', 'Remove ' + d.name);
        remove.addEventListener('click', () => busy(remove, () => removeDevice(d)));
        row.append(remove);
      }
      list.append(row);
    });
  }

  function drawClaude() {
    const view = L.accountClaudeView(connector, link);
    $('acctClaudeState').textContent = view.text;
    $('acctClaudeCreate').hidden = !view.buttons.includes('create');
    $('acctClaudeCopy').hidden = !view.buttons.includes('copy');
    $('acctClaudeNew').hidden = !view.buttons.includes('new');
    $('acctClaudeOff').hidden = !view.buttons.includes('off');
  }

  // An answer that says this computer is no longer signed in: show the sign-in fields.
  function refused(r) {
    if (r && r.reason === 'signed_out') { refresh(); return; }
    say('acctInError', (r && r.error) || FAILED);
  }

  // ── loading ───────────────────────────────────────────────────────────────
  async function refresh() {
    const s = await call('account_status');
    draw(s);
    return s;
  }

  async function loadDetails() {
    say('acctInError', '');
    connector = null; drawClaude();
    const [devices, claude] = await Promise.all([call('account_devices'), call('account_connector')]);
    if (L.accountView(status) !== 'in') return;
    if (devices.ok) drawDevices(devices.devices); else refused(devices);
    if (devices.reason === 'signed_out') return;
    connector = claude; drawClaude();
  }

  async function load() {
    const s = await refresh();
    if (L.accountView(s) === 'in') loadDetails();
    if (!ticker) ticker = window.setInterval(() => { if (open()) drawSync(); }, 30000);   // "a minute ago" moves on
  }

  // ── signing in ────────────────────────────────────────────────────────────
  function resetSignIn() {
    email = '';
    $('acctCodeRow').hidden = true;
    $('acctCode').value = '';
    $('acctSend').textContent = 'Send code';
    say('acctError', '');
  }

  async function sendCode() {
    say('acctError', '');
    const r = await call('account_start', $('acctEmail').value);
    if (!r.ok) { say('acctError', r.error || FAILED); $('acctEmail').focus(); return; }
    email = r.email;
    say('acctNotice', '');
    $('acctCodeDesc').textContent = 'We sent a code to ' + email + '. It works for 10 minutes.';
    $('acctCodeRow').hidden = false;
    $('acctSend').textContent = 'Send again';
    $('acctCode').value = '';
    $('acctCode').focus();
  }

  async function signIn() {
    say('acctError', '');
    const code = L.accountCode($('acctCode').value);
    $('acctCode').value = code;
    if (!L.accountCodeReady(code)) { say('acctError', 'Enter the 6-digit code from the email.'); $('acctCode').focus(); return; }
    const r = await call('account_verify', email || $('acctEmail').value, code);
    if (!r.ok) { say('acctError', r.error || FAILED); $('acctCode').focus(); $('acctCode').select(); return; }
    resetSignIn();
    $('acctEmail').value = '';
    draw(r);
    showToast('Signed in. Your meetings are syncing.', 'success', 4000);
    loadDetails();
    $('acctSyncNow').focus();
  }

  async function signOut() {
    const r = await call('account_sign_out');
    if (!r.ok) { say('acctInError', r.error || FAILED); return; }
    link = ''; connector = null;
    draw(r);
    if (r.note) showToast(r.note, 'info', 9000); else showToast('Signed out.', 'success', 3000);
    $('acctEmail').focus();
  }

  // ── signed in ─────────────────────────────────────────────────────────────
  async function syncNow() {
    say('acctInError', '');
    status.sync = Object.assign({}, status.sync, { syncing: true }); drawSync();
    const r = await call('account_sync_now');
    if (r.reason === 'signed_out') { refresh(); return; }
    status.sync = r.sync || Object.assign({}, status.sync, { syncing: false, error: r.error || FAILED });
    drawSync();
    $('acctSyncNow').disabled = false;
    if (r.ok) showToast('Synced.', 'success', 2500); else showToast(r.error || FAILED, 'error');
  }

  async function removeDevice(d) {
    say('acctInError', '');
    const r = await call('account_remove_device', d.id);
    if (!r.ok) { refused(r); return; }
    drawDevices(r.devices);
    showToast('Removed ' + d.name + '. It is signed out.', 'success', 4000);
    const next = $('acctDevices').querySelector('button');
    (next || $('acctSyncNow')).focus();
  }

  async function makeLink(name, done) {
    say('acctInError', '');
    const r = await call(name);
    if (!r.ok) { refused(r); return; }
    link = r.url || '';
    connector = { ok: true, exists: true };
    drawClaude();
    showToast(done, 'success', 5000);
    $('acctClaudeCopy').focus();
  }

  async function turnOff() {
    say('acctInError', '');
    const r = await call('account_connector_off');
    if (!r.ok) { refused(r); return; }
    link = ''; connector = r;
    drawClaude();
    showToast('Turned off. Claude can no longer read your account.', 'success', 4000);
    $('acctClaudeCreate').focus();
  }

  async function copyLink() {
    const a = api(); if (!link) return;
    try {
      if (a && a.copy_item) { if ((await a.copy_item(link)) === false) throw new Error('not copied'); }
      else await navigator.clipboard.writeText(link);
      showToast('Copied. Paste it into Claude and nowhere else.', 'success', 4000);
    } catch (e) { showToast('That could not be copied.', 'error'); }
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!$('secAccount')) return;
    $('acctEmailForm').addEventListener('submit', (e) => { e.preventDefault(); busy($('acctSend'), sendCode); });
    $('acctCodeForm').addEventListener('submit', (e) => { e.preventDefault(); busy($('acctSignIn'), signIn); });
    // A pasted code (or the whole email) becomes its six digits, and signs in.
    $('acctCode').addEventListener('paste', (e) => {
      const text = (e.clipboardData || window.clipboardData).getData('text');
      e.preventDefault();
      $('acctCode').value = L.accountCode(text);
      if (L.accountCodeReady($('acctCode').value)) busy($('acctSignIn'), signIn);
    });
    $('acctCode').addEventListener('input', () => { $('acctCode').value = L.accountCode($('acctCode').value); });
    $('acctSignOut').addEventListener('click', () => busy($('acctSignOut'), signOut));
    $('acctSyncNow').addEventListener('click', () => busy($('acctSyncNow'), syncNow));
    $('acctClaudeCreate').addEventListener('click', () => busy($('acctClaudeCreate'), () => makeLink('account_connector_create', 'Link made. Copy it into Claude.')));
    $('acctClaudeNew').addEventListener('click', () => busy($('acctClaudeNew'), () => makeLink('account_connector_rotate', 'New link made. The old one no longer works.')));
    $('acctClaudeOff').addEventListener('click', () => busy($('acctClaudeOff'), turnOff));
    $('acctClaudeCopy').addEventListener('click', copyLink);
  });

  // app.js's showSettingsSection calls this when Account opens.
  window.loadAccount = load;
  // Python calls this when a sync starts or ends, or the account signed this
  // computer out (app.py _account_changed). Only what is on screen is redrawn.
  window.waffler_account_changed = function () { if (open()) refresh(); };
})();
