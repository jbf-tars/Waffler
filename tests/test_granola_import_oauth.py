"""Import from Granola, signing in and the small MCP client
(src/meetings/granola_import.py), against a pretend Granola on 127.0.0.1
(tests/_granola_fake.py). Nothing here reaches the internet."""
import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _granola_fake import FakeGranola, meeting       # noqa: E402
from src.meetings import granola_import as gi        # noqa: E402


@pytest.fixture
def fake():
    f = FakeGranola([meeting(1), meeting(2)])
    yield f
    f.close()


def connect(fake, **kw):
    gi.connect(resource=fake.resource, open_browser=fake.browser, timeout_s=10, **kw)


def token_file(data):
    return data / gi.TOKEN_FILE


# ── addresses ───────────────────────────────────────────────────────────────

def test_only_https_addresses_are_spoken_to():
    assert gi.checked("https://mcp-auth.granola.ai/oauth2/token") == "https://mcp-auth.granola.ai/oauth2/token"
    for bad in ("http://mcp-auth.granola.ai/oauth2/token", "http://127.0.0.1:9/token", "ftp://granola.ai/x",
                "https://user:pw@granola.ai/x", "//granola.ai/x", "", None, "https://"):
        with pytest.raises(gi.GranolaError) as e:
            gi.checked(bad)
        assert e.value.reason == "not_https"
    # This computer passes only while the resource itself is this computer (these tests).
    assert gi.checked("http://127.0.0.1:9/token", "http://127.0.0.1:9/mcp")
    with pytest.raises(gi.GranolaError):
        gi.checked("http://localhost:9/token", "http://127.0.0.1:9/mcp")
    assert gi.RESOURCE == "https://mcp.granola.ai/mcp"


def test_a_sign_in_document_that_names_a_plain_http_address_is_refused(fake):
    fake.metadata = {"token_endpoint": "http://example.com/oauth2/token"}
    with pytest.raises(gi.GranolaError) as e:
        gi.discover(fake.resource)
    assert e.value.reason == "not_https" and e.value.sentence == gi.REFUSED
    fake.metadata = {"code_challenge_methods_supported": ["plain"]}
    with pytest.raises(gi.GranolaError) as e:
        gi.discover(fake.resource)
    assert e.value.reason == "no_pkce"
    fake.metadata = {"issuer": "https://somewhere-else.example"}
    with pytest.raises(gi.GranolaError):
        gi.discover(fake.resource)


def test_the_endpoints_are_read_from_granola_not_written_down(fake):
    found = gi.discover(fake.resource)
    assert found["authorization_endpoint"] == fake.base + "/oauth2/authorize"
    assert found["token_endpoint"] == fake.base + "/oauth2/token"
    assert found["registration_endpoint"] == fake.base + "/oauth2/register"
    assert found["revocation_endpoint"] == fake.base + "/oauth2/revoke"
    assert found["issuer"] == fake.base and found["scope"] == "mcp offline_access"
    source = (ROOT / "src" / "meetings" / "granola_import.py").read_text(encoding="utf-8")
    assert "mcp-auth.granola.ai" not in source and "oauth2/" not in source


def test_an_answer_that_sends_waffler_elsewhere_is_not_followed(fake):
    status, _ = gi._fetch("GET", fake.base + "/oauth2/authorize?" + "client_id=nobody")
    assert status == 400
    fake.clients["c"] = {"redirect_uris": ["http://127.0.0.1:1/callback"]}
    status, body = gi._fetch("GET", fake.base + "/oauth2/authorize?response_type=code&client_id=c"
                             "&redirect_uri=http%3A%2F%2F127.0.0.1%3A1%2Fcallback&code_challenge=x"
                             "&code_challenge_method=S256&resource=" + fake.resource)
    assert status == 302 and body == {}


# ── signing in ──────────────────────────────────────────────────────────────

def test_connecting_registers_a_public_client_and_uses_pkce(fake, _isolated_data_dir):
    connect(fake)
    asked = fake.registered
    assert asked["client_name"] == "Waffler" and asked["token_endpoint_auth_method"] == "none"
    assert len(asked["redirect_uris"]) == 1
    assert asked["redirect_uris"][0].startswith("http://127.0.0.1:") and asked["redirect_uris"][0].endswith("/callback")
    q = fake.authorize
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["resource"] == fake.resource and q["redirect_uri"] == asked["redirect_uris"][0]
    assert len(q["state"]) >= 32 and len(q["code_challenge"]) == 43
    form = fake.token_form
    assert form["grant_type"] == "authorization_code" and form["resource"] == fake.resource
    assert 43 <= len(form["code_verifier"]) <= 128 and "client_secret" not in form
    assert gi.connected(fake.resource) and not gi.connected()       # these tokens are not the real Granola's


def test_the_tokens_are_kept_in_their_own_owner_only_file(fake, _isolated_data_dir):
    connect(fake)
    path = token_file(_isolated_data_dir)
    kept = json.loads(path.read_text())
    assert kept["access_token"] in fake.access and kept["refresh_token"] in fake.refresh
    assert kept["resource"] == fake.resource and kept["client_id"] in fake.clients
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name(path.name + ".tmp").exists()
    # Nowhere else in the data folder.
    for other in _isolated_data_dir.rglob("*"):
        if other.is_file() and other != path:
            text = other.read_bytes()
            assert not any(s.encode() in text for s in fake.secrets), other.name


def test_the_callback_is_taken_once_and_the_state_must_match(fake, _isolated_data_dir):
    fake.wrong_state = True
    with pytest.raises(gi.GranolaError) as e:
        connect(fake)
    assert e.value.sentence == gi.REFUSED and not gi.connected(fake.resource)
    assert not token_file(_isolated_data_dir).exists()
    fake.wrong_state = False
    fake.send_iss = "https://someone-else.example"
    with pytest.raises(gi.GranolaError):
        connect(fake)
    fake.send_iss = False                       # a service that names no sender is taken on its state
    connect(fake)
    assert gi.connected(fake.resource)


def test_a_scope_granola_will_not_have_is_asked_for_once_more_without(fake, _isolated_data_dir):
    fake.refuse_scope = "mcp"
    connect(fake)
    assert gi.connected(fake.resource) and "scope" not in fake.authorize
    assert len(fake.clients) == 2, "each trip through the browser has its own listener"


def test_one_sign_in_document_that_cannot_be_read_is_not_the_end(fake, monkeypatch):
    real = gi._fetch

    def flaky(method, url, **kw):
        if url.endswith("/.well-known/oauth-authorization-server") and not flaky.failed:
            flaky.failed = True
            raise gi.GranolaError(gi.NO_INTERNET, "offline (test)")
        return real(method, url, **kw)
    flaky.failed = False
    monkeypatch.setattr(gi, "_fetch", flaky)
    assert gi.discover(fake.resource)["token_endpoint"] == fake.base + "/oauth2/token"


def test_a_second_call_to_the_listener_is_turned_away():
    import urllib.error
    import urllib.request
    server = gi._listener()
    server.judge = lambda answer: True
    port = server.server_address[1]
    got = []

    def visit(path):
        try:
            got.append(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).status)
        except urllib.error.HTTPError as e:
            got.append(e.code)
    for path in ("/favicon.ico", "/callback?code=one&state=s", "/callback?code=two&state=s"):
        t = threading.Thread(target=visit, args=(path,))
        t.start()
        server.handle_request()
        t.join()
    server.server_close()
    assert got == [404, 200, 404] and server.answer["code"] == "one"
    assert server.server_address[0] == "127.0.0.1"


def test_waiting_for_the_browser_ends(fake, _isolated_data_dir):
    with pytest.raises(gi.GranolaError) as e:
        gi.connect(resource=fake.resource, open_browser=lambda address: True, timeout_s=0.4)
    assert e.value.sentence == gi.TIMED_OUT
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(gi.GranolaError) as e:
        gi.connect(cancel, resource=fake.resource, open_browser=lambda address: True, timeout_s=30)
    assert e.value.reason == gi.STOPPED
    with pytest.raises(gi.GranolaError) as e:
        gi.connect(resource=fake.resource, open_browser=lambda address: False, timeout_s=5)
    assert e.value.sentence == gi.NO_BROWSER
    assert gi.BROWSER_WAIT_S == 300


def test_disconnecting_forgets_the_tokens_and_tells_granola(fake, _isolated_data_dir):
    connect(fake)
    kept = json.loads(token_file(_isolated_data_dir).read_text())
    gi.disconnect(fake.resource)
    assert not token_file(_isolated_data_dir).exists() and not gi.connected(fake.resource)
    assert set(fake.revoked) == {kept["access_token"], kept["refresh_token"]}
    gi.disconnect(fake.resource)                # nothing to forget: nothing happens


def test_disconnecting_works_when_granola_cannot_be_reached(fake, _isolated_data_dir):
    connect(fake)
    fake.close()
    gi.disconnect(fake.resource)
    assert not token_file(_isolated_data_dir).exists()


# ── the client ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sse", [False, True])
def test_a_tool_is_called_over_json_and_over_an_event_stream(sse, _isolated_data_dir):
    fake = FakeGranola([meeting(1)], sse=sse)
    try:
        connect(fake)
        client = gi.Client(fake.resource)
        text = client.call("search_meetings", {"limit": 100})
        assert "<meetings_data" in text and 'title="Meeting 1"' in text
        assert ("POST", "/mcp") in fake.requests and len(fake.sessions) == 1
        assert client.call("get_meeting_transcript", {"meeting_id": meeting(1)["id"]}).startswith("{")
        assert len(fake.sessions) == 1, "one session for the whole import"
    finally:
        fake.close()


def test_a_401_renews_the_token_once_and_carries_on(fake, _isolated_data_dir):
    connect(fake)
    client = gi.Client(fake.resource)
    client.call("search_meetings", {})
    before = json.loads(token_file(_isolated_data_dir).read_text())
    fake.expire_access()
    assert "<meetings_data" in client.call("search_meetings", {})
    after = json.loads(token_file(_isolated_data_dir).read_text())
    assert after["access_token"] != before["access_token"] and after["refresh_token"] != before["refresh_token"]
    assert fake.token_form["grant_type"] == "refresh_token" and fake.token_form["resource"] == fake.resource
    assert after["client_id"] == before["client_id"]


def test_a_token_granola_will_not_renew_means_connect_again(fake, _isolated_data_dir):
    connect(fake)
    client = gi.Client(fake.resource)
    fake.expire_access()
    fake.refresh.clear()
    with pytest.raises(gi.GranolaError) as e:
        client.call("search_meetings", {})
    assert e.value.sentence == gi.NOT_CONNECTED and not gi.connected(fake.resource)
    with pytest.raises(gi.GranolaError):
        gi.Client(fake.resource)


def test_a_rate_limit_waits_and_tries_again(fake, _isolated_data_dir):
    connect(fake)
    client = gi.Client(fake.resource, waits=(0.01, 0.01, 0.01))
    fake.rate_limit["search_meetings"] = 1
    assert "<meetings_data" in client.patiently("search_meetings", {})
    fake.http_429 = 2
    assert "<meetings_data" in client.patiently("search_meetings", {})
    fake.rate_limit["search_meetings"] = 5      # three tries, then it is left
    with pytest.raises(gi.GranolaError) as e:
        client.patiently("search_meetings", {})
    assert e.value.reason == "rate_limit"
    assert fake.rate_limit["search_meetings"] == 2
    assert gi._wait_named("rate_limit: retry after 12 seconds") == 12
    assert gi._wait_named("Retry in 500 ms") == 0.5 and gi._wait_named("try later") is None


def test_a_session_that_ended_is_opened_again(fake, _isolated_data_dir):
    connect(fake)
    client = gi.Client(fake.resource)
    client.call("search_meetings", {})
    fake.sessions.clear()
    assert "<meetings_data" in client.call("search_meetings", {})


def test_no_internet_is_one_plain_sentence(fake, _isolated_data_dir):
    connect(fake)
    fake.close()
    with pytest.raises(gi.GranolaError) as e:
        gi.Client(fake.resource).call("search_meetings", {})
    assert e.value.sentence == gi.NO_INTERNET
