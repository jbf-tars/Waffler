"""A stand-in for the account service, for the tests: no network.

It answers the routes src/account.py and the sync client use, in the shapes
the service's own reference gives (sign-in by emailed code, devices, the
Claude connector link, and sync with its opaque cursor, paging and push size
limit). `transport()` is an httpx transport: every request the client makes
arrives at `handle`, and is kept in `requests` for the tests to read.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

import httpx


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Seen:
    """One request as the service saw it."""

    def __init__(self, request: httpx.Request):
        self.method = request.method
        self.path = request.url.path
        self.query = dict(request.url.params)
        self.auth = request.headers.get("authorization", "")
        self.body = request.content or b""

    def __repr__(self):
        return f"<{self.method} {self.path} {len(self.body)}b>"


class FakeAccountServer:
    def __init__(self, page_rows: int = 200, max_push_bytes: int = 16 * 1024 * 1024):
        self.page_rows = page_rows
        self.max_push_bytes = max_push_bytes
        self.mailbox: dict[str, str] = {}        # email -> the code "sent"
        self.users: dict[str, str] = {}          # email -> user id
        self.user_created: dict[str, str] = {}   # user id -> when the account was made
        self.sessions: dict[str, dict] = {}      # token -> {"user", "email", "device"}
        self.connectors: dict[str, dict] = {}    # user id -> {"token", "created_at"}
        self.rows: dict[str, dict] = {}          # user id -> {(kind, id): (seq, row)}
        self.seq: dict[str, int] = {}
        self.requests: list[Seen] = []
        self.offline = False                     # every request fails to connect
        self.too_many = False                    # 429 on the sign-in routes
        self.sync_too_many = False               # 429 on sync
        self.over_allowance = False              # 402 on a push
        self.disabled: set[str] = set()          # emails of disabled accounts
        self.signups_closed = False              # 403 signups_closed for an address with no account
        self.email_off: int | None = None        # 503 on start; the number is Retry-After (0: none sent)
        self.server_full = False                 # 507 server_full on a push
        self.model_calls_cap = False             # 402 model_calls_cap on a signed request
        self.deleted: list[str] = []             # emails of accounts that were deleted
        self.fail_pushes_after: int | None = None   # connection lost after this many pushes
        self.pushes = 0                          # pushes accepted
        self.accepted_sizes: list[int] = []      # ... and how large each was
        self.too_large = 0                       # pushes refused for their size
        self.hold = None                         # a threading.Event: sync waits on it

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _json(status: int, body: dict, headers: dict | None = None) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    def _refuse(self, status: int, detail: str, code: str = "", headers=None) -> httpx.Response:
        body = {"detail": detail}
        if code:
            body["code"] = code
        return self._json(status, body, headers)

    def _who(self, request: httpx.Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return token, self.sessions.get(token)

    def _device_view(self, device: dict, current: bool) -> dict:
        return {**device, "signed_in_with": "email", "current": current}

    def devices_of(self, user: str) -> list[dict]:
        return [s["device"] for s in self.sessions.values() if s["user"] == user]

    def tokens_of(self, email: str) -> list[str]:
        return [t for t, s in self.sessions.items() if s["email"] == email]

    def notes_of(self, email: str) -> dict:
        user = self.users.get(email, "")
        return {key[1]: row for key, (_, row) in self.rows.get(user, {}).items() if key[0] == "notes"}

    def calls(self, method: str, path: str) -> list[Seen]:
        return [r for r in self.requests if r.method == method and r.path == path]

    # ── the routes ──────────────────────────────────────────────────────────
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(Seen(request))
        if self.offline:
            raise httpx.ConnectError("no route to host", request=request)
        method, path = request.method, request.url.path
        body = {}
        if request.content and method in ("POST", "PUT", "PATCH"):
            body = json.loads(request.content)

        if path == "/auth/email/start" and method == "POST":
            if self.too_many:
                return self._refuse(429, "Too many codes were asked for.", headers={"Retry-After": "600"})
            email = str(body.get("email", "")).strip().lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                return self._refuse(400, "That is not an email address.")
            if self.email_off is not None:
                return self._refuse(503, "The server has sent as many sign-in emails as it may for now. "
                                         "Try again later.",
                                    headers={"Retry-After": str(self.email_off)} if self.email_off else None)
            self.mailbox[email] = f"{secrets.randbelow(10 ** 6):06d}"
            return self._json(200, {"ok": True, "expires_in": 600})

        if path == "/auth/email/verify" and method == "POST":
            if self.too_many:
                return self._refuse(429, "Too many sign-in requests.", headers={"Retry-After": "60"})
            email = str(body.get("email", "")).strip().lower()
            code = re.sub(r"[\s-]", "", str(body.get("code", "")))
            if email in self.disabled:
                return self._refuse(403, "This account is disabled.", "account_disabled")
            if not code or self.mailbox.get(email) != code:
                return self._refuse(401, "That code is wrong or has expired.")
            del self.mailbox[email]                          # a code works once
            if email not in self.users and self.signups_closed:
                return self._refuse(403, "This server is not taking new accounts at the moment.",
                                    "signups_closed")
            user = self.users.setdefault(email, secrets.token_hex(16))
            made = self.user_created.setdefault(user, _now())
            token = "natter_" + secrets.token_urlsafe(32)
            device = {"id": secrets.token_hex(8), "name": str(body.get("device_name") or "A device")[:80],
                      "created_at": _now(), "last_used_at": _now()}
            self.sessions[token] = {"user": user, "email": email, "device": device}
            return self._json(200, {"token": token,
                                    "user": {"id": user, "email": email, "created_at": made},
                                    "device": self._device_view(device, True)})

        token, session = self._who(request)
        if session is None:
            return self._refuse(401, "Sign in again.")
        if session["email"] in self.disabled:
            return self._refuse(403, "This account is disabled.", "account_disabled")
        user = session["user"]

        if self.model_calls_cap:
            return self._refuse(402, "Today's questions and rewrites are used up. The count starts again "
                                     "tomorrow (midnight UTC).", "model_calls_cap")

        if path == "/auth/logout" and method == "POST":
            del self.sessions[token]
            return self._json(200, {"ok": True})

        if path == "/account" and method == "DELETE":        # every device, the link, the notes, the address
            for t, s in list(self.sessions.items()):
                if s["user"] == user:
                    del self.sessions[t]
            self.connectors.pop(user, None)
            self.rows.pop(user, None)
            self.users.pop(session["email"], None)
            self.user_created.pop(user, None)
            self.deleted.append(session["email"])
            return self._json(200, {"deleted": True})

        if path == "/account/devices" and method == "GET":
            return self._json(200, {"devices": [
                self._device_view(s["device"], s is session)
                for s in self.sessions.values() if s["user"] == user]})

        m = re.fullmatch(r"/account/devices/([^/]+)", path)
        if m and method == "DELETE":
            for t, s in list(self.sessions.items()):
                if s["user"] == user and s["device"]["id"] == m.group(1):
                    del self.sessions[t]
                    return self._json(200, {"ok": True})
            return self._refuse(404, "No such device.")

        if path == "/account/connector":
            have = self.connectors.get(user)
            if method == "GET":
                return self._json(200, {"exists": bool(have), "created_at": have and have["created_at"],
                                        "last_used_at": None})
            if method == "POST":
                ctoken = "natterc_" + secrets.token_urlsafe(32)
                self.connectors[user] = {"token": ctoken, "created_at": _now()}
                base = f"{request.url.scheme}://{request.url.netloc.decode()}"
                return self._json(200, {"exists": True, "created_at": _now(), "last_used_at": None,
                                        "token": ctoken, "path": f"/connect/{ctoken}/mcp",
                                        "url": f"{base}/connect/{ctoken}/mcp"})
            if method == "DELETE":
                self.connectors.pop(user, None)
                return self._json(200, {"exists": False, "created_at": None, "last_used_at": None})

        if path == "/api/sync/changes" and method == "GET":
            if self.hold is not None:
                self.hold.wait(10)
            if self.sync_too_many:
                return self._refuse(429, "Too many requests.", headers={"Retry-After": "30"})
            return self._json(200, self._changes(user, request.url.params.get("since", "")))

        if path == "/api/sync/push" and method == "POST":
            if self.sync_too_many:
                return self._refuse(429, "Too many requests.", headers={"Retry-After": "30"})
            if self.fail_pushes_after is not None and self.pushes >= self.fail_pushes_after:
                raise httpx.ReadError("connection lost", request=request)
            if len(request.content) > self.max_push_bytes:
                self.too_large += 1
                return self._refuse(413, "That is too much for one sync. Send fewer notes at a time.")
            if self.server_full:
                return self._refuse(507, "The server is full at the moment. Try again later.", "server_full",
                                    headers={"Retry-After": "3600"})
            if self.over_allowance:
                return self._refuse(402, "Your account's storage is full. Delete some notes to make room.",
                                    "storage_cap")
            self.pushes += 1
            self.accepted_sizes.append(len(request.content))
            return self._json(200, {"applied": self._merge(user, body)})

        return self._refuse(404, "Not found.")

    # ── sync: a sequence per user, an opaque cursor, pages ──────────────────
    def _changes(self, user: str, since: str) -> dict:
        tag = user[:8]
        after = 0
        m = re.fullmatch(rf"{tag}-(\d+)", since or "")
        if m:
            after = int(m.group(1))                          # any other cursor: from the beginning
        rows = sorted(((seq, key, row) for key, (seq, row) in self.rows.get(user, {}).items()
                       if seq > after), key=lambda r: r[0])
        page, rest = rows[:self.page_rows], rows[self.page_rows:]
        out = {"notes": [], "folders": [], "templates": []}
        for _, key, row in page:
            out[key[0]].append(row)
        out["now"] = f"{tag}-{page[-1][0] if page else after}"
        out["more"] = bool(rest)
        return out

    def _merge(self, user: str, changes: dict) -> int:
        rows = self.rows.setdefault(user, {})
        applied = 0
        for kind in ("notes", "folders", "templates"):
            for row in changes.get(kind, []):
                key = (kind, row["id"])
                have = rows.get(key)
                merged = dict(row)
                if have is not None:
                    old = have[1]
                    if kind == "notes":                      # segments from both sides, by id
                        seen = {s["id"] for s in row.get("segments", [])}
                        extra = [s for s in old.get("segments", []) if s["id"] not in seen]
                        if row["updated_at"] <= old["updated_at"]:
                            merged = dict(old)
                            merged["segments"] = old.get("segments", []) + [
                                s for s in row.get("segments", [])
                                if s["id"] not in {x["id"] for x in old.get("segments", [])}]
                            if merged == old:
                                continue                     # nothing new: no new sequence number
                        else:
                            merged["segments"] = row.get("segments", []) + extra
                    elif row["updated_at"] <= old["updated_at"]:
                        continue
                self.seq[user] = self.seq.get(user, 0) + 1
                rows[key] = (self.seq[user], merged)
                if kind == "notes":
                    applied += 1
        return applied
