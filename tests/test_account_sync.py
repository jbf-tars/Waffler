"""Meetings sync with the account (src/meetings/sync.py, the engine's
sync_soon, and src/meetings_ctl.py which makes the account drive them).

Two computers are two stores in two folders, signed in to one account on a
stand-in service (tests/_account_fake.py). No network.
"""
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account, meetings_ctl                    # noqa: E402
from src.meetings import sync                             # noqa: E402
from src.meetings.service import Service                  # noqa: E402
from src.meetings.store import Store, wait_until          # noqa: E402
from _account_fake import FakeAccountServer               # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"


class Computer:
    """One computer: a data folder of its own, signed in to the account."""

    def __init__(self, fake, folder, monkeypatch, name):
        self.fake, self.folder, self.monkeypatch, self.name = fake, folder, monkeypatch, name
        folder.mkdir()
        self.use()
        self.store = Store()
        assert account.start(EMAIL)["ok"]
        assert account.verify(EMAIL, fake.mailbox[EMAIL], name)["ok"]
        self.token = json.loads((folder / account.SESSION_FILE).read_text())["token"]

    def use(self):
        """Become this computer: its data folder is the one in use."""
        self.monkeypatch.setenv("WAFFLER_DATA_DIR", str(self.folder))
        self.monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(self.folder / "meetings"))
        return self

    def sync(self, **kw):
        time.sleep(0.004)            # two computers never act in the same millisecond; the tests could
        self.use()
        cred = account.sync_credentials()
        with account.http_client(cred["server"]) as client:
            return sync.sync_with_hub(self.store, client=client, token=cred["token"],
                                      cursors_key=meetings_ctl.ACCOUNT_CURSORS, scope=cred["scope"], **kw)

    def note(self, title, words="hello there", size=0):
        time.sleep(0.004)
        n = self.store.create_note(title=title)
        self.store.add_segments(n["id"], [{"source": "mic", "start_s": 0.0, "end_s": 2.0, "text": words}])
        self.store.update_note(n["id"], summary="## Notes\n- " + words + ("x" * size), status="ready")
        return n["id"]

    def titles(self):
        return sorted(n["title"] for n in self.store.list_notes())


@pytest.fixture
def fake(monkeypatch):
    fake = FakeAccountServer()
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    monkeypatch.delenv("NATTER_HUB_TOKEN", raising=False)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    return fake


@pytest.fixture
def two(fake, tmp_path, monkeypatch):
    return Computer(fake, tmp_path / "one", monkeypatch, "Mac one"), \
        Computer(fake, tmp_path / "two", monkeypatch, "Mac two")


# ── the client ──────────────────────────────────────────────────────────────

def test_a_meeting_made_on_one_computer_arrives_on_the_other(two, fake):
    one, other = two
    nid = one.note("Call with the supplier", "the quote arrives on Tuesday")
    assert one.sync() == {"pulled": 0, "pushed": 1}
    assert other.sync()["pulled"] == 1
    got = other.store.get_note(nid, with_segments=True)
    assert got["title"] == "Call with the supplier" and got["status"] == "ready"
    assert [s["text"] for s in got["segments"]] == ["the quote arrives on Tuesday"]
    # An edit on the second goes back to the first; a delete goes round too.
    time.sleep(0.01)
    other.store.update_note(nid, title="Supplier call", title_locked=1)
    other.sync()
    one.sync()
    assert one.titles() == ["Supplier call"]
    time.sleep(0.01)
    one.store.delete_note(nid)
    one.sync()
    other.sync()
    assert other.titles() == []


def test_each_request_is_signed_with_that_computers_own_token(two, fake):
    one, other = two
    one.note("A")
    one.sync()
    other.sync()
    used = {r.auth for r in fake.requests if r.path.startswith("/api/sync/")}
    assert used == {f"Bearer {one.token}", f"Bearer {other.token}"}


def test_the_client_keeps_fetching_while_the_service_says_there_is_more(two, fake):
    one, other = two
    for i in range(7):
        one.note(f"Meeting {i}")
    one.sync()
    fake.page_rows = 3                                   # the service cuts its answers
    fake.requests.clear()
    assert other.sync()["pulled"] == 7
    pulls = fake.calls("GET", "/api/sync/changes")
    assert len(pulls) == 3, "7 rows, 3 to a page: three answers, the last one saying no more"
    cursors = [p.query["since"] for p in pulls]
    assert cursors[0] == "" and len(set(cursors)) == 3, "each page asks from where the last one ended"
    assert other.titles() == [f"Meeting {i}" for i in range(7)]
    fake.requests.clear()
    assert other.sync() == {"pulled": 0, "pushed": 0}
    assert len(fake.calls("GET", "/api/sync/changes")) == 1 and fake.calls("POST", "/api/sync/push") == []


def test_a_service_that_says_more_forever_does_not_hang_the_client(two, fake, monkeypatch):
    one, _ = two

    def forever(request):
        return httpx.Response(200, json={"notes": [], "folders": [], "templates": [], "now": "same", "more": True})
    monkeypatch.setattr(account, "_transport", httpx.MockTransport(forever))
    assert one.sync() == {"pulled": 0, "pushed": 0}


def test_a_large_first_sync_is_sent_in_batches_the_service_accepts(two, fake):
    one, other = two
    fake.max_push_bytes = 60_000                         # the service's limit, scaled down
    for i in range(12):
        one.note(f"Long meeting {i:02d}", size=20_000)
    whole = len(json.dumps(one.store.export_changes("")))
    assert whole > 4 * fake.max_push_bytes, "sent as one push, this first sync would be refused"
    assert one.sync(push_bytes=50_000)["pushed"] == 12
    assert fake.too_large == 0, "nothing was ever sent that the service had to refuse"
    assert len(fake.accepted_sizes) >= 6 and max(fake.accepted_sizes) <= 50_000
    assert len(fake.notes_of(EMAIL)) == 12
    assert other.sync()["pulled"] == 12 and len(other.titles()) == 12
    # The limit the app ships with leaves room under the service's 16 MB.
    assert sync.PUSH_BYTES <= 8 * 1024 * 1024
    import inspect
    assert inspect.signature(sync.sync_with_hub).parameters["push_bytes"].default == sync.PUSH_BYTES


def test_batches_never_split_or_lose_a_note_and_carry_folders_once():
    notes = [{"id": f"{i:032x}", "updated_at": f"2026-11-21T10:00:{i // 2:02d}.000+00:00", "summary": "s" * 400}
             for i in range(10)]
    local = {"notes": notes, "folders": [{"id": "f", "name": "Work"}], "templates": [{"id": "t"}], "now": "x"}
    cut = sync.batches(local, limit=1200)
    assert [n["id"] for b, _ in cut for n in b["notes"]] == [n["id"] for n in notes]
    assert sum(len(b["folders"]) for b, _ in cut) == 1 and sum(len(b["templates"]) for b, _ in cut) == 1
    assert len(cut) > 2 and cut[-1][1] == ""
    for (batch, upto), (following, _) in zip(cut, cut[1:]):
        last, first = batch["notes"][-1]["updated_at"], following["notes"][0]["updated_at"]
        # "Sent up to here" is only said when nothing later shares that instant.
        assert upto == (last if last < first else "")
    assert sync.batches({"notes": [], "folders": [], "templates": []}) == []
    big = sync.batches({"notes": [{"id": "a", "updated_at": "1", "summary": "s" * 5000}], "folders": [],
                        "templates": []}, limit=100)
    assert len(big) == 1, "one note larger than the limit still goes, alone"


def test_a_service_with_a_smaller_limit_than_expected_is_met_by_halving(two, fake):
    one, _ = two
    for i in range(8):
        one.note(f"Meeting {i}", size=5_000)
    fake.max_push_bytes = 15_000                         # two notes fit, three do not
    assert one.sync()["pushed"] == 8                     # the app's own limit is far above this
    assert len(fake.notes_of(EMAIL)) == 8
    assert fake.too_large >= 1 and max(fake.accepted_sizes) <= 15_000


def test_a_sync_cut_short_carries_on_from_where_it_stopped(two, fake):
    one, _ = two
    for i in range(6):
        one.note(f"Meeting {i}", size=5_000)
        time.sleep(0.003)
    fake.fail_pushes_after = 2                           # the connection drops after two batches
    with pytest.raises(sync.SyncError) as e:
        one.sync(push_bytes=6_000)
    assert e.value.status is None and "natter_" not in str(e.value)
    assert len(fake.notes_of(EMAIL)) == 2
    fake.fail_pushes_after = None
    fake.requests.clear()
    assert one.sync(push_bytes=6_000)["pushed"] == 4, "only what had not arrived is sent"
    assert len(fake.notes_of(EMAIL)) == 6


def test_another_account_starts_from_the_beginning(two, fake, tmp_path, monkeypatch):
    one, _ = two
    one.note("Mine")
    one.sync()
    cursors = one.store.settings()[meetings_ctl.ACCOUNT_CURSORS]
    assert cursors["pulled"] and cursors["pushed"] and cursors["scope"].endswith("@" + SERVER)
    # The same computer signs in to a different account: its cursors are not reused.
    one.use()
    account.sign_out()
    account.start("grace@example.com")
    account.verify("grace@example.com", fake.mailbox["grace@example.com"])
    fake.requests.clear()
    assert one.sync()["pushed"] == 1
    assert fake.calls("GET", "/api/sync/changes")[0].query["since"] == ""
    assert list(fake.notes_of("grace@example.com").values())[0]["title"] == "Mine"


def test_the_older_hub_setting_still_works_and_keeps_its_own_cursors(fake, tmp_path, monkeypatch):
    """A hub: no pages, a token of its own, the address from the engine's settings."""
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(tmp_path / "meetings"))
    monkeypatch.setenv("NATTER_HUB_TOKEN", "hub-token")
    store = Store()
    nid = store.create_note(title="For the hub")["id"]
    seen = []

    def hub(request):
        seen.append((request.method, request.url.path, request.headers["authorization"]))
        if request.method == "GET":
            return httpx.Response(200, json={"notes": [], "folders": [], "templates": [],
                                             "now": "2026-11-21T10:00:00.000+00:00"})
        assert [n["id"] for n in json.loads(request.content)["notes"]] == [nid]
        return httpx.Response(200, json={"applied": 1})
    with httpx.Client(base_url="http://hub.test", transport=httpx.MockTransport(hub)) as client:
        assert sync.sync_with_hub(store, client) == {"pulled": 0, "pushed": 1}
    assert seen == [("GET", "/api/sync/changes", "Bearer hub-token"),
                    ("POST", "/api/sync/push", "Bearer hub-token")]
    cursors = store.settings()["sync_cursors"]
    assert cursors["pulled"] == "2026-11-21T10:00:00.000+00:00" and cursors["pushed"] and "scope" not in cursors
    assert meetings_ctl.ACCOUNT_CURSORS not in store.settings()
    with pytest.raises(sync.SyncError, match="Set the hub address"):
        monkeypatch.delenv("NATTER_HUB_TOKEN")
        sync.sync_with_hub(store)


def test_a_refusal_carries_the_services_sentence_and_its_status(two, fake):
    one, _ = two
    one.note("A")
    fake.over_allowance = True
    with pytest.raises(sync.SyncError) as e:
        one.sync()
    assert (e.value.status, e.value.code) == (402, "storage_cap")
    assert str(e.value) == "Your account's storage is full. Delete some notes to make room."
    fake.over_allowance = False
    fake.sync_too_many = True
    with pytest.raises(sync.SyncError) as e:
        one.sync()
    assert e.value.status == 429
    fake.sync_too_many = False
    assert one.sync()["pushed"] == 1, "nothing was marked as sent while it was refused"


# ── the engine and the controller: the account drives the same machinery ────

@pytest.fixture
def ctl(fake, tmp_path, monkeypatch):
    """Waffler's meetings controller on a computer signed in to the account."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(data))
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(data / "meetings"))
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(meetings_ctl, "_share_waffler_keys", lambda: None)
    logged, changes = [], []
    c = meetings_ctl.MeetingsController(log=logged.append, on_change=lambda: changes.append("list"),
                                        on_account_change=lambda: changes.append("account"))
    c.logged, c.changes, c.data, c.fake = logged, changes, data, fake
    account.start(EMAIL)
    assert account.verify(EMAIL, fake.mailbox[EMAIL], "This Mac")["ok"]
    c.token = json.loads((data / account.SESSION_FILE).read_text())["token"]
    yield c
    c.shutdown()


def a_meeting(store, title="Weekly sync"):
    n = store.create_note(title=title)
    store.add_segments(n["id"], [{"source": "mic", "start_s": 0.0, "end_s": 2.0, "text": "we ship on Friday"}])
    return n["id"]


def test_soon_after_the_notes_are_written_the_meeting_is_in_the_account(ctl, fake):
    svc = ctl._svc()
    svc.llm_backend = lambda *a, **k: "Weekly sync\n### Shipping\n- Friday"
    nid = a_meeting(svc.store)
    svc.enhance(nid, wait=True)                          # what the recorder does when a meeting ends
    assert wait_until(lambda: nid in fake.notes_of(EMAIL), 5)
    assert fake.notes_of(EMAIL)[nid]["summary"]
    assert wait_until(lambda: ctl.sync_state()["last_synced_at"] != "" and not ctl.sync_state()["syncing"], 5)
    assert ctl.sync_state()["error"] == ""
    assert "account" in ctl.changes, "the Account page is told"
    assert any(line.startswith("[account] synced: 0 in, 1 out") for line in ctl.logged)


def test_signed_out_nothing_is_contacted(ctl, fake):
    account.sign_out()
    fake.requests.clear()
    svc = ctl._svc()
    a_meeting(svc.store)
    assert svc.sync_now() is True
    assert fake.requests == [] and ctl.sync_state() == {"syncing": False, "last_synced_at": "", "error": ""}


def test_sync_now_waits_and_answers_with_where_things_stand(ctl, fake):
    nid = a_meeting(ctl._svc().store)
    out = ctl.sync_now()
    assert out["ok"] and out["sync"]["last_synced_at"] and out["sync"]["error"] == "" and not out["sync"]["syncing"]
    assert nid in fake.notes_of(EMAIL)
    assert ctl._svc().store.settings()[meetings_ctl.ACCOUNT_LAST_SYNCED]["at"] == out["sync"]["last_synced_at"]


def test_a_meeting_from_another_device_redraws_the_list(ctl, fake, tmp_path, monkeypatch):
    mine = str(ctl.data)
    other = Computer(fake, tmp_path / "other", monkeypatch, "Mac two")
    other.note("Made elsewhere")
    other.sync()
    monkeypatch.setenv("WAFFLER_DATA_DIR", mine)
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", mine + "/meetings")
    ctl.changes.clear()
    assert ctl.sync_now()["ok"]
    assert [n["title"] for n in ctl.list()] == ["Made elsewhere"] and "list" in ctl.changes


def test_a_failed_round_is_logged_once_without_a_secret_and_tried_again(ctl, fake, capsys):
    nid = a_meeting(ctl._svc().store)
    fake.offline = True
    for _ in range(3):
        out = ctl.sync_now()
        assert out == {"ok": False, "error": account.NO_INTERNET, "reason": "sync", "sync": ctl.sync_state()}
    failures = [line for line in ctl.logged if "did not go through" in line]
    assert failures == ["[account] sync did not go through (no answer); it will be tried again"]
    assert ctl._svc().store.get_note(nid)["title"] == "Weekly sync", "the meeting is untouched"
    fake.offline = False
    assert ctl.sync_now()["ok"] and nid in fake.notes_of(EMAIL)
    everything = "\n".join(ctl.logged) + capsys.readouterr().out
    assert ctl.token not in everything and "natter_" not in everything and EMAIL not in everything
    assert ctl.token not in json.dumps(ctl._svc().store.settings())


def test_over_the_allowance_is_shown_in_the_services_words(ctl, fake):
    a_meeting(ctl._svc().store)
    fake.over_allowance = True
    out = ctl.sync_now()
    assert out["ok"] is False and out["error"] == "Your account's storage is full. Delete some notes to make room."
    assert account.status()["signed_in"]


def test_a_computer_removed_from_the_account_is_refused_and_forgets_its_token(ctl, fake):
    a_meeting(ctl._svc().store)
    fake.sessions.clear()                                # removed from another device
    out = ctl.sync_now()
    assert out["ok"] is False and out["error"] == account.SIGNED_OUT
    assert fake.notes_of(EMAIL) == {}
    assert not (ctl.data / account.SESSION_FILE).exists()
    s = account.status()
    assert s["signed_in"] is False and s["notice"] == account.SIGNED_OUT
    fake.requests.clear()
    ctl.sync_now()
    assert fake.requests == [], "signed out, the next round contacts nothing"


def test_a_stuck_service_never_holds_up_the_meetings(ctl, fake):
    """Recording and the list go on while a sync waits on the network."""
    svc = ctl._svc()
    fake.hold = threading.Event()
    try:
        t = time.time()
        svc.sync_soon()
        assert time.time() - t < 0.5, "sync_soon returns at once"
        assert wait_until(lambda: ctl.sync_state()["syncing"], 5)
        t = time.time()
        nid = a_meeting(svc.store, "Made while syncing")
        svc.store.update_note(nid, summary="still writable")
        assert [n["title"] for n in ctl.list()] == ["Made while syncing"]
        assert ctl.state()["recording"] is False
        svc.sync_soon()                                  # a second request while one runs: no pile-up
        assert time.time() - t < 1.0
    finally:
        fake.hold.set()
    assert wait_until(lambda: not ctl.sync_state()["syncing"], 10)
    assert len(fake.calls("GET", "/api/sync/changes")) == 1


def test_a_round_that_blows_up_breaks_nothing(ctl, fake, monkeypatch):
    svc = ctl._svc()

    def boom(*a, **k):
        raise RuntimeError("natter_looks-like-a-token")
    monkeypatch.setattr(sync, "sync_with_hub", boom)
    out = ctl.sync_now()
    assert out["ok"] is False and out["error"] == account.FAILED
    assert ctl.logged[-1] == "[account] sync failed: RuntimeError"
    svc.account_sync = boom                              # even the hook itself failing
    assert svc.sync_now() is True
    svc.sync_soon()


def test_start_sync_runs_one_loop_however_often_it_is_asked(ctl, fake, monkeypatch):
    loops, soon = [], []
    monkeypatch.setattr(Service, "start_auto_sync", lambda self, every_s=120.0: loops.append(every_s))
    monkeypatch.setattr(Service, "sync_soon", lambda self: soon.append(1))
    ctl.start_sync()
    ctl.start_sync()
    ctl.start_sync()
    assert loops == [meetings_ctl.SYNC_EVERY_S] and len(soon) == 2
    assert 60 <= meetings_ctl.SYNC_EVERY_S <= 300, "every couple of minutes"


def test_the_engines_loop_syncs_at_the_start_and_then_on_the_clock(ctl, fake):
    nid = a_meeting(ctl._svc().store)
    ctl.start_sync(every_s=0.2)
    assert wait_until(lambda: nid in fake.notes_of(EMAIL), 5), "the first round is at once: on app start"
    second = a_meeting(ctl._svc().store, "Later")
    assert wait_until(lambda: second in fake.notes_of(EMAIL), 5), "and again without being asked"


def test_signing_out_clears_what_the_page_says_about_sync(ctl, fake):
    ctl.sync_now()
    assert ctl.sync_state()["last_synced_at"]
    ctl.account_signed_out()
    assert ctl.sync_state() == {"syncing": False, "last_synced_at": "", "error": ""}


def test_the_last_sync_time_survives_a_restart(ctl, fake, monkeypatch):
    at = ctl.sync_now()["sync"]["last_synced_at"]
    again = meetings_ctl.MeetingsController()
    try:
        fake.offline = True
        out = again.sync_now()
        assert out["ok"] is False and out["sync"]["last_synced_at"] == at, "offline at start: the old time stands"
    finally:
        again.shutdown()
