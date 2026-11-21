"""Meetings sync with the account (src/meetings/sync.py, the engine's
sync_soon, and src/meetings_ctl.py which makes the account drive them).

Two computers are two stores in two folders, signed in to one account on a
stand-in service (tests/_account_fake.py). No network.
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account, meetings_ctl                    # noqa: E402
from src.meetings import sync                             # noqa: E402
from src.meetings.service import Service                  # noqa: E402
from src.meetings.store import Store, new_id, wait_until  # noqa: E402
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
    monkeypatch.setattr(account, "_digits", {})
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
    assert one.sync() == {"pulled": 0, "pushed": 1, "skipped": 0}
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
    assert other.sync() == {"pulled": 0, "pushed": 0, "skipped": 0}
    assert len(fake.calls("GET", "/api/sync/changes")) == 1 and fake.calls("POST", "/api/sync/push") == []


def test_a_service_that_says_more_forever_does_not_hang_the_client(two, fake, monkeypatch):
    one, _ = two

    def forever(request):
        return httpx.Response(200, json={"notes": [], "folders": [], "templates": [], "now": "same", "more": True})
    monkeypatch.setattr(account, "_transport", httpx.MockTransport(forever))
    assert one.sync() == {"pulled": 0, "pushed": 0, "skipped": 0}


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
        assert sync.sync_with_hub(store, client) == {"pulled": 0, "pushed": 1, "skipped": 0}
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
    if c._service is not None:
        # start_sync's loop is a daemon thread that is never stopped. Left as it is, it goes on
        # syncing this test's store with whichever account the next test signs in to.
        c._service.account_sync = None
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
    assert ctl.logged[-1] == "[account] sync refused (answer 401): this computer is no longer signed in"
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


# ── what the service sends is checked before it is applied ──────────────────
# Only matters if the service has been taken over, or is wrong: a row that
# fails a check is skipped and counted, and never reaches the store.

def a_row(_=None, title="From the service"):
    """A note as the service would send it (the shape Store.export_changes gives)."""
    nid, now = new_id(), hours_from_now(0)
    return {"id": nid, "title": title, "title_locked": 0, "my_notes": "", "summary": "", "template_id": "general",
            "folder_id": None, "status": "ready", "error": "", "duration_s": 2.0, "created_at": now,
            "updated_at": now, "deleted": 0, "sessions": [],
            "segments": [{"id": new_id(), "note_id": nid, "session_n": 1, "source": "mic", "start_s": 0.0,
                          "end_s": 2.0, "text": "we ship on Friday", "created_at": now}]}


def test_the_rows_these_tests_plant_have_the_shape_a_real_one_has(two, fake):
    one, _ = two
    one.note("Real")
    real = one.store.export_changes("")["notes"][0]
    assert set(a_row()) == set(real) and set(a_row()["segments"][0]) == set(real["segments"][0])
    assert sync.valid_rows(one.store.export_changes("")) == ({"notes": [real], "folders": [], "templates": []}, 0)


def plant(fake, kind, row, email=EMAIL):
    """Put a row straight into the service, as if the service itself had made it."""
    user = fake.users[email]
    fake.seq[user] = fake.seq.get(user, 0) + 1
    fake.rows.setdefault(user, {})[(kind, str(row.get("id")) if isinstance(row, dict) else repr(row))] = (
        fake.seq[user], row)


def hours_from_now(h):
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat(timespec="milliseconds")


def _bad_id(r): r["id"] = "../../settings"
def _bad_id_slash(r): r["id"] = "a/b"
def _long_id(r): r["id"] = "a" * 65
def _no_id(r): del r["id"]
def _id_not_text(r): r["id"] = 7
def _far_future(r): r["updated_at"] = hours_from_now(25)
def _made_in_future(r): r["created_at"] = hours_from_now(24 * 365)
def _time_unreadable(r): r["updated_at"] = "zzzz"
def _time_missing(r): r["updated_at"] = None
def _huge_title(r): r["title"] = "t" * (sync.MAX_NAME + 1)
def _huge_summary(r): r["summary"] = "s" * (sync.MAX_TEXT + 1)
def _huge_notes(r): r["my_notes"] = "n" * (sync.MAX_TEXT + 1)
def _title_not_text(r): r["title"] = {"a": 1}
def _bad_folder(r): r["folder_id"] = "../x"
def _segment_bad_id(r): r["segments"][0]["id"] = "x y"
def _segment_of_another_note(r): r["segments"][0]["note_id"] = "0" * 32
def _segment_huge(r): r["segments"][0]["text"] = "w" * (sync.MAX_TEXT + 1)
def _segment_in_future(r): r["segments"][0]["created_at"] = hours_from_now(48)
def _segment_time_not_a_number(r): r["segments"][0]["start_s"] = "soon"
def _segments_not_a_list(r): r["segments"] = "all of them"
def _session_of_another_note(r):
    r["sessions"] = [{"id": "b" * 32, "note_id": "0" * 32, "n": 1, "offset_s": 0.0,
                      "started_at": r["created_at"], "ended_at": None}]
def _session_in_future(r):
    r["sessions"] = [{"id": "b" * 32, "note_id": r["id"], "n": 1, "offset_s": 0.0,
                      "started_at": hours_from_now(30), "ended_at": None}]


@pytest.mark.parametrize("spoil", [
    _bad_id, _bad_id_slash, _long_id, _no_id, _id_not_text, _far_future, _made_in_future, _time_unreadable,
    _time_missing, _huge_title, _huge_summary, _huge_notes, _title_not_text, _bad_folder, _segment_bad_id,
    _segment_of_another_note, _segment_huge, _segment_in_future, _segment_time_not_a_number, _segments_not_a_list,
    _session_of_another_note, _session_in_future], ids=lambda f: f.__name__.strip("_"))
def test_a_note_that_fails_a_check_is_skipped_and_counted_never_applied(two, fake, spoil):
    one, _ = two
    good, bad = a_row(one, "A good one"), a_row(one, "A bad one")
    spoil(bad)
    plant(fake, "notes", bad)
    plant(fake, "notes", good)
    before = one.store.db.execute("SELECT count(*) FROM segments").fetchone()[0]
    assert one.sync() == {"pulled": 1, "pushed": 0, "skipped": 1}
    assert one.titles() == ["A good one"], "the good row on the same page still arrives"
    assert one.store.db.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
    assert one.store.db.execute("SELECT count(*) FROM segments").fetchone()[0] == before + 1
    assert one.store.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert not (one.folder / "settings").exists()
    assert one.sync() == {"pulled": 0, "pushed": 0, "skipped": 0}, "it is not asked for again"


def test_things_that_are_not_rows_at_all_are_skipped(two, fake):
    one, _ = two
    for i, junk in enumerate(("a string", 7, None, ["a", "list"])):
        plant(fake, "notes", junk)
    plant(fake, "notes", a_row(one, "Real"))
    assert one.sync() == {"pulled": 1, "pushed": 0, "skipped": 4}
    assert one.titles() == ["Real"]


def test_a_clock_a_few_hours_out_is_not_a_reason_to_skip(two, fake):
    one, _ = two
    row = a_row(one, "From a computer whose clock is fast")
    row["updated_at"] = hours_from_now(23)
    plant(fake, "notes", row)
    assert one.sync()["skipped"] == 0 and one.titles() == ["From a computer whose clock is fast"]


def test_a_time_far_in_the_future_cannot_win_every_merge_from_then_on(two, fake):
    one, other = two
    nid = one.note("Mine")
    one.sync()
    forged = dict(fake.notes_of(EMAIL)[nid], title="Taken over", updated_at=hours_from_now(24 * 3650))
    plant(fake, "notes", forged)
    assert one.sync()["skipped"] == 1
    assert one.titles() == ["Mine"]
    time.sleep(0.01)
    one.store.update_note(nid, title="Still mine to edit")
    assert one.sync()["pushed"] == 1 and one.titles() == ["Still mine to edit"]


def test_folders_and_templates_are_checked_too(two, fake):
    one, _ = two
    now = hours_from_now(0)
    for kind, row in (
            ("folders", {"id": "f" * 32, "name": "Clients", "updated_at": now, "deleted": 0}),
            ("folders", {"id": "../f", "name": "Bad id", "updated_at": now, "deleted": 0}),
            ("folders", {"id": "e" * 32, "name": "From the future", "updated_at": hours_from_now(100), "deleted": 0}),
            ("folders", {"id": "d" * 32, "name": "n" * (sync.MAX_NAME + 1), "updated_at": now, "deleted": 0}),
            ("templates", {"id": "t" * 32, "name": "Standup", "prompt": "Summarise.", "updated_at": now, "deleted": 0}),
            ("templates", {"id": "u" * 32, "name": "Huge", "prompt": "p" * (sync.MAX_TEXT + 1), "updated_at": now}),
            ("templates", {"id": "v v", "name": "Bad id", "prompt": "x", "updated_at": now}),
            ("templates", {"id": "w" * 32, "name": "No prompt", "updated_at": now})):
        plant(fake, kind, row)
    assert one.sync() == {"pulled": 0, "pushed": 0, "skipped": 6}
    assert [f["name"] for f in one.store.folders()] == ["Clients"]
    assert "Standup" in [t["name"] for t in one.store.templates()]
    assert not {"Huge", "Bad id", "No prompt"} & {t["name"] for t in one.store.templates()}


def test_an_answer_with_more_rows_than_any_real_page_stops_the_round_and_applies_nothing(two, fake, monkeypatch):
    one, _ = two
    for i in range(4):
        plant(fake, "notes", a_row(one, f"Note {i}"))
    monkeypatch.setattr(sync, "MAX_PAGE_ROWS", 3)
    with pytest.raises(sync.SyncError) as e:
        one.sync()
    assert (e.value.status, e.value.code) == (None, sync.PAGE_TOO_LARGE)
    assert one.titles() == [] and not one.store.settings().get(meetings_ctl.ACCOUNT_CURSORS)
    fake.page_rows = 3                                     # in pages of a size it accepts, all of it arrives
    assert one.sync() == {"pulled": 4, "pushed": 0, "skipped": 0}


def test_an_answer_of_more_bytes_than_any_real_page_stops_the_round(two, fake, monkeypatch):
    one, _ = two
    plant(fake, "notes", a_row(one, "x" * 1500))
    monkeypatch.setattr(sync, "MAX_PAGE_BYTES", 1000)
    with pytest.raises(sync.SyncError) as e:
        one.sync()
    assert e.value.code == sync.PAGE_TOO_LARGE and one.titles() == []
    assert sync.MAX_PAGE_ROWS >= 1000 and sync.MAX_TEXT >= 1_000_000, "generous: nothing real is near these"


def test_an_answer_that_is_not_an_object_changes_nothing(two, fake, monkeypatch):
    one, _ = two
    monkeypatch.setattr(account, "_transport", httpx.MockTransport(
        lambda request: httpx.Response(200, json=["not", "an", "object"])))
    with pytest.raises(sync.SyncError):
        one.sync()
    assert one.titles() == []


def test_skipped_rows_are_counted_in_the_log(ctl, fake):
    store = ctl._svc().store
    bad = a_row(None, "Bad")
    bad["id"] = "../../x"
    worse = a_row(None, "Worse")
    worse["updated_at"] = hours_from_now(999)
    for row in (bad, worse, a_row(None, "Good")):
        plant(fake, "notes", row)
    assert ctl.sync_now()["ok"]
    assert "[account] synced: 1 in, 0 out, 2 skipped (not valid, never applied)" in ctl.logged
    assert [n["title"] for n in store.list_notes()] == ["Good"]
    assert ctl.sync_now()["ok"]
    ctl.logged.clear()
    a_meeting(store)
    assert ctl.sync_now()["ok"]
    assert ctl.logged == ["[account] synced: 0 in, 1 out"], "nothing skipped: nothing said about it"


def test_an_answer_too_large_is_said_plainly_and_logged_as_that(ctl, fake, monkeypatch):
    for i in range(3):
        plant(fake, "notes", a_row(None, f"Note {i}"))
    monkeypatch.setattr(sync, "MAX_PAGE_ROWS", 2)
    out = ctl.sync_now()
    assert out["ok"] is False and out["error"] == \
        "The account service sent more than Waffler accepts in one answer, so nothing was changed."
    assert [line for line in ctl.logged if line.startswith("[account]")] == [
        "[account] sync did not go through (an answer too large to accept); it will be tried again"]
    assert ctl._svc().store.list_notes() == [] and account.status()["signed_in"]


# ── a meeting deleted somewhere else never takes this computer's audio ──────
# There is no trash for meetings: a deleted one is kept, marked deleted, and
# hidden. Only this computer's own Delete removes a recording's audio.

def record_audio(computer, nid):
    computer.use()
    from src.meetings.paths import audio_dir
    path = audio_dir(nid) / "session-1-mic.flac"
    path.write_bytes(b"fLaC" + b"the only copy of what was said" * 100)
    return path


def test_a_meeting_deleted_on_another_computer_keeps_the_audio_recorded_on_this_one(two, fake):
    one, other = two
    nid = one.note("Recorded here")
    audio = record_audio(one, nid)
    kept = audio.read_bytes()
    one.sync()
    other.sync()
    time.sleep(0.01)
    other.store.delete_note(nid)                           # deleted on the other computer
    other.sync()
    assert one.sync()["pulled"] == 1
    assert one.titles() == [], "the meeting is gone from the list here too"
    note = one.store.get_note(nid, with_segments=True)
    assert note["deleted"] == 1 and note["title"] == "Recorded here", "kept, marked deleted"
    assert [s["text"] for s in note["segments"]] == ["hello there"]
    assert audio.exists() and audio.read_bytes() == kept, "the audio this computer recorded is untouched"
    one.sync()
    one.sync()
    assert audio.read_bytes() == kept


def test_a_service_that_says_a_meeting_is_deleted_cannot_remove_its_audio(ctl, fake):
    svc = ctl._svc()
    nid = a_meeting(svc.store, "Recorded here")
    from src.meetings.paths import audio_dir
    audio = audio_dir(nid) / "session-1-mic.flac"
    audio.write_bytes(b"fLaC" + b"\x00" * 4096)
    assert ctl.sync_now()["ok"]
    forged = dict(fake.notes_of(EMAIL)[nid], deleted=1, updated_at=hours_from_now(1))
    plant(fake, "notes", forged)
    assert ctl.sync_now()["ok"]
    assert ctl.get(nid) is None and ctl.list() == [], "hidden, as a deleted meeting is"
    assert svc.store.get_note(nid)["deleted"] == 1
    assert audio.exists() and audio.stat().st_size == 4100 and svc.has_audio(nid)


def test_nothing_in_the_pull_path_can_reach_the_code_that_removes_audio():
    engine = Path(sync.__file__).resolve().parent
    for name in ("sync.py", "store.py"):
        source = (engine / name).read_text(encoding="utf-8")
        for word in ("rmtree", "unlink", "delete_audio", "os.remove", "audio_dir"):
            assert word not in source, f"{name} must never touch audio files ({word})"
