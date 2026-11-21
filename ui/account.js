// Accounts in the window. Settings, Account: create an account or sign in
// (the same two steps: an email, then the 6-digit code sent to it), and then
// the account itself: when the meetings last synced, the devices signed in,
// the private link that lets Claude read the meetings, Sign out and Delete my
// account. The same sign-in fields are the optional last screen of first-run
// setup (index.html #obStepAccount; app.js wizShowStep), and Home offers an
// account once, on a card that can be put away for good.
// Python: Api.account_* (app.py, src/account.py). The small decisions (what a
// line says, which buttons show) are in logic.js.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const L = window.WafflerLogic;
  const FAILED = 'That didn’t work. Try again.';

  let status = null;      // the last Api.account_status()
  let connector = null;   // the last answer about the Claude link
  let link = '';          // the link made a moment ago: held here only, gone when the window closes
  let ticker = null;
  let meetings = 0;       // how many meetings Home last counted (for its card)
  let asking = null;      // the question open in the section: {opener, close}
  let settingsFlow = null;

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

  // ── create an account or sign in ──────────────────────────────────────────
  // One flow, in two places: Settings (ids that begin 'acct') and setup's
  // last screen ('obAcct'). `hooks.show(step)` puts 'email' or 'code' on
  // screen; `hooks.done(answer)` is called once the code has worked.
  function signInFlow(p, hooks) {
    const el = (name) => $(p + name);
    let email = '';         // the address the code was asked for
    let sentAt = 0;         // when, for "Send it again"
    let timer = null;
    const error = (text) => { el('Error').textContent = text || ''; el('Error').hidden = !text; };

    function drawResend() {
      const v = L.accountResendView(sentAt);
      const button = el('Resend'), had = document.activeElement === button;
      button.textContent = v.text;
      button.disabled = !v.ready;
      if (had && button.disabled) el('Code').focus();
      if (v.ready && timer) { window.clearInterval(timer); timer = null; }
    }
    function sent(address) {
      email = address;
      sentAt = Date.now();
      el('CodeDesc').textContent = L.accountCodeLine(email);
      if (!timer) timer = window.setInterval(drawResend, 1000);
      drawResend();
    }
    function reset() {
      email = ''; sentAt = 0;
      if (timer) { window.clearInterval(timer); timer = null; }
      el('Code').value = '';
      error('');
      hooks.show('email');
    }
    async function send() {
      error('');
      const r = await call('account_start', el('Email').value);
      if (!r.ok) { error(r.error || FAILED); el('Email').focus(); return; }
      sent(r.email);
      el('Code').value = '';
      hooks.show('code');
      el('Code').focus();
    }
    async function again() {
      const button = el('Resend');
      if (button.disabled || !email) return;
      button.disabled = true;
      error('');
      const r = await call('account_start', email);
      if (r.ok) { sent(r.email); showToast('Sent again.', 'success', 3000); } else { error(r.error || FAILED); drawResend(); }
      el('Code').focus();
    }
    async function verify() {
      error('');
      const code = L.accountCode(el('Code').value);
      el('Code').value = code;
      if (!L.accountCodeReady(code)) { error('Enter the 6-digit code from the email.'); el('Code').focus(); return; }
      const r = await call('account_verify', email || el('Email').value, code);
      if (!r.ok) { error(r.error || FAILED); el('Code').focus(); el('Code').select(); return; }
      el('Email').value = '';
      reset();
      hooks.done(r);
    }

    // Enter in a field submits its form; the button is the form's own.
    el('EmailForm').addEventListener('submit', (e) => { e.preventDefault(); busy(el('Send'), send); });
    el('CodeForm').addEventListener('submit', (e) => { e.preventDefault(); busy(el('SignIn'), verify); });
    // A pasted code (or the whole email) becomes its six digits, and goes.
    el('Code').addEventListener('paste', (e) => {
      const text = (e.clipboardData || window.clipboardData).getData('text');
      e.preventDefault();
      el('Code').value = L.accountCode(text);
      if (L.accountCodeReady(el('Code').value)) busy(el('SignIn'), verify);
    });
    el('Code').addEventListener('input', () => { el('Code').value = L.accountCode(el('Code').value); });
    el('Resend').addEventListener('click', again);
    el('Other').addEventListener('click', () => { reset(); el('Email').focus(); el('Email').select(); });
    return { reset, focus: () => el('Email').focus() };
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
      if (was === 'in') { link = ''; connector = null; closeAsk(); closeDelete(); say('acctWelcome', ''); if (settingsFlow) settingsFlow.reset(); }
    }
    if (view === 'in') {
      $('acctWho').textContent = s.email || 'Signed in';
      $('acctDevice').textContent = s.device_name ? 'Signed in on ' + s.device_name + '.' : 'Signed in on this computer.';
      drawSync();
    }
    drawOffer();
  }

  function drawSync() {
    if (!status || L.accountView(status) !== 'in') return;
    const sync = status.sync || {};
    $('acctSync').textContent = L.accountSyncLine(sync);
    $('acctSyncNow').disabled = !!sync.syncing;
  }

  // This computer carries a mark beside its name; every device says when it
  // was last used. Names come from the service: set as text, never as HTML.
  function drawDevices(devices) {
    const list = $('acctDevices');
    closeAsk();
    list.textContent = '';
    (devices || []).forEach((d) => {
      const row = document.createElement('div'); row.className = 'row'; row.setAttribute('role', 'listitem');
      const main = document.createElement('div'); main.className = 'row-main';
      const name = document.createElement('div'); name.className = 'row-t acct-dev'; name.textContent = d.name;
      if (d.current) {
        const mark = document.createElement('span'); mark.className = 'chip chip-ok'; mark.textContent = 'This computer';
        name.append(mark);
      }
      const sub = document.createElement('div'); sub.className = 'row-d'; sub.textContent = L.accountDeviceLine(d);
      main.append(name, sub); row.append(main);
      if (!d.current) {
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'btn btn-sec btn-sm'; remove.textContent = 'Remove';
        remove.setAttribute('aria-label', 'Remove ' + d.name);
        remove.addEventListener('click', () => askRemove(d, row, remove));
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
    say('acctWelcome', '');
    const s = await refresh();
    if (L.accountView(s) === 'in') loadDetails();
    if (!ticker) ticker = window.setInterval(() => { if (open()) drawSync(); }, 30000);   // "a minute ago" moves on
  }

  // ── a question, asked once before something is taken away ─────────────────
  // One is open at a time. No (or Esc) closes it and puts focus back on the
  // button that asked; only Yes reaches the app.
  function closeAsk(refocus) {
    const a = asking;
    asking = null;
    if (!a) return;
    a.close();
    if (refocus && a.opener && a.opener.isConnected && !a.opener.hidden) a.opener.focus();
  }

  function askRemove(d, row, opener) {
    closeAsk(); closeDelete();
    const c = L.accountConfirm('remove', d.name);
    const q = document.createElement('div'); q.className = 'row row-confirm'; q.setAttribute('role', 'listitem');
    const main = document.createElement('div'); main.className = 'row-main';
    const title = document.createElement('div'); title.className = 'row-t'; title.textContent = c.title;
    const desc = document.createElement('div'); desc.className = 'row-d'; desc.textContent = c.desc;
    main.append(title, desc);
    const no = document.createElement('button'); no.type = 'button'; no.className = 'btn btn-sec btn-sm'; no.textContent = c.no;
    const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'btn btn-danger btn-sm'; yes.textContent = c.yes;
    yes.setAttribute('aria-label', 'Remove ' + d.name);
    no.addEventListener('click', () => closeAsk(true));
    yes.addEventListener('click', () => busy(yes, () => removeDevice(d)));
    q.append(main, no, yes);
    row.after(q);
    asking = { opener, close: () => q.remove() };
    no.focus();
  }

  function askClaude(kind) {
    closeAsk(); closeDelete();
    const c = L.accountConfirm(kind);
    const row = $('acctClaudeConfirm'), yes = $('acctClaudeYes');
    $('acctClaudeConfirmTitle').textContent = c.title;
    $('acctClaudeConfirmDesc').textContent = c.desc;
    $('acctClaudeNo').textContent = c.no;
    yes.textContent = c.yes;
    yes.onclick = () => busy(yes, async () => {
      closeAsk();
      if (kind === 'new') await makeLink('account_connector_rotate', 'New link made. The old one no longer works.');
      else await turnOff();
    });
    row.hidden = false;
    asking = { opener: $(kind === 'new' ? 'acctClaudeNew' : 'acctClaudeOff'), close: () => { row.hidden = true; } };
    $('acctClaudeNo').focus();
  }

  // ── signing out ───────────────────────────────────────────────────────────
  async function signOut() {
    const r = await call('account_sign_out');
    if (!r.ok) { say('acctInError', r.error || FAILED); return; }
    link = ''; connector = null;
    draw(r);
    if (r.note) showToast(r.note, 'info', 9000); else showToast('Signed out. Your meetings are still on this computer.', 'success', 5000);
    $('acctEmail').focus();
  }

  // ── signed in ─────────────────────────────────────────────────────────────
  function signedIn(r) {
    draw(r);
    say('acctWelcome', L.accountSignedInLine(r.new_account) + ' Your meetings are syncing.');
    loadDetails();
  }

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
    if (!r.ok) { closeAsk(true); refused(r); return; }
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

  // ── Delete my account ─────────────────────────────────────────────────────
  // The row says what goes and what stays; the button wakes once DELETE is
  // typed, and Python checks the word again before it asks the service.
  function openDelete() {
    closeAsk();
    $('acctDeleteWord').value = '';
    $('acctDeleteYes').disabled = true;
    $('acctDeleteConfirm').hidden = false;
    $('acctDelete').setAttribute('aria-expanded', 'true');
    $('acctDeleteWord').focus();
  }

  function closeDelete(refocus) {
    if ($('acctDeleteConfirm').hidden) return;
    $('acctDeleteConfirm').hidden = true;
    $('acctDeleteWord').value = '';
    $('acctDelete').setAttribute('aria-expanded', 'false');
    if (refocus) $('acctDelete').focus();
  }

  async function deleteAccount() {
    say('acctInError', '');
    if (!L.accountDeleteReady($('acctDeleteWord').value)) { $('acctDeleteWord').focus(); return; }
    const r = await call('account_delete', $('acctDeleteWord').value);
    if (!r.ok) { refused(r); return; }
    link = ''; connector = null;
    draw(r);                       // signed out; the notice says the account is gone and what is still here
    showToast('Your account is deleted.', 'success', 5000);
    $('acctEmail').focus();
  }

  // ── Home: one quiet card, for someone signed out who has a meeting ────────
  // Its close button puts it away for good (settings.json, through
  // Api.account_offer_dismiss). With no account service it is never made.
  function openAccount() {
    showPage('settings');
    showSettingsSection('account');
    if (settingsFlow) settingsFlow.focus();
  }

  function drawOffer(count) {
    if (count != null) meetings = count;
    const host = $('journalNotices');
    if (!host) return;
    const have = $('acctOffer');
    if (!L.accountOfferView(status, meetings)) { if (have) have.remove(); return; }
    if (have || typeof makeNotice !== 'function') return;
    const go = document.createElement('button');
    go.type = 'button'; go.className = 'btn btn-sec btn-sm'; go.textContent = 'Create account or sign in';
    go.addEventListener('click', openAccount);
    const card = makeNotice({ icon: 'info', title: 'Your meetings on every device',
      desc: 'A free account keeps them in step.', actions: [go], id: 'acctOffer' });
    const x = card.querySelector('.dismiss');
    x.setAttribute('aria-label', 'Don’t show this again');
    x.title = 'Don’t show this again';
    x.addEventListener('click', () => {
      if (status) status.offer_dismissed = true;
      call('account_offer_dismiss');
      showToast('Hidden for good. Accounts are in Settings, Account.', 'info', 5000);
    });
    host.append(card);
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!$('secAccount')) return;
    settingsFlow = signInFlow('acct', {
      show(step) { $('acctEmailRow').hidden = step !== 'email'; $('acctCodeRow').hidden = step !== 'code'; },
      done(r) { signedIn(r); $('acctSyncNow').focus(); },
    });
    // The last, optional screen of first-run setup: the same fields.
    if ($('obStepAccount')) {
      signInFlow('obAcct', {
        show(step) { if (typeof wizSetState === 'function') wizSetState('account', step); },
        done(r) {
          signedIn(r);
          $('obTitleAccountDone').textContent = L.accountSignedInLine(r.new_account).replace(/\.$/, '');
          if (typeof wizSetState === 'function') wizSetState('account', 'done');
        },
      });
    }
    $('acctSignOut').addEventListener('click', () => busy($('acctSignOut'), signOut));
    $('acctSyncNow').addEventListener('click', () => busy($('acctSyncNow'), syncNow));
    $('acctClaudeCreate').addEventListener('click', () => busy($('acctClaudeCreate'), () => makeLink('account_connector_create', 'Link made. Copy it into Claude.')));
    $('acctClaudeNew').addEventListener('click', () => askClaude('new'));
    $('acctClaudeOff').addEventListener('click', () => askClaude('off'));
    $('acctClaudeNo').addEventListener('click', () => closeAsk(true));
    $('acctClaudeCopy').addEventListener('click', copyLink);
    $('acctDelete').addEventListener('click', () => { if ($('acctDeleteConfirm').hidden) openDelete(); else closeDelete(true); });
    $('acctDeleteNo').addEventListener('click', () => closeDelete(true));
    $('acctDeleteWord').addEventListener('input', () => { $('acctDeleteYes').disabled = !L.accountDeleteReady($('acctDeleteWord').value); });
    $('acctDeleteForm').addEventListener('submit', (e) => { e.preventDefault(); busy($('acctDeleteYes'), deleteAccount); });
    // Esc closes a question without acting.
    $('secAccount').addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (asking) { e.preventDefault(); closeAsk(true); } else if (!$('acctDeleteConfirm').hidden) { e.preventDefault(); closeDelete(true); }
    });
    // Settings' footer line and Home's card need to know before Account is opened.
    window.addEventListener('pywebviewready', refresh);
  });

  // app.js's showSettingsSection calls this when Account opens.
  window.loadAccount = load;
  // app.js calls this when Home has counted the meetings (loadHistory).
  window.accountOffer = async function (count) {
    if (!status) { meetings = count; await refresh(); } else drawOffer(count);
  };
  // Python calls this when a sync starts or ends, or the account signed this
  // computer out (app.py _account_changed).
  window.waffler_account_changed = function () { refresh(); };
})();
