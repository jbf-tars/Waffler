"""The Style settings through the bridge (S6): get_settings, save_settings,
preview_style, and what the logs bundle leaves out.

app.py's Api methods are lifted out of its source and run against a
temporary settings.json (tests/_settings_fake.py): no window, no keys.
"""
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import privacy_data  # noqa: E402
import style_layer  # noqa: E402
import style_post  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
KINDS = ("personal", "work", "email", "other")


def _method(name):
    return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _lift(name, **ns_extra):
    ns = {"os": os, "_pipeline": None, "_log_to_file": lambda m: None,
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN, "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN,
          "TIPS_UNKNOWN": um.TIPS_UNKNOWN, "_languages": __import__("languages")}
    ns.update(ns_extra)
    exec(compile(ast.Module([_method(name)], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def methods(tmp_path, initial=None):
    return settings_api(tmp_path, initial), _lift("save_settings"), _lift("get_settings")


# ── reading ──────────────────────────────────────────────────────────────────

def test_the_defaults_are_on_auto_and_no_signature(tmp_path):
    api, _save, get = methods(tmp_path)
    s = get(api)
    assert s["cleanup"] == "on"
    assert s["style_by_category"] == {"personal": "auto", "work": "auto", "email": "auto", "other": "auto"}
    assert s["email_signature"] == {"enabled": False, "text": ""}


def test_saved_choices_are_read_back(tmp_path):
    api, _save, get = methods(tmp_path, {
        "cleanup": "off", "style_by_category": {"personal": "casual", "work": "excited"},
        "email_signature": {"enabled": True, "text": "Best,\nJames"}})
    s = get(api)
    assert s["cleanup"] == "off"
    assert s["style_by_category"] == {"personal": "casual", "work": "excited", "email": "auto", "other": "auto"}
    assert s["email_signature"] == {"enabled": True, "text": "Best,\nJames"}


@pytest.mark.parametrize("junk", [None, 5, "x", [], {"cleanup": 1}])
def test_a_hand_edited_file_reads_as_the_defaults(tmp_path, junk):
    api, _save, get = methods(tmp_path, {"cleanup": junk, "style_by_category": junk, "email_signature": junk})
    s = get(api)
    assert s["cleanup"] == "on" and s["email_signature"] == {"enabled": False, "text": ""}
    assert set(s["style_by_category"].values()) == {"auto"}


# ── writing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["on", "off"])
def test_save_stores_the_clean_up_choice(tmp_path, value):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = save(api, {"cleanup": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "cleanup": value}
    assert get(api)["cleanup"] == value


def test_save_stores_one_kind_and_keeps_the_others(tmp_path):
    api, save, get = methods(tmp_path, {"style_by_category": {"personal": "casual"}})
    assert save(api, {"style_by_category": {"work": "excited"}})["ok"] is True
    assert api.data["style_by_category"] == {"personal": "casual", "work": "excited", "email": "auto", "other": "auto"}
    assert save(api, {"style_by_category": {"personal": "very_casual", "email": "formal"}})["ok"] is True
    assert get(api)["style_by_category"] == {"personal": "very_casual", "work": "excited", "email": "formal",
                                              "other": "auto"}
    assert save(api, {"style_by_category": {"work": "auto"}})["ok"] is True
    assert get(api)["style_by_category"]["work"] == "auto"


def test_save_stores_the_signature_tidy(tmp_path):
    api, save, get = methods(tmp_path)
    r = save(api, {"email_signature": {"enabled": True, "text": "  Best,\r\nJames\n  "}})
    assert r["ok"] is True
    assert api.data["email_signature"] == {"enabled": True, "text": "Best,\nJames"}
    assert save(api, {"email_signature": {"enabled": False}})["ok"] is True
    assert get(api)["email_signature"] == {"enabled": False, "text": "Best,\nJames"}
    assert save(api, {"email_signature": {"text": "Cheers"}})["ok"] is True
    assert get(api)["email_signature"] == {"enabled": False, "text": "Cheers"}


def test_the_notes_never_carry_the_signature(tmp_path):
    api, save, _get = methods(tmp_path)
    r = save(api, {"email_signature": {"enabled": True, "text": "Best, James Farrelly 07700 900123"}})
    assert "James" not in json.dumps(r) and "0770" not in json.dumps(r)


@pytest.mark.parametrize("payload,message", [
    ({"cleanup": "maybe"}, um.CLEANUP_CHOICE_UNKNOWN),
    ({"cleanup": True}, um.CLEANUP_CHOICE_UNKNOWN),
    ({"style_by_category": {"personal": "excited"}}, um.STYLE_CHOICE_UNKNOWN),
    ({"style_by_category": {"work": "very_casual"}}, um.STYLE_CHOICE_UNKNOWN),
    ({"style_by_category": {"moon": "casual"}}, um.STYLE_CHOICE_UNKNOWN),
    ({"style_by_category": "casual"}, um.STYLE_CHOICE_UNKNOWN),
    ({"email_signature": {"enabled": "yes"}}, um.SIGNATURE_BAD),
    ({"email_signature": {"text": 5}}, um.SIGNATURE_BAD),
    ({"email_signature": "Best"}, um.SIGNATURE_BAD),
    ({"email_signature": {"text": "x" * 501}}, um.SIGNATURE_TOO_LONG),
])
def test_a_refused_choice_saves_nothing_from_the_payload(tmp_path, payload, message):
    start = {"cleanup": "off", "dialect": "en-GB", "style_by_category": {"work": "casual"},
             "email_signature": {"enabled": True, "text": "Best"}}
    api, save, _get = methods(tmp_path, start)
    r = save(api, dict(payload, dialect="en-US", auto_paste=False))
    assert r == {"ok": False, "error": message}
    assert api.data == start
    assert api.saves == 0


def test_a_signature_of_exactly_the_limit_is_kept(tmp_path):
    api, save, get = methods(tmp_path)
    assert save(api, {"email_signature": {"enabled": True, "text": "x" * 500}})["ok"] is True
    assert len(get(api)["email_signature"]["text"]) == 500


def test_a_save_without_these_keys_leaves_them_alone(tmp_path):
    start = {"cleanup": "off", "style_by_category": {"work": "casual"},
             "email_signature": {"enabled": True, "text": "Best"}}
    api, save, _get = methods(tmp_path, start)
    assert save(api, {"auto_paste": False})["ok"] is True
    assert api.data == dict(start, auto_paste=False)


def test_both_methods_list_all_three_keys():
    """get_settings and save_settings list their keys explicitly (the roadmap's
    conventions): a key added to one and not the other is a bug."""
    for name in ("get_settings", "save_settings"):
        src = ast.unparse(_method(name))
        for key in ("'cleanup'", "'style_by_category'", "'email_signature'"):
            assert key in src, (name, key)


def test_the_settings_are_written_through_the_editing_helper_only():
    src = ast.unparse(_method("save_settings"))
    assert "_editing_settings" in src and "_save_settings_file" not in src


def test_they_run_in_a_namespace_without_the_style_module():
    """Other tests lift these two methods with a handful of names; the style
    code is imported inside them, so those tests keep working."""
    fn = _method("get_settings")
    assert "_style_layer" not in ast.unparse(fn)
    assert "style_layer" in ast.unparse(fn)


# ── the example under each choice ────────────────────────────────────────────

def test_preview_style_is_the_real_rules():
    preview = _lift("preview_style")
    for kind in KINDS:
        for style in ("auto",) + style_post.STYLES:
            got = preview(None, style, kind)
            assert got["ok"] is True
            assert got["text"] == style_post.preview(style, kind)
            assert got["sample"] == style_post.SAMPLES[kind]
    assert preview(None, "very_casual", "personal")["text"] == "sure, I'll be there at seven"


def test_preview_style_answers_odd_input_with_a_sentence_not_an_error():
    preview = _lift("preview_style")
    for style, kind in ((None, None), ("shouty", "moon"), (7, []), ("casual", {})):
        got = preview(None, style, kind)
        assert got["ok"] is True and got["text"] and isinstance(got["text"], str)


# ── the logs bundle leaves the signature out ─────────────────────────────────

def test_the_signature_text_is_blanked_in_the_logs_copy():
    raw = json.dumps({"theme": "dark", "email_signature": {"enabled": True, "text": "Best,\nJames Farrelly\n07700 900123"},
                      "style_by_category": {"work": "casual"}})
    out = privacy_data.settings_for_logs(raw)
    assert "Farrelly" not in out and "07700" not in out and "Best" not in out
    data = json.loads(out)
    assert data["email_signature"] == {"enabled": True, "text": "", "text_chars": 33}
    assert data["theme"] == "dark" and data["style_by_category"] == {"work": "casual"}


def test_the_logs_copy_without_a_signature_is_the_same_settings():
    raw = json.dumps({"theme": "dark", "language": "de"})
    assert json.loads(privacy_data.settings_for_logs(raw)) == {"theme": "dark", "language": "de"}


@pytest.mark.parametrize("raw", ["{ not json", "[]", "5", "", "null"])
def test_a_settings_file_that_cannot_be_read_is_not_included_at_all(raw):
    out = privacy_data.settings_for_logs(raw)
    assert "not included" in out and "{" not in out


def test_a_signature_of_the_wrong_shape_is_blanked_too():
    for sig in ("Best, James", ["Best"], 5):
        out = json.loads(privacy_data.settings_for_logs(json.dumps({"email_signature": sig})))
        assert out["email_signature"]["text"] == ""


def test_download_logs_uses_it_for_settings_json_and_says_so():
    fn = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    src = ast.unparse(fn)
    assert "_privacy.settings_for_logs(" in src
    assert "The text of your email signature (left out of config/settings.json)" in src
    # snippets.json and the others are still not in it.
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)
              and c.value.endswith(".json")]
    assert "settings.json" in listed and "snippets.json" not in listed


# ── nothing under prompts/ changed ───────────────────────────────────────────

PROMPTS = {
    "README.md": "310e658c1c6f79e5078e002330557c5561e1e1a6aadcc7554018b3a51018d8d7",
    "email.txt": "f4619da2d78b5760b68d3dfc40e7b8dc89c51bd90147e91432f286268e1bf248",
    "normal.txt": "14e2868af8da6836f08c9c4a308cd1a7d8ea08505c681883f7b4926d4d91b84c",
}


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_the_prompts_are_exactly_what_they_were_before_the_style_section(name):
    """The Style section is code, not a prompt (S6): a style, Off and the
    signature add no word to what a model is asked. If a prompt is edited on
    purpose, this hash moves with it and the corpus has been run."""
    data = (ROOT / "prompts" / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == PROMPTS[name], name


def test_the_prompt_template_still_has_only_its_two_places():
    import string
    template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    names = {f for _lit, f, _spec, _conv in string.Formatter().parse(template) if f}
    assert names == {"transcript", "dialect_instruction"}


def test_the_style_modules_add_nothing_to_the_prompt():
    for name in ("style_post.py", "style_layer.py"):
        src = (ROOT / "src" / name).read_text(encoding="utf-8")
        assert "dialect_instruction" not in src and "prompt_template" not in src, name
    assert style_layer.PROVIDER_OFF == "off"
