"""Try it here (Settings, Transforms and Command mode).

The text typed into the page's box stands in for the selection, and the typed
instruction for the spoken one. From there it is the path a transform
(src/transform_action.py) or a command (src/command_action.py) takes once the
text has been read: the same request, the same model call (the styler's
``_chat``, the person's own providers in their order) and the same gate on the
answer. The box's text is sent to that provider, as a transform's is, and the
call is counted in Usage.

Nothing else happens: no app is read, nothing is pasted or copied, nothing is
kept in the Journal, and a question opens no browser (the search it would open
comes back for the page to show as a link).

Every function answers ``{"ok": bool, ...}`` and never raises.
"""
import time
from typing import Callable, Optional

try:
    import command_mode as cm
    import transforms
    import user_messages as um
except ImportError:                 # pragma: no cover - imported as src.try_it
    from src import command_mode as cm
    from src import transforms
    from src import user_messages as um

ASK_DEADLINE_S = 28.0               # the whole model call, every provider tried (a transform's, less its margin)

EMPTY_BOX = "Type some text first."
TOO_LONG = "That's too much text for one go (limit 1,000 words)."
NO_INSTRUCTION = "Say what it should do first."
NEEDS_KEY = "Add a key first, in Keys and providers."
FAILED = "Something went wrong. Try again."

_PROVIDERS = ("groq", "cerebras", "openai")


def _noop(*_a, **_k):
    return None


def _plus_ready() -> bool:
    """Is Waffler Plus the chosen way and there to use (src/account.py)?"""
    try:
        try:
            from src import account
        except ImportError:
            import account
        return bool(account.plus_ready())
    except Exception:               # noqa: BLE001
        return False


def ready(styler) -> bool:
    """Is there a provider the styler's ``_chat`` can ask? That call is the
    one a transform and a command make, and it asks the person's own keys,
    or Waffler Plus when that is the chosen way: then no key is needed."""
    if styler is not None and _plus_ready():
        return True
    try:
        return any(bool(styler._provider_ready(name)) for name in _PROVIDERS)
    except Exception:               # noqa: BLE001 - no styler yet, or one that cannot say
        return False


def status(styler) -> dict:
    """``{"ok": True, "ready": bool, "needs": "" | "key"}``."""
    on = ready(styler)
    return {"ok": True, "ready": on, "needs": "" if on else "key"}


def _ask(chat, request, purpose: str, monotonic, record_usage, log):
    """(reply, None) or (None, the styler's reason for the failure)."""
    try:
        reply = chat(request.system, request.user, max_tokens=request.max_tokens,
                     temperature=request.temperature, deadline=monotonic() + ASK_DEADLINE_S,
                     purpose=purpose)
    except Exception as e:          # noqa: BLE001 - ChatError carries a reason; anything else has none
        reason = str(getattr(e, "reason", "") or "")
        log(f"[try-it] {purpose}: no answer ({reason[:120] or type(e).__name__})")
        return None, reason
    try:
        (record_usage or _noop)(reply)
    except Exception as e:          # noqa: BLE001 - a usage row never costs the result
        log(f"[try-it] usage not recorded: {type(e).__name__}")
    return reply, None


def transform(text, *, stored, chat: Callable, dashes: Optional[Callable] = None,
              profanity: Optional[Callable] = None, record_usage: Optional[Callable] = None,
              log: Callable = _noop, monotonic: Callable = time.monotonic) -> dict:
    """Polish ``text`` with the person's Polish rules.

    ``{"ok": True, "changed": True, "text": the rewrite}``, or with
    ``"changed": False`` and a ``"message"`` when the text needed nothing, or
    ``{"ok": False, "error": one sentence}``."""
    try:
        source = text if isinstance(text, str) else ""
        problem = transforms.selection_problem(source)
        if problem == transforms.EMPTY:
            return {"ok": False, "error": EMPTY_BOX}
        if problem:
            return {"ok": False, "error": TOO_LONG}
        spec = transforms.spec_for(stored, transforms.POLISH)
        reply, reason = _ask(chat, transforms.build_request(spec, source), "transform",
                             monotonic, record_usage, log)
        if reply is None:
            return {"ok": False, "error": um.transform_failed_message(reason)[1]}
        out = transforms.finish(source, reply.text, spec,
                                finish_reason=getattr(reply, "finish_reason", None),
                                dashes=dashes, profanity=profanity)
        if out.status == transforms.REPLACE:
            return {"ok": True, "changed": True, "text": out.text}
        if out.status == transforms.NO_CHANGE:
            return {"ok": True, "changed": False, "text": source, "message": um.TRANSFORM_NO_CHANGE[0] + "."}
        said = {transforms.TRUNCATED: um.TRANSFORM_CUT_SHORT,
                transforms.UNRELATED: um.TRANSFORM_UNRELATED,
                transforms.EMPTY_ANSWER: um.TRANSFORM_NO_ANSWER}.get(out.status, um.TRANSFORM_BAD_LENGTH)
        return {"ok": False, "error": said[1]}
    except Exception as e:          # noqa: BLE001 - the page gets a sentence, never a traceback
        log(f"[try-it] transform failed: {type(e).__name__}")
        return {"ok": False, "error": FAILED}


def command(text, instruction, *, stored, chat: Callable, dashes: Optional[Callable] = None,
            profanity: Optional[Callable] = None, record_usage: Optional[Callable] = None,
            log: Callable = _noop, monotonic: Callable = time.monotonic) -> dict:
    """Carry out ``instruction`` on ``text`` (the box; empty for nothing selected).

    An edit or new text: ``{"ok": True, "action": "edit" | "draft", "changed":
    True, "text": ...}`` (``"changed": False`` with a ``"message"`` when the
    text already matched). A question: ``{"ok": True, "action": "ask",
    "query", "engine": its name, "url": the search it would open}``; nothing
    is opened. Else ``{"ok": False, "error": one sentence}``."""
    try:
        selection = text if isinstance(text, str) and text.strip() else ""
        said = cm.tidy_instruction(instruction if isinstance(instruction, str) else "")
        bad = cm.instruction_problem(said)
        if bad == "empty":
            return {"ok": False, "error": NO_INSTRUCTION}
        if bad:
            return {"ok": False, "error": um.COMMAND_TOO_LONG[1]}
        if selection and transforms.selection_problem(selection) == transforms.TOO_LONG:
            return {"ok": False, "error": TOO_LONG}
        request = cm.build_request(said, selection, "")
        reply, reason = _ask(chat, request, "command", monotonic, record_usage, log)
        if reply is None:
            return {"ok": False, "error": um.command_failed_message(reason)[1]}
        parsed = cm.parse_reply(getattr(reply, "text", ""), has_selection=bool(selection),
                                instruction=said, own_text=selection,
                                max_chars=cm.reply_limit(request.max_tokens))
        if parsed.action == cm.ASK:
            engine = cm.engine_for(cm.settings(stored)["web_engine"])
            query = cm.ask_query(parsed.payload, said)
            return {"ok": True, "action": cm.ASK, "query": query, "engine": engine.label,
                    "url": cm.search_url(engine.id, query)}
        cut = "length" if parsed.cut else getattr(reply, "finish_reason", None)
        if parsed.action == cm.EDIT:
            out = cm.finish_edit(selection, parsed.payload, finish_reason=cut, tagged=parsed.tagged,
                                 dashes=dashes, profanity=profanity)
        else:
            out = cm.finish_draft(parsed.payload, said, "", finish_reason=cut,
                                  dashes=dashes, profanity=profanity)
        if out.status == transforms.REPLACE:
            return {"ok": True, "action": parsed.action, "changed": True, "text": out.text}
        if out.status == transforms.NO_CHANGE:
            return {"ok": True, "action": parsed.action, "changed": False, "text": selection,
                    "message": um.COMMAND_NO_CHANGE[1]}
        refused = {transforms.TRUNCATED: um.COMMAND_CUT_SHORT,
                   transforms.EMPTY_ANSWER: um.COMMAND_NO_ANSWER,
                   transforms.UNRELATED: um.TRANSFORM_UNRELATED,
                   cm.REFUSED: um.COMMAND_REFUSED}.get(out.status, um.COMMAND_BAD_LENGTH)
        return {"ok": False, "error": refused[1]}
    except Exception as e:          # noqa: BLE001 - the page gets a sentence, never a traceback
        log(f"[try-it] command failed: {type(e).__name__}")
        return {"ok": False, "error": FAILED}
