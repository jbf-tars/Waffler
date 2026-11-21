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

What is pulled is checked before it is applied (`valid_rows`): the other side
is trusted to hold the meetings, not to be right. A row with an id that is
not an id, a time more than a day in the future (it would win every merge
from then on), or a text field larger than any real one is skipped and
counted, never applied; an answer larger than any real page stops the round.
"""
from __future__ import annotations

import contextlib
import json
import math
from datetime import datetime, timedelta, timezone

from . import keys
from .paths import safe_note_id
from .store import Store, now_iso

keys.PROVIDERS.setdefault("hub", "NATTER_HUB_TOKEN")

PUSH_BYTES = 4 * 1024 * 1024      # one push stays well under the service's 16 MB
MAX_PAGES = 2000                  # a pull that never ends is stopped here
MAX_AHEAD = timedelta(hours=24)   # a pulled row's times: no later than this from now
MAX_TEXT = 4_000_000              # characters in one pulled text field (notes, a summary, a transcript line)
MAX_NAME = 2_000                  # ... and in a title, a name or a word such as the status
MAX_PARTS = 100_000               # sessions or transcript lines in one pulled note
MAX_PAGE_ROWS = 5_000             # notes, folders and templates in one answer (the service sends 200)
MAX_PAGE_BYTES = 512 * 1024 * 1024
PAGE_TOO_LARGE = "page_too_large"  # SyncError.code when an answer is over those two
KINDS = ("notes", "folders", "templates")


class SyncError(Exception):
    """`status` is the HTTP status when the other side answered (None when it
    could not be reached), `detail` and `code` what it said, if anything."""

    def __init__(self, message: str, status: int | None = None, detail: str = "", code: str = ""):
        super().__init__(message)
        self.status, self.detail, self.code = status, detail, code


# ── what is pulled is checked before it is applied ──────────────────────────

def _id_ok(value) -> bool:
    """The pattern an id already has to match to name a note's audio folder."""
    try:
        safe_note_id(value)
        return True
    except ValueError:
        return False


def _when_ok(value, latest: datetime, needed: bool = True) -> bool:
    """A time that can be read and is not after `latest`."""
    if value is None or value == "":
        return not needed
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= latest


def _text_ok(row: dict, limit: int, *fields) -> bool:
    """Each of these is text, and no longer than `limit`."""
    return all(isinstance(row.get(f), str) and len(row[f]) <= limit for f in fields)


def _number_ok(value, needed: bool = True) -> bool:
    if value is None:
        return not needed
    return isinstance(value, (int, float)) and math.isfinite(value)


def _note_ok(n, latest: datetime) -> bool:
    if not isinstance(n, dict) or not _id_ok(n.get("id")):
        return False
    if not _when_ok(n.get("updated_at"), latest) or not _when_ok(n.get("created_at"), latest):
        return False
    if not _id_ok(n.get("template_id")) or (n.get("folder_id") is not None and not _id_ok(n["folder_id"])):
        return False
    if not _text_ok(n, MAX_NAME, "title", "status") or not _text_ok(n, MAX_TEXT, "my_notes", "summary", "error"):
        return False
    if not all(_number_ok(n.get(k)) for k in ("duration_s", "title_locked", "deleted")):
        return False
    sessions, segments = n.get("sessions", []), n.get("segments", [])
    if not isinstance(sessions, list) or not isinstance(segments, list) \
            or len(sessions) > MAX_PARTS or len(segments) > MAX_PARTS:
        return False
    for s in sessions:                                # each belongs to this note and no other
        if not isinstance(s, dict) or not _id_ok(s.get("id")) or s.get("note_id") != n["id"] \
                or not _when_ok(s.get("started_at"), latest) \
                or not _when_ok(s.get("ended_at"), latest, needed=False) \
                or not _number_ok(s.get("n")) or not _number_ok(s.get("offset_s")):
            return False
    for s in segments:
        if not isinstance(s, dict) or not _id_ok(s.get("id")) or s.get("note_id", n["id"]) != n["id"] \
                or not _text_ok(s, MAX_TEXT, "text") or not _text_ok({"source": "mic", **s}, MAX_NAME, "source") \
                or not _when_ok(s.get("created_at"), latest, needed=False) \
                or not _number_ok(s.get("start_s")) or not _number_ok(s.get("end_s")) \
                or not _number_ok(s.get("session_n"), needed=False):
            return False
    return True


def _named_ok(row, latest: datetime, *long_text) -> bool:
    """A folder or a template."""
    return isinstance(row, dict) and _id_ok(row.get("id")) and _when_ok(row.get("updated_at"), latest) \
        and _text_ok(row, MAX_NAME, "name") and _text_ok(row, MAX_TEXT, *long_text) \
        and _number_ok(row.get("deleted"), needed=False)


def valid_rows(remote, size: int = 0, now: datetime | None = None) -> tuple[dict, int]:
    """One answer to a pull, as it may be applied: (the rows that passed,
    how many were skipped). Raises SyncError (code PAGE_TOO_LARGE) for an
    answer that is not an object or is larger than any real page: then
    nothing in it is applied and the round stops where it was."""
    if not isinstance(remote, dict):
        raise SyncError("The other side's answer could not be read.")
    rows = {k: remote.get(k) if isinstance(remote.get(k), list) else [] for k in KINDS}
    if size > MAX_PAGE_BYTES or sum(len(v) for v in rows.values()) > MAX_PAGE_ROWS:
        raise SyncError("The other side sent more than fits in one answer.", code=PAGE_TOO_LARGE)
    latest = (now or datetime.now(timezone.utc)) + MAX_AHEAD
    clean = {"notes": [n for n in rows["notes"] if _note_ok(n, latest)],
             "folders": [f for f in rows["folders"] if _named_ok(f, latest)],
             "templates": [t for t in rows["templates"] if _named_ok(t, latest, "prompt")]}
    return clean, sum(len(rows[k]) - len(clean[k]) for k in KINDS)


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
    pulled = pushed = skipped = 0
    try:
        local, taken = None, ""
        for _ in range(MAX_PAGES):
            r = client.get("/api/sync/changes", params={"since": pulled_to}, headers=headers)
            r.raise_for_status()
            remote = r.json()
            rows, bad = valid_rows(remote, len(getattr(r, "content", b"") or b""))
            skipped += bad                            # never applied; the count goes to the log
            if local is None:                         # before importing: only our own edits
                taken = _just_before(now_iso())       # read first: an edit made meanwhile is sent twice, not missed
                local = store.export_changes(pushed_to)
            pulled += store.import_changes(rows)["applied"]
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
            for key in ("code", "error"):             # the service's word for it: "error" from a newer one
                if isinstance(body.get(key), str):
                    code = body[key]
        if status == 401:
            raise SyncError("The hub refused the token.", status, detail, code) from None
        if status in (400, 402, 403, 413) and detail:   # a hosted account says why in a plain sentence
            raise SyncError(detail, status, detail, code) from None
        raise SyncError(f"Couldn't reach the hub ({type(e).__name__}).", status, detail, code) from None
    finally:
        if made:
            with contextlib.suppress(Exception):
                client.close()
    return {"pulled": pulled, "pushed": pushed, "skipped": skipped}
