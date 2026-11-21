// Settings, Transforms and Command mode: "Try it here". A box of text, one
// button, and the real feature run on it: Polish for Transforms, a typed
// instruction for Command mode. The result goes into the box with Undo; a
// question shows the search it would open, as a link, and opens nothing until
// that link is clicked. No selection, shortcut or permission is needed.
// Python: Api.try_it_status / try_transform / try_command /
// open_command_search (src/try_it.py). The words are logic.js's (tryItNeeds,
// tryItResult).
(function () {
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const BOXES = {
    transform: { text: 'transformsTryText', btn: 'transformsTryBtn', said: 'transformsTrySaid',
                 undo: 'transformsTryUndo', needs: 'transformsTryNeeds', needsText: 'transformsTryNeedsText' },
    command: { text: 'commandTryText', ask: 'commandTryAsk', btn: 'commandTryBtn', said: 'commandTrySaid',
               undo: 'commandTryUndo', needs: 'commandTryNeeds', needsText: 'commandTryNeedsText' },
  };
  const state = { transform: { busy: false, before: null }, command: { busy: false, before: null } };
  let ready = false;

  function say(kind, view) {
    const box = BOXES[kind], said = $(box.said), undo = $(box.undo);
    if (!said) return;
    said.textContent = view ? view.said : '';
    said.classList.toggle('is-err', !!(view && view.error));
    if (view && view.link) {
      // The words of the question only: the app builds the address again
      // from them when the link is clicked (Api.open_command_search).
      const a = document.createElement('a');
      a.className = 'link';
      a.href = '#';
      a.textContent = view.link.label;
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const p = api();
        if (p && p.open_command_search) p.open_command_search(view.link.query);
      });
      said.append(' ', a);
    }
    if (undo) undo.hidden = !(view && view.undo);
  }

  function drawButton(kind) {
    const box = BOXES[kind], btn = $(box.btn);
    if (!btn) return;
    const busy = state[kind].busy;
    btn.textContent = busy ? btn.dataset.busy : btn.dataset.idle;
    btn.disabled = busy || !ready;
    const needs = $(box.needs);
    const view = WL.tryItNeeds({ ok: true, ready });
    if (needs) needs.hidden = view.ready;
    const line = $(box.needsText);
    if (line) line.textContent = view.line;
  }

  async function refresh() {
    const p = api();
    let status = null;
    try { status = p && p.try_it_status ? await p.try_it_status() : null; } catch (e) { status = null; }
    ready = WL.tryItNeeds(status).ready;
    Object.keys(BOXES).forEach(drawButton);
  }

  async function run(kind) {
    const box = BOXES[kind], st = state[kind], text = $(box.text);
    const p = api();
    if (!text || st.busy || !ready || !p) return;
    const before = text.value;
    st.busy = true;
    drawButton(kind);
    say(kind, null);
    let r = null;
    try {
      r = kind === 'command' ? await p.try_command(before, $(box.ask).value) : await p.try_transform(before);
    } catch (e) {
      r = null;
    }
    st.busy = false;
    drawButton(kind);
    const view = WL.tryItResult(kind, r);
    if (view.text !== null) {
      st.before = before;
      text.value = view.text;
    }
    say(kind, view);
  }

  function undo(kind) {
    const box = BOXES[kind], st = state[kind], text = $(box.text);
    if (!text || st.before === null) return;
    text.value = st.before;
    st.before = null;
    say(kind, null);
    text.focus();
  }

  function wire() {
    Object.keys(BOXES).forEach((kind) => {
      const box = BOXES[kind], text = $(box.text), btn = $(box.btn), back = $(box.undo), ask = box.ask && $(box.ask);
      if (!text || !btn) return;
      text.value = WL.TRY_SAMPLE;
      if (ask) {
        ask.value = WL.TRY_ASK;
        ask.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); run(kind); } });
      }
      btn.addEventListener('click', () => run(kind));
      if (back) back.addEventListener('click', () => undo(kind));
      // Typing after a result: there is nothing left for Undo to put back.
      text.addEventListener('input', () => { state[kind].before = null; if (back) back.hidden = true; });
      drawButton(kind);
    });
  }

  window.tryItRefresh = refresh;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
  window.addEventListener('pywebviewready', refresh);
})();
