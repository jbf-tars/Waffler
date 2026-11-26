# ui/meetings-app

The meetings interface shown in the Meetings tab: folders, the list of
meetings, a meeting's notes and transcript, Ask. It is a BUILD, not source.

Source: the `ui/` folder of the natter-notes repository (React + Vite).
To rebuild: in natter-notes, `cd ui && npm run build`, then replace this
folder's contents with `ui/dist/` (keep this README).

Inside Waffler the page has no web server to talk to: each call goes through
`window.parent.pywebview.api.meetings_api` (app.py) to `src/meetings_bridge.py`.

Inside Waffler the page wears Waffler's look, so Home, Meetings and Settings
are one app: `ui/meetings.js` hands it every token of `ui/tokens.css` as the
window has resolved it (`window.wafflerTokens()`, and a `meetings-tokens`
message whenever the theme changes) and the address of `tokens.css` for the
font files; the source's `ui/src/waffler.ts` sets them on `<body>` and
`ui/src/waffler.css` draws every part in those names. A token added to
`tokens.css` is added to the list in `meetings.js` (tests/test_one_look.py
keeps the two the same).

After a rebuild, put back the `Content-Security-Policy` meta in this folder's
`index.html` (copy it from the previous version). Its `script-src` names the
sha256 of the page's one inline script; when that script changes,
tests/test_csp.py fails and says so, and the new hash is
`base64(sha256(<the text between <script> and </script>>))`. The policy lets
the page load only its own files: it calls Python through the parent window.
