"""A key saved in Settings is used from the next dictation, with no restart.

Before: saving an OpenAI key gave the running transcriber and styler new
clients, but a Groq or Cerebras key only reached .env and the environment;
both kept their old Groq client until Waffler was restarted, and the page
said "Waffler starts using the new Groq key after a restart". James had
revoked his old key and pasted a new one, so dictation went on failing
until he restarted. Now WhisperTranscriber.set_groq_key and
OpenAIStyler.set_groq_key / set_cerebras_key make the clients again with
the new key (clearing any pause an old key earned), and app.py
_use_new_key_now hands a saved key to the running pipeline.
"""
import ast
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import transcribe_whisper as tw  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

NEW, NEWER = "gsk_newkey_not_real_0000000001", "gsk_another_not_real_000000002"
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def test_a_new_groq_key_reaches_speech_to_text_at_once():
    t = tw.WhisperTranscriber(api_key="", groq_api_key="")
    assert not t._has_client("groq")
    t._groq_skip_until = 10 ** 12                         # a pause the old key earned
    t.set_groq_key(NEW)
    assert t._has_client("groq") and t._groq_client.api_key == NEW
    assert t._groq_skip_until == 0.0
    t.set_groq_key(NEWER)
    assert t._groq_client.api_key == NEWER


def test_a_new_groq_key_reaches_the_clean_up_at_once():
    for k in [k for k in os.environ if k.endswith("_API_KEY")]:
        os.environ.pop(k, None)
    s = OpenAIStyler()
    assert not s._provider_ready("groq")
    s._groq_skip_until = 10 ** 12
    s.set_groq_key(NEW)
    assert s._provider_ready("groq") and s._groq_client.api_key == NEW
    assert s._groq_model and s._groq_models[0] == s._groq_model and s._groq_skip_until == 0.0


def test_a_new_cerebras_key_reaches_the_clean_up_at_once():
    s = OpenAIStyler()
    s.set_cerebras_key("csk-newkey-not-real-0000000003")
    assert s._provider_ready("cerebras") and s._cerebras_client.api_key == "csk-newkey-not-real-0000000003"
    assert "cerebras" in str(s._cerebras_client.base_url) and s._cerebras_model


def _use_new_key_now():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_use_new_key_now")
    logged = []
    ns = {"_log_to_file": logged.append}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns, logged


def test_the_running_dictation_is_given_the_key_when_it_is_saved():
    calls = []

    class Part:
        def __init__(self, name):
            self.name = name

        def set_groq_key(self, key):
            calls.append((self.name, key))

    class Broken:
        def set_groq_key(self, key):
            raise RuntimeError("boom")

    ns, logged = _use_new_key_now()
    ns["_pipeline"] = types.SimpleNamespace(transcriber=Part("speech"), styler=Part("clean-up"))
    ns["_use_new_key_now"]("groq", NEW)
    assert calls == [("speech", NEW), ("clean-up", NEW)]
    ns["_pipeline"] = types.SimpleNamespace(transcriber=Broken(), styler=Part("clean-up"))
    ns["_use_new_key_now"]("groq", NEWER)                 # one part failing does not stop the other
    assert calls[-1] == ("clean-up", NEWER) and logged and NEWER not in " ".join(logged)
    ns["_pipeline"] = None
    ns["_use_new_key_now"]("groq", NEW)                   # nothing running yet: nothing to do


def _method(name):
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment((ROOT / "app.py").read_text(encoding="utf-8"), fn)


def test_every_place_a_key_is_saved_hands_it_on():
    save = _method("save_settings")
    groq_branch = save[save.index("new_groq = "):save.index("# ── Speech to text")]
    assert '_use_new_key_now("groq", new_groq)' in groq_branch
    assert '_use_new_key_now("groq", api_key)' in _method("validate_groq_key")
    assert '_use_new_key_now("cerebras", api_key)' in _method("validate_cerebras_key")
    assert "restart for speed boost" not in save
