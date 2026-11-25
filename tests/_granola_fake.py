"""A pretend Granola on 127.0.0.1, for tests/test_granola_import_*.py.

It is the parts of Granola an import touches: the two documents that say where
to sign in, registering a client, the approval page (which sends the browser
straight back), the token endpoint (with PKCE checked), revoking, and the MCP
endpoint with its three tools. Answers are shaped as the real one's were on
25 November 2026. Nothing here leaves the computer.
"""
import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.sax.saxutils import quoteattr


def meeting(n, **changes):
    """A meeting as the pretend Granola holds it."""
    out = {"id": f"00000000-0000-4000-8000-{n:012d}", "title": f"Meeting {n}",
           "meeting_at": f"2026-10-{1 + n % 28:02d}T09:30:00+01:00", "updated_at": "2026-10-30T10:00:00Z",
           "folders": [], "private_notes": "", "summary": f"### Notes {n}\n- Point {n}",
           "transcript": f"[09:30:00] Microphone: Hello from meeting {n}.\n[09:30:04] System audio: Hello back.",
           "status": ""}
    out.update(changes)
    return out


class FakeGranola:
    def __init__(self, meetings=None, *, sse=False, page=100, transcript_page=None):
        self.meetings = list(meetings or [])
        self.sse, self.page, self.transcript_page = sse, page, transcript_page
        self.access = set()               # access tokens that still work
        self.refresh = set()
        self.codes = {}                   # code -> (challenge, client_id, redirect_uri)
        self.clients = {}
        self.calls = []                   # (tool, arguments) in order
        self.requests = []                # (method, path) in order
        self.revoked = []
        self.rate_limit = {}              # tool -> how many more times it says rate_limit
        self.http_429 = 0                 # how many more MCP posts answer 429
        self.broken = set()               # meeting ids get_meeting_transcript always fails for
        self.omit = set()                 # meeting ids get_meetings never returns
        self.omit_once = set()            # ... leaves out the first time (an oversized batch)
        self.send_iss = True
        self.wrong_state = False
        self.refuse_scope = ""            # a scope the approval page sends back as invalid_scope
        self.metadata = {}                # overrides for the sign-in document
        self.sessions = set()
        self.secrets = []                 # every token and code ever issued
        self.on_call = None               # called with (tool, arguments) before each answer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.resource = self.base + "/mcp"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ── the browser: follow the approval page's redirect back to Waffler ────
    def browser(self, address):
        """What webbrowser.open would start: the person approves at once."""
        self.opened = address

        def go():
            opener = urllib.request.build_opener(_Stay)
            try:
                opener.open(address, timeout=10)
            except urllib.error.HTTPError as e:
                back = e.headers.get("location")
                if back:
                    try:
                        urllib.request.urlopen(back, timeout=10).read()
                    except Exception:  # noqa: BLE001
                        pass
        threading.Thread(target=go, daemon=True).start()
        return True

    def _issue(self, kind):
        token = f"{kind}-{secrets.token_hex(12)}"
        self.secrets.append(token)
        return token

    def expire_access(self):
        self.access.clear()

    # ── what the tools say ─────────────────────────────────────────────────
    def _listing(self, args):
        live = self.meetings
        start = int(args.get("cursor") or 0)
        page = live[start:start + self.page]
        more = start + self.page < len(live)
        out = [f'<pagination has_more="{str(more).lower()}" total_count="{len(live)}" '
               f'completion_status="{"more_available" if more else "complete"}"'
               + (f' next_cursor="{start + self.page}"' if more else "") + " />",
               f'<meetings_data from="2015-01-01" count="{len(page)}">']
        for m in page:
            extra = ' status="empty"' if m["status"] == "empty" else ""
            out.append(f'<meeting id="{m["id"]}" title={quoteattr(m["title"])} meeting_at="{m["meeting_at"]}" '
                       f'created_at="{m["meeting_at"]}" updated_at="{m["updated_at"]}"{extra}>')
            if m["folders"]:
                out.append("<folders>" + "".join(f'<folder id="f-{f.lower()}" title={quoteattr(f)} />'
                                                 for f in m["folders"]) + "</folders>")
            else:
                out.append("<folders />")
            out.append("<note_headers>Notes</note_headers>\n</meeting>")
        out.append("</meetings_data>")
        if page:
            out.append(f'<duplicate_recordings><meeting id="{page[0]["id"]}" other_ids="dup-1,dup-2" />'
                       "</duplicate_recordings>")
        return "\n".join(out)

    def _details(self, args):
        out = ["<meetings_data>"]
        left_out = []
        for mid in args.get("meeting_ids", []):
            m = next((x for x in self.meetings if x["id"] == mid), None)
            if m is None or mid in self.omit:
                continue
            if mid in self.omit_once:
                self.omit_once.discard(mid)
                left_out.append(mid)
                continue
            out.append(f'<meeting id="{m["id"]}" title={quoteattr(m["title"])} meeting_at="{m["meeting_at"]}" '
                       f'updated_at="{m["updated_at"]}" url="https://notes.granola.ai/d/{m["id"]}">')
            out.append("<known_participants>Ada from Example</known_participants>")
            out.append("<folders>" + "".join(f'<folder id="f" title={quoteattr(f)} />' for f in m["folders"])
                       + "</folders>" if m["folders"] else "<folders />")
            out.append(f'<private_notes>{m["private_notes"]}</private_notes>' if m["private_notes"]
                       else '<private_notes status="empty" />')
            out.append('<transcript available="true">Use get_meeting_transcript to read it.</transcript>')
            out.append(f'<summary>{m["summary"]}</summary>')    # as written: Markdown, not escaped
            out.append("</meeting>")
        out.append("</meetings_data>")
        if left_out:
            out.append(f'<omitted_meetings count="{len(left_out)}">{", ".join(left_out)}</omitted_meetings>')
        return "\n".join(out)

    def _transcript(self, args):
        mid = args.get("meeting_id")
        if mid in self.broken:
            raise _ToolError("Something went wrong reading that transcript.")
        m = next((x for x in self.meetings if x["id"] == mid), None)
        if m is None:
            raise _ToolError("Meeting not found")
        text = m["transcript"]
        if text is None:
            return json.dumps({"transcript": None, "transcript_unavailable": {"reason": m.get("why", "no_transcript")}})
        size = self.transcript_page or int(args.get("max_chars") or 40000)
        offset = int(args.get("offset") or 0)
        page = text[offset:offset + size]
        end = offset + len(page)
        skip = 0
        if end < len(text) and "\n" in page:
            page = page[:page.rindex("\n")]            # a page ends at a whole line, without its new line
            end, skip = offset + len(page), 1
        out = {"truncated": end < len(text), "total_chars": len(text), "meeting_at": m["meeting_at"],
               "transcript": page}
        if end < len(text):
            out["next_offset"] = end + skip
        return json.dumps(out)

    def _tool(self, name, args):
        self.calls.append((name, args))
        if self.on_call:
            self.on_call(name, args)
        if self.rate_limit.get(name, 0) > 0:
            self.rate_limit[name] -= 1
            raise _ToolError("rate_limit: too many calls at once. Retry in 10 ms, one at a time.")
        if name == "search_meetings":
            return self._listing(args)
        if name == "get_meetings":
            return self._details(args)
        if name == "get_meeting_transcript":
            return self._transcript(args)
        raise _ToolError(f"Unknown tool {name}")

    # ── HTTP ───────────────────────────────────────────────────────────────
    def _handler(fake):  # noqa: N805
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None, kind="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if body:
                    self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("content-length") or 0))

            def do_GET(self):  # noqa: N802
                parts = urlsplit(self.path)
                fake.requests.append(("GET", parts.path))
                if parts.path == "/.well-known/oauth-protected-resource":
                    return self._send(200, {"resource": fake.resource, "authorization_servers": [fake.base],
                                            "scopes_supported": ["mcp"]})
                if parts.path == "/.well-known/oauth-authorization-server":
                    return self._send(200, {
                        "issuer": fake.base, "authorization_endpoint": fake.base + "/oauth2/authorize",
                        "token_endpoint": fake.base + "/oauth2/token",
                        "registration_endpoint": fake.base + "/oauth2/register",
                        "revocation_endpoint": fake.base + "/oauth2/revoke",
                        "code_challenge_methods_supported": ["S256"],
                        "grant_types_supported": ["authorization_code", "refresh_token"],
                        "token_endpoint_auth_methods_supported": ["none"],
                        "scopes_supported": ["email", "offline_access", "openid", "profile"],
                        "authorization_response_iss_parameter_supported": True, **fake.metadata})
                if parts.path == "/oauth2/authorize":
                    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                    fake.authorize = q
                    client = fake.clients.get(q.get("client_id"))
                    if fake.refuse_scope and fake.refuse_scope in q.get("scope", "").split():
                        return self._send(302, headers={"location": q["redirect_uri"] + "?" + urlencode(
                            {"error": "invalid_scope", "state": q.get("state", "")})})
                    if not client or q.get("redirect_uri") not in client["redirect_uris"] \
                            or q.get("code_challenge_method") != "S256" or q.get("response_type") != "code" \
                            or q.get("resource") != fake.resource:
                        return self._send(400, {"error": "invalid_request"})
                    code = fake._issue("code")
                    fake.codes[code] = (q["code_challenge"], q["client_id"], q["redirect_uri"])
                    back = {"code": code, "state": "not-the-state" if fake.wrong_state else q.get("state", "")}
                    if fake.send_iss:
                        back["iss"] = fake.send_iss if isinstance(fake.send_iss, str) else fake.base
                    return self._send(302, headers={"location": q["redirect_uri"] + "?" + urlencode(back)})
                return self._send(404)

            def do_POST(self):  # noqa: N802
                parts = urlsplit(self.path)
                fake.requests.append(("POST", parts.path))
                raw = self._body()
                if parts.path == "/oauth2/register":
                    asked = json.loads(raw)
                    fake.registered = asked
                    cid = "client-" + secrets.token_hex(4)
                    fake.clients[cid] = asked
                    return self._send(201, {"client_id": cid, **asked})
                if parts.path == "/oauth2/token":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    fake.token_form = form
                    if form.get("grant_type") == "authorization_code":
                        held = fake.codes.pop(form.get("code"), None)
                        made = base64.urlsafe_b64encode(hashlib.sha256(
                            form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                        if not held or held[0] != made or held[1] != form.get("client_id") \
                                or held[2] != form.get("redirect_uri") or form.get("resource") != fake.resource:
                            return self._send(400, {"error": "invalid_grant"})
                    elif form.get("grant_type") == "refresh_token":
                        if form.get("refresh_token") not in fake.refresh:
                            return self._send(400, {"error": "invalid_grant"})
                        fake.refresh.discard(form["refresh_token"])
                    else:
                        return self._send(400, {"error": "unsupported_grant_type"})
                    access, refresh = fake._issue("access"), fake._issue("refresh")
                    fake.access.add(access)
                    fake.refresh.add(refresh)
                    return self._send(200, {"access_token": access, "refresh_token": refresh,
                                            "token_type": "Bearer", "expires_in": 3600})
                if parts.path == "/oauth2/revoke":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    fake.revoked.append(form.get("token"))
                    fake.access.discard(form.get("token"))
                    fake.refresh.discard(form.get("token"))
                    return self._send(200)
                if parts.path == "/mcp":
                    return self._mcp(raw)
                return self._send(404)

            def _mcp(self, raw):
                bearer = (self.headers.get("authorization") or "").removeprefix("Bearer ")
                if bearer not in fake.access:
                    return self._send(401, {"error": "unauthorized"}, headers={
                        "www-authenticate": 'Bearer error="invalid_token", resource_metadata="'
                                            + fake.base + '/.well-known/oauth-protected-resource"'})
                if fake.http_429 > 0:
                    fake.http_429 -= 1
                    return self._send(429, {"error": "rate_limit"}, headers={"retry-after": "0"})
                msg = json.loads(raw)
                method = msg.get("method")
                if "id" not in msg:
                    return self._send(202)
                headers = {}
                if method == "initialize":
                    sid = "session-" + secrets.token_hex(4)
                    fake.sessions.add(sid)
                    headers["mcp-session-id"] = sid
                    result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                              "serverInfo": {"name": "granola", "version": "1"}}
                elif self.headers.get("mcp-session-id") not in fake.sessions:
                    return self._send(404, {"error": "no session"})
                elif method == "tools/call":
                    try:
                        text = fake._tool(msg["params"]["name"], msg["params"].get("arguments") or {})
                        result = {"content": [{"type": "text", "text": text}], "isError": False}
                    except _ToolError as e:
                        result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
                else:
                    result = {}
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
                if not fake.sse:
                    return self._send(200, reply, headers=headers)
                stream = (": hello\n\n"
                          'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
                          "event: message\nid: 7\ndata: " + json.dumps(reply) + "\n\n").encode()
                return self._send(200, stream, headers=headers, kind="text/event-stream")
        return Handler


class _ToolError(Exception):
    pass


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
