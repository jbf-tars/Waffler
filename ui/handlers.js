// Event handlers written in the markup, without inline script.
//
// The window's Content-Security-Policy (index.html) forbids inline script,
// and with it `onclick="..."` attributes. The markup says the same thing as
// `data-onclick="showSettingsSection('keys')"` instead, and this file binds a
// real listener for it to the element, the moment the element is in the page
// (at load, and through a MutationObserver for markup drawn later), so a
// handler runs exactly where and when the inline one did.
//
// Nothing here evaluates text as code. A handler is one or more statements
// separated by ";", each of them one of:
//   name(arg, ...)            a function of the page (window[name])
//   return false              preventDefault()
//   event.stopPropagation()
// and an argument is one of: 'text', "text", a number, true, false, null,
// this, this.value, this.checked, event. Anything else is refused and
// logged, and tests/test_csp.py fails on it before it ships.
(function () {
  'use strict';

  // Anything the policy blocks is said in the console, and kept for
  // scripts/check_csp_webview.py to read.
  window.__cspViolations = window.__cspViolations || [];
  document.addEventListener('securitypolicyviolation', (e) => {
    const said = e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sample || '');
    window.__cspViolations.push(said);
    console.warn('Content-Security-Policy blocked:', said);
  });

  const EVENTS = ['click', 'change', 'input', 'keydown', 'submit', 'blur'];
  const IDENT = /^[A-Za-z_$][\w$]*$/;

  function splitTop(text, sep) {
    const parts = [];
    let cur = '', quote = null, depth = 0;
    for (const ch of text) {
      if (quote) {
        cur += ch;
        if (ch === quote) quote = null;
      } else if (ch === "'" || ch === '"') {
        quote = ch; cur += ch;
      } else if (ch === '(') {
        depth++; cur += ch;
      } else if (ch === ')') {
        depth--; cur += ch;
      } else if (ch === sep && depth === 0) {
        parts.push(cur); cur = '';
      } else {
        cur += ch;
      }
    }
    if (quote || depth !== 0) throw new Error('unbalanced: ' + text);
    parts.push(cur);
    return parts.map((p) => p.trim());
  }

  function parseArg(a) {
    if (/^'[^']*'$/.test(a) || /^"[^"]*"$/.test(a)) return { lit: a.slice(1, -1) };
    if (/^-?\d+(\.\d+)?$/.test(a)) return { lit: Number(a) };
    if (a === 'true') return { lit: true };
    if (a === 'false') return { lit: false };
    if (a === 'null') return { lit: null };
    if (a === 'this' || a === 'this.value' || a === 'this.checked' || a === 'event') return { ref: a };
    throw new Error('argument not allowed: ' + a);
  }

  // "a(1, 'x'); return false" -> [{call:'a', args:[...]}, {prevent:true}]
  function parse(text) {
    const steps = [];
    for (const st of splitTop(String(text || ''), ';')) {
      if (!st) continue;
      if (st === 'return false') { steps.push({ prevent: true }); continue; }
      if (st === 'event.stopPropagation()') { steps.push({ stop: true }); continue; }
      const m = /^([A-Za-z_$][\w$]*)\((.*)\)$/s.exec(st);
      if (!m || !IDENT.test(m[1])) throw new Error('statement not allowed: ' + st);
      const inner = m[2].trim();
      const args = inner ? splitTop(inner, ',').map(parseArg) : [];
      steps.push({ call: m[1], args });
    }
    return steps;
  }

  function value(arg, el, ev) {
    if ('lit' in arg) return arg.lit;
    if (arg.ref === 'this') return el;
    if (arg.ref === 'this.value') return el.value;
    if (arg.ref === 'this.checked') return el.checked;
    return ev;
  }

  function run(steps, el, ev) {
    let result;
    for (const s of steps) {
      if (s.prevent) { ev.preventDefault(); result = false; continue; }
      if (s.stop) { ev.stopPropagation(); continue; }
      const fn = window[s.call];
      if (typeof fn !== 'function') {
        console.warn('handler: no function ' + s.call);
        continue;
      }
      fn.apply(window, s.args.map((a) => value(a, el, ev)));
    }
    return result;
  }

  const bound = new WeakMap();   // element -> Set of event types bound

  function bind(el) {
    if (!el || el.nodeType !== 1) return;
    for (const type of EVENTS) {
      const text = el.getAttribute('data-on' + type);
      if (text == null) continue;
      let seen = bound.get(el);
      if (!seen) { seen = new Set(); bound.set(el, seen); }
      if (seen.has(type)) continue;
      seen.add(type);
      let steps;
      try { steps = parse(text); } catch (e) { console.warn('handler refused:', e.message); continue; }
      el.addEventListener(type, (ev) => run(steps, el, ev));
    }
  }

  const SELECTOR = EVENTS.map((t) => '[data-on' + t + ']').join(',');

  function bindTree(root) {
    if (!root || root.nodeType !== 1) return;
    bind(root);
    root.querySelectorAll(SELECTOR).forEach(bind);
  }

  function start() {
    bindTree(document.documentElement);
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === 'attributes') bind(r.target);
        else r.addedNodes.forEach(bindTree);
      }
    }).observe(document.documentElement, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: EVENTS.map((t) => 'data-on' + t),
    });
  }

  window.WaffleHandlers = { parse, bindTree };
  start();
})();
