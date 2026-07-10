#!/usr/bin/env python3
"""Tests for the `logging.log_transcripts` privacy gate.

Why this exists (the bug it guards against):
config.yaml has advertised `log_transcripts: false  # Privacy: don't log
transcripts by default` ever since the open-source prep spec asked for it
(docs/dev-notes/specs/2026-04-09-open-source-prep-workflow.md, "Don't log
transcribed speech unless `logging.log_transcripts: true`"). Nothing ever read
the key. It was dead config: a promise to the user that no code kept.

Meanwhile v3.14.79 really did leak. The wizard path logged
`f"Wizard transcription: {_wizard_result[:80]}"` - 80 characters of the user's
speech straight into ~/.waffler-hosted/app.log, which is exactly the file
`download_logs` zips up for bug reports. history.json is deliberately excluded
from that bundle because it holds dictations; app.log was quietly carrying them
anyway. v3.14.80 patched that one call site by hand, but nothing stopped the
next one from reappearing.

These tests pin the contract:
  1. Config.log_transcripts reads the flag and FAILS CLOSED (False) whenever the
     key, the `logging` block, or a truthy-looking non-bool is what's present.
  2. transcript_for_log() never returns speech unless a caller explicitly opts
     in, so forgetting a guard degrades to a length, not to a leak.
  3. app.py routes its transcript logging through the gate, so the v3.14.79
     slice-into-an-f-string bug cannot silently come back.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.config import Config
from src.log_util import transcript_for_log


def _config_with(tmp_path: Path, body: str) -> Config:
    """Build a Config from an inline YAML body written to a temp file."""
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return Config(str(path))


# ── Config.log_transcripts ────────────────────────────────────────────────


def test_shipped_config_keeps_transcripts_out_of_the_log():
    """The repo's own config.yaml must not opt in. This is the shipped default."""
    cfg = Config(str(REPO_ROOT / "config.yaml"))
    assert cfg.log_transcripts is False


def test_flag_true_when_user_opts_in(tmp_path):
    cfg = _config_with(tmp_path, "logging:\n  log_transcripts: true\n")
    assert cfg.log_transcripts is True


def test_fails_closed_when_key_missing(tmp_path):
    cfg = _config_with(tmp_path, "logging:\n  level: INFO\n")
    assert cfg.log_transcripts is False


def test_fails_closed_when_logging_block_missing(tmp_path):
    cfg = _config_with(tmp_path, "audio:\n  channels: 1\n")
    assert cfg.log_transcripts is False


@pytest.mark.parametrize("value", ['"true"', "'yes'", "1", "maybe"])
def test_fails_closed_on_non_boolean_values(tmp_path, value):
    """A quoted string or a stray int must not be read as consent."""
    cfg = _config_with(tmp_path, f"logging:\n  log_transcripts: {value}\n")
    assert cfg.log_transcripts is False


# ── transcript_for_log ────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["my card pin is 4417", "x" * 500])
def test_never_returns_speech_when_disallowed(text):
    out = transcript_for_log(text, allowed=False)
    assert text not in out
    assert out == f"{len(text)} chars"


def test_empty_transcript_renders_as_zero_chars():
    assert transcript_for_log("", allowed=False) == "0 chars"


def test_returns_text_verbatim_when_allowed():
    assert transcript_for_log("hello world", allowed=True) == "hello world"


# ── Regression guard on the real call sites ───────────────────────────────


def test_app_py_routes_transcript_logging_through_the_gate():
    """v3.14.79 sliced the transcript into an f-string. Never again."""
    app_src = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    assert "transcript_for_log(" in app_src, "app.py must log transcripts via the gate"
    assert "{_wizard_result[:" not in app_src, "raw transcript slice is back in app.log"
