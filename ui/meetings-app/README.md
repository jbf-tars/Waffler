# ui/meetings-app

The meetings interface shown in the Meetings tab: folders, the list of
meetings, a meeting's notes and transcript, Ask. It is a BUILD, not source.

Source: the `ui/` folder of the natter-notes repository (React + Vite).
To rebuild: in natter-notes, `cd ui && npm run build`, then replace this
folder's contents with `ui/dist/` (keep this README).

Inside Waffler the page has no web server to talk to: each call goes through
`window.parent.pywebview.api.meetings_api` (app.py) to `src/meetings_bridge.py`.
