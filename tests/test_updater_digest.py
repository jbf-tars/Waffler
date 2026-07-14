#!/usr/bin/env python3
"""Tests for the updater's SHA-256 artifact verification.

Why this exists (the bug it fixes):
`_verify_windows_exe_signature` required a *valid Authenticode signature* and
failed CLOSED. Waffler's Windows installers are not code-signed (there is no
Windows signing cert in CI — only the Mac build is signed/notarized), so every
single auto-update downloaded 100% and then aborted with:

    install failed: Authenticode verification failed for
    Waffler-Setup-3.14.80.exe: status='NotSigned'

i.e. auto-update on Windows could NEVER succeed. Users were silently stranded
on old versions (a live install was found still on v3.14.79).

The fix keeps a real, enforced trust anchor rather than just dropping the
check: GitHub publishes a SHA-256 `digest` for every release asset, so we
verify the downloaded bytes against it and still fail closed on any mismatch.
Authenticode becomes advisory (logged, not fatal) until a signing cert exists.

These tests pin: URL->asset parsing, hashing, and that verification fails
closed on mismatch / malformed / missing digests.
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402


# ── URL parsing ──────────────────────────────────────────────────────────────

def test_parse_release_asset_url_valid():
    url = ("https://github.com/jbf-tars/Waffler/releases/download/"
           "v3.14.81/Waffler-Setup-3.14.81.exe")
    parsed = updater._parse_release_asset_url(url)
    assert parsed == ("jbf-tars", "Waffler", "v3.14.81", "Waffler-Setup-3.14.81.exe")


def test_parse_release_asset_url_strips_query_string():
    """GitHub's signed-redirect URLs carry a query string."""
    url = ("https://github.com/jbf-tars/Waffler/releases/download/"
           "v3.14.81/Waffler-3.14.81-mac.dmg?token=abc123")
    parsed = updater._parse_release_asset_url(url)
    assert parsed == ("jbf-tars", "Waffler", "v3.14.81", "Waffler-3.14.81-mac.dmg")


@pytest.mark.parametrize("bad", [
    "",
    "not a url",
    "https://evil.com/releases/download/v1/x.exe",       # wrong host
    "https://github.com/jbf-tars/Waffler/archive/v1.zip",  # not a release asset
    "http://github.com/a/b/releases/download/v1/x.exe",   # not https
])
def test_parse_release_asset_url_rejects_bad(bad):
    assert updater._parse_release_asset_url(bad) is None


# ── Hashing ──────────────────────────────────────────────────────────────────

def _tmp_with(content: bytes) -> Path:
    fd, p = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return Path(p)


def test_sha256_file_matches_hashlib():
    content = b"waffler installer bytes" * 5000  # exercise the chunked read
    p = _tmp_with(content)
    try:
        assert updater._sha256_file(p) == hashlib.sha256(content).hexdigest()
    finally:
        p.unlink()


# ── Verification: the fail-closed gate ───────────────────────────────────────

def test_verify_digest_accepts_matching():
    content = b"the real installer"
    p = _tmp_with(content)
    good = "sha256:" + hashlib.sha256(content).hexdigest()
    try:
        updater._verify_artifact_digest(p, good)  # must not raise
    finally:
        p.unlink()


def test_verify_digest_rejects_tampered_file():
    """The whole point: a byte that differs from what GitHub published fails."""
    p = _tmp_with(b"a malicious installer")
    expected = "sha256:" + hashlib.sha256(b"the real installer").hexdigest()
    try:
        with pytest.raises(RuntimeError, match="(?i)digest mismatch"):
            updater._verify_artifact_digest(p, expected)
    finally:
        p.unlink()


@pytest.mark.parametrize("bad_digest", [
    None,
    "",
    "deadbeef",                    # no algo prefix
    "md5:" + "0" * 32,             # wrong algorithm
    "sha256:" + "0" * 63,          # wrong length
    "sha256:" + "z" * 64,          # non-hex
])
def test_verify_digest_fails_closed_on_bad_expected(bad_digest):
    """A missing/malformed expected digest must REFUSE, never silently pass."""
    p = _tmp_with(b"anything")
    try:
        with pytest.raises(RuntimeError):
            updater._verify_artifact_digest(p, bad_digest)
    finally:
        p.unlink()


def test_verify_digest_is_case_insensitive_on_hex():
    content = b"installer"
    p = _tmp_with(content)
    upper = "sha256:" + hashlib.sha256(content).hexdigest().upper()
    try:
        updater._verify_artifact_digest(p, upper)  # must not raise
    finally:
        p.unlink()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
