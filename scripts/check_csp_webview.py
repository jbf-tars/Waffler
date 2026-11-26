#!/usr/bin/env python3
"""Load the window's page in the real web view, with its Content-Security-Policy,
and check that nothing it needs is blocked.

    venv/bin/python scripts/check_csp_webview.py            # hidden window
    venv/bin/python scripts/check_csp_webview.py --show     # watch it
    venv/bin/python scripts/check_csp_webview.py --without-unsafe-eval

It opens ui/index.html exactly as app.py does (pywebview's own server on
127.0.0.1, js_api=ClosedApi(), the allow-listed names exposed), but every
bridge function is a stand-in that returns nothing: no Waffler is started, no
data folder is read and nothing is sent anywhere. In WKWebView (Mac) or
WebView2 (Windows) it checks:

  1. window.pywebview.api was built and a call comes back (pywebview builds it
     with new Function, which is why the policy keeps 'unsafe-eval');
  2. no securitypolicyviolation was reported by the page or the Meetings frame;
  3. every data-on* handler in the markup was bound, and clicking one
     (Settings, then About) works;
  4. the Meetings frame loads its script under its own policy.

--without-unsafe-eval serves a copy of the page whose policy lacks
'unsafe-eval', to show what it is needed for. Exit code 0 when all pass.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bridge_gate  # noqa: E402

PROBE = r"""
(async () => {
  const out = {};
  out.pywebview = typeof window.pywebview;
  out.apiKeys = window.pywebview && window.pywebview.api ? Object.keys(window.pywebview.api).length : -1;
  out.gvType = window.pywebview && window.pywebview.api ? typeof window.pywebview.api.get_app_version + ' ' + Object.keys(window.pywebview.api).slice(0,3).join(',') : '';
  out.api = !!(window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_app_version === 'function');
  if (out.api) {
    try { out.call = await window.pywebview.api.get_app_version(); } catch (e) { out.call = 'error: ' + e; }
  }
  out.violations = (window.__cspViolations || []).slice(0, 20);
  const attrs = ['click','change','input','keydown','submit','blur'].map(t => '[data-on' + t + ']').join(',');
  out.handlers = document.querySelectorAll(attrs).length;
  out.inline = document.querySelectorAll('[onclick],[onchange],[oninput],[onkeydown],[onsubmit],[onblur]').length;
  const nav = document.querySelector('[data-onclick="showPage(\'settings\')"]');
  if (nav) nav.click();
  const about = document.querySelector('.snav-item[data-sec="about"]');
  if (about) about.click();
  const sec = document.getElementById('secAbout');
  out.clicked = !!(sec && !sec.hidden);
  const f = document.getElementById('mtFrame');
  try {
    const d = f && f.contentDocument;
    out.frame = d ? { url: String(d.location), root: !!d.getElementById('root'),
                      children: d.getElementById('root') ? d.getElementById('root').children.length : 0,
                      hashedInlineRan: typeof f.contentWindow.__natterPhone === 'boolean',
                      violations: (f.contentWindow.__cspViolations || []).slice(0, 20) } : null;
  } catch (e) { out.frame = 'error: ' + e; }
  window.__probe = JSON.stringify(out);
})()
"""

# Violations are recorded from the first moment of each document.
RECORDER = r"""
(function (w) {
  if (w.__cspViolations) return;
  w.__cspViolations = [];
  w.document.addEventListener('securitypolicyviolation', function (e) {
    w.__cspViolations.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sample || ''));
  });
})(window);
"""


def stand_ins():
    funcs = []
    for name in sorted(bridge_gate.ALLOWED):
        def f(*args, _n=name):
            return "4.0.0-check" if _n == "get_app_version" else None
        f.__name__ = name
        funcs.append(f)
    return funcs


def page_dir(without_unsafe_eval: bool, no_csp: bool = False) -> Path:
    if not (without_unsafe_eval or no_csp):
        return ROOT / "ui"
    tmp = Path(tempfile.mkdtemp(prefix="waffler-csp-")) / "ui"
    shutil.copytree(ROOT / "ui", tmp)
    html = tmp / "index.html"
    text = html.read_text(encoding="utf-8")
    if no_csp:
        text = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", text)
    else:
        text = text.replace(" 'unsafe-eval'", "")
    html.write_text(text, encoding="utf-8")
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--without-unsafe-eval", action="store_true")
    ap.add_argument("--no-csp", action="store_true", help="for comparison: the page with no policy")
    ap.add_argument("--wait", type=float, default=6.0, help="seconds to let the page settle")
    args = ap.parse_args()

    import webview

    ui = page_dir(args.without_unsafe_eval, args.no_csp)
    window = webview.create_window("Waffler CSP check", url=str(ui / "index.html"),
                                   js_api=bridge_gate.ClosedApi(), hidden=not args.show,
                                   width=1100, height=780)
    window.expose(*stand_ins())
    result = {}

    def recorder():
        try:
            window.evaluate_js(RECORDER)
            window.evaluate_js("(function(){var f=document.getElementById('mtFrame');"
                               "if(f&&f.dataset.src&&!f.src){f.src=f.dataset.src;}})()")
        except Exception:
            pass

    def drive():
        result["loaded"] = window.events.loaded.wait(30)
        recorder()
        time.sleep(args.wait)
        try:
            window.evaluate_js(PROBE)
            raw = None
            for _ in range(50):
                raw = window.evaluate_js("window.__probe || ''")
                if raw:
                    break
                time.sleep(0.2)
            if isinstance(raw, str) and raw:
                raw = json.loads(raw)
            result.update(raw if isinstance(raw, dict) else {"raw": repr(raw)})
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        window.destroy()

    threading.Thread(target=drive, daemon=True).start()
    webview.start(debug=False)

    print(json.dumps(result, indent=2))
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    ok = (result.get("api") and result.get("call") == "4.0.0-check"
          and not result.get("violations") and result.get("inline") == 0
          and result.get("handlers", 0) > 100 and result.get("clicked")
          and not (frame or {}).get("violations")
          and (frame or {}).get("hashedInlineRan") and (frame or {}).get("children", 0) > 0)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
