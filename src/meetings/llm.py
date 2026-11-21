"""The AI layer: structured notes, titles and questions, on the user's own key.

Providers: Anthropic (Claude), OpenAI, Groq, or any OpenAI-compatible
endpoint (OpenRouter, a local Ollama, ...). "auto" picks the first provider
that has a key, Claude first.
"""
from __future__ import annotations

import re
import time

from . import keys
from .transcribe import GROQ_BASE_URL

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
_GROQ_PREFERRED = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2",
                   "qwen/qwen3-32b", "llama-3.1-8b-instant")
_OPENAI_PREFERRED = ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "gpt-4o")
# How much transcript one request may carry. Groq's free tier limits tokens
# per minute, so long transcripts go through in parts there.
# "custom" is sized for a local model served with a 16k-token window.
_PART_CHARS = {"groq": 16000, "anthropic": 600000, "openai": 300000, "custom": 36000}
_LOCAL_PREFERRED = ("gemma4:e2b", "gemma4:e4b")     # measured best for notes at their size
_OLLAMA_CTX_STEPS = (4096, 8192, 16384)             # few distinct sizes = few model reloads
_ollama_roots: dict[str, str | None] = {}

_models_cache: dict[str, tuple[float, list[str]]] = {}


# What a meeting says in place of its notes when no model can write them: the
# transcript is there, and this is a plain line, not a failure. (Notes and
# questions are written by a language model, which the speech model on this
# computer is not.)
NOTES_NEED_KEY = ("The transcript is saved. Writing notes needs an API key or Waffler Plus: "
                  "add a key in Settings and the notes can be written then.")
ASK_NEEDS_KEY = "Answering questions needs an API key or Waffler Plus. Add a key in Settings."


class LLMError(Exception):
    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


def can_write(settings: dict) -> bool:
    """Is there a model to ask (a key, or a custom endpoint)? Asks nobody."""
    try:
        resolve_provider(settings)
        return True
    except LLMError:
        return False


def available_providers() -> list[str]:
    return [p for p in ("anthropic", "openai", "groq", "custom") if keys.get_key(p)]


def resolve_provider(settings: dict) -> str:
    want = settings.get("llm_provider") or "auto"
    have = available_providers()
    if want != "auto":
        if want == "custom" and settings.get("custom_base_url"):
            return "custom"      # a local endpoint may need no key
        if want in have:
            return want
        raise LLMError(f"No {want} key is set. Add one in Settings.", "no_key")
    for p in have:
        if p != "custom" or settings.get("custom_base_url"):
            return p
    raise LLMError(NOTES_NEED_KEY, "no_key")


def _openai_client(provider: str, settings: dict):
    from openai import OpenAI
    base = {"groq": GROQ_BASE_URL, "custom": settings.get("custom_base_url") or None}.get(provider)
    # A model on your own machine can take minutes; a hosted one should not.
    return OpenAI(api_key=keys.get_key(provider) or "none", base_url=base,
                  timeout=1800.0 if provider == "custom" else 180.0, max_retries=1)


def list_models(provider: str, settings: dict | None = None) -> list[str]:
    """The provider's own model list (cached for ten minutes); [] if it can't be read."""
    settings = settings or {}
    hit = _models_cache.get(provider)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=keys.get_key("anthropic"), timeout=30.0)
            ids = [m.id for m in client.models.list(limit=100)]
        else:
            ids = sorted(m.id for m in _openai_client(provider, settings).models.list())
            skip = ("whisper", "tts", "embedding", "guard", "audio", "image", "dall-e", "moderation",
                    "realtime", "transcribe")
            ids = [i for i in ids if not any(s in i.lower() for s in skip)]
    except Exception:
        return []
    _models_cache[provider] = (time.time(), ids)
    return ids


def resolve_model(provider: str, settings: dict) -> str:
    chosen = (settings.get("llm_model") or "").strip()
    if chosen and (settings.get("llm_provider") or "auto") == provider:
        return chosen
    if provider == "anthropic":
        return ANTHROPIC_DEFAULT_MODEL
    ids = list_models(provider, settings)
    for want in (_GROQ_PREFERRED if provider == "groq" else _LOCAL_PREFERRED if provider == "custom"
                 else _OPENAI_PREFERRED):
        if want in ids:
            return want
    if ids:
        return ids[0]
    return _GROQ_PREFERRED[1] if provider == "groq" else _OPENAI_PREFERRED[2]


def _anthropic_complete(system: str, messages: list[dict], model: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=keys.get_key("anthropic"), timeout=600.0)
    common = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
    try:
        # Server-side refusal fallback: if a safety classifier declines, the
        # API reroutes the request instead of failing the note.
        with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                         fallbacks="default", **common) as stream:
            msg = stream.get_final_message()
    except (anthropic.BadRequestError, TypeError):
        with client.messages.stream(**common) as stream:
            msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise LLMError("The model declined to write notes for this recording.", "refusal")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _openai_complete(provider: str, settings: dict, system: str, messages: list[dict],
                     model: str, max_tokens: int, effort: str | None = None,
                     on_text=None) -> str:
    client = _openai_client(provider, settings)
    kwargs = dict(model=model, messages=[{"role": "system", "content": system}, *messages])
    if provider == "custom":
        # Self-hosted models (llama.cpp, Ollama, vLLM): "thinking" before the
        # answer multiplies the wait on home hardware and adds little to notes,
        # so it is off unless the user turns it on. Servers that don't know the
        # field get the plain request.
        root = ollama_root(settings.get("custom_base_url") or "")
        if root:
            return strip_thinking(ollama_complete(root, system, messages, model, min(max_tokens, 3000),
                                                  thinking=bool(settings.get("local_thinking", False))))
        if not settings.get("local_thinking", False):
            try:
                resp = client.chat.completions.create(
                    max_tokens=min(max_tokens, 3000), **kwargs,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False},
                                "reasoning_effort": "none"})
                return strip_thinking(resp.choices[0].message.content or "")
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) not in (400, 422):
                    raise
        resp = client.chat.completions.create(max_tokens=min(max_tokens, 3000), **kwargs)
        return strip_thinking(resp.choices[0].message.content or "")
    def create(**more) -> str:
        if on_text is None:
            resp = client.chat.completions.create(max_completion_tokens=max_tokens, **more, **kwargs)
            return (resp.choices[0].message.content or "").strip()
        # `on_text(the words so far)` as they are written, so a long answer
        # is read while it arrives instead of waited for.
        text = ""
        for chunk in client.chat.completions.create(max_completion_tokens=max_tokens, stream=True,
                                                    **more, **kwargs):
            piece = chunk.choices[0].delta.content if chunk.choices else None
            if piece:
                text += piece
                on_text(text)
        return text.strip()

    if effort and provider == "openai":
        # A model that thinks before it answers takes ten seconds and more over
        # a plain question; told not to deliberate it takes three or four. A
        # model that doesn't know the setting is asked again without it.
        try:
            return create(reasoning_effort=effort)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status_code", None) not in (400, 422):
                raise
    return create()


def ollama_root(base_url: str) -> str | None:
    """'http://127.0.0.1:11434/v1' -> 'http://127.0.0.1:11434' when the server is
    Ollama, else None. Ollama's OpenAI-style endpoint cannot set the context
    window (it silently cuts long transcripts to 4,096 tokens) and ignores the
    thinking switch, so Ollama is spoken to through its own API."""
    if not base_url:
        return None
    if base_url not in _ollama_roots:
        import httpx
        root = base_url.rstrip("/")
        root = root[:-3] if root.endswith("/v1") else root
        try:
            ok = "version" in httpx.get(root + "/api/version", timeout=3.0).json()
        except Exception:
            ok = False
        _ollama_roots[base_url] = root if ok else None
    return _ollama_roots[base_url]


def ollama_context(chars: int, max_tokens: int) -> int:
    """The smallest context step that holds the prompt and the reply
    (about 3 characters per token, which is pessimistic for English)."""
    need = chars // 3 + max_tokens + 256
    return next((step for step in _OLLAMA_CTX_STEPS if step >= need), _OLLAMA_CTX_STEPS[-1])


def ollama_complete(root: str, system: str, messages: list[dict], model: str, max_tokens: int,
                    thinking: bool = False) -> str:
    import httpx
    msgs = [{"role": "system", "content": system}, *messages]
    body = {"model": model, "messages": msgs, "stream": False, "think": thinking,
            "options": {"num_ctx": ollama_context(sum(len(m["content"]) for m in msgs), max_tokens),
                        "num_predict": max_tokens}}
    r = httpx.post(root + "/api/chat", json=body, timeout=1800.0)
    if r.status_code == 400 and "think" in r.text.lower():     # a model with no thinking switch
        body.pop("think")
        r = httpx.post(root + "/api/chat", json=body, timeout=1800.0)
    if r.status_code == 404:
        raise LLMError(f"The model '{model}' isn't installed in Ollama. Run: ollama pull {model}", "model")
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content", "")


def strip_thinking(text: str) -> str:
    """Some local models write their reasoning inline as <think>...</think>."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "</think>" in text.lower():                 # an unopened block: keep what follows it
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text.strip()


def complete(system: str, messages: list[dict], settings: dict, max_tokens: int = 16000,
             backend=None, effort: str | None = None, on_text=None) -> str:
    """One model reply. `backend(system, messages)` replaces the network in tests.
    `effort` is how hard a thinking model should think (Ask passes "low").
    `on_text(words so far)` is called as the reply is written, where the
    provider can do that; the whole reply is still what comes back."""
    if backend is not None:
        return backend(system, messages)
    provider = resolve_provider(settings)
    model = resolve_model(provider, settings)
    last: Exception | None = None
    for attempt in range(4):
        try:
            if provider == "anthropic":
                return _anthropic_complete(system, messages, model, max_tokens)
            return _openai_complete(provider, settings, system, messages, model,
                                    min(max_tokens, 8000), effort=effort, on_text=on_text)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            status = getattr(e, "status_code", None)
            name = type(e).__name__.lower()
            if status == 429 or "ratelimit" in name:
                if attempt < 3:
                    time.sleep(min(60, 15 * (attempt + 1)))
                    continue
                raise LLMError("The AI provider is rate-limiting. Try again in a minute.", "rate")
            if status in (401, 403) or "authentication" in name:
                raise LLMError(f"The {provider} key was refused. Check it in Settings.", "auth")
            if status == 404 or "notfound" in name:
                raise LLMError(f"The model '{model}' isn't available on {provider}. "
                               "Choose another in Settings.", "model")
            if ("connection" in name or "timeout" in name) and attempt < 1:
                time.sleep(2)
                continue
            break
    raise LLMError(f"Couldn't write notes ({type(last).__name__}).", "other")


# ── notes ───────────────────────────────────────────────────────────────────

NOTES_SYSTEM = """You turn a recording's transcript into notes its owner will rely on later.

You are given the transcript (lines marked "Me:" are the owner speaking into their microphone, \
"Them:" is everyone heard through the computer's speakers) and, sometimes, rough notes the owner \
typed while recording. The typed notes show what the owner cares about: keep every point in them, \
correct them against the transcript, and fill in what they left out.

Rules:
- Only what was said. No invented names, numbers, dates, decisions or actions. If something is \
unclear in the transcript, say so briefly rather than guessing.
- Speech-to-text mishears names and jargon; use the most plausible spelling consistently.
- Write numbers, money, dates and times as figures (£412,000, 14 January, 3 pm), never spelled out.
- Be specific and compact: concrete facts, figures, names, decisions and reasons. No filler, no \
commentary on the conversation itself, no closing remarks.
- Markdown: `###` headings, `-` bullets, nested bullets for detail. No top-level title, no bold \
labels at the start of bullets, no tables.
- Write in the language of the transcript.

Output format, exactly: a first line `TITLE: <a specific title, at most 8 words, no quotes>`, a \
blank line, then the notes."""


def _split_parts(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, cur, cur_len = [], [], 0
    for line in text.split("\n"):
        while len(line) > size:                       # one enormous line
            parts.append(line[:size])
            line = line[size:]
        if cur_len + len(line) + 1 > size and cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


def tidy_markdown(text: str) -> str:
    """Models sometimes write a section heading as a bare or bold line
    ("Next steps", "**Next steps**"). A short line with no end punctuation,
    directly above a bullet, is a heading: mark it as one."""
    lines = text.replace("\r", "").split("\n")
    if not any(ln.lstrip().startswith("#") for ln in lines):
        # Small models sometimes write sections as top-level bullets with the
        # points nested beneath. Promote those bullets to headings.
        promoted = []
        for i, line in enumerate(lines):
            m = re.match(r"^[-*•]\s+(?:\*\*)?(.+?)(?:\*\*)?:?\s*$", line)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if m and re.match(r"^\s{2,}[-*•]\s", nxt) and len(m.group(1).split()) <= 8 \
                    and m.group(1)[-1] not in ".!?":
                promoted.append(("\n" if promoted else "") + f"### {m.group(1)}")
            else:
                promoted.append(re.sub(r"^\s{2,4}([-*•]\s)", r"\1", line) if promoted else line)
        if any(p.lstrip("\n").startswith("### ") for p in promoted):
            lines = "\n".join(promoted).split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
        bare = re.sub(r"^\*\*(.+?)\*\*:?$", r"\1", s).rstrip(":")
        if (s and not s.startswith(("#", "-", "*", "•", ">")) or s.startswith("**")) \
                and not re.match(r"^\d+[.)]\s", s) and bare and len(bare.split()) <= 7 \
                and bare[-1] not in ".!?,;" and re.match(r"^([-*•]|\d+[.)])\s", nxt):
            if out and out[-1].strip():
                out.append("")
            out.append(f"### {bare}")
        else:
            out.append(line)
    return "\n".join(out).strip()


def parse_notes(reply: str) -> tuple[str, str]:
    """(title, markdown) from the model's reply."""
    reply = reply.strip()
    m = re.match(r"^\s*(?:TITLE|Title)\s*:\s*(.+?)\s*(?:\n|$)", reply)
    if m:
        return m.group(1).strip().strip('"').strip("*#").strip(), tidy_markdown(reply[m.end():])
    m = re.match(r"^#\s+(.+?)\s*\n", reply)
    if m:
        return m.group(1).strip(), tidy_markdown(reply[m.end():])
    # Small local models often give the title as a bare first line, then a blank line.
    m = re.match(r"^(?:\*\*)?([^\n#\-*•>][^\n]{2,80}?)(?:\*\*)?[ \t]*\n[ \t]*\n(?=\s*(?:#|[-*•]\s))", reply)
    if m and len(m.group(1).split()) <= 10 and m.group(1).strip()[-1] not in ".!?:":
        return m.group(1).strip(), tidy_markdown(reply[m.end():])
    return "", tidy_markdown(reply)


def write_notes(transcript: str, my_notes: str, template_prompt: str, settings: dict,
                backend=None, when: str = "") -> tuple[str, str]:
    """(title, notes markdown) for a whole transcript."""
    transcript = transcript.strip()
    if not transcript and not my_notes.strip():
        raise LLMError("There is nothing to write notes from yet.", "empty")
    provider = "custom" if backend else resolve_provider(settings)
    size = _PART_CHARS.get(provider, 60000)
    parts = _split_parts(transcript, size)
    if len(parts) > 1:
        # Too long for one request: condense each part, then write from those.
        condensed = []
        for i, part in enumerate(parts, 1):
            condensed.append(complete(
                "Condense this part of a transcript into detailed bullet points. Keep every fact, "
                "figure, name, decision, request and action, and who said it (Me/Them). No preamble.",
                [{"role": "user", "content": f"Part {i} of {len(parts)}:\n\n{part}"}],
                settings, max_tokens=4000, backend=backend))
        transcript = "\n\n".join(f"[Part {i}]\n{c}" for i, c in enumerate(condensed, 1))
        source = "Detailed digest of the transcript (it was too long to send whole)"
    else:
        source = "Transcript"
    user = []
    if when:
        user.append(f"Recorded: {when}")
    user.append(f"How to structure these notes:\n{template_prompt}")
    if my_notes.strip():
        user.append(f"The owner's own typed notes:\n<my_notes>\n{my_notes.strip()}\n</my_notes>")
    user.append(f"{source}:\n<transcript>\n{transcript}\n</transcript>")
    reply = complete(NOTES_SYSTEM, [{"role": "user", "content": "\n\n".join(user)}], settings,
                     backend=backend)
    return parse_notes(reply)


ASK_SYSTEM = """You answer questions about the user's own recorded meetings and voice notes, using \
only the material provided. "Me" in a transcript is the user. Be direct and specific; quote short \
phrases when wording matters. If the material doesn't contain the answer, say so plainly. When \
several notes are provided, name the note (title and date) each point comes from. Keep it short: \
the answer first, in a few lines, and more only when the question asks for detail. Plain Markdown, \
no headings."""


def ask(question: str, context: str, history: list[dict], settings: dict, backend=None,
        extra: str = "", on_text=None) -> str:
    """`extra` is more material besides the notes (Waffler's dictations). It
    keeps up to half the room, so long notes never push it out."""
    provider = "custom" if backend else resolve_provider(settings)
    size = _PART_CHARS.get(provider, 60000)
    cut = "\n[...material cut here: it was too long to send whole...]"
    extra = (extra or "")[:size // 2]
    room = size - len(extra)
    if len(context) > room:
        context = context[:room] + cut
    if extra:
        context = (context + "\n\n" + extra).strip()
    messages = [{"role": "user", "content": f"<material>\n{context}\n</material>\n\n"
                                            "Use this material to answer my questions."},
                {"role": "assistant", "content": "Understood."}]
    for h in history[-12:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": question})
    return complete(ASK_SYSTEM, messages, settings, max_tokens=4000, backend=backend, effort="low",
                    on_text=on_text)
