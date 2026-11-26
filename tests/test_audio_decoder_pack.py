"""The audio converter Import audio fetches on Windows (src/audio_decoder_pack.py).

Offline: the BtbN zip is stood in for by a small one laid out the same way
(one top folder; bin/ with ffmpeg.exe, its seven libraries, ffprobe and
ffplay; doc, include, lib; LICENSE.txt), served by a fake GET. What matters:
nothing is unpacked before the download's SHA-256 matches; only ffmpeg.exe,
its libraries and the licence are kept; ffmpeg.exe arrives last, so
audio_decode.helper_path() (the only lookup) never finds it with its
libraries missing; a download cut short carries on; and the prompt
(Offer) asks once, waits for the answer, and reports how it went.
"""
import hashlib
import io
import os
import threading
import zipfile

import pytest

import audio_decoder_pack as pack
import local_stt
from meetings import audio_decode as ad


def _zip_bytes(top=None, drop=()) -> bytes:
    top = top or pack._TOP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for want in pack.KEEP:
            if want not in drop:
                z.writestr(f"{top}/{want}", f"contents of {want}\n" * 50)
        for extra in ("bin/ffprobe.exe", "bin/ffplay.exe", "doc/ffmpeg.html",
                      "include/libavcodec/avcodec.h", "lib/avcodec.lib", "presets/x.ffpreset"):
            z.writestr(f"{top}/{extra}", f"not wanted: {extra}\n")
    return buf.getvalue()


class _Resp:
    def __init__(self, data: bytes, status: int = 200):
        self.status_code, self._data = status, data

    def iter_content(self, size):
        for i in range(0, len(self._data), 4096):
            yield self._data[i:i + 4096]

    def close(self):
        pass


@pytest.fixture()
def served(tmp_path, monkeypatch):
    """A data folder in tmp_path, ASSET pinned to a small zip, and a GET that serves it."""
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "data"))
    data = _zip_bytes()
    monkeypatch.setattr(pack, "ASSET", pack.ASSET._replace(size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    asked = []

    def get(url, headers):
        asked.append(dict(headers))
        start = int(headers["Range"].split("=")[1].rstrip("-")) if "Range" in headers else 0
        return _Resp(data[start:], 206 if start else 200)

    return {"data": data, "get": get, "asked": asked}


def test_the_pin_is_bt_bn_s_lgpl_shared_build_of_ffmpeg_9():
    a = pack.ASSET
    assert a.url.startswith("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/")
    assert a.name == "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-shared-9.0.zip" and a.url.endswith(a.name)
    assert "lgpl" in a.name and "gpl-" not in a.name.replace("lgpl-", "")
    assert a.size == 76576818
    assert a.sha256 == "e907b01fa4783913151237e37df2a10fa97d99a1598a021bf59d4ae62ffed4c0"
    assert pack.download_mb() == 77
    assert pack.KEEP[-1] == "bin/ffmpeg.exe"                    # last: see the module's docstring


def test_it_is_fetched_checked_and_only_what_ffmpeg_needs_is_kept(served, monkeypatch):
    replaced = []
    real = os.replace
    monkeypatch.setattr(pack.os, "replace", lambda a, b: (replaced.append(os.path.basename(b)), real(a, b))[1])
    seen = []
    pack.fetch(lambda n, total: seen.append((n, total)), threading.Event(), get=served["get"])
    here = sorted(p.name for p in pack.folder().iterdir())
    kept = sorted(w.rsplit("/", 1)[-1] for w in pack.KEEP)
    assert here == sorted(kept + [pack._READY])                 # no ffprobe, ffplay, docs, headers, zip
    assert replaced[-1] == "ffmpeg.exe"
    assert ad.helper_path("win32") == pack.folder() / "ffmpeg.exe" and pack.is_ready()
    assert seen[-1] == (len(served["data"]), len(served["data"])) and all(t == len(served["data"]) for _, t in seen)
    assert (pack.folder() / "LICENSE.txt").read_text().startswith("contents of LICENSE.txt")
    assert pack.bytes_left() == 0


def test_nothing_is_unpacked_when_the_download_does_not_match(served, monkeypatch):
    monkeypatch.setattr(pack, "ASSET", pack.ASSET._replace(sha256="0" * 64))
    with pytest.raises(local_stt.LocalSTTError):
        pack.fetch(lambda n, t: None, threading.Event(), get=served["get"])
    assert ad.helper_path("win32") is None and not pack.is_ready()
    assert not [p for p in pack.folder().iterdir() if p.is_file()]          # no library, no licence


def test_a_download_cut_short_carries_on(served):
    here = pack.folder() / pack._DOWNLOADS
    here.mkdir(parents=True)
    half = len(served["data"]) // 2
    (here / (pack.ASSET.name + ".part")).write_bytes(served["data"][:half])
    pack.fetch(lambda n, t: None, threading.Event(), get=served["get"])
    assert served["asked"] == [{"Range": f"bytes={half}-"}]
    assert pack.is_ready()


def test_cancelled_while_unpacking_leaves_no_program_and_no_parts(served, monkeypatch):
    cancel = threading.Event()
    real = pack._unpack_one

    def one(z, info, dest, name, cancel_):
        if name == "avcodec-63.dll":
            cancel.set()
        return real(z, info, dest, name, cancel_)

    monkeypatch.setattr(pack, "_unpack_one", one)
    with pytest.raises(pack.Cancelled):
        pack.fetch(lambda n, t: None, cancel, get=served["get"])
    assert ad.helper_path("win32") is None
    assert not list(pack.folder().glob("*.part"))


def test_a_zip_without_ffmpeg_is_refused(served, monkeypatch):
    data = _zip_bytes(drop=("bin/ffmpeg.exe",))
    monkeypatch.setattr(pack, "ASSET", pack.ASSET._replace(size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    with pytest.raises(pack.PackError):
        pack.fetch(lambda n, t: None, threading.Event(), get=lambda url, h: _Resp(data))
    assert ad.helper_path("win32") is None and not pack.is_ready()


def test_an_older_pin_is_fetched_again(served):
    pack.fetch(lambda n, t: None, threading.Event(), get=served["get"])
    (pack.folder() / pack._READY).write_text('{"asset": "an-older-build.zip", "files": {}}', encoding="utf-8")
    assert not pack.is_ready()
    pack.fetch(lambda n, t: None, threading.Event(), get=served["get"])
    assert pack.is_ready()


def test_the_failure_sentences_name_the_converter():
    assert "audio converter" in pack.failure_sentence(local_stt.LocalSTTError(local_stt.DOWNLOAD_FAILED, "download"))
    assert pack.failure_sentence(local_stt.LocalSTTError(local_stt.DOWNLOAD_DAMAGED, "download")) == local_stt.DOWNLOAD_DAMAGED
    assert pack.failure_sentence(pack.PackError(pack.UNPACK_FAILED)) == pack.UNPACK_FAILED
    assert "audio converter" in pack.failure_sentence(OSError("disk full"))


# -- the prompt ------------------------------------------------------------------

def _offer(fetch_fn):
    o = pack.Offer(fetch_fn=fetch_fn)
    o.WAIT_S = 5.0
    return o


def test_the_prompt_downloads_when_asked_and_reports_it():
    got = []
    o = _offer(lambda progress, cancel: (progress(5, 10), progress(10, 10), got.append("fetched")))

    def show(view):
        got.append(("shown", view["mb"]))
        threading.Timer(0.05, o.answer, args=("download",)).start()
        return True

    ok = o.run(show, lambda n, t: got.append((n, t)), lambda ok, s: got.append(("done", ok, s)), ready=lambda: False)
    assert ok is True
    assert got == [("shown", 77), (5, 10), (10, 10), "fetched", ("done", True, "")]


def test_not_now_returns_without_fetching():
    got = []
    o = _offer(lambda progress, cancel: got.append("fetched"))
    ok = o.run(lambda v: threading.Timer(0.05, o.answer, args=("not_now",)).start() or True,
               lambda n, t: None, lambda ok, s: got.append(("done", ok, s)), ready=lambda: False)
    assert ok is False and got == [("done", False, "")]


def test_a_prompt_that_cannot_be_shown_waits_for_nothing():
    o = _offer(lambda progress, cancel: pytest.fail("fetched"))
    assert o.run(lambda v: False, lambda n, t: None, lambda ok, s: pytest.fail("done"), ready=lambda: False) is False


def test_no_answer_in_time_is_not_now():
    got = []
    o = _offer(lambda progress, cancel: got.append("fetched"))
    o.WAIT_S = 0.2
    assert o.run(lambda v: True, lambda n, t: None, lambda ok, s: got.append(("done", ok, s)), ready=lambda: False) is False
    assert got == [("done", False, "")]


def test_a_failed_download_says_why_and_cancel_says_nothing():
    for raised, sentence in ((local_stt.LocalSTTError(local_stt.DOWNLOAD_FAILED, "download"), "audio converter"),
                             (pack.Cancelled(), "")):
        got = []

        def fetch_fn(progress, cancel, raised=raised):
            raise raised

        o = _offer(fetch_fn)
        ok = o.run(lambda v: threading.Timer(0.05, o.answer, args=("download",)).start() or True,
                   lambda n, t: None, lambda ok, s: got.append((ok, s)), ready=lambda: False)
        assert ok is False and got[0][0] is False
        assert (sentence in got[0][1]) if sentence else got[0][1] == ""


def test_cancel_reaches_the_download():
    started = threading.Event()

    def fetch_fn(progress, cancel):
        started.set()
        assert cancel.wait(5)
        raise pack.Cancelled()

    o = _offer(fetch_fn)
    threading.Thread(target=lambda: (started.wait(5), o.cancel()), daemon=True).start()
    got = []
    ok = o.run(lambda v: threading.Timer(0.05, o.answer, args=("download",)).start() or True,
               lambda n, t: None, lambda ok, s: got.append((ok, s)), ready=lambda: False)
    assert ok is False and got == [(False, "")]


def test_already_here_asks_nothing():
    o = _offer(lambda progress, cancel: pytest.fail("fetched"))
    assert o.run(lambda v: pytest.fail("shown"), lambda n, t: None, lambda ok, s: None, ready=lambda: True) is True


def test_a_second_import_waits_for_the_first_prompt_and_is_not_asked_again():
    here = {"ready": False}
    o = _offer(lambda progress, cancel: here.update(ready=True))
    shown = []

    def show(view):
        shown.append(1)
        threading.Timer(0.2, o.answer, args=("download",)).start()
        return True

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        o.run(show, lambda n, t: None, lambda ok, s: None, ready=lambda: here["ready"]))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == [True, True] and shown == [1]
