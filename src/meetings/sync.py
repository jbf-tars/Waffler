"""Sync between a device and a hub.

The hub is the same app running headless on a machine that is always on
(`python -m natter hub`). A device pulls what changed on the hub since its
last sync, then pushes what changed locally. Notes merge last-write-wins,
transcript segments by id, so running it twice or from two devices is safe.
The hub token is kept in the credential store under "hub".
"""
from __future__ import annotations

from . import keys
from .store import Store

keys.PROVIDERS.setdefault("hub", "NATTER_HUB_TOKEN")


class SyncError(Exception):
    pass


def sync_with_hub(store: Store, client=None) -> dict:
    """`client` is an httpx.Client-like object (tests pass one bound to an app)."""
    settings = store.settings()
    url = (settings.get("hub_url") or "").rstrip("/")
    token = keys.get_key("hub")
    if client is None:
        if not url or not token:
            raise SyncError("Set the hub address and token in Settings first.")
        import httpx
        client = httpx.Client(base_url=url, timeout=60.0)
    headers = {"Authorization": f"Bearer {token}"}
    cursors = settings.get("sync_cursors") or {}
    try:
        r = client.get("/api/sync/changes", params={"since": cursors.get("pulled", "")},
                       headers=headers)
        r.raise_for_status()
        remote = r.json()
        local = store.export_changes(cursors.get("pushed", ""))   # before importing: only our own edits
        pulled = store.import_changes(remote)["applied"]
        r = client.post("/api/sync/push", json=local, headers=headers)
        r.raise_for_status()
    except SyncError:
        raise
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 401:
            raise SyncError("The hub refused the token.") from None
        raise SyncError(f"Couldn't reach the hub ({type(e).__name__}).") from None
    # Store the cursors outside DEFAULT_SETTINGS filtering: this is internal state.
    store.set_settings({"sync_cursors": {"pulled": remote["now"], "pushed": local["now"]}})
    return {"pulled": pulled, "pushed": len(local["notes"])}
