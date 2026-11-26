"""What the window's JavaScript can call in Python, and nothing else.

pywebview hands the page every public method of the ``js_api`` object, and
when the page calls a name it does not know it resolves it as an attribute
path on that object (``webview.util.js_bridge_call`` -> ``get_nested_attribute``),
so ``_private_method`` or ``save_settings.__func__.__globals__.clear`` would
reach Python too. Waffler therefore gives pywebview:

- ``ClosedApi()`` as ``js_api``: an object with no attributes at all, so a
  path resolves to nothing;
- each name in ``ALLOWED`` through ``window.expose``, as a plain function
  that calls the Api method of that name. pywebview looks an exposed name up
  by exact match before any attribute path, and an exposed function is all
  the page gets.

``ALLOWED`` is written out by hand on purpose. tests/test_bridge_gate.py
fails when a public method is added to ``app.Api`` without being added here
(or the other way round), and when the window calls a name that is not here.
"""
from __future__ import annotations

from typing import Callable, Iterable, List

ALLOWED = frozenset({
    "account_connector",
    "account_connector_create",
    "account_connector_off",
    "account_connector_rotate",
    "account_delete",
    "account_delete_start",
    "account_devices",
    "account_offer_dismiss",
    "account_remove_device",
    "account_sign_out",
    "account_start",
    "account_status",
    "account_sync_now",
    "account_verify",
    "answer_everyday_sound",
    "check_accessibility_permission",
    "check_everyday_sounds",
    "check_for_updates",
    "check_input_monitoring_permission",
    "check_permissions",
    "check_shortcut",
    "claude_connect",
    "claude_disconnect",
    "claude_status",
    "clear_history",
    "complete_setup",
    "copy_item",
    "copy_last",
    "delete_all_unsent",
    "delete_history_item",
    "delete_my_data",
    "delete_recent_audio",
    "delete_unsent",
    "demo_overlay_hide",
    "demo_overlay_show",
    "download_logs",
    "edit_history_text",
    "export_history",
    "export_snippets",
    "export_vocabulary",
    "factory_reset",
    "get_active_app",
    "get_app_version",
    "get_audio_devices",
    "get_cleanup_pause",
    "get_current_mode",
    "get_fn_key_conflict",
    "get_fn_key_state",
    "get_history",
    "get_history_retention",
    "get_hotkey_config",
    "get_input_source_popup",
    "get_languages",
    "get_learn_from_edits",
    "get_mic_level",
    "get_mic_setup",
    "get_modes",
    "get_onboarding_status",
    "get_permission_explanations",
    "get_recent_audio",
    "get_selected_device",
    "get_settings",
    "get_shortcuts",
    "get_snippets",
    "get_start_at_login",
    "get_stats",
    "get_theme",
    "get_unsent_summary",
    "get_update_progress",
    "get_usage_stats",
    "get_vocab",
    "get_vocab_book",
    "gpu_pack_cancel",
    "gpu_pack_download",
    "gpu_pack_remove",
    "gpu_pack_status",
    "granola_cancel",
    "granola_disconnect",
    "granola_import",
    "granola_status",
    "import_snippets",
    "import_vocabulary",
    "install_update_and_restart",
    "local_llm_cancel",
    "local_llm_download",
    "local_llm_remove",
    "local_llm_status",
    "local_stt_cancel",
    "local_stt_download",
    "local_stt_remove",
    "local_stt_status",
    "meetings_api",
    "meetings_ask",
    "meetings_delete",
    "meetings_get",
    "meetings_list",
    "meetings_rename",
    "meetings_state",
    "meetings_toggle",
    "migrate_mic_ranking",
    "open_accessibility_settings",
    "open_command_search",
    "open_input_monitoring_settings",
    "open_licences",
    "open_keyboard_settings",
    "open_permission_settings",
    "open_practice_editor",
    "open_url",
    "paste_last",
    "peek_clipboard_key",
    "preview_history_retention",
    "preview_snippets_import",
    "preview_style",
    "preview_vocab_import",
    "redo_history_item",
    "request_accessibility_permission",
    "request_input_monitoring_permission",
    "request_mic_permission",
    "request_permission",
    "reset_pill_position",
    "reset_shortcuts",
    "reset_usage",
    "restart_app",
    "retry_all_unsent",
    "retry_unsent",
    "reveal_unsent",
    "save_command_hotkey",
    "save_hotkey_config",
    "save_settings",
    "save_setup_step",
    "save_shortcut",
    "save_transform_hotkey",
    "set_audio_device",
    "set_history_retention",
    "set_input_source_popup",
    "set_mic_ranking",
    "set_mode",
    "set_recent_audio",
    "set_snippets",
    "set_start_at_login",
    "set_theme",
    "set_vocab",
    "shortcut_recording",
    "start_dictation_for_setup",
    "start_update_download",
    "suggest_command_hotkey",
    "suggest_transform_hotkey",
    "test_hotkey",
    "trigger_permission_requests",
    "try_command",
    "try_it_status",
    "try_transform",
    "try_vocab",
    "undo_history_change",
    "validate_api_key",
    "validate_cerebras_key",
    "validate_groq_key",
    "wizard_cleanup_step2",
    "wizard_init_step2",
    "wizard_start_hotkey_test",
    "wizard_stop_hotkey_test",
})


class ClosedApi:
    """The ``js_api`` pywebview is given: it has no attributes, so neither the
    list of functions pywebview builds nor an attribute path reaches Python."""

    __slots__ = ()

    def __getattribute__(self, name):
        raise AttributeError(name)

    def __dir__(self):
        return []

    def __repr__(self):  # pragma: no cover - for logs only
        return "<ClosedApi>"


def _bridge_function(api, name: str) -> Callable:
    method = getattr(api, name)

    def call(*args):
        return method(*args)

    call.__name__ = name
    call.__qualname__ = name
    call.__doc__ = getattr(method, "__doc__", None)
    return call


def bridge_functions(api, allowed: Iterable[str] = ALLOWED) -> List[Callable]:
    """One plain function per allowed name, each calling ``api.<name>``. A
    name the Api does not have is skipped (and the test names it)."""
    out = []
    for name in sorted(allowed):
        if name.startswith("_") or "." in name:
            raise ValueError(f"not a bridge name: {name!r}")
        if callable(getattr(api, name, None)):
            out.append(_bridge_function(api, name))
    return out


def expose(window, api, allowed: Iterable[str] = ALLOWED) -> List[str]:
    """Expose the allowed Api methods on a pywebview window created with
    ``js_api=ClosedApi()``. Returns the names exposed."""
    funcs = bridge_functions(api, allowed)
    window.expose(*funcs)
    return [f.__name__ for f in funcs]
