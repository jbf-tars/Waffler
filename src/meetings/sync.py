"""Sync between a device and a hub, or a device and an account.

The hub is the same app running headless on a machine that is always on
(`python -m natter hub`). A device pulls what changed on the hub since its
last sync, then pushes what changed locally. Notes merge last-write-wins,
transcript segments by id, so running it twice or from two devices is safe.
The hub token is kept in the credential store under "hub".

An account (Waffler's src/account.py) speaks the same protocol: the token is
this device's session token and the address is the account service's. Two
things an account needs, which a hub does not mind:

* the service answers a pull in pages (`more`): keep asking until it is done;
* the service refuses a push over 16 MB: a large first sync goes in batches.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta

from . import keys
from .store import Store, now_iso

keys.PROVIDERS.setdefault("hub", "NATTER_HUB_TOKEN")

PUSH_BYTES = 4 * 1024 * 1024      # one push stays well under the service's 16 MB
MAX_PAGES = 2000                  # a pull that never ends is stopped here


class SyncError(Exception):
    """`status` is the HTTP status when the other side answered (None when it
    could not be reached), `detail` and `code` what it said, if anything."""

    def __init__(self, message: str, status: int | None = None, detail: str = "", code: str = ""):
        super().__init__(message)
        self.status, self.detail, self.code = status, detail, code


def _just_before(iso: str) -> str:
    """One millisecond earlier, in the same spelling. What was pushed is
    remembered as "everything up to just before this round": a note edited in
    the very millisecond the round read the store is then sent again next
    time (which changes nothing) instead of never."""
    return (datetime.fromisoformat(iso) - timedelta(milliseconds=1)).isoformat(timespec="milliseconds")


def _size(row) -> int:
    return len(json.dumps(row).encode("utf-8"))


def batches(local: dict, limit: int = PUSH_BYTES) -> list[tuple[dict, str]]:
    """What to push, cut into pieces of at most `limit` bytes (a single note
    larger than that goes alone). Each piece comes with the time up to which
    everything has been sent once it is accepted, or "" when that cannot be
    said yet (the next piece starts with a note edited at the same instant).
    Notes are in the order they were edited, so a sync cut short carries on
    from where it stopped."""
    batch = {"notes": [], "folders": list(local.get("folders", [])),
             "templates": list(local.get("templates", []))}
    size = _size(batch["folders"]) + _size(batch["templates"])
    out = []
    for note in local.get("notes", []):
        n = _size(note)
        if (batch["notes"] or batch["folders"] or batch["templates"]) and size + n > limit:
            out.append(batch)
            batch, size = {"notes": [], "folders": [], "templates": []}, 0
        batch["notes"].append(note)
        size += n
    if batch["notes"] or batch["folders"] or batch["templates"]:
        out.append(batch)
    cut = []
    for i, b in enumerate(out):
        last = b["notes"][-1]["updated_at"] if b["notes"] else ""
        following = out[i + 1]["notes"][0]["updated_at"] if i + 1 < len(out) else None
        cut.append((b, last if following is not None and last and last < following else ""))
    return cut


def _push(client, headers: dict, batch: dict) -> None:
    """One push. If the other side says it is too large and there is more
    than one note in it, it is sent as two halves."""
    r = client.post("/api/sync/push", json=batch, headers=headers)
    if r.status_code == 413 and len(batch["notes"]) > 1:
        half = len(batch["notes"]) // 2
        _push(client, headers, {**batch, "notes": batch["notes"][:half]})
        _push(client, headers, {"notes": batch["notes"][half:], "folders": [], "templates": []})
        return
    r.raise_for_status()


def sync_with_hub(store: Store, client=None, token: str | None = None, *, url: str | None = None,
                  cursors_key: str = "sync_cursors", scope: str = "",
                  push_bytes: int = PUSH_BYTES) -> dict:
    """`client` is an httpx.Client-like object (tests pass one bound to an app).

    `token` is what the hub is shown: by default the hub token kept in the
    credential store. For a hosted account it is this device's session token
    (from signing in), and the "hub address" is the service's address; the
    protocol is otherwise the same.

    An account keeps its own cursors (`cursors_key`), apart from a hub's, and
    names itself in `scope`: cursors left by another account are not used."""
    settings = store.settings()
    url = (url or settings.get("hub_url") or "").rstrip("/")
    token = token or keys.get_key("hub")
    made = client is None
    if client is None:
        if not url or not token:
            raise SyncError("Set the hub address and token in Settings first.")
        import httpx
        client = httpx.Client(base_url=url, timeout=60.0)
    headers = {"Authorization": f"Bearer {token}"}
    cursors = settings.get(cursors_key) or {}
    if scope and cursors.get("scope") != scope:
        cursors = {}                                  # another account's cursors mean nothing here
    pulled_to, pushed_to = cursors.get("pulled", ""), cursors.get("pushed", "")

    def remember() -> None:
        # Stored outside DEFAULT_SETTINGS filtering: this is internal state.
        store.set_settings({cursors_key: {"pulled": pulled_to, "pushed": pushed_to,
                                          **({"scope": scope} if scope else {})}})
    pulled = pushed = 0
    try:
        local, taken = None, ""
        for _ in range(MAX_PAGES):
            r = client.get("/api/sync/changes", params={"since": pulled_to}, headers=headers)
            r.raise_for_status()
            remote = r.json()
            if local is None:                         # before importing: only our own edits
                taken = _just_before(now_iso())       # read first: an edit made meanwhile is sent twice, not missed
                local = store.export_changes(pushed_to)
            pulled += store.import_changes(remote)["applied"]
            stuck = remote.get("now", pulled_to) == pulled_to
            pulled_to = remote.get("now", pulled_to)
            remember()
            if not remote.get("more") or stuck:       # a hub never says `more`
                break
        for batch, sent_up_to in batches(local, push_bytes):
            _push(client, headers, batch)
            pushed += len(batch["notes"])
            if sent_up_to and sent_up_to < taken:     # this far is safely there, whatever happens next
                pushed_to = sent_up_to
                remember()
        pushed_to = taken
        remember()
    except SyncError:
        raise
    except Exception as e:  # noqa: BLE001
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        detail = code = ""
        with contextlib.suppress(Exception):
            body = response.json()
            if isinstance(body.get("detail"), str):
                detail = body["detail"]
            if isinstance(body.get("code"), str):
                code = body["code"]
        if status == 401:
            raise SyncError("The hub refused the token.", status, detail, code) from None
        if status in (400, 402, 403, 413) and detail:   # a hosted account says why in a plain sentence
            raise SyncError(detail, status, detail, code) from None
        raise SyncError(f"Couldn't reach the hub ({type(e).__name__}).", status, detail, code) from None
    finally:
        if made:
            with contextlib.suppress(Exception):
                client.close()
    return {"pulled": pulled, "pushed": pushed}
